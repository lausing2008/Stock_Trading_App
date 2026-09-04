## Feature Reference: AUD-SQUEEZE250725-BATCH — 6 Squeeze-Audit Issues + 2 Performance Items (2026-08-16)

**Closes all 6 real issues and both cheap performance suggestions** confirmed still open by the
doc review above. All 7 fixes landed in one batch across `services/market-data/src/services/
scheduler.py`, `email_service.py`, and `src/api/admin.py`.

### Issues 1 & 5 — fundamentals-cache-miss counters were log-only, not admin-visible

**Symptom**: none live — an observability gap, not a functional bug, per the audit's own framing.
`check_short_squeeze_alerts()` already counted `_fundamentals_cache_misses` (a symbol whose
`stockai:fundamentals:v2:{symbol}` cache entry expired between page-views) and logged it, but
never exposed it anywhere admin-visible — a sustained spike (Redis degradation, the
fundamentals-refresh job falling behind) was only visible by reading logs. `check_squeeze_watch_
reverts()` had NO equivalent counter at all.

**Fix applied**: two new rolling-48h Redis counters (`_SQUEEZE_FUND_CACHE_MISS_COUNTER_KEY`,
`_SQUEEZE_WATCH_FUND_CACHE_MISS_COUNTER_KEY`), reusing the EXISTING `_incr_rolling_counter()`
mechanism already proven for AUD266's conviction/fired-ratio pair — no new infrastructure
invented. Surfaced via a new `"gauge"` `_DQ_CHECKS` source type (a genuinely different check
shape from the existing `"job_status"`/`"ratio"` types — purely informational, always
`ok: True`, NEVER appended to the `failing` list, since a nonzero miss count is expected
background noise, not a functional failure) — auto-visible on the admin health page via the
EXISTING generic `/dq-status` endpoint (which just reads every `dq_check:*` Redis key) with zero
new frontend code needed.

**What to check if this looks wrong**:
```bash
docker exec stockai-redis-1 redis-cli get 'dq_check:squeeze_fund_cache_misses_48h'
docker exec stockai-redis-1 redis-cli get 'dq_check:squeeze_watch_fund_cache_misses_48h'
# Both should show {"ok": true, "count_48h": N, ...} — ok is ALWAYS true by design, only
# count_48h should ever be watched for a sustained spike.
```

### Issue 2 — 30-day short-interest staleness cutoff had no intermediate warning

**Root cause**: exchange short interest settles ~2x/month with a 1-2 week reporting lag, so a
30-day-old reading can legitimately be up to ~6 weeks stale — but a reading 2 days old and one
28 days old rendered IDENTICALLY in the alert email (just the bare age in days). The audit
offered two options: tighten the hard reject to 21 days, or add staleness tiers. A hard tighten
would silently drop currently-firing candidates 21-30 days old with no visibility into what
changed — chose the tier approach instead.

**Fix applied**: new shared `_short_interest_age_str()` helper (`email_service.py`) — replaces
TWO independently-duplicated copies of the same age-string logic (`send_short_squeeze_email()`
and `send_prebreakout_email()`) with one implementation. Renders "moderately stale" for 16-21
days, "very stale" for 22+ days, no tier below 15 days. The 30-day HARD reject in both
`check_short_squeeze_alerts()`/`check_prebreakout_alerts()` is UNCHANGED — this is a rendering
addition, not a gating change.

### Issue 4 — 0-DTE gamma-unwind staleness note was inline text only

**Root cause**: open interest is exchange-published once per day, as of the PRIOR session's
close — for a `days_to_expiry=0` row (expires TODAY), the OI figure is up to a full trading
session stale right when it matters most. The email already said so in prose
("expires TODAY (OI as of yesterday's close)") but as plain inline text, easy to miss scanning
quickly, unlike the existing `days_to_cover_critical` red-border visual treatment.

**Fix applied**: 0-DTE rows now get an amber row border (`rgba(217,119,6,0.35)`) plus a `⚠️`
marker appended to the existing text — matching the established `is_critical`/`row_border`
pattern from `send_short_squeeze_email()`, just amber (a staleness NOTE) rather than red (a risk
escalation).

### Issue 6 — backtest endpoint couldn't distinguish two different zero-candidate states

**Root cause**: `squeeze_alert_backtest()` (`admin.py`) returns both `n_snapshots_qualifying` and
`n_candidate_days`, but when both were 0 there was no way to tell "no stock ever cleared the
short-float floor" from "stocks cleared the floor but never had a qualifying intraday move" —
two genuinely different diagnostic signals for someone debugging why the backtest returned
nothing.

**Fix applied**: a new `reason` field — `"no_qualifying_snapshots"` for the first zero-case
(the early-return branch), `"no_qualifying_moves"` for the second (`candidate_days` empty after
real snapshots existed), `None` in the normal case. Live-verified against real production data:
`GET /admin/squeeze-alert-backtest?weeks_back=52` correctly returned `reason: None` with
`n_snapshots_qualifying: 93, n_candidate_days: 131` (real, non-zero data).

### Perf 4.1 — N individual Redis GETs collapsed to one MGET

**Root cause**: `check_short_squeeze_alerts()`'s candidate-building loop did one `_rc.get(f"stockai:
fundamentals:v2:{sym}")` per symbol inside the loop — for a typical N-symbol `stockai:live_prices`
list, N round-trips where 1 would do.

**Fix applied**: a new price-only pre-pass over `_live_raw` collects symbols that already clear
the cheap filters (presence, market-hours, intraday-move threshold) into
`_pricefilter_qualifying`, then ONE `_rc.mget()` call pre-warms every qualifying symbol's
fundamentals blob into `_fund_by_symbol` before the main loop runs — the main loop's own filter
conditions are BYTE-IDENTICAL duplicates of the pre-pass's (guarded by a dedicated test
confirming both copies reference the same `_SQUEEZE_MIN_INTRADAY_MOVE_PCT` constant, not two
literals that could silently drift apart), and now reads from the pre-warmed dict instead of a
fresh GET. Fails open to an empty dict on any MGET error.

### Perf 4.3 — calibration buckets re-queried the DB every 1-minute cycle

**Root cause**: `_build_squeeze_family_calibration()`/`_build_prebreakout_calibration()` ran a
fresh DB query every time `check_short_squeeze_alerts()`/`check_gamma_unwind_alerts()`/
`check_prebreakout_alerts()` fired — but the underlying outcomes only actually resolve once
daily (`evaluate_squeeze_alert_outcomes()`/`evaluate_prebreakout_alert_outcomes()`), so a
1-minute-interval job was re-running the identical query ~1,440 times a day for no new data.

**Fix applied**: new `_cached_calibration_buckets(cache_key, builder)` wrapper — a 5-minute
Redis cache (fail-open to a fresh DB call on ANY Redis error, so a cache outage never makes
calibration silently unavailable), wrapping all 4 real calibration-builder call sites
(`short_squeeze`, `gamma_unwind_calls`, `gamma_unwind_puts`, `prebreakout`), each with its OWN
distinct Redis cache key (`stockai:cal:squeeze_family:{alert_type}` / `stockai:cal:prebreakout`)
— sharing one key across any two would silently serve one alert type's calibration data to a
different alert type.

**Live-verified end-to-end against real production data** (not just tests): directly invoked
`_cached_calibration_buckets()` twice in a row inside the running container — the first call
computed fresh, the second call's builder was replaced with one that raises if ever called, and
it correctly did NOT raise, proving the cache hit served from Redis without invoking the
builder. Confirmed the real Redis key (`stockai:cal:squeeze_family:short_squeeze`) was written
with a real ~300s TTL.

### Tests, adversarial verification, and a real collateral-regression lesson

New `services/market-data/tests/test_squeeze_audit_20260725_fixes.py` (32 cases) covers all 7
fixes directly where possible (`_short_interest_age_str()` and `send_gamma_unwind_email()` are
both directly importable — tested with real behavioral assertions, not just source-text checks)
and via source-text extraction where `scheduler.py` functions can't be imported in this test
environment.

**A genuine collateral-regression lesson, not a shortcut taken**: refactoring 3 duplicated call
sites (the shared staleness helper, the MGET restructuring, the calibration cache wrapper) broke
5 PRE-EXISTING tests across 3 other test files whose literal source-text assertions no longer
matched the legitimately-refactored code shape (`test_squeeze_family_recommendations_wiring.py`
x3, `test_prebreakout_confidence_wiring.py` x1, `test_short_squeeze_alert.py` x1) — all 5 were
updated to assert against the NEW correct code shape (helper delegation, cache-wrapper presence,
build-before-loop ordering) rather than the old literal strings, each confirmed to still test
the same underlying invariant the original author intended, not just patched to pass.

