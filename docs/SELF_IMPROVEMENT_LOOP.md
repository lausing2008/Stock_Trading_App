# Self-Improvement Loop — Reference

How the system tunes its own trading parameters against real outcome data: what exists, how the
pieces connect, what's live right now, and what's still missing. This is a living reference —
update it when any piece below changes. For the original design rationale and phased rollout plan,
see `docs/DESIGN_SELF_IMPROVEMENT_LOOP_2026-07-04.md` and
`docs/DESIGN_BACKTEST_HARNESS_PHASE2_2026-07-06.md`.

---

## 1. The ground-truth tables

Everything in this loop is tuned against one of two outcome tables. Neither is a simulation —
both record what actually happened.

### `SignalOutcome` (`shared/db/models.py:446`)

One row per evaluated BUY/SELL signal. Written by the scheduler after each hold window closes.

| Column | Meaning |
|---|---|
| `signal_id`, `stock_id`, `symbol`, `horizon`, `signal_direction`, `signal_date` | Identity |
| `confidence`, `fused_prob`, `ta_score`, `ml_prob`, `ml_auc`, `market_regime` | State at signal time |
| `entry_price`, `entry_date`, `exit_price`, `exit_date`, `hold_days`, `pct_return`, `is_correct` | Legacy single-window outcome |
| `price_5d`/`return_5d`/`is_correct_5d`, `_10d`, `_20d` | Multi-window forward returns (INT-8) — filled independently as each window closes |

This is the table `outcomes_calibrate`, `outcomes_calibrate_apply`, `tune_style_profiles`, and the
new gate-threshold backtest harness all read from. **Convention, fixed this session (T232-OC4):**
`avg_return_pct` across all trades in a bucket already IS the expected value — never multiply it by
`win_rate` again, that double-counts win probability. Every tuning function listed below follows
this convention now.

### `PaperTrade` (`shared/db/models.py:584`)

One row per real (paper) trade — entry, exits, scale-in/out history, realized P&L. This is the
second ground truth: it captures gate AND sizing AND exit-rule effects that `SignalOutcome` alone
can't (a good signal can still lose money on bad sizing or a bad exit). Used by RL training
(`rl_agent.py`) and entry-factor calibration (`calibrate_entry_weights`), not yet by the gate
backtest harness (see §4).

---

## 2. What tunes what — the six live mechanisms

| # | Mechanism | Endpoint | Optimizes | Train/validation split? | Writes to |
|---|---|---|---|---|---|
| 1 | Signal threshold calibration | `POST /signals/outcomes/calibrate/apply` | Real expected value (win_rate × avg_return, correctly *not* double-multiplied) | **Yes** — chronological 70/30 (T232-OC3) | Redis `stockai:signal_thresholds:{HORIZON}` and `:SELL:{HORIZON}` |
| 2 | Style gate-parameter tuning | `POST /signals/tune_style_profiles` | Same EV metric, three params | **Yes** — chronological 70/30 (T234-SIG-INSAMPLE-GATE-TUNING) | Redis `stockai:style_tune:{STYLE}:{param}` |
| 3 | ML fusion weight | `POST /signals/calibrate_ml_weight` | Calibration-set accuracy for selection, gated by validation-slice EV (fixed 2026-07-07, T234-ML-WEIGHT-NO-VALIDATION-GATE) | **Yes** — chronological 70/30, min 15 validation samples, only applies if the candidate beats a 0.5 baseline on validation EV | `{model_dir}/ml_weight_override.json` (disk, not Redis) |
| 4 | ML hyperparameters | `POST /ml/tune_all` | **AUC** via Optuna + `TimeSeriesSplit(n_splits=5)` — a classification proxy, not P&L | Yes, for AUC only | Per-symbol `{symbol}_params.json` |
| 5 | Gate-threshold backtest (research only) | `GET /paper-portfolio/backtest/min-entry-score` | Real expected value via `SignalOutcome` | **Yes** — chronological 70/30, min 15 samples/side | Nothing — returns a report, does not touch Redis or config |
| 6 | Gate-logic drift check (research only) | `GET /signals/gate_backtest` | N/A — compares old vs. new gate logic on historical signals | No | Nothing — 1-hour cache only |

**Read side — who consumes what, and priority order:**

- `_get_dynamic_buy_threshold()` (`signals.py`): `stockai:watchdog:{STYLE}:threshold` → falls back to
  `stockai:signal_thresholds:{STYLE}` → falls back to hardcoded `_STYLE_PROFILES[STYLE]["buy_threshold"]`.
  SELL reads `stockai:signal_thresholds:SELL:{STYLE}`.
