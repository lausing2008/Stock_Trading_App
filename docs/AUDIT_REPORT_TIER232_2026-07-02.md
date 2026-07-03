# Deep System Audit — Tier 232 (2026-07-02)

**Scope:** signal engine (`signals.py`, calibration in `routes.py`), ML prediction (`builder.py`,
`trainer.py`, `tuner.py`, `meta_trainer.py`, `hmm_regime.py`), decision engine (`scorer.py`,
`sizer.py`, `hard_rejects.py`, `regime.py`, `aggregator.py`), paper trading engine, ranking
engine (`kscore.py`), technical-analysis core, and the outcome-tracking / calibration feedback loop.

**Method:** 6 parallel deep-read audits + live database interrogation (local dev, then
production EC2 after the local DB proved stale). All 4 critical findings (CAL-1, CAL-2, CAL-3,
ML-1) were verified by direct source read and **fixed and deployed to production on 2026-07-02**
— see Part 2 for per-finding fix/deploy notes. The corrupted `stockai:signal_thresholds:SWING`
Redis key found active in production was deleted and a full signal refresh verified clean before
the code fix shipped.

---

## Part 1 — What the Live Data Says (PRODUCTION EC2, measured 2026-07-02)

> Initial queries against the local docker DB turned out to be a **stale dev copy** (data ends
> 2026-06-12, zero paper trades). All numbers below are from **production** (18.205.121.71),
> queried the same day. Production has 1,308 scored outcomes (2026-05-25 → 2026-06-24, evaluation
> job current — later signal_dates are still maturing).

### ⚠️ CAL-1 IS ACTIVE IN PRODUCTION