**Adversarially verified 4 sabotage/revert cycles, all caught**: (1) collapsing the
moderately-stale tier boundary in `_short_interest_age_str()` — caught by exactly the 2
dedicated boundary tests; (2) making the `"gauge"` DQ-check dispatch branch report a real
pass/fail instead of always `ok: True` — caught by the dedicated dispatch test; (3) making
`squeeze_alert_backtest()`'s `reason` always `None` regardless of `candidate_days` — caught by 2
of 3 dedicated reason tests (the third, testing the OTHER zero-case, correctly stayed green
since it's a different code path); (4) diverging the MGET pre-warm pass's intraday-move
threshold from the main loop's own copy — caught by a dedicated duplicated-filter-consistency
test added SPECIFICALLY because the two-pass restructuring introduces exactly this drift risk
(not an afterthought — written because the refactor itself created a new class of possible bug).
All 4 sabotages reverted and confirmed byte-identical via md5 before moving on.

**Verification**: full 1,461-test market-data suite green (up from 1,429); pyflakes clean on all
3 touched files (confirmed via `git stash` that every pre-existing warning predates this
change). Committed `278e836`, deployed to EC2 (`market-data` restarted clean, `run_data_quality_
checks()` directly invoked post-deploy confirming both new gauge entries populate real Redis
keys, `check_short_squeeze_alerts()` directly invoked with no exception, the calibration cache
wrapper live-verified end-to-end as described above, the backtest endpoint live-verified against
real production data). Frontend rebuilt and redeployed for the tracker update, confirmed live at
`lausing.com/improvements`.

**Tracker**: `improvements.tsx` Tier 285 / id `AUD-SQUEEZE250725-BATCH`.

**What to check if any of these 7 look wrong**:
```bash
# Confirm all 7 fixes are present in the live container:
docker exec stockai-market-data-1 grep -n 'def _short_interest_age_str\|rgba(217,119,6,0.35)\|no_qualifying_snapshots\|no_qualifying_moves\|_SQUEEZE_FUND_CACHE_MISS_COUNTER_KEY\|_SQUEEZE_WATCH_FUND_CACHE_MISS_COUNTER_KEY\|_rc.mget(_fund_mget_keys)\|def _cached_calibration_buckets' /app/src/services/email_service.py /app/src/services/scheduler.py /app/src/api/admin.py

# Check current cache-miss counts:
docker exec stockai-redis-1 redis-cli get 'dq_check:squeeze_fund_cache_misses_48h'
docker exec stockai-redis-1 redis-cli get 'dq_check:squeeze_watch_fund_cache_misses_48h'

# Check calibration cache TTLs (should all be <=300s, never missing during active market hours):
docker exec stockai-redis-1 redis-cli ttl 'stockai:cal:squeeze_family:short_squeeze'
docker exec stockai-redis-1 redis-cli ttl 'stockai:cal:squeeze_family:gamma_unwind_calls'
docker exec stockai-redis-1 redis-cli ttl 'stockai:cal:squeeze_family:gamma_unwind_puts'
docker exec stockai-redis-1 redis-cli ttl 'stockai:cal:prebreakout'
```

---


## Feature Reference: AUD288-SQUEEZE-NO-VOLUME-CONFIRM — RVOL Gate for the Classic Short-Squeeze Alert (Built 2026-08-18)

**Closes a real gap from reviewing `docs/COMPREHENSIVE_SYSTEM_AUDIT_2026-08-16.md`** (an
external audit document from another model/session — most of its 12 claims were stale or
false, but 4 were real; this was one of them). `check_short_squeeze_alerts()` (`services/
market-data/src/services/scheduler.py`) previously gated purely on `short_percent_of_float`
(>=15%) and `change_pct` (>=3%) — a stock could clear both bars on thin, low-conviction volume
(a handful of large trades in an illiquid name), a materially weaker setup than the same price
move on genuinely elevated volume.

**What it is**: an RVOL (relative volume — today's volume ÷ the stock's own 20-day average
volume) floor added to the classic squeeze alert, reusing the SAME session-elapsed-scaled
formula already established for `check_volume_anomalies()` (T257) and
`check_squeeze_ignition_alerts()` (T260) — a flat RVOL threshold over-triggers early in the
trading session, since a stock that's traded 20% of its average daily volume by 10am looks
"abnormal" against a FULL-day average even on a completely normal day.

**How it works — the shared helper**:
```python
def _session_elapsed_rvol_thresholds(base: float, floor: float) -> tuple[float, float]:
    # scales `base` by how much of the trading session has elapsed (390 min for US, 330 for
    # HK, both from 9:30 local open), floored at `floor` so the bar never drops to zero
    # right at the open. Returns (us_threshold, hk_threshold) for the current moment.
```
This was previously duplicated inline, once each, inside `check_volume_anomalies()` and
`check_squeeze_ignition_alerts()` — extracted into one shared function before adding a 3rd
copy for this fix, closing a real DRY gap in the same pass. `check_short_squeeze_alerts()` now
calls it with `_SQUEEZE_RVOL_BASE = 2.2` — deliberately BETWEEN the ignition tier's 1.8 (a move
still building toward 3% should need less volume conviction than one already there) and the
general volume-anomaly scanner's 2.5 (this alert is already narrowed by the short-float gate,
so it doesn't need the general scanner's stricter bar).

**Where the check lives**: both the MGET pre-warm pass (a Redis batch-fetch efficiency layer,
AUD-SQUEEZE250725-PERF4.1) and the main candidate-building loop apply the identical RVOL
filter — `stockai:avg_volume` (Redis) is read alongside the existing `stockai:live_prices`
read, and a candidate below its market's RVOL threshold is skipped in BOTH passes. The real,
measured RVOL is threaded into the candidate dict and rendered in the alert email (both HTML
and text) right after the short-float %, e.g. `"0.2% of float short · 3.4x avg volume"`.

**How to see it work**:
```bash
# Confirm the shared helper and threshold are present:
docker exec stockai-market-data-1 grep -n '_session_elapsed_rvol_thresholds\|_SQUEEZE_RVOL_BASE' /app/src/services/scheduler.py

# Compute the CURRENT live threshold directly (varies by time of day — floored outside market hours):
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0, '/app'); sys.path.insert(0, '/app/src')
from src.services.scheduler import _session_elapsed_rvol_thresholds
us, hk = _session_elapsed_rvol_thresholds(2.2, 1.5)
print('US threshold:', round(us, 2), '| HK threshold:', round(hk, 2))
"

# Confirm the job is running on its real 1-minute schedule with no exception:
docker logs stockai-market-data-1 --since 5m | grep 'check_short_squeeze_alerts'
```
If a stock clears the short-float and price-move bars but STILL doesn't alert, check its real
volume ratio against the printed threshold above — that's expected, working-as-designed
behavior now, not a bug.

**Tests**: new `services/market-data/tests/test_session_elapsed_rvol_thresholds.py` (7 cases)
tests the shared helper directly via a frozen-datetime harness (session-open floor, full-
session-elapsed reaching the base, linear scaling in between, US 390min vs HK 330min session
lengths, post-close clamping, pre-open never going negative). Extended
`test_short_squeeze_alert.py` (+6 cases) and fixed 2 pre-existing source-text tests in
`test_volume_anomaly_alert.py`/`test_squeeze_ignition_alert.py` that asserted on the now-
removed inline `_us_frac`/`_hk_frac` locals.

**A real "still passes after sabotage" gap, self-caught during adversarial verification**: the
first version of the two-pass-consistency test only counted the threshold-ASSIGNMENT line
appearing twice (once per pass) — sabotaging just the pre-warm pass's own comparison
(`if float(vol)/float(avg_vol) < rvol_threshold:` → `if False:`) went undetected, since the
assignment line itself was untouched. Fixed by also asserting the literal comparison strings
exist in the body, re-ran the same sabotage, and confirmed it's now caught. Full 1,771-test
market-data suite green at ship time; pyflakes clean (all remaining warnings independently
confirmed pre-existing via `git stash`).

---


## Feature Reference: AUD288-AUTO-LIQUIDATION-DEFERRED — One-Click Portfolio Liquidation (Built 2026-08-18)

**The gap**: `check_portfolio_drawdown_alerts()` (`services/market-data/src/services/
scheduler.py`) already emails a user once a portfolio crosses its own configured
`max_portfolio_drawdown_pct` — but never automatically closes any positions, and there was no
way to act on it beyond manually force-closing each open position one at a time via the
existing per-trade `POST /paper-portfolio/trades/{trade_id}/exit` endpoint.