- `_get_style_tuned_param()` (`signals.py`): reads `stockai:style_tune:{STYLE}:{param}` for
  `ml_weight_cap`, `adx_min`, `high_vol_compression`, `breadth_compression` — falls back to
  hardcoded `_STYLE_PROFILES` values when the Redis key is absent or expired.
- Both fallback chains mean a Redis TTL expiring (30 days on every write above) silently reverts
  the live signal generator to hardcoded defaults — not a bug, but worth knowing when reading
  live behavior: **"what's currently live" always requires checking Redis, not just the code.**

---

## 3. The weekly cycle — what actually runs, in order

Scheduled Sunday **14:00 America/Los_Angeles** (`scheduler.py:3336`; the function's own docstring
says "16:00 PST" — that comment is stale, the `CronTrigger` itself is the source of truth). Chosen
to land ~19 hours before HK Monday open so both markets start the week with fresh data.

`_weekly_full_refresh()` (`scheduler.py:1897`) runs, in this exact order:

1. Force re-ingest 3 years of daily bars, all active US+HK symbols (`ingest_universe(..., force=True)`)
2. Rankings refresh — US, then HK
3. Signals refresh — US, then HK (split by market to isolate failures / avoid OOM)
4. Fundamentals batch refresh (must run after price ingest; ~46s for 138 symbols)
5. `POST /ml/tune_all` — fire-and-forget, runs 2-4h in the background in ml-prediction
6. `POST /signals/calibrate_ta_weights` — fire-and-forget, no ordering dependency with step 5
7. `POST /signals/calibrate_conviction_weights`
8. `POST /signals/outcomes/calibrate/apply` — writes `stockai:signal_thresholds:*`
9. `POST /signals/tune_style_profiles` — writes `stockai:style_tune:*`
10. `calibrate_entry_weights()` — called in-process, not HTTP (the service token has no DB user record)
11. `run_rl_training()` — needs ≥50 closed paper trades, saves `rl_policy.json`

Steps 5-9 are all "fire and forget" — the weekly job doesn't wait for `tune_all` to finish before
kicking off the others; they're independent HTTP calls with no ordering dependency on each other.

**Staleness safety net** (`scheduler.py:438-451`): a separate, more frequent job checks
`scheduler:job:tune_all_sent` in Redis — if `tune_all` hasn't successfully run in >21 days, it's
triggered immediately rather than waiting for the next Sunday. Guards against a silent multi-week
gap if the weekly job itself fails.

**Other related scheduled jobs:**

