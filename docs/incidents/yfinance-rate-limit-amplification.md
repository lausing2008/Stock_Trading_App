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