**Fully-automatic liquidation was considered and explicitly rejected** as too risky: no
empirically-validated trigger threshold exists yet (matching this repo's own
`T234-CONFIG-UNJUSTIFIED-THRESHOLDS` catalog of unvalidated numeric constants), and an
unattended circuit breaker could itself lock in losses at a bad moment (e.g. force-selling
into a flash dip that would have reversed). A pure alert-only status quo also leaves a real
gap — a user away from the screen can't act quickly. **Built the confirming-click middle
ground instead**: a real, one-click bulk-close action, but NEVER fired automatically by any
scheduled job — only ever runs when a human explicitly requests it.

**What it is**: `POST /paper-portfolio/{portfolio_id}/liquidate?confirm=true` (`services/
market-data/src/api/paper_portfolio.py`) force-closes EVERY open `PaperTrade` in one portfolio
at once, at current live prices.

**How it works**:
1. Extracted the existing `manual_exit_trade()` endpoint's own close-math (stop-slippage/
   commission/cash-credit) into a new shared `_close_one_paper_trade(session, p, trade,
   exit_price, exit_reason)` helper — so this bulk endpoint reuses it rather than a THIRD
   independent reimplementation of the same math (a 2nd copy already exists in
   `conditional_orders.py`'s own `_execute_close_position()`, which additionally does
   broker-exit routing + `SignalOutcome` writeback — this simpler manual-close path
   intentionally does NOT, matching `manual_exit_trade()`'s own pre-existing, narrower scope
   exactly, not silently expanding it during extraction).
2. Batch-fetches live prices via `_fetch_live_prices()` — the SAME clean one-`yf.download()`-
   call path `BUG-YFCALLVOL` already fixed elsewhere — instead of one yfinance call per open
   position, so force-closing N positions never becomes an N-request rate-limit amplifier. A
   symbol missing from the batch fetch falls back to the trade's own last-known
   `current_price`/`entry_price`.
3. **Two independent confirmation layers**: the frontend's own browser `confirm()` dialog
   ("Force-close ALL N open positions... this cannot be undone."), then the backend's own
   required `?confirm=true` query param — a POST alone is not enough; the request is rejected
   with a real `400` if `confirm` is omitted or `false`.
4. Each open trade closes independently inside its own try/except — one trade's failure
   (a corrupted row, a math edge case) does NOT abort closing the rest of the portfolio.
   `exit_reason = "manual_liquidation"` (a new, distinct value from `manual_exit`, so a trade
   history can tell a bulk liquidation apart from a single manual close).

**Frontend**: a new "⛔ Liquidate All (N)" button on the Paper Portfolio admin page, next to
the existing Start/Pause/Stop engine controls — styled distinctly (dark red) to signal its
destructive nature, disabled when there are zero open positions, showing a live result summary
("Closed 7 positions — cash now $103,450.12") after completion.

**A real bug caught live in production, before any real portfolio was ever touched**: the
first deployed version used `from .services.paper_trading_engine import _fetch_live_prices`
(a single dot) — but `paper_portfolio.py` lives at `src/api/paper_portfolio.py` and
`paper_trading_engine.py` lives at `src/services/paper_trading_engine.py`, SIBLING packages
under `src/`, not parent/child — the correct relative import needed TWO dots
(`from ..services.paper_trading_engine import ...`), matching every other `src/api/*.py`
file's own established convention (`broker.py`, `rl.py`, and this same file's own other 7
lazy imports of `paper_trading_engine`). This produced a real `500 Internal Server Error`
(`ModuleNotFoundError: No module named 'src.api.services'`) when live-verified against the
running EC2 container — caught specifically because the confirm=false rejection path was
tested against a real, live portfolio (portfolio 1, with 7 real open positions) BEFORE trusting
the deploy, not just from a green test suite. Fixed the import, added a dedicated assertion in
the test's own source-extraction step confirming the real module uses the correct 2-dot
relative path (the original test had silently masked this exact bug — its `.replace()` call
matched the OLD, buggy single-dot string, so the test never actually exercised the real import
statement at all). Adversarially re-verified by reintroducing the original single-dot bug and
confirming the new assertion fails loudly at collection time instead of silently passing.

**How to see it work**:
```bash
# Confirm the endpoint exists and rejects without confirm=true (safe — never touches a position):
docker exec stockai-market-data-1 python3 -c "
import sys, uuid, time; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from common.config import get_settings; from jose import jwt as _jwt; import httpx
s = get_settings()
tok = _jwt.encode({'sub':'<your_username>','jti':str(uuid.uuid4()),'exp':int(time.time())+86400}, s.jwt_secret, algorithm='HS256')
r = httpx.post('http://localhost:8001/paper-portfolio/1/liquidate', headers={'Authorization': f'Bearer {tok}'}, timeout=15)
print(r.status_code, r.json())
"
# Expect: 400 {'detail': 'Liquidation requires explicit confirmation — retry with ?confirm=true'}

# Confirm a portfolio's open-position count is unaffected by the rejection above:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT COUNT(*) FROM paper_trades WHERE portfolio_id = 1 AND stage = 'open';"

# To ACTUALLY liquidate a real portfolio (irreversible — closes every open position for real):
# use the "⛔ Liquidate All (N)" button on the /paper-portfolio admin page, or call the API
# directly with confirm=true appended to the URL above.
```