| Job | Schedule | Purpose |
|---|---|---|
| `signal_watchdog_daily` | Mon-Fri 06:10 America/New_York | writes the highest-priority `stockai:watchdog:*` threshold override |
| `fundamentals_snapshot_weekly` | Sun 16:30 America/New_York | populates `FundamentalsSnapshot` (T234-ML-FUND-BROADCAST-LEAKAGE's point-in-time source — see §6) |
| `meta_model_monthly_retrain` | first Sunday, 03:00 UTC | meta-model retrain, independent of the weekly Optuna tune |
| `data_quality_checks` | every 2 hours, all days | checks actual data freshness (not job-run status) — the safety net any tuning loop needs to trust its own inputs |

---

## 4. The Backtest Harness (Phase 2a) — the newest piece

`services/market-data/src/backtest/gate_harness.py` — added this session (T233-SELFIMPROVE-PHASE2).
Full design/scoping rationale in `docs/DESIGN_BACKTEST_HARNESS_PHASE2_2026-07-06.md`; summary here.

**What it does:** replays the REAL, unmodified `_should_enter()` (the paper-trading engine's
composite entry-score gate) against historical BUY signals, with a candidate config substituted in,
to answer "would a different `min_entry_score` have produced a better expected value?"

```
GET /paper-portfolio/backtest/min-entry-score?style=SWING&market=US&window_days=60
```
Admin-only (`get_admin_user` — stricter than the signal-engine calibration endpoints, which only
require a logged-in user). Read-only research tool: **no config write, no Redis write, no promotion
gate.** A human reads the JSON response and decides whether to hand-edit `portfolio.config`.

**Mechanics:**
1. Fetch every `(Signal, SignalOutcome)` pair for the style/market/window where the outcome's hold
   bucket (`5d`/`10d`/`20d` per style — see `_HORIZON_BUCKET`) has resolved.
2. For each: reconstruct `game_plan` via the real `_build_game_plan_for_style()`, using a
   **historical** ATR computed from `Price` rows strictly before the signal date (feeds the real
   `_ewm_atr_from_ohlc` math — not the live-only `_batch_compute_atr` yfinance wrapper).
3. Call the real `_should_enter()` unmodified. If it says yes, record that signal's actual
   `SignalOutcome.return_{bucket}`/`is_correct_{bucket}` as the realized result — **not** a
   synthetic exit-price simulation (see design doc §2a.2 for why).
4. Chronological 70/30 split (`MIN_SAMPLES_PER_SPLIT = 15` per side, matching the pattern already
   proven in items #1/#2 above): search candidate `min_entry_score` values on the train slice only,
   only report `promoted: true` if the winning candidate ALSO beats the current baseline on the
   validation slice — data the search never saw.

**Deliberately out of scope for this phase** (see design doc §1c/§1d for the "why"):

- `min_kscore` / `min_ta_score` / `min_volume_z` — these are checked in `_scan_for_entries`'s own
  candidate loop, BEFORE `_should_enter()` is ever called, not inside the function this harness
  replays. Testing them needs a full bar-by-bar equity-curve replay (open positions, running
  equity, entry caps, cooldowns) — a larger, riskier build, proposed as **Phase 2b**.
- Sizing multipliers — mostly hardcoded literals inside function bodies, not `cfg.get(...)` lookups;
  would need an actual refactor before a harness could vary them.
- Decision-engine's scoring path — `_should_enter()` is only the **fallback** gate (live when
  `decision_engine_mode != "primary"`, or when DE is unreachable). The primary DE-scored path most
  live trades take today isn't what this harness tests yet — proposed as **Phase 2c**, blocked on
  reconciling `T232-DL-DUALSCORER-DEBT` (34 known differences between the two scoring paths) or
  building a DE-specific harness.

**Trust-building cross-check (run against production, 2026-07-06):** compared the harness's SWING
findings against the independently-computed `outcomes/calibrate` endpoint for the same window. Both
tools, reading the same `SignalOutcome` rows through completely different code paths, pointed at the
same underlying reality — SWING's recent BUY signals trending negative expected value. Not an exact
numeric match (they tune different parameters — `min_entry_score` vs. `fused_prob` threshold), but
qualitatively consistent, which is the intended sanity check before trusting either tool further.

---

## 5. Known gaps — read this before trusting any single number blindly

1. **Data depth is thin, structurally, not from a bug.** As of 2026-07-06, production's `signals`
   table spans ~35 distinct days; `rankings` ~44. Per-style/market `SignalOutcome` rows with a
   resolved `is_correct_10d`: SHORT ~120, SWING ~115, LONG ~77, GROWTH ~94. LONG and GROWTH
   currently fall BELOW `outcomes_calibrate_apply`'s own 100-sample floor (`min_samples=50 × 2`)
   and get silently skipped every week. No purge job is deleting this data — it simply hasn't
   accumulated yet. This should self-resolve as the system keeps running; no code fix identified.

2. **FIXED 2026-07-07 (`T234-ML-WEIGHT-NO-VALIDATION-GATE`) — `calibrate_ml_weight` used to pick its
   fusion weight from the training slice, not validation.** Originally found while writing this doc
   (correcting an over-credit in T232-OC3's tracker entry, which had described this endpoint as
   already doing a walk-forward split "correctly" — the split existed but didn't gate anything: the
   weight selection loop maximized CALIBRATION-side accuracy, and validation-side accuracy was
   computed only for display, never used to decide whether to apply). Also fixed in the same pass:
   `exit_p` previously used whatever the MOST RECENT close happened to be, mixing holding periods
   from days to ~180 days into one sweep — now uses each signal's own style-specific fixed hold
   window (`_OUTCOME_HOLD_DAYS`, same convention as items #1/#2 in §2). The endpoint now only applies
   a candidate weight if it beats a neutral 0.5 baseline on validation-slice EV, with the same
   15-sample floor used elsewhere in this loop. §2's table above reflects the fixed behavior.

3. **ML hyperparameter tuning optimizes AUC, not P&L.** A model can have excellent AUC and mediocre
   trading expected value if it's confident-but-wrong specifically on the highest-conviction trades.
   The original design (§3a of `DESIGN_SELF_IMPROVEMENT_LOOP_2026-07-04.md`) proposes adding the
   backtest harness as a *secondary* gate after Optuna's AUC search, not replacing it — not yet
   built (Phase 4, explicitly sequenced after Phases 1-3 are proven).

4. **Redis TTLs mean "live behavior" always requires checking Redis, not just reading code or this
   doc.** Every write in §2 has a 30-day TTL. If a key expires and the next scheduled write fails
   silently, the signal generator reverts to hardcoded `_STYLE_PROFILES` defaults with no alert.
   `GET /signals/tune_status` (referenced in the codebase's own priority-order comments) is the
   place to check current live values, not this document — treat any numbers written here as a
   point-in-time snapshot (2026-07-06), not a live source of truth.

5. **FIXED 2026-07-05 (`T233-SELFIMPROVE-PHASE3`) — Promotion Gate + `tune_history` table now
   exist, scoped to the `min_entry_score` mechanism only.** See
   `docs/DESIGN_PROMOTION_GATE_PHASE3_2026-07-05.md` for the full scoping (it corrects the original
   4-rule design — rules 1-2 were already computed by the Phase 2a harness; rule 3 needed a
   clearly-labeled APPROXIMATE "worst single trade" check instead of a true portfolio drawdown,
   since that needs Phase 2b's equity-curve replay; rule 4 isn't checkable at all until a
   `PaperTrade`-based backtest exists — every row explicitly records this as
   `not_yet_available` rather than silently omitting it). `POST /paper-portfolio/backtest/
   min-entry-score/promote` runs the check and writes exactly one `tune_history` row per call,
   promoted or not; `GET /paper-portfolio/tune-history` browses it. Still manually triggered, does
   NOT write to `portfolio.config` — a human still decides. **The other 5 mechanisms in §2's table
   do not write to `tune_history` yet** — each still only has its own internal walk-forward gate
   (fixed earlier this session), with no shared record of "what changed and did it help" across
   mechanisms. Extending the table to cover them is a natural follow-up, not yet scoped.

6. **Signal-level (`SignalOutcome`) and trade-level (`PaperTrade`) tuning are not yet reconciled.**
   A good signal can still lose money on bad sizing or a bad exit rule — `SignalOutcome`-based
   tuning (items #1, #2, #5 in §2) is blind to this; `PaperTrade`-based tuning (RL training, entry
   factor calibration) captures it but isn't yet wired into the same walk-forward/promotion
   discipline. Phase 2b's full engine replay is the piece that would let a candidate be validated
   against both ground truths simultaneously (the original design's Promotion Gate rule #4).

---

## 6. Related fixes this session that hardened the ground truth itself

The six mechanisms in §2 are only as trustworthy as the data they read. These fixes (same session)
targeted `SignalOutcome`/training-feature integrity directly, not the tuning logic:

- **T234-ML-FUND-BROADCAST-LEAKAGE**: `FundamentalsSnapshot` extended from 5 to 15 data fields
  (`gross_margin`, `fcf_yield`, `short_ratio`, `short_ratio_delta`, `short_percent_of_float`,
  `price_to_book`, `peg_ratio`, `debt_to_equity`, `ddm_discount`, `piotroski_score` added) so ML
  training's point-in-time join can eventually cover all fundamentals features, not just the
  original 4 — previously 8-9 fundamentals columns were broadcasting today's value across every
  historical training row (lookahead bias). History for the new fields accumulates going forward
  only; existing rows correctly resolve to NaN rather than a leaky broadcast value until enough
  weekly snapshots exist. Along the way, found and fixed a separate bug where the weekly snapshot
  job could pick an arbitrary historical `fundamentals` row instead of the latest one.
- **T234-ML-TUNER-MISSING-PIT**: `tuner.py`'s Optuna sweep (item #4 in §2) was never passing
  `fund_snapshots` into feature building — even the 4 originally-protected PIT columns were
  broadcast-leaky specifically on the tuning path. Now wired to match `trainer.py`.
- **T234-PT-SCALEIN-COST-BASIS-BUG**: scale-in trades were overstating `PaperTrade.pct_return`,
  which flows directly into the `SignalOutcome` calibration writeback — a scale-in trade was
  quietly making the calibration data look better than the strategy actually performed. Fixed for
  new trades; 2 pre-existing open positions (UPST, IMVT) left uncorrected per an explicit decision
  to avoid hand-editing live financial data (see the tracker entry for the full reasoning).
- **T234-SIG-INSAMPLE-GATE-TUNING**, **T232-OC3**: added the walk-forward split to items #2 and #1
  respectively — see §2's table.
