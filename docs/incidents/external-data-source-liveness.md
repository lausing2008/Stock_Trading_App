## Recurring Issue: Congress Trading Data Silently Empty — Free Source Domains Permanently Dead

**Symptom:** `/congress/trades` (market-data) returns an empty list with no error to every real
user; `congress.tsx`/`insider.tsx` show a permanently empty page with zero indication anything is
broken. `congress_trades` table (shared, written by event-intelligence) has 0 rows no matter how
long the scheduler has been running. Catalyst scoring's congress component
(`compute_congress_score()`, `_compute_risk_score()`'s congress-selling check) silently operates
on zero real data — not fail-open-with-a-flag, just quietly always-zero.

**Root cause (found 2026-07-09):** Both free congress-trading data sources this app depended on —
`housestockwatcher.com/api/transactions` and `senatestockwatcher.com/api/transactions` — are
**permanently dead**: the domains fail to resolve via DNS at all (not a 403/301/timeout on a live
host — confirmed via direct `curl`/`nslookup` from inside the running market-data container). The
underlying project's maintainer has been inactive since March 2021 and never responded to a 2024
GitHub issue asking about a shutdown. This affected TWO independent call sites that both silently
degraded to empty results on fetch failure with no alerting: event-intelligence's
`sync_congress_trades()` (writes the shared `congress_trades` table) and market-data's
`/congress/trades` endpoint (`_fetch_house`/`_fetch_senate`, since replaced by `_fetch_kadoa`).

**Fix applied (2026-07-09):** Repointed both call sites to
`https://raw.githubusercontent.com/kadoa-org/congress-trading-monitor/main/public/data/trades.json`
— a live, unauthenticated, MIT-licensed GitHub JSON feed that updates via daily automated commits.
Covers House Clerk + Senate eFD + OGE executive-branch filings in one combined response (a rolling
~5000-row window, not full history — fine for keeping the feed current going forward, not a
substitute for deep historical backfill). Both call sites now filter to congress-only records
(`branch == "congress"` in event-intelligence; `chamber in ("house", "senate")` in market-data) —
executive-branch OGE filings are ~85% of the feed's rolling window and are NOT congress trades.
Verified live in production: triggered a real sync via `POST /events/sync/congress`, confirmed
441 real rows upserted into `congress_trades` with correct politician names, tickers, transaction
types, and dates.

**What to check if this recurs (either this source dies too, or a similar silent-empty-fetch
pattern shows up elsewhere):**
```bash
# Confirm the current source is actually reachable — DNS failure looks different from a 4xx/5xx:
docker exec stockai-market-data-1 curl -sv 'https://raw.githubusercontent.com/kadoa-org/congress-trading-monitor/main/public/data/trades.json' --max-time 15 2>&1 | head -20
docker exec stockai-market-data-1 nslookup raw.githubusercontent.com

# Check current row count / staleness in the shared table:
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from db import SessionLocal; from sqlalchemy import text
s = SessionLocal()
print(s.execute(text('SELECT COUNT(*), MAX(trade_date) FROM congress_trades')).fetchone())
s.close()"

# Manually trigger a resync (uses the same _service_token() pattern as other scheduler jobs):
docker exec stockai-market-data-1 python3 -c "
import sys, uuid, time; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from common.config import get_settings; from jose import jwt as _jwt; import httpx
s = get_settings()
tok = _jwt.encode({'sub':'scheduler','jti':str(uuid.uuid4()),'exp':int(time.time())+86400}, s.jwt_secret, algorithm='HS256')
r = httpx.post('http://event-intelligence:8010/events/sync/congress', headers={'Authorization': f'Bearer {tok}'}, timeout=30)
print(r.status_code, r.text[:200])"
```

**Design invariant:** Any external free-tier data source this app depends on should have its
fetch failures surfaced somewhere visible (a log line grep, a staleness check) rather than
silently degrading to an empty result — the original bug went undetected for an unknown period
specifically because both call sites' `except: return []` pattern is indistinguishable from
"genuinely no trades today" at the API response level. When adding a new free external data
source, prefer one with committed, checkable update activity (this fix's replacement source
updates via visible daily commits) over an opaque scraped API with no way to verify liveness
without actually calling it.

---


## Recurring Issue: "It's Reachable" ≠ "It's Current" — Always Check Last-Modified, Not Just HTTP 200

**Symptom:** A recommended external data source returns `HTTP 200` and looks like a solid,
official choice, but is actually not being maintained anymore — the page/file is still served,
just frozen at some point in the past. Reachability alone gave false confidence.

**Root cause (found 2026-07-14, while sourcing data for the CAPE/AI-bubble-warning feature):**
An initial research pass recommended Robert Shiller's own Yale dataset
(`econ.yale.edu/~shiller/data/ie_data.xls`) as the primary CAPE data source, citing that it
returned `HTTP 200` as proof it was "verified live." A direct re-check before committing to
that architecture found the file's `Last-Modified` header was **October 2023** — ~2.75 years
stale at investigation time — and Shiller's own site had migrated to a new Yale SOM page with
no working direct CAPE download found there either. The file being downloadable said nothing
about whether its *contents* were still being updated.

**What to check before trusting any "the data source is live" claim** (from an agent, a web
search summary, or your own quick check):
```bash
curl -sI "<candidate-url>" -A "Mozilla/5.0" --max-time 15
# Look at Last-Modified, not just the status code. A 200 with a Last-Modified from
# months/years ago means the URL still resolves but the DATA behind it is frozen.
```
Also directly inspect a few of the most recent rows/values in the actual payload and compare
against today's date — a `.csv`/`.xls` ending "2 years ago" is a hard stop, not a caveat.

**Fix pattern applied:** Re-researched and found `multpl.com` publishes a genuine Atom feed per
indicator (`multpl.com/{indicator}/atom`) — confirmed as a real, intentional, site-wide feature
(identical structure across `shiller-pe`, `s-p-500-pe-ratio`, `s-p-500-dividend-yield`, not a
one-off scrape) and verified via its own `<updated>` timestamp matching the current date, not
just a `200` on the URL. See the CAPE feature reference below for the full source used.

**Design invariant:** Before adopting ANY new external data source (especially one an agent or
a web-search summary recommends), verify current-ness directly — `Last-Modified` header, or the
payload's own embedded timestamp/most-recent-row — not just that the URL responds. An
"official" or "authoritative" source that has gone stale is worse than a well-verified
secondary source, because it looks trustworthy while silently serving frozen data.

---


---

## AUD-ING-POLYGONDELAYED — A Delayed Data Plan Answering "No Bars" Authoritatively (Fixed 2026-09-09)

**How it surfaced.** Not from an alert. While listing the 54 active stocks that belonged to no
watchlist, UBER and CWEN stood out with D1 bars four days older than every US peer. Widening the
query found **4** symbols — UBER, CWEN, ARMK, BIP — all frozen at exactly **2026-09-04**.

**Root cause.** The configured Polygon key is on a **`DELAYED`** plan whose newest daily bar *was*
2026-09-04 — the stall date is the cutoff, not a coincidence. Polygon sat **first** in
`_PRIORITY`, on the reasoning "Polygon is tried first for US because it has a real API (vs
yfinance scraping)" — sound about the *interface*, wrong about the *data*.

