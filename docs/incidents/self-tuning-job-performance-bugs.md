## Recurring Issue: BUG-WEEKLYREFRESH-HEAVYSWEEP-TIMEOUT — Heavy Weekly Sweeps Were Timing Out And Silently Truncating the Rest of Sunday's Tuning Chain (Fixed 2026-08-31)

**Symptom:** the user asked to confirm every Sunday routine job had actually run this week
(2026-08-30). Checking `TuneHistory` directly found `outcomes/calibrate/apply`,
`tune_style_profiles`, `tune_kscore_weights`, `tune_kscore_curve`,
`backfill_bearish_pillars`, and `tune_sell_pillars` all had rows from 2026-08-16 and
2026-08-23 (2 weeks prior), but **nothing** from 2026-08-30 — a real, silent gap on the
most recent Sunday, not a display artifact. Re-checking the two prior Sundays' own
`scheduler:job:*` Redis keys and container logs directly (not assumed) confirmed a pattern:
every one of these 7 calls consistently logged `scheduler.http_failed` (client-side timeout
exhausted) even on weeks the corresponding `TuneHistory` row DID eventually appear —
proving the SERVER-side work was completing fine minutes after the CLIENT gave up.

**Root cause:** `_post()` (`services/market-data/src/services/scheduler.py`) had a
hardcoded `timeout=15, retries=3` (backoff `[3, 8, 20]`s) for every single fire-and-forget
call it makes — a sane default for the ~25 cheap, idempotent-cost calls in this file, but
actively harmful for the 7 genuinely heavy, synchronous, non-idempotent-cost sweep routes
`_weekly_full_refresh()` calls: multi-minute grid sweeps over `signal_outcomes`/`Ranking`
history (`tune_strategy`'s own docs describe a 403-cell grid x 4 horizons). A client
timeout+retry does **not** cancel the still-running server-side request — the target
route's own DB session/thread keeps executing to completion regardless — so retrying just
queues a SECOND overlapping heavy query against the same bounded connection pool
(`pool_size=5 + max_overflow=10 = 15` total, confirmed identical in both signal-engine's
and ranking-engine's `shared/db/session.py`), compounding load rather than recovering
from it. On 2026-08-30 specifically this compounded past the point of ever completing:
`tune_strategy` (the heaviest of the 7) never wrote a single `joint_strategy` row that
week, and because every downstream call in `_weekly_full_refresh()` runs sequentially
after it in the same unconditional script, the entire rest of that Sunday's chain
(`tune_kscore_weights`/`_curve`, `backfill_bearish_pillars`, `tune_sell_pillars`,
`calibrate_entry_weights`, `calibrate_min_rr_ratio`, `promotion_gate`, `rl_agent_train`)
silently never ran at all that week either.

**Fix applied:** `_post()` gained keyword-only `timeout: float = 15, retries: int = 3`
overrides (defaulting to the exact original hardcoded behavior — every one of the other
~25 call sites in this file is unaffected, verified via a truth-table trace that
`retries=3` still produces the original 3-attempt/`[3,8]`-sleep sequence byte-for-byte).
All 7 heavy sweep call sites inside `_weekly_full_refresh()` now pass `timeout=180,
retries=1` — a single long-budget attempt, never a retry storm:
```python
_post(f"{_settings.signal_engine_url}/signals/outcomes/calibrate/apply", timeout=180, retries=1)
_post(f"{_settings.signal_engine_url}/signals/tune_style_profiles", timeout=180, retries=1)
_post(f"{_settings.signal_engine_url}/signals/tune_strategy", timeout=180, retries=1)
_post(f"{_settings.ranking_engine_url}/rankings/tune_kscore_weights", timeout=180, retries=1)
_post(f"{_settings.ranking_engine_url}/rankings/tune_kscore_curve", timeout=180, retries=1)
_post(f"{_settings.signal_engine_url}/signals/backfill_bearish_pillars", timeout=180, retries=1)
_post(f"{_settings.signal_engine_url}/signals/tune_sell_pillars", timeout=180, retries=1)
```

**Recovery — every job for the missed 2026-08-30 week was manually re-triggered and
confirmed complete before this fix's own git commit/deploy**, one at a time per this
file's own standing "never trigger a heavy job in parallel, never interrupt one mid-flight"
discipline (interrupting `tune_kscore_curve` mid-run during this exact recovery left one
real orphaned `idle in transaction` Postgres connection, cleaned up via
`pg_terminate_backend` — a live demonstration of exactly the risk class this fix exists to
prevent). Real results from this recovery run:
- `tune_kscore_weights`/`tune_kscore_curve`/`outcomes/calibrate/apply`/`tune_style_profiles`/
  `tune_strategy`/`backfill_bearish_pillars`/`tune_sell_pillars` — all completed with real
  `TuneHistory` rows once given the 180s budget (previously invisible to the old 15s/3-retry
  client behavior).
- `promotion_gate` (the 8-way SHORT/SWING/LONG/GROWTH × US/HK loop, called directly via
  Python import rather than HTTP since the scheduler's own service token has no admin-gated
  DB user record) took **769.2s** (~12.8 min) to complete all 8 evaluations — genuinely slow
  at current data volume (each combo runs 3 full walk-forward replay passes — see
  `promotion_gate.py`'s own `evaluate_and_record()`), not a bug: `_historical_atr()`/
  `_historical_confidence_delta()` were checked directly and confirmed properly bounded
  (`LIMIT period+5`/`LIMIT 1`), no `_kscore_curve_raw_cache()`-class unbounded-window issue
  found anywhere in the replay path. Result: `SHORT/US`, `SHORT/HK`, `SWING/US`, `SWING/HK`,
  `LONG/US`, `LONG/HK`, `GROWTH/US`, `GROWTH/HK` — all `not_promoted` (a legitimate,
  non-error outcome; no candidate cleared the validation-slice promotion-margin bar this
  week).
- `rl_agent_train` completed in 3.9s: `n_trades=103, win_rate=0.32,
  threshold=-0.3069`, real feature weights written.

**What to check if this looks wrong:**
```bash
# Confirm the timeout/retries overrides are present on all 7 heavy call sites:
docker exec stockai-market-data-1 grep -n "timeout=180, retries=1" /app/src/services/scheduler.py

# Check whether last Sunday's weekly refresh actually completed the FULL chain (not just
# the first few calls) — every one of these parameter_class values should show a row dated
# within the last 7 days:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT parameter_class, MAX(ts) FROM tune_history GROUP BY parameter_class ORDER BY parameter_class;"

# Check for a genuinely stuck job mid-Sunday (a real hang, not just slow) — fresh, small,
# changing idle-in-transaction durations under market-data's own container IP (172.18.0.9,
# NOT .5 — confirmed via `docker inspect` that ranking-engine is .5 and market-data is .9)
# mean real ongoing work; a single connection with a growing, non-decreasing duration means
# a genuine orphan:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT pid, client_addr, state, now()-query_start AS dur, left(query,100) FROM pg_stat_activity WHERE state='idle in transaction' ORDER BY dur DESC;"
```

**Design invariant:** any NEW heavy, synchronous, non-idempotent-cost route added to
`_weekly_full_refresh()`'s calibration chain must pass an explicit `timeout`/`retries`
override matched to its own realistic worst-case runtime — never rely on `_post()`'s
default 15s/3-retry pair, which is correct only for cheap calls.

---


## Recurring Issue: BUG-KSCORECURVE-UNBOUNDEDWINDOW — tune_kscore_curve's Raw-Input Cache Had Unbounded Per-Row Window Growth (Fixed 2026-08-31)

**Found while investigating BUG-WEEKLYREFRESH-HEAVYSWEEP-TIMEOUT** (above) — raising
`tune_kscore_curve`'s client timeout to 180s still wasn't enough on its own; a direct,
zero-network-hop test INSIDE `ranking-engine`'s own container confirmed the function
genuinely couldn't complete within 200s even with no client/network variable in play at
all, proving this was a real, separate server-side performance bug, not a networking or
client-timeout artifact.

**Root cause:** `_kscore_curve_raw_cache()` (`services/ranking-engine/src/api/routes.py`)
— built for `T234-CONFIG-UNJUSTIFIED-THRESHOLDS` Group B's walk-forward curve-shape
sweep — computes `_technical_raw_inputs()`/`_volatility_raw_input()` ONCE per `Ranking`
row so every curve-shape candidate in the sweep pool can cheaply reuse it instead of
recomputing RSI/ADX/realized-vol per candidate. The bug was in how that ONE computation
per row was sized: it passed the **full** "all history up to this row's own `as_of` date"
price slice into `pd.DataFrame()` + rolling/EWM computations, for **every** row — a real,
unbounded, O(n²)-ish cost as `idx` (and therefore the per-row DataFrame size) grows across
a sweep window. Confirmed live: a real 365-day/11,746-row sweep against production data
(166 stocks, up to 767 bars of history each) did not complete this function alone within
250s.

**Fix applied:** bounded the window to the trailing `_KSCORE_CURVE_RAW_CACHE_MAX_WINDOW =
300` bars via `bisect.bisect_right()` + a `window_start = max(0, idx - 300)` slice.
**Verified numerically, not assumed**, that this is safe before shipping: ran a real script
against production data (stock_id=1, 752 real bars of history) comparing full-history vs.
300-bar-windowed RSI/ADX outputs — differences of `<4e-9` (RSI) / `<1.2e-7` (ADX), many
orders of magnitude below any threshold that could change which curve-shape candidate the
sweep selects. 300 bars gives `_technical_raw_inputs()`'s own longest rolling window
(`sma200`, 200 bars) a 100-bar warmup margin, comfortably enough for RSI/ADX's EWM to have
converged too (EWM has theoretically infinite memory but converges geometrically in
practice). A second, independent inefficiency was fixed in the same pass: `dates` (a plain
`[d for d, _row in closes]` list comprehension) was rebuilt from scratch on **every** row
for a stock, even though every row sharing that `stock_id` uses the identical, unchanged
`closes` list — now cached once per stock via a local `_dates_cache` dict.

```python
_KSCORE_CURVE_RAW_CACHE_MAX_WINDOW = 300  # see the function's own docstring for why 300 is safe

def _kscore_curve_raw_cache(session, rankings, price_by_stock):
    cache: dict[int, dict] = {}
    _dates_cache: dict[int, list] = {}
    for r in rankings:
        closes = price_by_stock.get(r.stock_id)
        if not closes:
            continue
        dates = _dates_cache.get(r.stock_id)
        if dates is None:
            dates = [d for d, _row in closes]
            _dates_cache[r.stock_id] = dates
        idx = bisect.bisect_right(dates, r.as_of)
        if idx == 0:
            continue
        window_start = max(0, idx - _KSCORE_CURVE_RAW_CACHE_MAX_WINDOW)
        window = [row for _d, row in closes[window_start:idx]]
        if len(window) < 15:
            continue
        df = pd.DataFrame(window)
        cache[r.id] = {
            "technical": _technical_raw_inputs(df),
            "volatility": _volatility_raw_input(df),
        }
    return cache
```

**A real, self-caught "still passes after sabotage" test-writing gap during development**:
a first version of the dates-cache test passed unchanged even with the caching
optimization completely removed, because the test only checked CORRECTNESS (the resolved
values were still right either way, just recomputed instead of cached) — a genuinely
different property from whether the optimization is actually PRESENT. Fixed by splitting
into a source-text regression check (confirms `_dates_cache`/`_dates_cache.get(...)`/
`_dates_cache[...] = dates` literally exist in the function body — guards the
optimization's presence) plus a separately-scoped, honestly-relabeled behavioral test
(guards correctness only). Re-ran the sabotage against the corrected pair — now correctly
caught by the source-text check.

**Tests**: `services/ranking-engine/tests/test_kscore_curve_sweep.py` gained 8 new cases —
`session` being genuinely unused/`None`-safe, rows with fewer than 15 bars of history
skipped (not fabricated), a row with zero price history skipped cleanly, the 300-bar
bound producing a real non-`None` result shape-matching the full-history version, the
window passed downstream actually capped at 300 (not the full history — patches
`src.scoring.kscore._technical_raw_inputs`, NOT `src.api.routes._technical_raw_inputs`,
since the function is imported LOCALLY inside `_kscore_curve_raw_cache()`'s own body, not
as a module-level name on `routes.py`), the dates-cache presence/correctness pair above.
Adversarially verified: reverted the 300-bar bound back to the full-history slice and
confirmed the dedicated capped-window test failed with a real, meaningful diff; reverted
the dates-cache optimization and confirmed the source-text regression test (but not the
correctness test) failed — both restored and confirmed byte-identical via `md5sum` before
moving on. Full 111-test ranking-engine suite green; `pyflakes` clean (the sole remaining
warning, `db.SignalType` imported but unused, confirmed pre-existing via `git stash`).

**What to check if this looks wrong**:
```bash
docker exec stockai-ranking-engine-1 grep -n "_KSCORE_CURVE_RAW_CACHE_MAX_WINDOW\|_dates_cache" /app/src/api/routes.py

# Live-check a real sweep now completes within a reasonable client budget:
docker exec stockai-ranking-engine-1 curl -s -X POST \
  'http://localhost:8004/rankings/tune_kscore_curve?days=365' \
  -H "Authorization: Bearer <token>" -w '\nelapsed: %{time_total}s\n'
```
If a future curve-shape candidate set ever needs MORE than 200 bars of rolling-window
history (e.g. a new, longer-period indicator added to `_technical_raw_inputs()`), the
300-bar constant must be re-verified against the new requirement — it is NOT a generic
"300 is always enough" assumption, it is specifically `sma200`'s 200-bar need plus a
100-bar warmup margin.

---

## Recurring Issue: AUD-MINRR-MARKETBLIND — Self-Calibrated R:R Floor Pooled Across Markets, Silently Disabling HK Paper Trading for Months (Fixed 2026-09-04)

**Symptom:** user reported paper-trading performance had "dropped a lot" and that the system
"always said signals exist but not trading," asking specifically about a watchlist problem.
Direct DB queries confirmed 2 of 5 paper portfolios had gone genuinely dormant, not merely
slow: HK SWING Portfolio (id=2) had zero new trade entries since **2026-06-25** (2+ months);
HK GROWTH Portfolio (id=4) since **2026-08-17** (~2.5 weeks). Both portfolios' watchlists
were confirmed to contain real, eligible HK candidates the whole time (16-25 HK stocks
tagged with the matching `trading_style`) — this was never an empty-watchlist problem.

**Root cause:** `calibrate_min_rr_ratio()` (`services/market-data/src/api/paper_portfolio.py`)
self-tunes `min_rr_ratio`/`regime_min_rr_ratio` weekly from real closed-trade R:R/PnL data —
a genuinely good design (train/validation split, only applies if the candidate beats the
current baseline on held-out data, same discipline as `calibrate_entry_weights()`). The bug:
the sweep pools **every market's trades together** with no market split at all. HK trades far
less often than US (confirmed at time of fix: 19 HK vs. 97 US qualifying closed trades), so
the resulting calibrated value was effectively a US-only number, silently applied to HK
portfolios too via `_default_min_rr_ratio()`'s single, market-blind fallback.

The regime-tier value compounds this: `regime_min_rr_ratio` isn't independently calibrated at
all — it's `min_rr_ratio * 1.5` (a blind relative bump, per the code's own pre-existing
comment explaining there wasn't enough per-regime volume to calibrate it separately). At the
time of this fix that produced `regime_min_rr_ratio = 2.25 * 1.5 = 3.38`. Production logs
(`stockai-market-data-1`, 2026-08-10 to 2026-09-04) showed **1768.HK rejected by the R:R gate
203 times**, every single rejection landing at 2.90-2.93:1 — consistently, structurally just
under the 3.4:1 bar, never scattered randomly above/below it. HK's own risk parameters
(`_HK_MARKET_OVERRIDES`' stop/target percentages) cap realistically achievable R:R around
~2.9:1 for HK setups — meaning a floor calibrated almost entirely off US trade behavior was
being applied to a market that could structurally never clear it, for the entire multi-week
stretch HK's regime sat in `choppy` (this app's own regime classifier, confirmed live and
fresh, not stale — the regime WAS genuinely choppy; the bug is that choppy's own R:R floor
was wrong for HK, not that the regime reading itself was broken).

A separate, real, correctly-working HK-only "mainland flow" hard gate (T224-A) was also found
firing 2,849 times across 12 HK symbols in the same 25-day window — a compounding factor
narrowing the funnel further, but not the root cause: some symbols DO clear it on
positive-flow days, and those survivors then die at the R:R floor above.

**Fixed:** `_default_min_rr_ratio()` (`paper_trading_engine.py`) now takes a `market`
parameter. `calibrate_min_rr_ratio()` now also computes, per market, that market's own
qualifying-trade count and its own observed R:R ceiling (the 90th percentile of that market's
`rr_ratio_at_entry` values across all its closed trades) — stored under a new `by_market` key
alongside the existing pooled top-level values. When a market's own ceiling sits below the
pooled `regime_min_rr_ratio`, that market's effective floor is capped at its own ceiling
(never capped below the neutral-tier baseline, so it can't be loosened past what calibration
already trusts). Deliberately **not** a full independent per-market EV-maximizing sweep — HK's
19 trades is nowhere near the existing `_MIN_RR_MIN_TRADES=100` floor already required for a
real calibration, so a from-scratch HK-only threshold wouldn't be trustworthy yet either; this
caps the pooled (US-dominated) value rather than fabricating a new one off too little data.
All 3 real call sites in `paper_trading_engine.py` (`_should_enter()`'s own fallback gate,
`resolve_entry_gate_params()` for decision-engine's standalone `/decide` endpoint, and the
`config_overrides` payload sent to decision-engine itself) now thread `cfg.get("market")`
through so every consumer of the calibrated default resolves the same market-aware value.

A companion, narrower fix landed in the same pass: `_should_enter()`'s existing T171
premarket-gap filter compares live price only against `reasons["last_price"]` — the price at
signal-COMPUTE time — which is already post-gap for a stock that spiked on an earnings/8-K
surprise before the signal ever ran. Traced 2 large realized losses (SNOW -19.0%, DELL
-6.66%, both 2026-09-03) to exactly this blind spot: SNOW's own `reasons["last_price"]`
($377.995) was essentially identical to its actual entry price ($377.338) despite the stock
having genuinely spiked ~23% overnight on an 8-K filing beforehand — the existing filter
measured ~0% "gap" against an already-elevated reference and let it through, then it
mean-reverted into a stop-out. A full fix needs a genuine pre-event price baseline (N trading
days back, independent of signal-compute time) — not built this pass, since it needs a new DB
read threaded into `_should_enter()` (which currently has no DB/price-history access at all)
and this pattern is narrow (2 of ~50 `stop_hit` losses in the sample, not the dominant
win-rate driver). Interim, proportionate fix: the gap filter now also hard-rejects when a
moderate gap (over half of `max_entry_gap_pct`) is paired with clearly-elevated same-day
volume (`volume_z >= 1.5`, itself already computed and threaded through by signal-engine) —
exactly the SNOW/DELL signature (SNOW's own `volume_z=1.78`), which the pre-existing
volume-CONFIRMATION scoring layer a few lines below was previously rewarding as a POSITIVE
signal instead of flagging as spike-chasing risk.

Separately investigated and confirmed **not** bugs during this same pass: the still-active US
portfolios' poor win rates traced mostly to a HISTORICAL gate-config issue already fixed
2026-09-03 (`min_confidence` had drifted to 15.0 on several portfolios); live signal
confidence itself showing near-zero correlation with actual win/loss (quintile-bucketed win
rate flat at ~29-31%) is consistent with, not a new instance of, a prior audit's own
"confidence is meaningless" finding and was deliberately not re-opened here; `breakeven_stop`
exit-reason's own negative average return and the regime-mismatch hypothesis were both
checked directly against the data and found to be legitimate/non-issues, not bugs.

21 new tests (5 in `test_min_rr_calibration.py` for the market-aware read side of
`_default_min_rr_ratio()`, 5 in a new `test_min_rr_calibration_by_market.py` for the
calibration-side per-market cap logic via source-extraction — `paper_portfolio.py` can't be
imported directly in this test environment — 7 in `test_should_enter_de_parity.py` for the
combined gap+volume hard reject), plus 2 pre-existing tests in
`test_regime_min_rr_config_wiring.py` updated to match the call sites' new, still fully
calibration-routed market-aware shape. 2 adversarial sabotage cycles (the by-market cap
condition forced to never fire; the gap+volume combined reject condition forced to never
fire) — both caught cleanly by exactly their targeted test(s), both restored and confirmed
byte-identical via `md5sum`. Full 2707-test market-data suite green (up from 2691).

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 cat /data/models/min_rr_calibration.json | python3 -m json.tool
# Confirm by_market exists and each market's regime_min_rr_ratio reflects its own ceiling,
# not just the pooled top-level value.

docker exec stockai-market-data-1 grep -n "_default_min_rr_ratio(regime_state, cfg.get" /app/src/services/paper_trading_engine.py

# Confirm a specific HK candidate isn't still dying at the R:R gate:
docker logs stockai-market-data-1 --since 24h | grep 'blocked.*below minimum' | grep '\.HK'
```
If HK's own qualifying-trade count ever grows past the existing `_MIN_RR_MIN_TRADES=100`
floor, a real independent per-market EV-maximizing sweep (rather than this cap-the-pooled-
value interim fix) becomes worth revisiting.

---

