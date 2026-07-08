# Design: Backtest Harness — Phase 2 Scoping (Self-Improvement Loop)

**Status:** Design + grounding research complete, implementation starting. Supersedes §3b/§4-Phase-2
of `docs/DESIGN_SELF_IMPROVEMENT_LOOP_2026-07-04.md` with concrete findings from reading the actual
code and querying real data, rather than the earlier design's assumptions. Phase 1 (walk-forward
split for `outcomes/calibrate`) is done — see `T233-SELFIMPROVE-PHASE1` / `T232-OC3`.

---

## 1. Grounding findings (supersede the Phase 1 design doc's assumptions)

### 1a. Data retention is thinner than assumed — real constraint, not hypothetical

Queried production directly (`stockai-postgres-1`, 2026-07-06):

| Table | Span | Notes |
|---|---|---|
| `signals` | 2026-05-25 → 2026-07-04 (35 distinct days) | Per style/market breakdown below |
| `rankings` | 2026-04-29 → 2026-07-03 (44 distinct days) | 7-day gap 2026-06-25→07-02; HK stops 06-24 |
| `prices` (daily) | 2023-06-29 → 2026-07-02 | Not a bottleneck — 3 years |

Signal rows by style/market:

| Style | US days | HK days |
|---|---|---|
| SHORT | 25 | 21 |
| SWING | 34 | 29 |
| LONG | 25 | 21 |
| GROWTH | 20 | 16 |

A walk-forward split (70/30) on ~30 days gives ~21 train days / ~9 validation days — thin, and it
gets thinner once `SignalOutcome.is_correct IS NOT NULL` is also required (needs the hold period to
have elapsed, so the most recent ~10-20 days of signals have no resolved outcome yet). **This
confirms and sharpens Open Question #2 from the Phase 1 design doc** — the constraint isn't isolated
to one thin horizon, it's structural: the system hasn't been running long enough yet to have deep
signal/ranking history. This is a "wait for more calendar time to pass" problem, not a code bug — no
purge job was found deleting this data; it just hasn't accumulated yet.

**Consequence for scope:** Phase 2 ships now, scoped to what the data supports today (US SWING/
SHORT/LONG have the deepest history), with an enforced minimum-sample-size floor per split so a
backtest silently doesn't pretend to validate something it can't. HK and GROWTH are explicitly
flagged as data-thin rather than silently included. Revisit sample-size floors as more history
accumulates — this should get easier over time with zero further code changes.

### 1b. Gate thresholds are ALREADY overridable — easier than the Phase 1 doc assumed

`_scan_for_entries` and `_monitor_positions` both build config identically:

```python
cfg = {**_DEFAULT_CONFIG, **_STYLE_OVERRIDES.get(style, {}), **portfolio.config}
```

`portfolio.config` (a live Postgres JSON column) wins the merge — confirmed against real portfolio
rows that this override already works. **A backtest harness needs zero refactoring to vary
`min_entry_score`, `min_confidence`, `min_rr_ratio`, `max_entry_gap_pct`, etc.** — construct a
candidate config dict, pass it as `portfolio.config` for a synthetic/detached portfolio object, done.

### 1c. `min_kscore` / `min_ta_score` are NOT inside `_should_enter()` — harder than the Phase 1 doc implied

The Phase 1 design doc's §3a table lists `min_kscore`, `min_ta_score`, `min_volume_z` as gate
thresholds to tune via the harness, implicitly grouping them with `_should_enter()`'s scoring logic.
They are not there. `_should_enter()` (paper_trading_engine.py:1227-1477) only reads
`min_confidence`, `min_rr_ratio`, `max_entry_gap_pct`, `min_entry_score` — and its only I/O is one
fail-open `economic_events` query (a genuine historical lookup, safe to replay). It is otherwise pure
arithmetic on passed-in dicts — the cleanest function in the file to backtest.