For a symbol whose DB head had reached 09-04, the incremental window (`head - 7d .. today`) fell
entirely past the cutoff, and Polygon answered:

```
{"status": "DELAYED", "resultsCount": 0}     HTTP 200
```

An empty success, not an error.

**Why only 4 of 131.** A symbol whose head was further back still had a window *overlapping*
Polygon's coverage, got real bars, and stayed healthy. Only symbols already caught up to the
cutoff could be starved. That uneven blast radius is what made this look like four broken tickers
rather than one broken provider — and it is the reason it survived four days.

**Why nothing alerted — the part worth internalising.** `ingest_symbol("UBER")` returned
`{'inserted': 5}` and logged a clean `ingest.done`. `result.rowcount` on an
`ON CONFLICT DO UPDATE` counts rows **SENT**, not rows **CHANGED**; the 5 were pre-existing bars
re-upserted to identical values. **Zero new data, reported as success.** The `stale_symbols_d1`
DQ gauge — added by the 2026-09-08 ingestion audit specifically to catch per-symbol death — also
missed it.

Same class as the 2026-09-07 six-part series: **not a crash, a silent wrong answer.**

**A correction to the record.** `docs/2026-09-04/DATA_QUALITY_AUDIT.md` states Polygon and Alpha
Vantage are "confirmed dead in live production (blank keys, no runtime override)... yfinance is
the de facto sole data source today." **That is wrong.** Polygon has a live 32-character key
returning HTTP 200, and it was the *preferred* US daily provider. It was not dead — it was worse
than dead: **silently two trading days stale and trusted first**. A genuinely blank key would have
failed over to yfinance and caused no incident. The likely cause of the error is that the audit
inferred deadness from configuration rather than issuing a request.

