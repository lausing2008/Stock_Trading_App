## Recurring Issue: BUG-YFCALLVOL2 — `_fetch_live_bulk()`'s Unconditional Per-Symbol Fallback Amplified a Real Yahoo Rate-Limit Event (2026-08-17)

**Symptom**: user reported "HK market not trading, the market seems good" while looking at a
real, live Paper Portfolio dashboard. Both HK SWING and HK GROWTH portfolios showed a
"Not trading: Signal exists but st..." badge.

**First checked and ruled out — HK's own market-hours logic was correct**: confirmed live
against production that `_is_market_hours("HK")` correctly returned `True` (Monday 2:39pm HKT,
squarely inside HK's regular 13:00-16:00 afternoon session, not a holiday). This was never an
HK-specific bug.

**Root cause — a genuine, live, active Yahoo-side rate-limit event, amplified by this app's own
retry pattern**: `_fetch_live_bulk()` (`services/market-data/src/api/routes.py`) fetches the
whole tracked universe (~150+ symbols) in ONE `yf.download()` call, then unconditionally retries
EVERY symbol missing from that result via an individual `_fetch_live_one()` call (up to 4
concurrent, each up to 2 HTTP requests — a `fast_info` attempt plus a `history()` fallback
inside it). Direct log inspection during the live incident showed `live_prices.bulk_fallback`
firing with `count=165` — the ENTIRE universe — repeatedly, every 1-2 minutes, oscillating
against cycles that fully succeeded. Confirmed the rate limit was real and Yahoo-side (not a
local network issue) via a direct `curl` from inside the container to Yahoo's own chart
endpoint, which returned a clean `200`. The SAME condition that made the bulk call fail
guaranteed the ~150-request individual fallback ALSO got rate-limited — but running that same
storm again every single minute with zero backoff kept re-triggering (and very plausibly
extending) the exact throttle window this app needed to wait out, rather than letting it
recover. `stockai:live_prices` (the shared Redis cache every fast alert/screener/dashboard
reads) ended up serving only a handful of symbols on the worst cycles — HK stocks looked
"not trading" simply because they were a minority of whatever tiny trickle succeeded that
minute, with zero HK-specific cause.

**This is the exact BUG-YFCALLVOL (2026-08-07) amplification pattern recurring in a SECOND,
never-touched call site.** The original fix only rewrote `paper_trading_engine.py`'s
`_fetch_live_prices()` (a per-5-minute paper-trading price fetch that falsely claimed to batch
but issued ~107 individual requests) to use one clean `yf.download()` call with NO fallback at
all. `_fetch_live_bulk()` in `routes.py` already had the correct single-`yf.download()` batch
call — its bug was the unconditional per-symbol fallback loop layered ON TOP of an already-
correct batch call, a subtly different defect the original fix never touched since it lived in
a completely different file.

**Fix applied**: capped the fallback — `_LIVE_BULK_FALLBACK_MAX = 20`. A miss count `<= 20` (a
real, small handful of stragglers the batch endpoint occasionally omits even under normal
conditions) still uses the individual fallback exactly as before. A miss count above 20 is
treated as evidence of an active rate-limit event and the fallback is skipped ENTIRELY for that
cycle, logging `live_prices.bulk_fallback_skipped_too_many_misses` instead of firing more
requests. The cache simply serves fewer symbols that one minute and recovers on its own the
moment Yahoo's throttle clears.

**A separate, unrelated finding from the same investigation, correctly NOT treated as a bug**:
the "Not trading: Signal exists but stock isn't on this style's watchlist" badge on both HK
portfolios traced to `_scan_for_entries()`'s `growth_stock_ids` query
(`paper_trading_engine.py`), which correctly restricts real BUY candidates to `Stock.market ==
cfg["market"]` (confirmed HK-only, no market-crossover bug) but checks membership against
`WHERE Watchlist.trading_style == style` with no market scoping at all. Directly queried
production: every SWING-style watchlist is US-heavy (6 HK stocks total across all SWING
watchlists combined; GROWTH is somewhat better at 22 HK vs. 53 US). A real HK SWING BUY signal
correctly gets "not on watchlist" simply because the matching HK stock was never added to any
SWING-labeled watchlist — working as designed, not a bug, but a real, previously-undocumented
data-thinness gap worth a future watchlist-curation pass (add more HK stocks to the relevant
watchlists) rather than a code fix.

**Tests**: `services/market-data/tests/test_fetch_live_bulk_fallback_cap.py` (5 cases) — a small
miss count still uses the fallback (the real straggler-filling case), a large miss count (150
symbols, matching the real live incident's exact universe size) skips the fallback entirely with
zero `_fetch_live_one` calls, exact-boundary tests at and one-over `_LIVE_BULK_FALLBACK_MAX`, and
a zero-miss case confirming the fallback branch is never touched when the bulk call fully
succeeds.

**A real test-design bug of my own, caught via adversarial verification, not shipped**: the
first version of the two boundary tests derived their fixture size directly from
`_LIVE_BULK_FALLBACK_MAX` itself (`range(_LIVE_BULK_FALLBACK_MAX + 1)`) — correct at the real
threshold (20), but sabotaging the constant to `999999` to verify the test catches a threshold
misconfiguration made the test attempt to build and process a MILLION-item `ThreadPoolExecutor`
fixture and time out, rather than failing on a real assertion — the exact "still passes/hangs
after sabotage" red flag this repo's own testing discipline treats as a finding in its own
right, not a shrug. Fixed two ways: (1) the large-miss-count test was changed to use a FIXED
size (150, matching the real live incident, rather than scaling off the value under test); (2)
the two genuine boundary tests (which by their own nature must derive fixture size from the real
constant to test the exact boundary) gained an explicit sanity assert
(`_LIVE_BULK_FALLBACK_MAX < 1000`) that converts a misconfigured/sabotaged huge constant into a
fast, clear failure instead of a hang.

Adversarially verified 2 sabotage/revert cycles, both caught cleanly (no hang) after the test
fix: (1) disabling the cap check entirely (`if False:`) — caught by exactly the 2 dedicated
large-miss-count tests; (2) raising `_LIVE_BULK_FALLBACK_MAX` to `999999` — caught by all 3
boundary/large-count tests, each failing fast on a real assertion in well under a second rather
than hanging. Both sabotages reverted and confirmed byte-identical via md5 before moving on.
Full 1,466-test market-data suite green (up from 1,461); pyflakes clean (confirmed via
`git stash` that all 6 pre-existing warnings predate this change — only line numbers shifted).

**Tracker**: `improvements.tsx` Tier 286 / id `BUG-YFCALLVOL2`.

**What to check if this recurs**:
```bash
# Confirm the fix is present:
docker exec stockai-market-data-1 grep -n '_LIVE_BULK_FALLBACK_MAX\|bulk_fallback_skipped_too_many_misses' /app/src/api/routes.py

# Check whether a rate-limit event is currently happening — a real, external Yahoo throttle,
# not this app's own network/DNS issue (confirmed via a direct curl from inside the container):
docker exec stockai-market-data-1 curl -s -o /dev/null -w '%{http_code}\n' 'https://query1.finance.yahoo.com/v8/finance/chart/AAPL' -A 'Mozilla/5.0' --max-time 10

# Check recent fallback-skip frequency (a sustained pattern of this line means a rate-limit
# event is actively ongoing, which this fix correctly refuses to make worse):
docker logs stockai-market-data-1 --since 10m | grep -c 'bulk_fallback_skipped_too_many_misses'

# Check the live cache's current symbol count/HK representation directly:
docker exec stockai-redis-1 redis-cli get stockai:live_prices | python3 -c "
import json, sys
data = json.load(sys.stdin)
hk = [r for r in data if r.get('symbol','').upper().endswith('.HK')]
print(f'Total: {len(data)}, HK: {len(hk)}')"

# Check HK watchlist thinness (the SEPARATE, correct-behavior finding from the same investigation):
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "
SELECT w.id, w.name, w.trading_style,
       COUNT(wi.id) FILTER (WHERE s.market = 'HK') AS hk_items,
       COUNT(wi.id) FILTER (WHERE s.market = 'US') AS us_items
FROM watchlists w
LEFT JOIN watchlist_items wi ON wi.watchlist_id = w.id
LEFT JOIN stocks s ON s.id = wi.stock_id
WHERE w.trading_style IN ('SWING', 'GROWTH')
GROUP BY w.id, w.name, w.trading_style;"
```

---


---

## AUD-ADDSTOCK-MISATTRIBUTED (2026-10-02) — "not able to add stock": two things I got wrong

**Reported by the user**: adding a stock from the dashboard failed that morning.

**What the logs establish.** `POST /admin/add_stock` returned **502** at 15:38, 15:39, 15:45
and 17:23 UTC, then **200** at 23:44. market-data had been up since 07:15, so this was not a
deploy artefact, and the handler's code path was untouched by that day's releases. Each
failure logged `add_stock.start` and then nothing — no `add_stock.done` — with ~3 seconds in
between.

**THE DIAGNOSIS I GOT WRONG, AND WHY IT MATTERS.** I concluded `_fetch_yf_info` had no retry
and added one. It already had one. `BUG-ADDSTOCK-NORETRY` (2026-08-07, above) had given it a
3-attempt tenacity policy with 1–8s exponential backoff, sitting in a decorator directly above
the `def`. I had run `grep -n "_fetch_yf_info" -A 16`, which showed the function BODY and
nothing above it, and never checked.

What I then shipped into my working tree was a second retry layer wrapping the first: 3 × 3 =
**up to nine calls into a live rate-limit storm** — the exact amplification this file exists
to warn about, introduced while citing BUG-YFCALLVOL2 as my reason for being careful. It was
caught by this repo's own pre-existing test for the August fix, which my change broke; without
that test it would have reached production as a worsening of the condition it was meant to
fix.

**The lesson is narrow and mechanical:** `grep -A` from a `def` shows the body, not the
decorators. A function's retry, auth, caching and rate-limit policy all live in the lines
*above* its name. Read the whole definition before concluding a behaviour is absent.

So the handler's one source of 502 was not a bare call — it was an already-retried call whose
final failure was misclassified:

```python
try:
    info = _fetch_yf_info(symbol)      # 3 tenacity attempts, up to ~8s, since 2026-08-07
except Exception as exc:
    raise HTTPException(502, f"yfinance error: {exc}")   # ...then everything became 502
```

**A NUMBER I REPORTED AND HAD TO WITHDRAW.** I first said "44,016 rate-limit lines in 12
hours" and attributed them to yfinance. That came from
`grep -icE "429|rate.?limit|too many requests"` across the whole market-data log, which does
not distinguish providers — and the first lines it returned were
`{"adapter": "unusual_whales", "error": "Unusual Whales rate limit exceeded"}`. The user
challenged it on exactly the right grounds: heavy options-chain traffic *had* been migrated to
Unusual Whales, so a yfinance exhaustion claim did not fit what they knew about the system.

The same session had already been bitten once by a loose grep matching `500` inside
`limit=500`. Twice in one investigation is a pattern, not bad luck: **when counting provider
errors, group by the provider field rather than by a text match across every line.**

**The corrected figures, for the same 12-hour window:**

| | Count |
|---|---:|
| `ingest.adapter_failed`, adapter `unusual_whales` | 13,131 |
| `ingest.adapter_failed`, adapter `yfinance` | 6,986 |
| — of which `"Too Many Requests. Rate limited."` | **6,566** |
| `ingest.adapter_failed`, adapter `alpha_vantage` | 563 |

So yfinance *is* genuinely rate-limited — the original conclusion survived — but the evidence
for it is 6,566 provider-attributed errors, not the 44,016 mixed-provider lines first quoted.

**Why yfinance is still exhausted after the UW migration.** The migration was real and it
holds: options chains moved. What did not move is **intraday bar ingestion**.

| yfinance calls, 12h | Count | Share |
|---|---:|---:|
| `tf: 5m` | **25,601** | 91.5% |
| `tf: 1d` | 2,372 | 8.5% |

183 active symbols on a 5-minute cycle is ~140 cycles per 12 hours — which is exactly 25,601
calls. The 5-minute refresh, not the options work, is what consumes the budget.

**What is still NOT established.** The handler never logged the exception, so the specific
four 502s cannot be *proved* to have been rate-limit responses. 6,566 rate-limit errors in the
same window makes it the overwhelmingly likely cause, and a successful manual fetch of ASTS,
COIN and AAPL hours later is consistent with a transient window — but "likely" is where this
stops. The fix adds `add_stock.rate_limited` / `add_stock.provider_error` logging precisely so
the next occurrence is decided by evidence rather than inference.

**The fixes that remain, after removing my duplicate retry.**

1. A throttle returns **503 + `Retry-After`**, not 502. "Come back shortly" and "the upstream
   is broken" are different facts, and only one of them is true here.
2. The failure is **logged** (`add_stock.rate_limited` / `add_stock.provider_error`), not just
   returned in the response body. That is why the four production 502s cannot be diagnosed
   today, and why the next one will be.
3. **The message no longer blames the user.** The modal's fallback read *"Failed to add —
   check the ticker symbol"* for every failure including a throttle, sending someone to hunt
   for a typo in a symbol that was correct. That is the half of this incident that actually
   wasted the user's time, and it was a one-line default nobody had revisited.

There is now **exactly one** retry policy on this path, pinned by a test that counts the
decorators on `_fetch_yf_info` and fails if a second wrapper reappears. The new multi-symbol
endpoint is sequential and capped at 25 for the same underlying reason.

**Left open, and worth its own decision:** UW's 13,131 adapter failures are a larger number
than yfinance's and are not addressed here at all. And nothing was done about the 5-minute
ingest volume itself — reducing it, staggering it, or moving bars to a paid provider are all
real options with different costs, and none is a bug fix.

---

## AUD-5M-DUPLICATE-9AM (2026-10-02) — a correct intent, defeated by cron semantics

**Found by asking "where are those requests coming from?"** — the user's question after the
add-stock incident above, which is the right question and had not been asked.

**Where the yfinance volume comes from.** All of it is 5-minute bar ingestion, from two
functions across what were four scheduled jobs, each fetching the whole universe:

| Job | Window (local) | Fires/day | Symbols | Calls/day |
|---|---|---:|---:|---:|
| `us_premarket_5m_early` | 04:00–08:55 ET | 60 | 142 | 8,520 |
| `us_premarket_5m_9am` *(removed)* | 09:00–09:25 ET | 6 | 142 | 852 |
| `us_5m_intraday` | 09:00–15:55 ET | 84 | 142 | 11,928 |
| `hk_5m_intraday` | 09:30–15:55 HKT | 72 | 42 | 3,024 |

≈24,300 calls/day, consistent with the 25,601 measured over a window covering one US session
plus its premarket. **Options chains did migrate to Unusual Whales; bar ingestion never did.**

**THE DUPLICATION.** `us_premarket_5m_9am` fired at 9:00–9:25 ET, justified by a comment
saying it handed off to the intraday job at 9:30. There was no such handoff. `us_5m_intraday`
declares `hour="9,10,11,12,13,14,15"` and `minute="30,...,55,0,...,25"`, and **a cron minute
list applies to every hour in the hour list** — so it already fired at 9:00, 9:05, 9:10, 9:15,
9:20 and 9:25, the exact six slots the premarket job existed to cover. Both then called
`ingest_universe(_symbols_for("US"), "5m")` within the same minute.

Cost: ~852 duplicate calls per trading day against a provider measured refusing 6,566 requests
in a 12-hour window, plus a same-row race of the shape `_run_paper_trading_step`'s lock
already exists for.

**This was not a careless comment.** The author split the 9am trigger out specifically to stop
at 9:25 and avoid double-firing at 9:30 — the intent was exactly right, and cron's
hour×minute cross-product defeated it. Reading the code cannot catch that; **expanding the
trigger into its actual fire times can**, which is what the new test does.

**Fixed by removing the redundant job, deliberately not by narrowing the intraday one.**
Making `us_5m_intraday` genuinely start at 9:30 would match the documented intent, but it
would also stop `_run_paper_trading_step()` running at 9:00–9:25 — a change to **when live
trading logic runs**, which is not a side effect to bundle into a de-duplication. The
premarket PRE-session rows the removed job fed are still written, because `_refresh_5m`
ingests the same bars in the same slots.

**TWO QUESTIONS THIS LEAVES OPEN, both for the owner, neither a defect to fix unilaterally:**

1. **Paper trading runs premarket.** `_refresh_5m("US")` calls `_run_paper_trading_step()` at
   9:00–9:25 ET. `paper_trading_step` has no market-hours gate of its own, and its own
   docstring records `AUD-PT-CROSSMARKETSWEEP` — exits firing outside regular hours that
   closed positions "at a price that never traded that day". Whether premarket monitoring is
   wanted is a trading decision, not a cleanup.
2. **44% of US calls happen before the open.** The premarket window costs 8,520 calls/day and
   exists to populate `session="PRE"` rows for one section of one daily email. Reducing the
   cadence, narrowing the symbol set, or accepting the cost are all legitimate; none is a bug
   fix.