`min_kscore` is checked in `_scan_for_entries`'s own candidate loop (line ~3105), BEFORE
`_should_enter()` is ever called — along with signal-staleness, GROWTH-watchlist membership,
cross-portfolio symbol locks, and multi-timeframe confluence checks. `_scan_for_entries` also makes
extensive independent `datetime.now(timezone.utc)` calls (open-position counts, drawdown lookback,
daily/weekly loss windows, cooldowns) rather than accepting one shared `as_of` — replaying it exactly
as a "point-in-time" function needs every one of these threaded through, plus live-portfolio-state
concepts (current equity, open positions, today's entry count) that don't have an obvious single
historical analogue without also replaying the WHOLE trading history bar-by-bar, not just the
threshold-decision layer.

**Consequence for scope:** Phase 2 (this document) targets `_should_enter()` only — the composite
entry-SCORE decision, parameterized by `min_entry_score` and the few thresholds it reads directly.
Testing `min_kscore`/`min_ta_score`/`min_volume_z` requires a full bar-by-bar equity-curve replay of
`_scan_for_entries` (open positions, equity, caps, cooldowns, all evolving day-over-day) — this is
real, valuable work but a distinctly larger and riskier build than parameterizing one pure function.
Proposed as **Phase 2b**, after Phase 2a (this doc) is built, trusted, and its numbers cross-checked
against real paper-trading results.

**Correction found while scoping `game_plan` reconstruction:** `game_plan` is NOT stored in
`signal.reasons` (an earlier draft of this section assumed it was) — it's derived fresh at
trade-decision time by `_build_game_plan_for_style(symbol, style, live_price, signal_reasons, atr)`
(paper_trading_engine.py:1482), a pure deterministic function of `live_price`/`atr`/style constants.
This is directly reusable, but its `atr` input is normally supplied by `_batch_compute_atr()`
(line 523), which makes a LIVE yfinance call — not replayable against historical dates as-is.
However, the actual per-symbol math (`_ewm_atr_from_ohlc`, line 492) is pure — it takes
high/low/close `pd.Series` with no I/O of its own. The harness computes its own historical ATR by
feeding `_ewm_atr_from_ohlc` the trailing 14+ bars from the `Price` table (already has high/low/close)
as of each signal's date, then calls the real `_build_game_plan_for_style()` unmodified — reusing the
production math without reusing the live-only data-fetch wrapper around it.

### 1d. decision-engine is out of scope for Phase 2 — confirmed cleanly separable

`de_mode = cfg.get("decision_engine_mode", "primary")` — default is `"primary"`, meaning DE is the
live gate for real trading today. But setting `portfolio.config["decision_engine_mode"]` to anything
else routes entirely to `_should_enter()` with zero HTTP calls (`gate_source="legacy"`). **Phase 2 can
backtest the fallback gate-threshold layer today without resolving `T232-DL-DUALSCORER-DEBT` first**
— easier than the Phase 1 doc's §5 Open Question #1 implied. This backtests "what the fallback path
would have done," which is a real and useful question on its own (it's what runs during any DE
outage), even though it isn't testing the literal DE-scored path most live trades take today. Revisit
once dual-scorer reconciliation gives one canonical scoring path to backtest instead of two.

---

## 2. Phase 2a scope (what gets built now)

**Goal:** given a candidate `_should_enter()`-relevant config (chiefly `min_entry_score`, plus
`min_confidence`/`min_rr_ratio`/`max_entry_gap_pct` if varied), replay historical BUY signals through
the real, unmodified `_should_enter()` function and report whether the candidate would have produced
a better expected value than the current production config, on a held-out validation window it never
saw during the search.

**Explicitly NOT in Phase 2a** (see §1c/1d): `min_kscore`, `min_ta_score`, `min_volume_z`, sizing
multipliers, decision-engine's scoring path, live equity-curve replay of `_scan_for_entries`. These
are real gaps, tracked as Phase 2b/2c below, not silently dropped.

### 2a.1 Module location — correction from the Phase 1 design doc's `shared/backtest/` proposal

The Phase 1 design doc proposed `shared/`. Checked for precedent: no existing `shared/` module
imports from any `services/*` package — every dependency arrow in this codebase points from services
INTO `shared/`, never the reverse. The harness needs to import `_should_enter`,
`_build_game_plan_for_style`, and `_ewm_atr_from_ohlc` directly from `paper_trading_engine.py`
(market-data-specific), so placing it under `shared/` would invert that direction for the first time
in the codebase. Since nothing outside market-data needs this harness today, it lives inside
market-data instead: **`services/market-data/src/backtest/gate_harness.py`**. Revisit moving it to
`shared/` only if a second service later needs to call it directly (unlikely — Phase 3's Promotion
Gate/orchestrator is planned to live in market-data's own scheduler per the Phase 1 doc's §3e).

### 2a.2 Module: `services/market-data/src/backtest/gate_harness.py`

```python
def replay_should_enter(
    session,
    style: str,
    market: str,
    cfg: dict,
    window_start: date,
    window_end: date,
) -> BacktestResult:
    """Replay _should_enter() against every historical BUY signal for (style, market)
    in [window_start, window_end] with `cfg` substituted in, using each signal's own
    SignalOutcome as the realized forward-return ground truth (not a synthetic exit
    simulation — see §2a.2 for why).
    """
```

Per matched `(Signal, SignalOutcome)` pair in the window:
1. Reconstruct `signal_data` (confidence, reasons dict — including `last_price`/`macro_blackout`/
   `days_to_earnings` already stored on the signal) and `game_plan` (entry1/entry2/breakout/stop/
   take_profit — stored in `signal.reasons` at generation time, confirmed present).
2. Call the REAL, unmodified `_should_enter(symbol, signal_data, live_price, game_plan, cfg,
   live_regime=None, kscore=None)` — `kscore=None` is a deliberate Phase 2a limitation (see §1c: the
   K-score gate lives in the caller, not here) — imported directly from `paper_trading_engine`, not
   reimplemented, per the Phase 1 design doc's Principle 5 (reuse the real engine, don't build a
   fourth backtest simulator).