### The fixes (4)

1. **`_PRIORITY` reordered** to `["unusual_whales", "yfinance", "alpha_vantage", "polygon"]`.
   Polygon is kept, not deleted — it remains a real second opinion for backfills well inside its
   coverage window.
2. **Polygon now RAISES on a delayed-uncovered window** rather than returning an empty frame, so
   the failure is visible in logs instead of being an indistinguishable shrug.
3. **New `UnusualWhalesAdapter`** (`services/market-data/src/adapters/unusual_whales_adapter.py`)
   implementing the real `DataAdapter` ABC against `/api/stock/{ticker}/ohlc/{candle_size}`.
   **US-only** — UW has no HK coverage, so `supports()` returns False for HK and HK routing to
   yfinance is unchanged (verified: `get_adapters("HK","1d") == ["yfinance"]`).
4. **`ingest.done` now reports whether the head actually MOVED** (`advanced`, `head_before`,
   `head_after`, `rows_sent`, `adapter`). `inserted` is retained for backward compatibility but
   no longer means "new bars".

### Three traps in the UW OHLC endpoint — read before touching that adapter

1. **It returns THREE rows per calendar date**, tagged `market_time`: `pr` / `r` / `po`.
   Ingesting the payload as-is writes **3 bars per day** and corrupts every rolling feature.
   Only `r` is the daily bar. Measured: AAPL returns 756 rows over 252 distinct dates — exactly 3×.
2. **`limit` silently returns an EMPTY list when too large.** `limit=5000` → `{"data":[]}`,
   HTTP 200. The *same silent-empty shape* as the Polygon bug this adapter exists to fix. Capped
   at 500 in the adapter rather than trusted to callers.
3. **`/api/screener/stocks` cannot substitute for it.** It genuinely batches (comma-separated
   tickers; 131 US symbols in 3 requests) but **silently caps at 50 rows** and returns
   **`open: None`** — and `validate_ohlcv()` requires `open` and enforces `low <= open <= high`.
   It is a fine *freshness sweep*; it is not a bar source.

### Budget — why per-symbol, not batched

| Approach | Requests/day (131 US symbols) | % of 120k quota |
|---|---|---|
| Per-symbol `/ohlc/1d`, 5 refreshes | 655 | **0.55%** |
| Batched screener, 3 reqs × 5 | 15 | 0.01% |
| Per-symbol, every 5 min (78 cycles) | 10,218 | 8.5% |

Batching would save **640 requests out of 120,000** and buys that saving by fabricating `open`.
Deliberately not done.

### Verification

- Backfilled all 4 symbols from UW; **129/131 US symbols current at 09-08**, zero duplicates.
  OHLC matched yfinance to the cent (UBER `75.615/75.615/72.715/73.13`); volume differs ~0.4%
  (consolidated vs primary-exchange tape).
- The 2 remaining stale symbols are the known-dead SKHYV and SSNLF, not new problems.
- 22 new tests, **five sabotages caught**; full suite **3335 passed, 1 skipped**.

### A vacuous test of my own, caught by sabotage-testing

`test_the_limit_is_capped_below_the_silent_empty_threshold` first asserted the substring
`"_MAX_LIMIT = 500"` — which **also appears in the adapter's own explanatory comment**. Raising
the constant back to the silent-empty 5000 left the test **passing**. Fixed by importing the real
module attribute (`mod._MAX_LIMIT == 500`) instead of grepping source text. This is at least the
fifth instance of this exact failure in this codebase: **a source-text assertion that matches
prose rather than a live statement.** Assert on the imported value, or on a statement that cannot
appear in a comment.