`stockai:signal_thresholds:SWING = 0.62` exists in production Redis (TTL ≈ 26 days remaining →
set ~2026-06-28, the weekly Sunday calibrate/apply). That confidence-scale 62 (≡ fused 0.81) is
being applied as **fused > 0.62** in all regimes — 0.10 below the vetted bull threshold (0.72),
0.14 below bear (0.76). Measured impact: **150 of 373 SWING BUY signals in the last 7 days (40%)
fired below the vetted threshold** (TSLA 0.672, MDB 0.615, SMH 0.625 among 2026-07-02's).
Earlier Sunday applies may have corrupted thresholds for weeks.

**✅ MITIGATION APPLIED 2026-07-02 ~13:45 UTC (user-approved):** the key was deleted from
production Redis (no other `signal_thresholds`/`watchdog` keys existed) and a full US+HK signal
refresh was triggered (112 + 41 symbols). Verified clean: every SWING BUY written after
13:50 UTC has fused ≥ 0.723; symbols that had been BUY at 0.59–0.70 (BE, INTC, RTX, KGS…)
re-graded to HOLD. **The code fix (unit conversion + per-regime keys + reader clamp) is still
required before the next weekly Sunday apply re-writes a corrupted key** — either ship the fix
or disable the `outcomes/calibrate/apply` scheduler job this week. BUY email alerts sent since
~2026-06-28 for SWING signals may have been based on the loosened threshold.

### Win rate by direction (all scored outcomes)

| Direction | n | Win rate | Avg return |
|---|---|---|---|
| BUY | 534 | **44.6%** | **−1.19%** |
| SELL | 774 | 52.5% | +0.19% (i.e. price fell after SELL slightly more often than not) |

**Production BUY signals are net losing money.** (The local dev copy showed the opposite —
its 8-day sample was a bull window; production's 4-week sample is the real base rate.)

### BUY win rate by horizon × market

| Horizon | Market | n | Win rate |
|---|---|---|---|
| SHORT | US | 227 | 53.3% ← best BUY cohort |
| SHORT | HK | 71 | 42.3% |
| SWING | US | 139 | **38.1%** |
| SWING | HK | 53 | **28.3%** ← worst BUY cohort |
| GROWTH | US | 23 | 52.2% |
| GROWTH | HK | 17 | 29.4% |
| LONG | HK | 4 | 50.0% |

**SWING BUY — the horizon whose threshold is corrupted by CAL-1 — is the worst-performing
BUY cohort in both markets.** These outcomes cover signals through 2026-06-17; if earlier
weekly applies also wrote miscalibrated keys, part of this underperformance IS the CAL-1 bug.

### SELL win rate by horizon × market

| Horizon | Market | n | Win rate |
|---|---|---|---|
| SHORT | US | 222 | 43.7% |
| SHORT | HK | 119 | **68.1%** |
| SWING | US | 220 | 46.4% |
| SWING | HK | 87 | 66.7% |
| GROWTH | US | 82 | 47.6% |
| GROWTH | HK | 40 | 62.5% |

**HK SELLs are genuinely good (62–68%); US SELLs hover below 50%.** The direction-blind
suppression findings (SIG-3/SIG-5) mean the healthy HK SELL cohort is being muted in exactly
the regimes that confirm it.

### Fixed-window win rates (scored rows only)

| Direction | 5d | 10d | 20d |
|---|---|---|---|
| BUY | 45.1% (n=534) | 42.3% (n=381) | 47.3% (n=186) |
| SELL | 59.8% (n=492) | 60.1% (n=404) | 48.4% (n=289) |

### Paper trading P&L (production — the engine IS live there)

| Portfolio | Closed | Open | Win rate | Closed P&L |
|---|---|---|---|---|
| GROWTH Paper Portfolio | 9 | 7 | 11.1% | −$574 |
| US SWING Portfolio | 14 | 7 | 35.7% | −$452 |
| HK SWING Portfolio | 4 | 0 | 0% | −$6,611 |
| HK GROWTH Portfolio | 5 | 0 | 0% | −$4,153 |
| ETrade Sandbox SWING | 0 | 1 | — | — |

**All portfolios are losing; aggregate closed P&L ≈ −$11,800.** The HK portfolios lost the
most per trade (avg −$1,300+/trade) despite HK SELL signals being the system's most accurate
cohort — consistent with the engine buying into the cohort (HK SWING/GROWTH BUY: 28–29% win
rate) that the signal data says is worst.

### Structural data gaps (production)

- **DATA-1 (revised):** Production outcome evaluation is current (ts_evaluated = 2026-07-02).
  The local docker DB is a stale dev copy — earlier "tracking stopped" alarm applies to dev only.
- **DATA-3 (HIGH, confirmed in prod):** LONG horizon has 8 outcome rows total, none since
  2026-06-03 — effectively untracked.
- **DATA-5 (NEW):** Local dev and production have materially diverged (schema-identical but
  data 3 weeks apart, different portfolio sets). Any local analysis of win rates is misleading —
  add a periodic prod→dev sync or always interrogate production.

---

## Part 2 — Critical Findings (fix before the next calibration cycle)

### CAL-1 — Calibration loop writes confidence-scale values into fused-probability thresholds ✅ FIXED & DEPLOYED (2026-07-02)
**Files:** `services/signal-engine/src/api/routes.py:3340,3406` → consumed at
`services/signal-engine/src/generators/signals.py:1434-1442`

Confidence is `abs(fused − 0.5) × 200` (signals.py:1970) — a 0–100 distance from neutral.
The weekly `outcomes/calibrate/apply` sweeps confidence values 40–85, then writes
`best_t / 100` to `stockai:signal_thresholds:{STYLE}`. But `_decide_style` compares that number
against **fused probability**. Confidence 40 ≡ fused 0.70; writing `0.40` means `fused > 0.40`
fires BUY — ~30 probability points looser than the vetted 0.72 SWING bull threshold. The SELL
key (`:SELL:{h}`) has the same unit bug.

**Status right now:** Redis has **no** `stockai:signal_thresholds:*` or `stockai:watchdog:*`
keys locally, so the corruption is latent, not active. Check production Redis too:
`docker exec stockai-redis-1 redis-cli --scan --pattern 'stockai:signal_thresholds:*'` — delete
any keys found until the fix ships.

**Fix (shipped):** `GET /outcomes/calibrate` and `POST /outcomes/calibrate/apply` now sweep
`SignalOutcome.fused_prob` directly (0.55–0.85 for BUY, 0.15–0.40 for SELL) and write the
fused-scale value — the same scale `_decide_style` reads. `min_samples` default raised 15→50;
`ev_lift` no longer assumes an unmeasurable baseline is 0.0 (skips instead, closing OC-3's
overstated-lift issue). Deployed to `stockai-signal-engine-1` via `docker cp` + restart;
verified live: `GET /signals/outcomes/calibrate` now returns `current_threshold: 0.72` for
SWING (previously would have shown a 0–100 value).

### CAL-2 — Dynamic threshold override ignores regime; watchdog floor is stale ✅ FIXED & DEPLOYED (2026-07-02)
**File:** `signals.py:1389-1400`

`_get_dynamic_buy_threshold(style_key, reg)` never uses `reg` — one Redis value overrides all
four regime-tiered thresholds (SWING bull 0.72 / bear 0.76), silently nullifying SA-32's
bear-market protection whenever any calibration or watchdog key exists. The watchdog's
`_DEFAULT_THRESHOLDS` (routes.py:3583, SWING 0.67) is also stale vs `_STYLE_PROFILES` (0.72),
so a watchdog "relax" pins SWING below the bull threshold in any regime.

**Fix (shipped):** `_get_dynamic_buy_threshold` now applies the calibrated/watchdog value as a
delta from the bull baseline, added per-regime to that regime's hardcoded threshold —
preserving SA-32's bear/high_vol tiering instead of overriding it flat. Added a `[0.55, 0.85]`
sanity clamp (SELL: `[0.15, 0.45]`) that rejects corrupted/wrong-scale Redis values and falls
back to the hardcoded profile. All three drifted hardcoded threshold tables (`GET
/outcomes/calibrate`, `POST /outcomes/calibrate/apply`, `POST /watchdog`) now import bull
thresholds from `_STYLE_PROFILES` directly. Deployed to `stockai-signal-engine-1`.

### ML-1 — Point-in-time fundamentals join produces 1970 epoch dates → all-NaN features ✅ FIXED & DEPLOYED (2026-07-02)
**File:** `services/ml-prediction/src/features/builder.py:788`

`out = pd.DataFrame(index=df.index)` (line 585) carries a **RangeIndex** (0..n−1).
`pd.to_datetime(out.index)` interprets those integers as *nanoseconds since 1970*, so every
"price date" becomes 1970-01-01 and the backward `merge_asof` against real 2025–2026 snapshot
dates matches nothing. All 4 `_PIT_COLS` (`revenue_growth`, `earnings_growth`,
`return_on_equity`, `recommendation_mean`) are silently overwritten with NaN for the entire
training set; the `except Exception: pass` never fires; nothing is logged. The T228
point-in-time fix is completely inert, and 4 features that carry real values at inference are
dead (all-NaN) at training.

**Fix (shipped):** uses the existing `dates` Series (already computed from `df["ts"]` for the
macro/sector/outcome joins) instead of `out.index`. Added a post-merge check that logs
`builder.pit_join_all_nan` if the join ever produces no real values, and
`builder.pit_join_failed` on exception (previously silent). Verified with a standalone
`merge_asof` reproduction: old code produced `1970-01-01` for every row; new code produces
correct dates with PIT values transitioning exactly on each snapshot date. Deployed to
`stockai-ml-prediction-1` via `docker cp` + restart. **Retraining is still required** to pick
up the now-live PIT features in production models — schedule the next `tune_all`/retrain batch.

### CAL-3 — SELL calibration is inverted AND rewards adverse moves ✅ FIXED & DEPLOYED (2026-07-02)
**File:** `routes.py:3374-3406`

Three compounding bugs: (a) `o.confidence <= t_int` selects the **weakest** SELLs (for SELL,
high confidence = strong conviction; the comment has it backwards); (b) EV uses
`abs(o.pct_return)` — a SELL where the stock **rallied +10%** (a maximal miss) contributes +10%
to "average return", so EV rewards volatility, not correctness; (c) same unit mismatch as CAL-1.

**Fix (shipped):** sweep now uses `fused_prob <= t` (mirroring the BUY sweep's direction,
t ∈ [0.15, 0.40]) and signed SELL profit (`−pct_return`, so a rally after a SELL correctly
counts against EV). Writes fused-scale values with a `[0.15, 0.45]` bounds check. Deployed
alongside CAL-1.

### PT-1 (revised) — Paper trading disabled in local dev; production runs it — and loses
**File:** `shared/common/config.py:71` + production P&L

`enable_paper_trading` defaults False and `ENABLE_PAPER_TRADING` is absent from every tracked
env file — the local engine has never run (three untouched $50k portfolios). Production's EC2
`.env` **does** set it: 47 trades across 5 portfolios. But every portfolio is net negative
(aggregate ≈ −$11,800 closed P&L, win rates 0–36%) — see Part 1. The PT-2…PT-12 findings
(gates, sizing stack, pnl accounting) are therefore live production behavior, not theoretical.
Add the flag to `.env.example` and a startup log line so the dev/prod divergence is visible.

---

## Part 3 — Findings by Subsystem

### Signal Engine (signals.py + calibration plumbing)

| ID | Sev | Location | Finding |
|---|---|---|---|
| SIG-1 | CRIT | routes.py:3306-3340 | = CAL-1 above ✅ FIXED |
| SIG-2 | CRIT | signals.py:1389-1400 | = CAL-2 above ✅ FIXED |
| SIG-3 ✅ | HIGH | signals.py:1551-1571 | **Pillar gate is direction-blind and erases the clearest SELLs.** `independent_pillars_active` counts *bullish* evidence; a deeply bearish stock has 0–1 bullish pillars by definition, so the <2-pillar compression (×0.85/×0.70 toward 0.5) pulls a fused-0.30 SELL up to ~0.36 → flips to WAIT. Apply the gate only when `fused > 0.5` (same pattern as the line-1738 sector fix). |
| SIG-4 | HIGH | routes.py:3374-3406 | = CAL-3 above ✅ FIXED |
| SIG-5 ✅ | HIGH | signals.py:1613-1630, 1933-1938 | **Macro compressions mute SELLs the macro data confirms.** High-vol regime, breadth<40%, and the HSI-bear gate all compress `fused` toward 0.5 regardless of direction — so in bear/high-vol/thin-breadth conditions (exactly when SELLs are most correct) SELL signals are weakened. Add `and fused > 0.5` to those three paths (ADX chop compression is legitimately bidirectional — leave it). |
| SIG-6 | HIGH | routes.py:2145-2153, signals.py:247-248 | **calibrate_ta_weights never takes effect until container restart.** The endpoint writes to file+Redis but module globals `_ta_weights`/`_ta_weights_calibrated` load once at import. Weekly calibration is a no-op for the running process. Refresh the module globals at the end of the endpoint, or re-read Redis with a short-TTL cache in `_ta_score`. |
| SIG-7 | MED | signals.py:1139 | "Calibrated blend" branch always active: `ta_weights is not None` is always true (callers always pass the dict), so the STY-001 15% flag-blend runs even uncalibrated, double-counting correlated flags the pillar design de-correlated. Guard on `_ta_weights_calibrated` only. |
| SIG-8 | MED | routes.py:362-368 | Catalyst call sends 0–1 `ta_score` where 0–100 expected (default is `50.0`) — event-intelligence composite gets an effectively-zero technical component. Multiply by 100. |
| SIG-9 | MED | routes.py:412-426 | Catalyst-nudge re-grade uses `min()` of all regime thresholds (most lenient) regardless of live regime, references a nonexistent `"sell_threshold"` profile key, and doesn't recompute confidence after mutating `bullish_probability`. Re-run `_decide_style(new_bp, horizon, regime)` instead. |
| SIG-10 | MED | signals.py:1438 vs 1203-1277 | **Structural BUY/SELL asymmetry explains the 43.7% SELL win rate.** BUY needs fused >0.72 (0.22 from neutral, regime-tiered, pillar-gated); SELL needs <0.35 (0.15 from neutral, no regime tiers, no pillar/evidence gate) — and bullish nudges (+0.05 breakout, +0.04 options, +0.07 pullback, +0.08 kscore…) outnumber bearish ones. SELLs are cheap to trigger on weak evidence AND suppressed when evidence is strong (SIG-3/5). Regime-tier the sell threshold (bull 0.32 / bear 0.38) and require ≥2 bearish pillars. |
| SIG-11 | LOW | signals.py:333, routes.py:3326 | Missing ML AUC defaults to 0.55 (grants full 0.20 base weight to unknown-quality models); calibrate/apply assumes EV 0.0 when baseline has <min_samples, overstating ev_lift. Default AUC 0.52; skip apply when baseline is unmeasurable. |
| SIG-12 | LOW | routes.py (3 tables) | ✅ FIXED — all three hardcoded "current thresholds" copies now import bull thresholds from `_STYLE_PROFILES` directly instead of drifting independently. |

### ML Prediction (builder / trainer / tuner / meta / HMM)

| ID | Sev | Location | Finding |
|---|---|---|---|
| ML-1 | CRIT | builder.py:788 | = ML-1 above (epoch-date PIT join) ✅ FIXED |
| ML-2 | HIGH | trainer.py:746-780 | **BUY threshold optimized on the test set, metrics reported on the same test set.** `_precision_threshold` scans the test-set PR curve (~30–60 rows) for the lowest threshold hitting the precision floor — textbook threshold overfitting. Reported precision is systematically inflated; live win rate will undershoot the floor. Select the threshold on the calibration slice; keep the test set untouched. **This is the most likely explanation for "model says 73%, live trades win less."** |
| ML-3 | HIGH | trainer.py:591-593 | **T229-C2 overlap-drop is dead code** — `X.index` (RangeIndex ints) intersected with `X_out.index` (Timestamps) is always empty, so outcome rows from the calibration/test windows leak into the final fit at 2× weight. Convert both to dates; drop outcome rows dated at/after the train split. |
| ML-4 | HIGH | trainer.py:619-666, tuner.py:146-160 | **No purge/embargo anywhere.** Labels are 5–20-day forward returns but `TimeSeriesSplit` and the 70/80/90 splits have zero gap — the last H training bars share label information with validation/test. CV AUC is inflated, and it drives the `oos_suppressed` gate, ensemble weights, and the entire Optuna objective. Use `TimeSeriesSplit(gap=horizon)` and purge `horizon` bars at each split boundary. |
| ML-5 | MED-HIGH | tuner.py:137-171 | **Optuna optimizes mean CV AUC, but the system trades only the extreme right tail** (prob > threshold with precision floors 0.53–0.78). AUC is nearly insensitive to tail precision. Optimize precision@(recall≥5%) on purged folds; AUC as tiebreaker. |
| ML-6 | MED | builder.py:773-777 | CRIT-3/4 incomplete: 12 of 16 fundamental columns are still broadcast from today's snapshot (price_to_book, fcf_yield, ddm_discount, piotroski…). In per-symbol models they're dead constants (XGBoost can't split) rather than active leakage — but they become genuine lookahead in any cross-symbol use, and feature_importance misrepresents them. Extend PIT joins (after ML-1) or drop them from per-symbol FEATURE_COLUMNS. |
| ML-7 | MED | hmm_regime.py:71-102 | **HMM: no convergence check, unscaled features, VIX-dominated states, no stale-model fallback.** (1) `model.fit(X)` never checks `monitor_.converged`. (2) Features are unscaled (VIX ~15–80 vs returns ±0.05) so likelihood is VIX-dominated; state names assigned by ascending mean VIX — a low-VIX grinding downtrend labels "bull", and middle states can swap semantics each weekly refit → the paper-trading bear overlay can flap across retrains. (3) If the weekly refit throws (yfinance outage), `predict_current` returns an error instead of falling back to the week-old pickle. Standardize X, check convergence, label states by composite (SPY return sign + VIX rank), fall back to the existing model file. |
| ML-8 | MED | trainer.py:474, builder.py:740-742 | Outcome-augmentation rows rebuilt with `macro_df=None` → all 11 macro columns zero-filled (VIX=0 never occurs naturally) and vstacked at 2× weight next to real rows — the model can learn "impossible macro state → outcome label". Pass the real macro/sector frames or NaN the columns. |
| ML-9 | MED | meta_trainer.py:133-266, trainer.py:1117-1124 | Meta model: split is by symbol-block, not chronological (regime info leaks across symbols); gating AUC measured on its own early-stopping set; and at inference `_ta_score = float(prob)` (ensemble prob) where training used the signal engine's real `ta_score` — train/serve feature mismatch. |
| ML-10 | MED | builder.py:344-368, 740-742 | Macro zero-fill + shared Redis macro cache ignores requested date range: a 400-day cache can serve a 5-year training run, zero-filling 80% of history when yfinance rate-limits — garbage models still publish. Key cache by date-range bucket; fail training if macro coverage <90%. |
| ML-11 | LOW-MED | trainer.py:726-732 | Platt calibration with `C=1e6` (unregularized) on ~30–60 rows — the calibrated probabilities behind T223's UI win rate carry huge variance. Use C=1.0 and pool out-of-fold predictions. |
| ML-12 | LOW | trainer.py:1028-1031, 1145 | Ensemble weights use raw AUC ratio (a 0.50 coin-flip model still gets ~47% of a 0.60 model's weight) — weight by `max(auc−0.5, 0)`. Agreement-nudge gate silently disabled if any model's metrics lack `auc` keys (contributes 0.0 to min). Docstring says 40/35/25; code uses 0.30/0.45/0.25. |

### Decision Engine

| ID | Sev | Location | Finding |
|---|---|---|---|
| DE-1 | HIGH | sizer.py:107-112 | **VIX double-counted; composition diverges from paper engine.** Sizer multiplies all 7 multipliers; paper engine composes regime/breadth/VIX via `min()`. VIX≥30 sets risk_off (0.50×) AND vix_size_mult 0.67× → 0.335 combined vs paper's 0.50. Worst realistic case: 0.50×0.60×0.50×0.60×0.50×1.25 ≈ **0.056** — meaningless dust positions that still occupy `max_positions` slots. Use `min(regime, breadth, vix)` for market-condition mults; skip entry entirely below combined 0.30. |
| DE-2 ✅ | HIGH | sizer.py:70-75 + hard_rejects.py:97-100 | **Confidence multiplier is a constant 1.25×.** The hard-reject floor (62×0.90=55.8) guarantees every surviving trade has confidence ≥55.8, so the `>=50 → 1.25` branch always fires; the 1.00/0.75 tiers are unreachable. Every position is silently 25% oversized and sizing doesn't vary with conviction. Rescale: ≥80→1.25, 62–80→1.00, floor–62→0.85. |
| DE-3 | HIGH | aggregator.py:38 + hard_rejects.py:112-118 | **SCALP is structurally always BLOCKED** with default game plans: stop 0.975/target 1.040 → R:R 1.60 < min 2.0 — every SCALP decision rejected. SWING (R:R 2.18) always fails the regime-raised 3.0 floor in choppy/risk_off. Fix SCALP defaults (stop 0.985 → R:R 2.7) or per-style min_rr. |
| DE-4 ✅ | HIGH | models.py:19, routes.py:56 | `req.max_daily_loss_pct` is accepted but never merged into cfg — the gate always uses the 0.04 default; a caller requesting 0.02 is silently ignored. `cfg.setdefault("max_daily_loss_pct", req.max_daily_loss_pct)`. |
| DE-5 | MED | scorer.py:101-105 | Missing ML probability scores **-1 (bearish)** instead of neutral: `get("bullish_probability") or 0.0` → 0.0 → "<0.58 → −1". Routes resolves the `reasons.ml_probability` fallback for display but not for scoring. Skip the layer when None. |
| DE-6 | MED | scorer.py:36, 216-218 | `recent_win_rate` param to `compute_score` is dead (never referenced); the win-rate floor only works via an undocumented `config_overrides` convention. |
| DE-7 | MED | regime.py:14, 154-174 | **No regime hysteresis** — pure point-in-time thresholds with a 15-min cache. SPY oscillating around EMA50 flips choppy↔bull intraday, swinging score ±2, size 1.0↔0.75, min_rr 2.0↔3.0; a borderline candidate is BUY at 10:00 and BLOCKED at 10:15. Require 2 consecutive refreshes or asymmetric enter/exit thresholds. |
| DE-8 | MED | regime.py:183-185 | `is_pre_choppy` fires whenever SPY is 0–3% above EMA50 — the base case of a normal grinding bull, costing −1 score across long healthy stretches. Require corroboration (band 1.0–1.015 AND vix_5d_trend rising). |
| DE-9 | MED | scorer.py:61-64, 168-183 + hard_rejects.py:145-154 | Chase extension penalized three times (up to −5 + hard reject); conversely a stock collapsing below entry2 gets **+3** ("deep pullback" +2, drift +1) with no falling-knife check. Collapse extension penalties into one layer; invalidate the plan below entry2×0.95. |
| DE-10 | LOW | hard_rejects.py:45-72 vs routes.py:277-281 | Market-closed gate defeats the batch endpoint's stated pre-market scanning purpose (all symbols return BLOCKED score −99). HK public holidays uncovered; NYSE half-days not modeled. Add a scan_mode that downgrades market-closed to advisory. |
| DE-11 | LOW | scorer.py:189, sizer.py:20-26,58 | Falsy-zero hides a legitimate research score of 0; STRONG BUY with score None falls to 1.00× instead of 1.20×; `_RESEARCH_MULT` dict is dead code. |
| DE-12 | LOW | scorer.py:136-137, hard_rejects.py:89-91 | Signal-age penalty hits every Monday morning (Friday's signal is ~65h old → −1 for all symbols uniformly). `max_consecutive_losses=0` fires after one loss instead of disabling. |

### Ranking Engine & Technical Analysis

| ID | Sev | Location | Finding |
|---|---|---|---|
| KS-1 ✅ | HIGH | ranking-engine routes.py:29 | **Wrong port: `TA_URL` defaults to `technical-analysis:8006`** — TA listens on **8002** (8006 is strategy-engine). Every `_fetch_patterns_bulk` call connection-refuses, swallowed by `except: pass` → the leaderboard pattern column has silently never worked. Change to 8002; log the exception. |
| KS-2 ✅ | HIGH | ranking-engine routes.py:525-527 | `rank_symbol` builds a **one-entry sector map**, so every peer gate (`len >= 3`) fails → the per-symbol K-Score always uses price proxies and returns value/growth None — stock-detail K-Score diverges from leaderboard K-Score for the same stock/day. Pass the full universe's sectors. |
| KS-3 | MED | kscore.py:110-119, routes.py:131 | Momentum/volatility/RS are absolute heuristics, not cross-sectional percentiles; RS scaling `(rs_rank−1)×100` puts realistic spreads in 45–55 (±0.5pt composite effect — the 10% RS weight has no discriminating power). Convert to winsorized percentile ranks at persist time. |
| KS-4 | MED | routes.py:188-196, kscore.py:122-141 | Loss-making stocks (negative PE) skip valuation percentile and fall to the 52-week-discount proxy, which awards up to 100 to a stock 50% off its high — **worst fundamentals can get the best value score**. Assign a low fixed percentile (25–35) when fundamentals exist but PE<0; cap the proxy. |
| KS-5 | LOW | kscore.py:104-105, routes.py:96-113 | Momentum returns flat 50 for <127 bars (6-month IPO ripping +80% reads neutral); a single yfinance failure caches `None` for 1h → all HK RS pinned to 50 for the refresh, unlogged. |
| TA-1 ✅ | MED | ta core.py:21-25 | **RSI warm-up NaNs become RSI=100** via `fillna(100)` — first 13 bars of every series, and the *current* RSI of any stock with <14 bars reads max-overbought. Fill only the genuine `avg_loss==0` case. |
| TA-2 | MED | core.py:50-53 | "VWAP" is cumulative from row 0 of a 400-day window — an arbitrarily-anchored average that changes with the query param. Replace with rolling or explicitly-anchored VWAP. |
| TA-3 | MED | trendlines.py:118-143 | Trendlines least-squares fit ALL pivots in 400 days with no r² filter (V-shaped year → near-flat "uptrend" line, r²≈0, still served). Fit the last 3–5 pivots, require r²≥0.7. |
| TA-4 | LOW | trendlines.py:31-36 | Pivot plateaus count every bar as both support and resistance (strength inflation on flat/thin HK names); right-edge pivots lag 5 bars by construction. |
| TA-5 | LOW | TA+ranking caching | Bulk patterns cached 6h in TA + 6h in ranking-engine (~12h worst-case staleness); after warm-up, fetch failures silently serve the old snapshot forever; no `computed_at` in the payload. |

### Outcome Tracking & Feedback Loop

| ID | Sev | Location | Finding |
|---|---|---|---|
| OC-1 | CRIT | routes.py:3306-3340 | = CAL-1 ✅ FIXED |
| OC-2 | HIGH | routes.py:3374-3406 | = CAL-3 ✅ FIXED |
| OC-3 | HIGH | routes.py:3306-3347 | **Threshold sweep is an overfit argmax over 46 nested subsets with no holdout**, applied to production on ev_lift ≥ 0.1pp (noise). At the winning subset's n=15, win-rate SE is ±13pp. The sibling `calibrate_ml_weight` does a 70/30 temporal split correctly (routes.py:1078) — reuse that pattern; min_samples ≥50; require lift > bootstrap SE multiple. |
| OC-4 | MED | routes.py:4418, 4348 | "Win" = any positive close-to-close move (+0.01% after 14 days counts); no stop-loss modeling (a −15% drawdown that recovers to +0.2% scores correct), no costs. EV = win_rate × avg_return double-counts win probability. Require a cost hurdle (+0.5%); track max-adverse-excursion; use plain mean return as EV. |
| OC-5 | MED | routes.py:79-106 | **T223 confidence calibration pools BUY+SELL, all horizons, both markets** into one band — a LONG US BUY's displayed "win rate" is dominated by whatever populates the band (mostly SELLs, which fire 2.3×). min-count 10 → ±30pp CI, displayed with green/amber color distinctions far inside noise. Key by (direction, horizon); raise min-count to ≥30; show n. |
| OC-6 | MED | routes.py:4394-4409 | Survivorship bias: outcomes silently dropped when later prices are missing (delisted/halted stocks — disproportionately the worst BUYs). Write censored rows with skip_reason; score confirmed delistings as full losses. |
| OC-7 | MED | routes.py:3171-3277 | Baseline for ev_lift is hardcoded, stale, regime-blind, and never reads the previously-applied Redis threshold — an unanchored feedback loop that drifts weekly. |
| OC-8 | LOW | routes.py:4297 | HOLD/WAIT signals never scored — the system measures false positives but structurally cannot measure missed winners, so calibration can never learn to loosen. Shadow-score a random HOLD sample. |
| OC-9 | LOW | routes.py:4243-4249 | "5d/10d/20d" windows are calendar days (~3–4 trading days for "5d"), shifting non-uniformly over holidays. Index the per-stock date list by position. |
| OC-10 | LOW | routes.py:3291, signals.py:1395 | 30-day Redis TTL: if the weekly calibrate job fails >30 days (the recurring jose-401 failure mode), all calibrated thresholds silently expire and behavior snaps back overnight, unlogged. Watchdog key wins over calibration with no arbitration logging. |

### Paper Trading Engine

| ID | Sev | Location | Finding |
|---|---|---|---|
| PT-1 | MED (revised) | shared/common/config.py:71, scheduler.py:450,2283 | `enable_paper_trading` defaults `False`; `ENABLE_PAPER_TRADING` absent from every tracked env file — the **local dev** engine has never run (three untouched $50k portfolios; masked because `ensure_portfolio_exists` runs regardless). **Production's EC2 .env does set it** — 47 trades, 5 portfolios, all losing (Part 1). Fix: add the flag (commented) to `.env.example`/`.env.production.example` and a startup log line stating its value so dev/prod divergence is visible. |
| PT-2 | HIGH | paper_trading_engine.py:2221-2231 | **Second 100% blocker in line:** the scan aborts unless a watchlist has `trading_style` matching the portfolio style — production watchlists are themed lists with NULL style, and `GROWTH` isn't even in the documented value set (`SHORT\|SWING\|LONG\|None`). After PT-1 is fixed, entries will still be zero until a watchlist is tagged per style (or the scan falls back to all active market stocks with a warning). |
| PT-3 | HIGH | paper_trading_engine.py:2873 | `float(None)` TypeError: signal-engine stores `reasons["volume_z"] = None` when NaN; `.get("volume_z", 0)` returns None (key exists) → `float(None)` raises, propagates to the outer except → **one bad candidate aborts the scan for ALL portfolios**. Fix: `float(x or 0.0)` (the ta_score gate 30 lines later already does this) + wrap the per-candidate loop in try/except. |
| PT-4 | HIGH | paper_trading_engine.py:2967-2988 | TIER66 conviction-gate cross-block: if the *alert* gate (confidence ≥60, analyst consensus, confluence ≥75) failed for a subscribed symbol, paper entry is hard-blocked — silently raising min_confidence from 45/50 to the alert standard, invisible in the gate-block UI. Make it a size multiplier or score penalty, and log via `_write_gate_block`. |
| PT-5 | MED-HIGH | scheduler.py:901-934 | Redis lock: 90s TTL vs a step that can run multi-minute (regime downloads + per-candidate HTTP across 3 portfolios) → overlapping runs can double-credit exits. The `finally` deletes the lock **without a token compare**, so a slow run deletes the next run's lock. Use UUID + compare-and-delete; TTL 300s. |
| PT-6 | MED | paper_trading_engine.py:1791-1836, 1710 | Scale-out profits excluded from `trade.pnl` — a winner that took +15%/+22% partials then trailed to breakeven records pnl ≈ 0/negative. Consumed by win-rate, consecutive-loss streak, heat brake, loss limits, outcome writeback, RL training — **best-managed trades recorded as losers**. Accumulate realized partial P&L into the trade. |
| PT-7 | MED | paper_trading_engine.py:2166-2170 vs 2829-2839 | Contradiction: the signal query window was widened to 120h *because* dedup-on-change persistence means a persistent BUY never re-writes — but the 72h age gate then skips those same signals. **The most durable uptrends are systematically excluded**; candidates skew toward freshly-flipped (noisier) signals. Bump a `last_confirmed_ts` on refresh even when deduped. |
| PT-8 | MED | paper_trading_engine.py:3049-3153 | Multiplier stacking: regime-family correctly composes via `min()`, but the cross-family product bottoms at ~0.11× → ~$56 risk → under `min_position_value: 200` → silent no-trade. `sig_conf >= 50 → 1.25×` fires on every SWING/HK entry (negating T222-F's HK 0.7% risk reduction); the <30 tier is unreachable. VIX is NOT double-applied to shares (engine ignores DE's vix mult), but regime stress double-dips via DE score → score_size_mult. Clamp non-regime product at 0.5; recenter confidence bands per-style. |
| PT-9 | LOW-MED | paper_trading_engine.py:1582-1604, 1701-1712 | Exits evaluate one live snapshot per 5-min cycle: stop-before-target is conservative (fine); gap-downs fill pessimistically (QW-7, correct) but target exits fill at `live_price×(1−slippage)` — booking the full gap **above** target (optimistic vs a real limit fill). Fill targets at `target`, mirroring the stop convention. |
| PT-10 | LOW-MED | paper_trading_engine.py:2300,2331,1707,2052 | Daily loss/entry counters reset at UTC midnight (19-20:00 ET); "weekly gain lock until Monday" is actually a rolling 7 days; breakeven-stop exits book pnl < 0 via slippage so `_consec_loss_streak` counts breakevens as losses — 3 breakevens halt HK trading. Use market-local dates, Monday-anchored weeks, and treat \|pct_return\| < 0.3% as streak-neutral. |
| PT-11 | LOW | paper_trading_engine.py:2264, 1103-1106 | Confidence floor silently 10% under config everywhere (`min_confidence × 0.90` in both the SQL filter and hard reject; nothing enforces the full value in DE-primary mode): GROWTH 40.5 not 45, HK 58.5 not 65 — weakens T222-A. `regime_bear_size_mult: 0.0` is dead config (bear is a hard return). |
| PT-12 | LOW | paper_trading_engine.py:1881-1897 | `atr` only assigned when `not earnings_near` — latent NameError / stale wrong-symbol carryover if any of the five downstream `and` chains is reordered. Assign unconditionally per iteration. |

---

## Part 4 — Top 10 Recommendations to Improve Win Rate & Returns

Ranked by expected impact per unit effort:

1. ✅ **DONE (2026-07-02):** Deleted the live corrupted key and fixed CAL-1/CAL-2/CAL-3.
   `stockai:signal_thresholds:SWING = 0.62` was ACTIVE in production — 40% of that week's SWING
   BUYs fired below the vetted threshold. Deleted the key, triggered a US+HK signal refresh
   (verified clean), and shipped the unit-conversion + per-regime-key + SELL-sweep-direction
   fix to `stockai-signal-engine-1`. SWING BUY (38.1% US / 28.3% HK) was already the worst BUY
   cohort — monitor whether it recovers now that the threshold is correctly enforced.
2. ✅ **DONE (2026-07-02):** Fixed ML-1 — now uses the real bar dates instead of the RangeIndex,
   resurrecting 4 dead time-varying fundamental features. Deployed to `stockai-ml-prediction-1`.
   **Still needed: trigger a retrain** so production models actually pick up the now-live PIT
   features (the fix alone doesn't retroactively fix already-trained model weights).
3. **Make SELL direction-aware (SIG-3 + SIG-5 + SIG-10):** guard the pillar gate and the three
   macro compressions with `fused > 0.5`, regime-tier the sell threshold, require bearish-pillar
   evidence. SELL is 43.7% overall / 33.3% US-SHORT — the single biggest measured accuracy hole,
   and these are one-line guards. *(S–M)*
4. **Exploit the SELL edge where it exists:** production SELLs win 59.8% at 5d / 60.1% at 10d,
   decaying to 48% at 20d — and HK SELLs win 62–68% at the primary horizon. Score SELL outcomes
   on 5–10d windows, and stop suppressing HK SELLs via the direction-blind HSI-bear gate (SIG-5)
   — the system's most accurate cohort is the one being muted. *(M)*
5. **Make the ML test set honest (ML-2 + ML-3 + ML-4):** threshold from the calibration slice,
   purge/embargo `horizon` bars (`TimeSeriesSplit(gap=horizon)`), fix the type-mismatched
   overlap drop. Reported precision will DROP — to the truth — making every downstream gate
   meaningful. *(M)*
6. **Retarget Optuna to precision-at-threshold (ML-5):** the economics live in the top tail;
   AUC tuning optimizes the wrong region. *(M)*
7. **Fix decision-engine sizing (DE-1 + DE-2):** `min()` composition for market mults matching
   the paper engine, reachable confidence tiers. Currently under-sizes to ~5.6% in stressed
   regimes and uniformly over-sizes 25% via the constant 1.25×. *(S–M)*
8. **Unblock structurally dead trade universes (DE-3 + DE-7 + PT-1):** SCALP never trades,
   SWING can't trade in choppy/risk_off, paper trading has never traded at all. Silent
   zero-trade states → deliberate, tunable ones. *(M)*
9. **Fix KS-1 (wrong TA port — one line) and KS-2 (one-entry sector map):** restores the
   pattern column and makes detail-page K-Score agree with the leaderboard. *(S)*
10. **Add LONG-horizon outcome tracking (DATA-3) and a prod→dev data sync (DATA-5):** LONG has
    8 outcome rows ever; and the local dev DB being 3 weeks stale produced a completely inverted
    picture of system health during this audit. *(S)*

**Confront the production base rate: BUY signals lose money (44.6% win, −1.19% avg return,
n=534).** The weakest cohorts are SWING BUY (38.1% US / 28.3% HK) — the exact horizon whose
threshold CAL-1 corrupted — and HK BUYs generally (28–42%). Until the calibration loop, ML test
honesty (ML-2/3/4), and SELL-suppression fixes land and a clean 4-week outcome sample exists,
treat BUY alerts (especially SWING and HK) as unvalidated. Paper trading confirms it with real
losses: −$11,800 aggregate, worst in the HK portfolios that buy into the 28–29% win-rate cohort.

---

## Part 5 — Design Invariants (add to review checklist)

1. **Scale discipline:** `confidence` (0–100, distance from neutral) vs `fused_prob` (0–1) vs
   `ta_score` (0–1 in reasons, 0–100 in some consumers) — any code moving values between these
   MUST convert explicitly. Two critical bugs (CAL-1, SIG-8) came from this.
2. **Direction discipline:** any compression/gate that pulls `fused` toward 0.5 MUST decide
   whether it applies to bullish evidence, bearish evidence, or both. Default: guard with
   `fused > 0.5` unless the condition genuinely implies "no signal" (e.g. ADX chop).
3. **Index discipline:** pandas RangeIndex vs DatetimeIndex — `pd.to_datetime(<RangeIndex>)`
   produces 1970 epoch dates silently. Two bugs (ML-1, ML-3) came from this. Prefer joining on
   explicit `ts` columns.
4. **No silent `except: pass` on data-integrity paths** — every swallowed exception in this
   audit's critical findings (ML-1, KS-1, PT entry path) hid a total failure for weeks.
5. **Calibration writes must be validated on a temporal holdout** and applied with sanity
   clamps at the reader (thresholds outside [0.55, 0.80] fused are rejected).
6. **One source of truth for thresholds:** `_STYLE_PROFILES` — no hardcoded copies in routes.py.