3. If `should_enter`: record the signal's actual `SignalOutcome.pct_return`/`is_correct` (whichever
   hold-window bucket matches the style's horizon — reuse the existing `5d`/`10d`/`20d` bucket
   convention already in `SignalOutcome`) as this candidate's realized trade result.
4. If not: the signal contributes nothing to this candidate's synthetic result (correctly modeling
   "the gate would have skipped this trade").

Aggregate into `BacktestResult`: `n_trades`, `win_rate`, `avg_return_pct`, `expected_value_pct`
(= `win_rate * avg_return_pct`, matching the T232-OC4-fixed convention already used elsewhere — avg
return over ALL trades already IS the expected value; do not multiply by win_rate again, that
double-counts exactly the bug already fixed in `outcomes_calibrate_apply`/`tune_style_profiles`).

### 2a.3 Why `SignalOutcome`-based, not a synthetic exit-price simulation

`_should_enter()` decides whether to enter — it does not decide the exit (that's
`_monitor_positions`'s stop/target/trailing logic, a separate and more stateful function, out of
scope for 2a per §1c). Two ways to get a realized return for "would this trade have won":

- **(a)** Replay `_monitor_positions`'s exit logic too, bar-by-bar, to get a realistic exit price.
- **(b)** Use `SignalOutcome`'s already-computed forward return at a fixed hold horizon.

Phase 2a uses (b). This is a deliberate simplification, not an oversight: `SignalOutcome`'s forward
returns are already the ground truth the REST of the tuning infrastructure uses (`outcomes/calibrate`,
`tune_style_profiles`, both fixed this session) — reusing it keeps Phase 2a's results directly
comparable to those existing, trusted numbers, and avoids taking on `_monitor_positions`'s full
statefulness (open positions, trailing stops, scale-in/out, portfolio equity) before it's needed.
The real limitation this introduces: a candidate `min_entry_score` change can only be evaluated
against "how did this signal do over N fixed days," not "how did this signal do under the ACTUAL
sizing/exit rules the live engine would apply" — a gap that Phase 2b (full engine replay) closes.
Documented as a known simplification, not hidden.

### 2a.4 Walk-forward split + minimum sample floor

Mirrors the pattern already proven in `outcomes_calibrate_apply` (T232-OC3) and `tune_style_profiles`
(T234-SIG-INSAMPLE-GATE-TUNING, this session): sort matched signals chronologically, split
70% train / 30% validation, search candidate `min_entry_score` values on train only, report/gate on
validation only.

**New per §1a:** enforce `MIN_SAMPLES_PER_SPLIT = 15` (both train and validation must independently
clear this) — below it, return a `skipped` result with a clear reason rather than a
statistically-meaningless number. Given the current data depth (§1a), this means:

- US SWING/SHORT/LONG: likely clears the floor today.
- US GROWTH, all HK: likely skipped today, revisit as history accumulates — no code change needed
  later, just more rows to search over.

### 2a.5 Output — reuses the `tune_history` concept from the Phase 1 design, simplified for 2a

Phase 2a does NOT yet build the full `tune_history` table / Promotion Gate (Phase 3, still separately
tracked as `T233-SELFIMPROVE-PHASE3`, still `todo`) — that requires the harness to exist first and be
trusted. Phase 2a's `BacktestResult` is returned directly from a new manually-triggered endpoint
(`POST /paper/backtest/gate_threshold` on market-data, following the existing manual-trigger pattern
of `outcomes/calibrate`) for a human to read and decide on, matching Phase 1 design doc §4's explicit
sequencing (harness before gate before history table before automation).

---

## 3. Trust-building step (Phase 1 design doc §5 Open Question #1, answered)

Before trusting Phase 2a's numbers for any real decision: run it against the CURRENT production
`min_entry_score` (i.e. candidate == current config, no change) over the full available window, and
compare its reported win_rate/EV against what `GET /signals/outcomes/calibrate` already reports for
the same style/market/window using the existing, separately-verified calculation. They read the same
underlying `SignalOutcome` rows through a different code path — if they disagree materially, that's a
bug in the harness to find before trusting it for anything else, not a real finding about the gate.

---

## 4. Explicitly deferred (tracked, not forgotten)

- **Phase 2b:** full `_scan_for_entries` bar-by-bar equity-curve replay (open positions, equity,
  entry caps, cooldowns evolving day-over-day) — needed to actually test `min_kscore`/`min_ta_score`/
  `min_volume_z` and sizing multipliers, per §1c. Larger, riskier build; do after 2a is proven.
- **Phase 2c:** decision-engine-path backtesting — blocked on `T232-DL-DUALSCORER-DEBT` resolution
  (one canonical scoring path) per §1d, or build a separate DE-specific harness if that resolution is
  delayed.
- **Data retention:** no code fix identified or needed — signal/ranking history depth (§1a) should
  improve automatically as the system continues running. Worth a periodic re-check (e.g. next time
  this harness is extended) rather than a one-time fix.