**Tests**: new `services/market-data/tests/test_liquidate_portfolio.py` (12 cases) exercises
`_close_one_paper_trade()` and `liquidate_portfolio()` directly against a real in-memory
SQLite session (the established real-sqlalchemy-via-stub-pop-and-restore technique, matching
`test_trade_postmortem.py`/`test_broker_position_sync.py`) — pnl/cash-credit math, the
`confirm=true` gate, portfolio-scoping isolation (must never touch a DIFFERENT portfolio's
open trades), the live-price-fetch fallback to last-known price, and per-trade failure
isolation. Adversarially verified 3 sabotage/revert cycles, all caught: removing the
`confirm=true` gate, removing the portfolio-scoping `WHERE` clause (a real, dangerous
cross-portfolio close leak — closed 2 portfolios' positions instead of 1), and removing the
per-trade try/except isolation (an unguarded `ZeroDivisionError` aborted the whole batch).
Full 1,783-test market-data suite green; pyflakes clean on all touched files.

---


## Feature Reference: Short-Squeeze Alert Pipeline Audit — Confirmed Healthy (2026-08-19)

**User report**: no short-squeeze alert emails received for several days, after a change a
few days earlier (this session's own `AUD288-SQUEEZE-NO-VOLUME-CONFIRM` RVOL gate, shipped
`a3fbab5`, 2026-08-18). Investigated end-to-end before touching any code — found the pipeline
is genuinely healthy, not regressed.

**What was checked and confirmed clean**: zero exceptions in 7 days of logs for either
`check_short_squeeze_alerts()`/`check_squeeze_ignition_alerts()`; deployed container code
byte-identical (`md5sum`) to the local repo — no stale/reverted `docker cp`; the user IS a
valid recipient (77 real untriggered `PriceAlert` rows, real email on file); SMTP delivered
dozens of OTHER real alert emails to the same address in the prior 24h, ruling out a broader
outage; the per-user `stockai:squeeze_active:{uid}` dedup key was empty (nothing stuck
"active" and silently suppressing a resend); the RVOL gate shipped 2026-08-18 and STILL
produced 2 real, confirmed-sent candidates (FCEL, TMDX) that same day, proving it wasn't
DOA at deploy.

**Root cause of the reported silence**: a genuine 2-day quiet stretch for a narrow,
multi-condition filter, not a bug. `squeeze_alert_outcomes` (the real, authoritative DB
ground truth, unaffected by Docker log retention) shows `short_squeeze` fired on 2026-08-17
(7 real candidates) and 2026-08-18 (2 real candidates), then zero on 08-19/08-20. Live-
simulated the EXACT filter chain against real production Redis data (`stockai:live_prices`/
`stockai:avg_volume`/`stockai:fundamentals:v2:*`) at the moment of investigation: 5 stocks
cleared the 3%+ move + RVOL floor, but NONE cleared the `>=15%` short-float floor (the
closest, BULL/MRVL, were only ~5-6% short) — the market simply hadn't produced a candidate
clearing all 4 gates (real move + volume confirmation + genuinely high short interest +
non-stale short-interest data) simultaneously in that window.

**One real, flagged-but-not-yet-actioned observation**: `squeeze_ignition` (T260, the earlier-
warning tier below the classic alert's 3% floor) has ZERO rows across its entire 5-day
retained history (`squeeze_alert_outcomes` table spans 2026-08-15 through 08-19). Traced its
logic and found it structurally sound — same shared `_session_elapsed_rvol_thresholds()`
helper, same fundamentals cache (confirmed `dq_check:squeeze_ignition_fund_cache_misses_48h`
count_48h: 0, i.e. no fetch failures), zero exceptions. Plausible this is simply a rare-enough
intersection (1-3% move AND RVOL confirmation AND >=15% short float AND fresh data,
simultaneously) that 5 quiet days is normal — but 5-for-5 on a WIDER net than the classic
alert (which did fire 9 times in the same window) is worth a longer observation window before
concluding it's fine, rather than dismissing outright.

**What to check if this recurs**:
```bash
# Confirm the job is running with no exceptions:
docker logs stockai-market-data-1 --since 24h 2>&1 | grep -iE 'squeeze.*(traceback|error|exception)'

# Confirm the deployed code matches the repo (rules out a stale/reverted container):
docker exec stockai-market-data-1 md5sum /app/src/services/scheduler.py
md5sum services/market-data/src/services/scheduler.py

# Check the real, authoritative candidate history directly (unaffected by log retention):
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT alert_type, fired_date, COUNT(DISTINCT symbol) FROM squeeze_alert_outcomes GROUP BY alert_type, fired_date ORDER BY fired_date, alert_type;"

# Live-simulate the exact filter chain against real current data (safe, read-only):
docker exec stockai-market-data-1 python3 -c "
import json, redis
rc = redis.Redis(host='redis', port=6379, decode_responses=True)
rows = json.loads(rc.get('stockai:live_prices'))
avg_vol = json.loads(rc.get('stockai:avg_volume') or '{}')
for row in rows:
    sym, price, prev_close = row.get('symbol'), row.get('price'), row.get('prev_close')
    vol, av = row.get('volume'), avg_vol.get(sym)
    if not all([sym, price, prev_close, vol, av]): continue
    change_pct = (float(price) - float(prev_close)) / float(prev_close) * 100
    if change_pct < 3.0: continue
    rvol = float(vol) / float(av)
    if rvol < 1.5: continue
    print(sym, round(change_pct, 2), round(rvol, 2))
"

# Check the DQ cache-miss gauges (AUD-SQUEEZE250725-ISSUE1/5):
docker exec stockai-redis-1 redis-cli get 'dq_check:squeeze_fund_cache_misses_48h'
docker exec stockai-redis-1 redis-cli get 'dq_check:squeeze_ignition_fund_cache_misses_48h'
```
A truly broken pipeline shows real exceptions in logs, a `dq_check` job-status entry with
`status: error`, or an SMTP-delivery gap affecting OTHER alert types too — none of which were
present at the time of this investigation. Zero candidates clearing every gate for a few days
is, on its own, consistent with correct behavior for this specific narrow filter.

---


## Design Reference: Short-Squeeze Watch Revert — Real User Report Traced, One Real Gap Found

**A user asked directly (2026-08-20)** why a "Short Squeeze Watch Reverted — DFNS" email
arrived, whether reverted means price went up (it doesn't necessarily), why the dashboard
showed "days to cover: 0.0d" for a stock the same dashboard flagged as a 🔥 Prime Candidate,
and whether that's a contradiction.

**A real unit-misread caught and corrected mid-investigation**: the first pass at this
question misread `short_percent_of_float: 0.3577` as 0.36% instead of the correct **35.77%**
(the field is stored as a fraction-of-1, not fraction-of-100) — this led to an initially wrong
conclusion that DFNS's short interest was negligible. Re-checked directly against the same
Redis fundamentals blob and the real `/stocks/short_squeeze` endpoint (note: requires the
`/stocks` prefix — a bare `/short_squeeze` 404s, a mistake also made during the first pass)
and confirmed the dashboard's own `35.8% short` display is correct. Both mistakes were
self-caught by cross-checking against the actual dashboard screenshot the user provided,
rather than trusting an initial, uncorroborated API read.

**"Days to cover: 0.0d" is real, correct math, not a bug**: `shares_short (279,125) ÷
average_volume (3,288,659) ≈ 0.08 days`. This measures something DIFFERENT from "is this
stock hot" — it answers "how fast could shorts exit if they wanted to," which is a genuinely
separate axis from "how much short interest exists" (35.8%) and "how fast did that short
interest build" (`shares_short_prior_month: 72,419` → `279,125`, a real +285% MoM increase).
A stock can simultaneously have (a) a large, freshly-built short position, (b) high liquidity
allowing a fast exit if shorts choose to cover, and (c) still be a legitimate "prime candidate"
by this app's own 15%-short-float threshold — these are not contradictory.

**The one real gap this surfaced**: `check_squeeze_watch_reverts()`'s `price_recovered` check
(`scheduler.py`) is a bare `float(current_price) > float(w.price_at_add)` — zero tolerance
band. DFNS's real watch record showed `price_at_add=$24.35` (2026-08-05); the revert fired
when price ticked to `$24.40` — a **0.2%** move — while DFNS's `short_percent_of_float` was
STILL 35.77% (well above the 15% floor) and `change_pct` was -6.19% that same day (negative
momentum, not a "shorts squeezed, price ran" scenario). Documented as
`AUD292-SQUEEZEWATCH-REVERT-NOTOLERANCE` (tier 292, `todo`) — not fixed yet, since the right
tolerance/confirmation semantics need a real design decision (a percentage floor? requiring
BOTH OR-conditions instead of either? a multi-cycle confirmation window?), not a reflexive
patch. The existing `POST /squeeze-watch` re-add flow already correctly re-arms a reverted
watch with fresh values, so the immediate user-facing workaround (re-add the symbol) already
works without any code change.

**What to check if a similar "reverted" report looks premature**:
```bash
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT symbol, watch_type, price_at_add, added_at, reverted, revert_reason FROM squeeze_watches WHERE symbol = '<SYMBOL>';"
docker logs stockai-market-data-1 --since 24h 2>&1 | grep "squeeze_watch.done"
# Check the real fundamentals at the moment of the revert to judge whether metric_faded or
# price_recovered (or both) actually fired, and by how much:
docker exec stockai-redis-1 redis-cli get 'stockai:fundamentals:v2:<SYMBOL>'
```

---


## Feature Reference: DESIGN-SQUEEZE-1D2D3D-WINDOWS — 1d/2d/3d Forward-Return Windows for
## Squeeze / Prebreakout Alerts (Built 2026-08-21)

**Trigger**: user asked to review `docs/DESIGN_SQUEEZE_ALERT_PERFORMANCE_MEASUREMENT.md` and
assess whether it's already implemented, worth building, and how sound the design is — before
building anything, verified every claim in the doc against real current code, matching this
session's own repeatedly-applied discipline for design/audit documents in this repo.

### What the doc got right, confirmed by direct code read

`_SQUEEZE_OUTCOME_WINDOWS = (5, 10, 20)` in `services/market-data/src/services/scheduler.py`
had no 1d/2d/3d equivalent, and neither `SqueezeAlertOutcome` nor `PreBreakoutAlertOutcome`
carried the matching columns — exactly as the doc described. This meant the app could not yet
answer the user's own original squeeze-alert question ("will the price go up the other day or
later") without waiting the full 5 calendar days the pre-existing windows require.

### What the doc got wrong — 2 proposals already exist under different names

- **§6.1.B "Volume Confirmation"** proposed a flat `>=1.5x avg volume` gate. Checked
  `check_short_squeeze_alerts()` directly and found this ALREADY LIVE, and materially
  stricter: `_SQUEEZE_RVOL_BASE = 2.2` gated through `_session_elapsed_rvol_thresholds()`
  (shipped `AUD288-SQUEEZE-NO-VOLUME-CONFIRM`, 2026-08-18) — a session-elapsed-scaled
  threshold specifically designed to avoid the flat-threshold false-trigger-early-in-the-day
  problem the doc's own simpler proposal would reintroduce. Building the doc's version as
  written would have been a genuine regression.
- **§6.4.A "Symbol Blacklist"** proposed a new auto-blacklist mechanism (`<30% win rate over
  20+ alerts`). Checked and found a real, working mechanism already exists —
  `RestrictedSymbol` (`shared/db/models.py`), with real admin CRUD routes
  (`paper_portfolio.py`) and already consulted directly inside `_scan_for_entries()`
  (`paper_trading_engine.py:4395`). Any future auto-blacklist logic for squeeze-alert
  underperformers should write into that existing table, never a parallel new one.
- **§6.1.C "Short-Interest-Trend Check"** assumed a `shares_short_prior_month` field this app
  stores — checked directly (`grep -n "shares_short_prior_month" shared/db/models.py
  services/market-data/src/services/scheduler.py`) and found no such field is captured
  anywhere. Not free, already-available data as the doc implied.

### The design's real weakness — no train/validation discipline on the filter/sizing proposals

§6.1.A (pre-squeeze-momentum filter), §6.1.C, §6.3.A (stop-loss recommendation), §6.3.B (Kelly
sizing), §6.4.A/B (blacklist/sector filter) are all "compute a hand-picked threshold, hard-gate
on it" with no chronological train/validation split and no promotion-margin check — a step
backward in rigor relative to this codebase's own established pattern for exactly this kind of
change (`gate_harness.py`'s `_passes_promotion_margin()` — must beat the live baseline on
held-out data, unconditional rejection of non-positive lift). At the doc's own stated data
volume (107 total alerts, zero resolved forward returns as of the doc's date), any of these
would be pure overfitting bait against this repo's own `MIN_SAMPLES_PER_SPLIT`-style floors.

### What was built — only the P0 schema/evaluator/UI gap, nothing else from the doc

1. **Schema** (`shared/db/models.py`) — `price_1d/return_1d/is_correct_1d` +the 2d/3d
   equivalents added to BOTH `SqueezeAlertOutcome` and `PreBreakoutAlertOutcome`.
2. **Migration** (`shared/db/session.py`) — a small loop over `("squeeze_alert_outcomes",
   "prebreakout_alert_outcomes") × (1, 2, 3)`, matching this repo's own `create_all()`-gap
   invariant for adding columns to an existing, already-populated table.
3. **Evaluator** (`scheduler.py`) — `_SQUEEZE_OUTCOME_WINDOWS = (1, 2, 3, 5, 10, 20)`. Both
   `evaluate_squeeze_alert_outcomes()` and `evaluate_prebreakout_alert_outcomes()` already
   loop over this constant generically (`for window in _SQUEEZE_OUTCOME_WINDOWS:`) — widening
   the constant was the ENTIRE evaluator-side fix, no per-window code duplicated. The
   `pending` predicate (`return_20d.is_(None)`) is correctly unaffected — it still marks a row
   pending until the LARGEST window closes, and the per-window loop independently skips
   already-filled fields regardless of window order.
4. **Free bonus**: `squeeze_alert_backtest()` (`admin.py`, a separate retroactive research
   endpoint) reads `_SQUEEZE_OUTCOME_WINDOWS` generically too — it now automatically reports
   `window_1d/2d/3d` alongside its existing 5d/10d/20d, at zero extra code cost. Confirmed via
   its own existing test suite staying green; no UI was built for this since that section of
   the page is already de-emphasized.
5. **API** (`admin.py`) — `squeeze_alert_performance()`'s `by_window` dict comprehension
   extended to `(1, 2, 3, 5, 10, 20)`; `by_alert_type` and `recent_alerts` both extended with
   the 3 new fields.
6. **Frontend** (`api.ts`, `squeeze-alert-performance.tsx`) — `SqueezeAlertTypeSummary`/
   `SqueezeAlertOutcomeRow` types extended; the per-type summary line and the recent-alerts
   table both render 1d/2d/3d alongside the pre-existing columns.

**Deliberately NOT built**: everything in §6 of the doc. Revisit only once the 5d/10d/20d
windows have real resolved data (starting ~2026-08-22 per the doc's own math) and a genuine
sample floor is cleared — any filter proposal at that point should go through
`gate_harness.py`'s own walk-forward promotion-margin gate, not a hardcoded threshold shipped
as-is from the doc's example code.

**A real reasoning trap caught and fixed before shipping, in the test suite itself**:
`test_prebreakout_alert.py`'s pre-existing "leaves a window open" fixture uses a fire 3 days
old — this session's first draft of an added assertion (`return_1d is None`, `return_2d is
None`) was WRONG: with `entry_date = fired+1`, the 1d/2d targets (`entry_date+1`,
`entry_date+2`) are actually already in the PAST relative to `date.today()` in that specific
fixture, not still-open windows the way 5d/10d genuinely are. They correctly resolve against
the fixture's own deliberately-future-dated bar (added to that test for a DIFFERENT reason —
guarding the `target > today` skip logic) via the nearest-on-or-after lookup. Caught by
tracing the actual dates by hand before trusting the assertion, not by the test failing —
fixed to assert `is not None` for 1d/2d in that specific fixture, with a comment explaining
why, distinct from the genuinely-open 5d/10d/20d windows in the same row.

**Tests**: 4 new cases in `test_squeeze_alert_outcomes.py` (the bullish-win fixture extended
to assert real 1d/2d/3d values; a same-day fire correctly leaving all 3 new windows `None`; a
2-day-old fire correctly resolving `return_1d` while 5d/10d/20d stay open — proving the
per-window independence, not an all-or-nothing gate; 3 source-text checks confirming
`admin.py`'s `by_window` comprehension, `by_alert_type` dict, and `recent_alerts` row
construction all genuinely include the new fields) and 3 new/extended cases in
`test_prebreakout_alert.py` (the same bullish-win extension, a dedicated bearish-loss-on-1d
case confirming this always-BUY-thesis table has no accidental bearish variant on the new
windows, and the corrected "leaves a window open" assertion described above).

**Verification**: full 1967-test market-data suite green (up from 1958); `pyflakes` clean on
all 4 touched backend files (confirmed via `git stash` that every pre-existing warning
predates this change — only line numbers shifted). Frontend: `tsc --noEmit` clean, full
132-test Vitest suite unaffected, full `next build` clean (all 51 routes,
`/squeeze-alert-performance` compiled at 3.51 kB) — confirmed the actual compiled chunk
contains all 6 new field names via a direct grep against
`.next/static/chunks/pages/squeeze-alert-performance-*.js`, not just correct-looking source.

**Tracker**: `improvements.tsx` Tier 296 / id `DESIGN-SQUEEZE-1D2D3D-WINDOWS`.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n "_SQUEEZE_OUTCOME_WINDOWS = " /app/src/services/scheduler.py
# Should show (1, 2, 3, 5, 10, 20), not (5, 10, 20).

docker exec stockai-postgres-1 psql -U stockai -d stockai -c "\d squeeze_alert_outcomes" | grep -E "price_1d|return_1d|is_correct_1d|price_2d|price_3d"
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "\d prebreakout_alert_outcomes" | grep -E "price_1d|return_1d|is_correct_1d|price_2d|price_3d"

# Check whether the evaluator has actually populated the new windows for any resolved alert
# (needs an alert at least 1 real trading day past its own fired_date):
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT alert_type, symbol, fired_date, return_1d, return_2d, return_3d, return_5d FROM squeeze_alert_outcomes WHERE return_1d IS NOT NULL ORDER BY fired_date DESC LIMIT 10;"

docker exec stockai-market-data-1 curl -s 'http://localhost:8001/admin/squeeze-alert-performance?days_back=180' \
  -H "Authorization: Bearer <admin token>" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d['by_alert_type'][0].keys())"
```

---


## Feature Reference: Next Improvement Batch — 2 Real Fixes From a Fresh Survey (Squeeze Alerts + Broker Integration) (2026-08-25)

**Trigger**: after 3 prior survey angles this session (alerting send-loop gaps, ML/calibration
validation-discipline gaps, frontend mobile-responsive grids) all came back genuinely clean —
including one candidate finding (`calibrate_conviction_weights`) that turned out to already be
fixed under `AUD263-CONVICTION-WEIGHTS-UNGATED`, a stale report from a survey agent — the user
asked for a fresh area. Targeted 3 genuinely untouched angles: squeeze/gamma alert accuracy,
broker integration edge cases, and the news-intelligence pipeline. News-intelligence came back
clean (the original EPS/CEO/AI false-positive fix in `tickers.py` plus 2 follow-on fixes already
cover the matching logic). 2 real findings survived independent verification.

### 1. BUG-ETRADEORDERFIELDS-GETORDER — `get_order()` never received the fix `list_orders()`
### already has, and it's the one function real broker trades' fill-confirmation depends on

**Root cause**: `EtradeBroker.list_orders()` (`services/market-data/src/services/broker/
etrade_broker.py`) already carries an inline comment tagged `BUG-ETRADEORDERFIELDS` documenting
a real, already-fixed bug: E*Trade's real quantity field is `orderedQuantity`, not `quantity`
(which never exists on a real response, silently defaulting to 0); order status lives on
`OrderDetail["status"]`, not a top-level `orderStatus` key (which always fell through to the
`"OPEN"` → `"pending"` default regardless of the order's real state). `get_order()` — a
separate function a few lines above `list_orders()`, hitting the same E*Trade endpoint with an
`orderId` filter — had **both original mistakes still present**, never touched when
`list_orders()` was fixed. A third divergence found in the same pass: `get_order()` also still
used an exact `== "BUY"` match for side detection, while `list_orders()` was already fixed to
`.upper().startswith("BUY")` since E*Trade options orders carry values like `"BUY_OPEN"`/
`"SELL_CLOSE"`, which the exact-match check misclassifies as `SELL`.

**Why this matters more than `list_orders()`'s own bug did**: `get_order()` — not
`list_orders()` — is the ONE function `paper_trading_engine.py`'s real order-fill-confirmation
path actually calls: `_place_broker_entry()`/`_place_broker_exit()`'s immediate-fill check (both
call `broker.get_order(order.order_id)` right after placing a real order) and the scheduled
`poll_broker_order_fills()` re-poll job. With this bug live, a genuinely-filled E*Trade order
would be silently misreported as `status="pending"`, `qty=0` — forever, on every poll — never
recognized as filled by this app regardless of its real state on E*Trade's side.

**Fix**: ported `list_orders()`'s exact corrected reads (`instr.get("orderedQuantity", 0)`,
`detail.get("status", "OPEN")`, `.upper().startswith("BUY")`) into `get_order()`, with a comment
cross-referencing `BUG-ETRADEORDERFIELDS` so both functions' fix history stays linked for anyone
reading either one in the future.

**Tests**: `services/market-data/tests/test_broker_order_history.py` gained 6 new cases — no
dedicated `get_order()` tests existed before this at all (only `list_orders()` was tested).
Mirrors `list_orders()`'s own established coverage: ordered-quantity field read,
status-from-`OrderDetail` read, both options-side-detection directions (`BUY_OPEN`/
`SELL_CLOSE`), HTTP-failure and order-not-found error paths, `filled_qty`/`filled_avg_price`
parsing. Adversarially verified: reverted all 3 fixed properties back to the original bug and
confirmed exactly the 3 dedicated tests failed with real, meaningful assertion diffs (`qty 0.0
!= 100.0`, `status "pending" != "filled"`, `side SELL != BUY`) — restored and confirmed
byte-identical via `diff`. Full 2015-test market-data suite green (up from 2004); pyflakes
clean (all 5 remaining warnings confirmed via `git stash` to predate this change).

### 2. BUG-SQUEEZEIGNITION-CALIBRATION-CROSSCONTAMINATION — `squeeze_ignition` alerts showed
### `short_squeeze`'s own historical win rate, not its own

**Root cause**: `check_squeeze_ignition_alerts()` (`services/market-data/src/services/
scheduler.py`) built its calibration buckets via `_build_squeeze_family_calibration(session,
"short_squeeze")` and looked up win rates via `_squeeze_family_calibration_for_alert_type
(_sqi_cal_buckets, "short_squeeze", ...)` — both hardcoded to a DIFFERENT alert type than the
one this function actually is. `squeeze_ignition` (a materially smaller-move, earlier-warning
tier — fires on a 1-3% intraday move, per `_SQUEEZE_IGNITION_MIN_MOVE_PCT`/`_MAX_MOVE_PCT`) and
`short_squeeze` (its own separate alert, firing later on a ≥3% move) are structurally different
alert types with their own separate resolved-outcome tracking — this same function correctly
records ITS OWN outcomes under `alert_type="squeeze_ignition"` a few lines later, via
`_record_squeeze_alert_outcome(session, "squeeze_ignition", ...)`. So the calibration lookup and
the outcome-recording were scoring/reading two genuinely different populations under one
function.

**A misleading comment made this easy to miss on a casual read**: the code's own comment
claimed *"a candidate's measured win rate is reported using whichever alert type's own resolved
history is being asked about, via alert_type below"* — as if a real variable carried the
correct type through — but both call sites used a hardcoded `"short_squeeze"` string literal,
not a variable. Confirmed the CORRECT pattern is well-established elsewhere in the SAME file:
`check_gamma_unwind_alerts()` (a few hundred lines below) correctly threads its own
`_gamma_alert_type` (`"gamma_unwind_calls"`/`"gamma_unwind_puts"`) through as BOTH the
buckets-dict key and the `alert_type` argument to `_squeeze_family_calibration_for_alert_type()`
— `check_squeeze_ignition_alerts()` never followed that same pattern. `_SQUEEZE_FAMILY_CAL_
BANDS` (the dict resolving which threshold bands apply per alert type) had no `"squeeze_
ignition"` entry at all, confirming this alert type was never actually wired into its own
calibration in the first place — it was simply riding on `short_squeeze`'s.

**Fix**: added a `"squeeze_ignition": _PREBREAKOUT_CAL_BANDS` entry to `_SQUEEZE_FAMILY_CAL_
BANDS` — `squeeze_ignition` genuinely DOES share `short_squeeze`'s own band SCHEME (both gate
on the identical `short_percent_of_float` metric/scale/floor per the pre-existing comment), so
this part of the reuse was legitimate and correct to keep. Changed the calibration-buckets
builder call AND its cache key to `"squeeze_ignition"` (a distinct Redis cache key from
`short_squeeze`'s own — `"stockai:cal:squeeze_family:squeeze_ignition"` vs. `"...short_
squeeze"` — so the two entries can never collide), and changed the win-rate lookup's `alert_
type` argument from `"short_squeeze"` to `"squeeze_ignition"`. The RESOLVED-OUTCOME POPULATION
being scored against is now correctly `squeeze_ignition`'s own, while the band thresholds stay
legitimately shared with its sibling.

**Tests**: 4 new source-text regression tests added to `services/market-data/tests/
test_squeeze_ignition_alert.py`, checking the actual `alert_type` ARGUMENT passed at each call
site — not just that the helper function was called by name. This distinction matters: the
pre-existing `test_reuses_the_game_plan_and_calibration_helpers_not_a_reimplementation` test
only asserted the helper name string appeared in the function body, never which argument it was
called with — exactly why this bug went unnoticed for however long it's been live. Adversarially
verified: reverted both call sites back to `"short_squeeze"` and confirmed 3 of 4 new tests
failed with real, meaningful diffs (a 4th test, checking the band-scheme dict entry
independently, correctly stayed green since that part of the sabotage wasn't touched) — restored
and confirmed byte-identical via `diff`. Full 2015-test market-data suite green; pyflakes clean.

**What to check if either looks wrong**:
```bash
# Confirm get_order()'s field reads:
docker exec stockai-market-data-1 grep -n "orderedQuantity\|detail.get(\"status\"" /app/src/services/broker/etrade_broker.py

# Confirm squeeze_ignition's own calibration wiring:
docker exec stockai-market-data-1 grep -n '"squeeze_ignition"' /app/src/services/scheduler.py

# Confirm the two calibration cache keys never collide:
docker exec stockai-redis-1 redis-cli keys 'stockai:cal:squeeze_family:*'
```

---


## Feature Reference: SR-WATCH-PROXIMITY-ALERT — Support/Resistance Proximity Watch (Built 2026-08-26/27)

**User ask, verbatim**: "Can I get alert when stock gets close to the support and resistance
level, so that I can watch and see if I buy or sell the stock? Does AI Signal has this
feature?" — answered directly first: AI Signal only reacts once price is AT a level (baked
invisibly into the fused BUY/SELL probability, no distance dimension, no separate user-visible
signal), and no alert mechanism anywhere proximity-checks a computed S/R level. User confirmed
("yes go ahead") and made 3 explicit design choices via `AskUserQuestion`: threshold **scales
with volatility (ATR-based)**, not a fixed %; fires **once per approach, then resets** once
price moves away (not a permanent one-shot, not a daily cadence); lives as a **separate,
dedicated "S/R Watch" alert type**, not a new metric bolted onto the existing compound-condition
`PriceAlert` engine.

**Mirrors `SqueezeWatch`'s (`T260-BEARISH-PUTS-WATCHLIST`) whole architecture** — a dedicated
per-user watch table, CRUD API (GET list / POST create-or-rearm / DELETE, no separate reset
endpoint), a 1-minute scheduler job with a Redis non-blocking lock, per-watch market-hours
gating for mixed US/HK lists, reading only already-cached Redis state (never a fresh yfinance
call in the fast-alert loop), and a dedicated email function — but with one genuinely different
lifecycle piece, built specifically because the user asked for it: `SrWatch.currently_near` is
a **transitional** `True`/`False` state (fires on the False→True transition, resets to `False`
once price moves back out of the band, can fire multiple times over the watch's life), not
`SqueezeWatch`'s permanent one-shot `reverted` flag.

**New table** — `SrWatch` (`shared/db/models.py`), `(user_id, symbol)` unique, `atr_multiplier`
(default 1.0), `currently_near`, `last_alert_at`/`last_alert_level_kind`/`last_alert_level_price`
(display/audit only — NOT the dedup mechanism, `currently_near` is). Brand new table,
`create_all()`-friendly — no manual migration needed.

**Detection** — `check_sr_watch_reverts()` (`services/market-data/src/services/scheduler.py`,
1-minute interval, registered inside the existing `if _is_alerting_enabled():` gate matching
every other alert job). Reads live price from `stockai:live_prices` (never a fresh fetch per
watch). ATR(14) is Redis-cached per symbol (`stockai:sr_watch_atr:{sym}`, 4h TTL, matching
`stockai:avg_volume`'s own established cadence for this class of slow-moving indicator) — cache
misses are batch-computed ONCE via `_batch_compute_atr()` for the whole watch list, never once
per watch. Nearest support/resistance comes from a real, per-symbol
`GET /ta/{symbol}/levels` call to technical-analysis (`sr_context.sr_nearest_support`/
`sr_nearest_resistance` — deliberately NOT `sr_cleared_*`, which are the already-broken levels
from breakout-quality assessment, the wrong field pair for a proximity watch). `band = atr *
atr_multiplier`; `is_near` is true if price is within `band` of EITHER level (an OR, not an
AND). Whichever level is closer wins when both qualify. `currently_near` is set to `True` only
**after** a confirmed successful send (never before — a failed send must not silently mark the
watch as alerted, or a real approach hitting a delivery failure would go unnoticed and the
watch would incorrectly stay silent next cycle too).

**Email** — `send_sr_watch_alert_email()` (`email_service.py`), framed by level kind (support:
green, bounce-zone framing; resistance: red, rejection-zone framing, matching the "How to Trade
It" language already established for Volume Profile elsewhere in this app), reports the level
price/current price/distance %/ATR(14), and states explicitly this is a measured fact, not a
prediction — a level can hold, break, or get retested — and that the watch **fires again** once
price moves away and returns (worded slightly differently between the HTML body, "will alert
again," and the text body, "fires again" — both correct, just not identically phrased).

**API** — `GET/POST/DELETE /stocks/sr-watch` (`services/market-data/src/api/routes.py`).
Re-adding an existing watch updates its settings (atr_multiplier/note) rather than 409-ing,
matching `SqueezeWatch`'s own re-arm convention — and deliberately resets `currently_near` to
`False` on re-add, so a symbol removed-then-re-added while already near a level fires fresh
rather than silently inheriting a stale "already alerted" state.

**Frontend** — a compact `SrWatchButton` component (self-contained, its own fetch — matching
`StockGoalsPanel`'s established precedent for keeping `stock/[symbol].tsx`, already 4000+
lines, from growing further), embedded directly inside the page's existing Support &
Resistance card. Shows "🔕 Watch this level" → an inline ATR-multiplier input + Save → "🔔
Watching (Nx ATR)", plus a small "● currently near {level kind}" indicator when
`currently_near` is true.

**Tests**: `services/market-data/tests/test_sr_watch_alert.py` (30 cases) and
`test_sr_watch_routes.py` (8 cases) — `send_sr_watch_alert_email()` tested directly (pure
composition), `check_sr_watch_reverts()`/route wiring covered via source-text regression checks
matching `test_squeeze_watch_revert_alert.py`/`test_squeeze_watch_routes.py`'s own established
pattern for functions with heavy DB/apscheduler dependencies. Adversarially verified 3 sabotage/
restore cycles, all caught correctly and reverted (confirmed byte-identical via `diff`/`md5sum`
before moving on): removing the `currently_near = False` reset on move-away (caught by the
dedicated reset test); removing one leg of the per-watch HK market-hours gate (caught by the
dedicated per-watch gate test); marking `currently_near = True` before, not after, the send call
(caught by the dedicated send-ordering test).

Full 2129-test market-data suite green (up from 2099); `pyflakes` clean on all 3 touched
backend files (confirmed via `git stash` that every warning predates this change — only line
numbers shifted). Frontend: `npx tsc --noEmit` clean, full 132-test vitest suite unaffected, a
full `next build` clean (`/stock/[symbol]` 56.5kB → 57.2kB) — confirmed via direct grep that
"Watch this level" reached the actual compiled `stock/[symbol]` chunk, not just source.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n "def check_sr_watch_reverts\|sr_watch_check" /app/src/services/scheduler.py
docker logs stockai-market-data-1 --since 1h | grep 'sr_watch.done\|sr_watch.symbol_error'

# Confirm a real watch's currently_near state directly:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT symbol, atr_multiplier, currently_near, last_alert_at, last_alert_level_kind FROM sr_watches ORDER BY added_at DESC LIMIT 10;"

# Spot-check the Redis-cached ATR for a specific symbol:
docker exec stockai-redis-1 redis-cli get 'stockai:sr_watch_atr:AAPL'
```

---

## Feature Reference: AUD-UWFINDINGS-GEXSHORT — Real UW GEX + Short-Interest Corroboration (2026-09-04)

**Closes the 2 "free wins" identified by the UW Advanced-tier/yfinance comparison review**
(published artifact, approved via "So API Basic should be enough and let's fix the findings") —
staying on UW Basic tier while wiring real UW data into 2 places that were previously running on
free-tier proxies their own code already flagged as imperfect. Both follow the exact
corroboration-not-replacement pattern `check_short_squeeze_alerts()` established first (its own
`_SQUEEZE_UW_DISAGREEMENT_REL_THRESHOLD` / `uw_disagrees` flag): a real UW reading only ever
*annotates* a candidate, never suppresses or replaces the free-tier decision that already drives
inclusion/scoring.

### Fix 1 — real GEX corroboration for `check_gamma_unwind_alerts()`

**Before**: the gamma-unwind alert's own docstring already discloses its OI-concentration
"GEX" is a defensible proxy, "NOT a real GEX calculation" — computed from yfinance open
interest, not genuine dealer-hedging data.

**Now**: for every candidate, a real Unusual Whales GEX reading (`call_wall`/`put_wall`/
`gamma_flip`, via the already-existing `unusual_whales.get_gex_levels()`) is fetched and checked
against the current price. A new `_GEX_CORROBORATE_BAND_PCT = 0.03` constant in `scheduler.py`
sets how close (as a % of price) the price must sit to a real level to count as corroborating —
a proximity check, not a disagreement-magnitude one, since GEX gives real strike prices while
the free proxy gives an OI *share*; the two aren't directly comparable, but "is price actually
near a level where real dealer gamma concentrates" is. On a match, `cand["gex_corroborates"] =
True` plus the specific nearby level(s) in `cand["gex_nearby_levels"]`. `send_gamma_unwind_email()`
renders a purple "✓ Real GEX corroborates: price is near {level}" line only when both keys are
present — never suppresses or adds a candidate, and a UW lookup failure/disabled subscription
just means no corroboration line ever appears.

### Fix 2 — real UW short-interest corroboration for signal-engine's squeeze-boost gate

**Before**: the SWING/GROWTH squeeze-boost gate in `_apply_style_signal()` (a small confidence
boost when `short_pct_float >= 0.20`/`0.30`) read exclusively from `_fetch_short_interest()`,
itself sourced from market-data's yfinance-derived `fundamentals` table — the exact field this
codebase's own `AUD265-SHORT-INTEREST-AGE-NEVER-CHECKED` comments already flag as lagging real
exchange settlement by up to ~6 weeks.

**Now**: signal-engine has no direct Python import path to `unusual_whales.py` (separate
service/container), so a new `GET /{symbol}/short-interest-uw` route was added to market-data —
a thin, fail-open wrapper around `unusual_whales.get_short_interest()`, mirroring
`/gamma-exposure`'s own `available: False` contract exactly. A new
`_check_uw_short_interest_disagreement(symbol, short_pct_float)` in `signals.py` calls this route
and compares UW's real `si_float` (scaled ×100 to match the free reading's percentage
convention) against the free value using the *same* 20% relative-difference threshold as
`check_short_squeeze_alerts()`'s own `_SQUEEZE_UW_DISAGREEMENT_REL_THRESHOLD` (a new,
independent `_SHORT_INTEREST_UW_DISAGREEMENT_REL_THRESHOLD = 0.20` constant). On real
disagreement, `reasons["short_interest_uw_disagrees"] = True` plus
`reasons["short_interest_uw_short_percent_of_float"]` are set — `short_pct_float` itself and the
boost gate's own `>=0.30`/`>=0.20` threshold logic are never touched.

### Testing

18 new tests total: 10 in `test_gamma_unwind_alert.py` (market-data), 8 in a new
`test_short_interest_uw_route.py` (market-data), 10 in a new
`test_uw_short_interest_corroboration.py` (signal-engine) — plus one incidental fix to
`test_gamma_exposure_route.py`'s own source-extraction end-marker, since the new
`short-interest-uw` route now sits between `gamma-exposure` and `earnings-transcript` in
`routes.py` (matching that file's own established "end marker moved again" precedent from
`AUD-TRANSCRIPT`).

3 adversarial sabotage cycles, all caught cleanly by exactly their targeted test(s) and restored
+ confirmed byte-identical via `md5sum`:
1. GEX proximity-band filter stripped to "any non-None level counts as nearby" — caught by
   `test_gex_corroboration_uses_its_own_proximity_band_constant`.
2. Email rendering guard weakened to check only `gex_nearby_levels`, not also
   `gex_corroborates` — caught by
   `test_gex_corroborates_false_renders_no_extra_content_even_with_stale_levels_present`.
3. Short-interest relative-diff threshold check stripped to always-true — caught by exactly the
   2 tests verifying threshold-gating (`test_no_flag_when_readings_agree`,
   `test_boundary_just_under_threshold_no_flag`), no others.

Full 2691-test market-data suite green (up from 2673). signal-engine: 393 relevant tests green
(1 pre-existing collection error in `test_signal_generator.py` + 4 pre-existing failures in
`test_analyst_momentum.py`, both confirmed identical with this diff stashed out entirely via
`git stash` — unrelated to this change). Frontend `tsc --noEmit` clean.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n "_GEX_CORROBORATE_BAND_PCT\|gex_corroborates" /app/src/services/scheduler.py
docker exec stockai-market-data-1 grep -n "short-interest-uw" /app/src/api/routes.py
docker exec stockai-signal-engine-1 grep -n "_check_uw_short_interest_disagreement\|_SHORT_INTEREST_UW_DISAGREEMENT_REL_THRESHOLD" /app/src/generators/signals.py

# Confirm the new route responds for a real symbol:
docker exec stockai-market-data-1 curl -s http://localhost:8001/stocks/AAPL/short-interest-uw

docker logs stockai-signal-engine-1 --since 1h | grep 'short_interest_uw'
```

---

## Feature Reference: AUD-DQCHECKS-VISIBILITY — 12 More Scheduler-Job Liveness Checks + a Unusual Whales Rate-Limit Gauge (2026-09-04)

**User request:** "let's create more data quality checks for monitoring" — "you can decide...I
wanna have a full visibility on the server health and caught every error beforehand" — surfaced
directly out of the same session's `AUD-MISFIREGRACE-OPTIONSFLOW` investigation (see
`docs/incidents/self-tuning-job-performance-bugs.md`), which found 3 real-time alert jobs had
silently stopped re-firing with zero visible symptom short of directly querying Redis.

Built entirely on the existing declarative `_DQ_CHECKS` framework (`services/market-data/src/
services/scheduler.py`) rather than a new mechanism — that framework already had 4 check shapes
(`query` for table freshness, `job_status` for scheduler-job liveness, `ratio` for two-counter
comparisons, `gauge` for pure observability counters), one scheduled job (`run_data_quality_
checks`), Redis persistence (`dq_check:{name}`), and an email-on-failure path.

### 1. `job_status` liveness checks for 12 more 1-minute jobs

Before this pass, only 5 of the platform's ~17 genuinely-1-minute scheduler jobs had any
liveness check at all (`check_price_alerts`, `check_signal_alerts`, `check_earnings_reactions`,
`check_earnings_impact_alerts`, `check_macro_reaction_alerts` — added by an earlier
`AUD266-FIVE-ALERT-JOBS-RECORD-NO-STATUS` / `AUD266-ALERT-JOBS-LACK-STATUS-CONSEQUENCE-DQ`
fix). Added 12 more, closing the gap for the exact 3 jobs `AUD-MISFIREGRACE-OPTIONSFLOW` found
silently dead (`check_options_flow_alerts`, `check_dark_pool_alerts`, `check_sr_watch_reverts`)
plus 9 others sharing the same latent risk: `check_volume_anomalies`,
`check_conditional_orders`, `check_short_squeeze_alerts`, `check_squeeze_ignition_alerts`,
`check_squeeze_watch_reverts`, `check_value_area_breakdown`,
`check_portfolio_drawdown_alerts`, `check_early_earnings_news_alerts`,
`check_top3_conviction`.

Two of these twelve needed a companion fix before a `job_status` check could work at all:
- **`check_conditional_orders`** (`services/market-data/src/services/conditional_orders.py`)
  made **zero** `_record_job_status()` calls anywhere in its body — the same
  `AUD266-FIVE-ALERT-JOBS-RECORD-NO-STATUS` gap class found again. Fixed by adding the calls
  directly (a local `from .scheduler import _record_job_status` import, not module-level, since
  `scheduler.py` already imports `check_conditional_orders` FROM this file — a top-level import
  the other direction would be circular).
- **`check_portfolio_drawdown_alerts`** already called `_record_job_status()` correctly on
  every path — just under its scheduler `id=` string (`"portfolio_drawdown_alert_check"`)
  rather than its own function name, an inconsistency with the majority convention but not
  itself a bug. The new DQ check's `job_name` intentionally matches that real, already-written
  key rather than "fixing" a naming mismatch that was never actually broken — changing it would
  have been the one thing that COULD have broken it (the check would then read a key nothing
  writes to).

### 2. A new gauge: `uw_rate_limit_events_48h`

The platform had **no admin-visible signal at all** for Unusual Whales rate-limiting before this
— only a per-call log line (`unusual_whales.rate_limit`, `services/market-data/src/services/
unusual_whales.py`'s `_get()`), with no rollup anywhere, despite this same session's own UW
API-volume audit finding real 429 events on production.

New `_incr_rate_limit_counter()` in `unusual_whales.py` itself (self-contained — a plain
INCR-then-expire-once-on-first-write, the same idiom `scheduler.py`'s own
`_incr_rolling_counter()` uses, but not imported from there to avoid a circular import since
`unusual_whales.py` has no reason to otherwise depend on `scheduler.py`) increments a rolling
48h Redis counter (`stockai:metric:uw_rate_limit_count_48h`) every time `_get()` sees a real 429.
`scheduler.py` imports just the counter's Redis-key constant at module level (`from
.unusual_whales import _RATE_LIMIT_COUNTER_KEY as _UW_RATE_LIMIT_COUNTER_KEY` — a plain string
constant carries none of the circularity risk a function import into a hot module-load path
would) rather than duplicating the literal key string, so the two can never silently drift
apart. Same "gauge, no pass/fail concept" framing as the 3 pre-existing fundamentals-cache-miss
counters (squeeze/squeeze-watch/squeeze-ignition) — a nonzero count is expected background
noise some of the time (real 429s happen even in healthy operation, already handled by
`tenacity`-based retry/backoff elsewhere in `_get()`), not itself proof of a problem; this is
purely observability so a *sustained* pattern is visible somewhere other than grepping logs.

### Testing

27 new/updated tests: 4 in `test_unusual_whales.py` (a 429 triggers the counter increment;
the counter itself INCRs the real key, sets a TTL only on first write, never resets that TTL on
a later increment, and fails open on a Redis exception without ever raising); 13 in
`test_dq_check_job_status_source.py` (all 12 new `job_status` entries exist with the correct
`job_name` — including the `check_portfolio_drawdown_alerts` naming exception — none carry a
stray `query` key, the 2 jobs confirmed dead in tier 343 specifically have checks now, and
`conditional_orders.py` genuinely has the new `_record_job_status()` calls rather than just
`scheduler.py`'s own `_DQ_CHECKS` entry assuming they exist) plus 2 new tests there for the UW
gauge (a `gauge` source with no `query`/`job_name` key; `counter_key` correctly imports the real
constant rather than a literal that could drift); 1 pre-existing test in
`test_squeeze_audit_20260725_fixes.py` updated for the new gauge count (3 → 4, matching that
test's own established "bump this number when a gauge is added" precedent from its prior 2 → 3
update). 1 adversarial sabotage cycle (`check_portfolio_drawdown_alerts`'s `job_name` reverted
to its wrong, never-actually-written function-name form) — caught cleanly by exactly the 3
tests targeting that specific wiring, restored and confirmed byte-identical via `md5sum`. Full
2721-test market-data suite green.

**What to check if this looks wrong**:
```bash
# Confirm a specific new job_status check is reading real, fresh data:
docker exec stockai-redis-1 redis-cli get dq_check:check_options_flow_alerts

# Confirm the UW rate-limit gauge is wired to the real counter:
docker exec stockai-redis-1 redis-cli get stockai:metric:uw_rate_limit_count_48h
docker exec stockai-redis-1 redis-cli get dq_check:uw_rate_limit_events_48h

# Confirm check_conditional_orders now genuinely records status:
docker exec stockai-market-data-1 grep -n '_record_job_status("check_conditional_orders"' /app/src/services/conditional_orders.py
```

---