### SECURITY — the Polygon key is exposed in plaintext logs

`PolygonAdapter` passes the key as a **URL query parameter** (`?apiKey=...`), and httpx logs full
request URLs at INFO. The key is therefore sitting in `docker logs stockai-market-data-1` in
plaintext, readable by anyone with host access:

```
INFO:httpx:HTTP Request: GET https://api.polygon.io/v2/aggs/...&apiKey=<REDACTED>
```

**Treat the key as compromised and rotate it.** Polygon accepts `Authorization: Bearer`, which
keeps it out of the URL. **Not fixed in this change** — rotating a credential is the user's call,
and moving it to a header without rotating leaves the already-logged value exposed. Recorded here
so it is not lost.

---

## AUD-ING-POLYGONBUDGET-SKIPSPRIMARY — A Guard That Named a Provider Instead of Excluding One (Fixed 2026-09-09)

**Found one day after the change that caused it**, while writing the Data Pipeline reference page
— i.e. by documenting the system, not by any alert.

`AUD-ING-POLYGONDELAYED` (above) reordered `_PRIORITY` to put `unusual_whales` first and Polygon
last. It did **not** touch this branch in `ingest_symbol()`:

```python
elif not _polygon_budget_available():
    adapters = [get_adapter("yfinance")]
```

That line was **correct** while Polygon was first — *"don't send a request we know will 429, go
straight to the fallback."* Once Polygon moved to last, the same line became actively harmful: it
hardcodes yfinance and therefore **skips the new primary entirely**.

### Why the blast radius was ~126 of 131, not "some Polygon calls"

**The budget counter increments on EVERY US incremental ingest**, whether or not Polygon is ever
reached. With `_POLYGON_BUDGET_PER_MINUTE = 5` and ~131 active US symbols, only the **first 5
symbols per minute** ever saw Unusual Whales. The other **~126 were forced onto yfinance-only** —
the unauthenticated, rate-limiting source the reorder existed to stop depending on.

Verified live before fixing:

```
9 consecutive _polygon_budget_available() calls in one minute
    -> [True, True, True, True, True, False, False, False, False]
```

**So the previous day's fix was, in practice, inert for 96% of the universe.** Nothing failed:
yfinance answered, bars landed, `ingest.done` logged success. Same family as everything else in
this file — not a crash, a silent wrong answer.

### The fix

Make the gate do what its **name** says: **exclude Polygon**, not select yfinance. Everything else
in `_PRIORITY` keeps its normal order, so exhausting Polygon's tiny free budget can never again
decide which *other* provider answers. An empty-list guard keeps Polygon if it were somehow the
only candidate — one doomed request beats no request at all.

### The generalisable lesson

> **A guard written as "fall back to X" silently encodes the priority order that was current when
> it was written.** Reordering the list does not update the guard — it just leaves it naming a
> provider that is no longer the right answer. **Prefer "exclude Y" over "use X"** so the guard
> stays correct under reordering.

This is a close cousin of the wrong-path pattern from the gate audit: the fix landed somewhere
real, but the *deciding* path had moved. The check is the same — after changing a priority or
routing order, grep for every guard that names a specific member of that order.

### Also worth knowing

Three further gating gaps were found in the same pass and are **recorded but NOT fixed** (all the
`AUD-DIGEST-HOLIDAYBLIND` class — *a `mon-fri` cron is not a market-open check*):

- `live_price_cache_refresh` gates on raw `weekday()` + an hour window rather than a trading-day
  check, so it runs a full bulk download on market holidays.
- The six `18:0x` ET outcome evaluators are registered with **no `day_of_week`** and fire on
  Saturdays and Sundays.
- `edgar_8k_ingest_daily` has **no NYSE holiday check** (its HK sibling `_ingest_hk_connect_flows`
  does check `_is_hk_trading_day()`).
- `avg_volume_cache_refresh` omits `misfire_grace_time` — the exact gap that silently killed three
  jobs in `AUD-MISFIREGRACE-OPTIONSFLOW`.

They are surfaced on the **Admin → Data Pipeline** page so they are not re-derived from scratch.
