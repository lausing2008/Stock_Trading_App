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

---

## Part 6 — Session Log: Fixes Shipped, Features Built, New Findings (2026-07-02 → 2026-07-03)

Everything below happened after the initial audit (Parts 1–5) was written, in the same working
session. All items are cross-referenced by their `improvements.tsx` tracker ID (Tier 232).

### 6.1 — Critical/high fixes deployed to production

All four **critical** findings were fixed and deployed the same day they were found:

- **CAL-1 / CAL-2 / CAL-3** (calibration unit mismatch, regime-blind override, inverted SELL
  sweep) — fixed in `signal-engine`. **CAL-1 was found ACTIVE in production**:
  `stockai:signal_thresholds:SWING = 0.62` had been silently loosening the SWING BUY threshold
  since ~2026-06-28. The corrupted key was deleted and a full signal refresh verified clean
  (all fresh SWING BUYs ≥ the vetted 0.72) before the code fix shipped.
- **ML-1** (PIT fundamentals epoch-date bug) — fixed in `ml-prediction`. Retraining still
  required to actually benefit from the resurrected features.

Twelve additional **high/medium** findings were fixed and deployed: SIG-3/SIG-5 (direction-blind
SELL suppression), KS-1 (wrong TA port), KS-2 (broken per-symbol sector map), TA-1 (RSI warmup
bug), DE-2/DE-4 (sizing/config bugs) — see the Tier 232 tracker for the complete per-item list
(`T232-CAL1…T232-TA1`).

### 6.2 — Production incident found and fixed mid-session: ETrade Sandbox / HK crash

User reported the ETrade Sandbox SWING portfolio had only 1 trade ever and none in two full
trading days. Root cause: **`live_regime.get("vix", "?"):.1f` crashed with `TypeError` whenever
HK was in `risk_off`/`bear` regime** (HK has no VIX; the key exists but is `None`, so the `"?"`
fallback never applied). This crashed `paper_trading_step()` **for every portfolio scanned after
HK in the loop** (GROWTH → HK SWING → US SWING → HK GROWTH → ETrade Sandbox by id order) —
measured at 103 crashes in a 40-hour log window versus exactly 1 successful entry system-wide.
Two more instances of a related bug class (wrong absolute import path, silently swallowed) were
found and fixed in the same pass: `poll_broker_order_fills` (broker order fills never polled)
and the HK-holiday check (`_is_market_hours`). See `T232-PT13`, `T232-PT14`.

**One follow-up incident during the fix itself:** a VIX-removal edit used an f-string with
escaped quotes, which is valid on Python 3.12 (the dev machine) but a hard `SyntaxError` on
Python 3.11 (production) — crash-looped `market-data` for ~2–3 minutes before caught and fixed.
Lesson recorded: compile-check with `py_compile` against the *actual* deployed Python version,
not just `ast.parse` locally.

### 6.3 — New feature: post-open digest emails (user request)

Originally built as **one combined HK+US email at 08:50 HKT** (~20:50 ET the prior evening).
User clarified they wanted **separate per-market emails, 30–40 min before each market's own
open** — re-split into `morning_digest_us` (08:50 ET) / `morning_digest_hk` (08:50 HKT), each
scoped to only its own market's data (open positions and pattern alerts had been silently
combined across markets even in the original two-job design — fixed while splitting).

Then added two **new** emails per market: **+30min** and **+1hr** after open
(`post_open_digest_{market}_{window}`), covering regime changes, open-position moves/signal
flips, new BUY/SELL signals, a **top-5 volume-surge section** (reusing the existing `volume_z`
ML feature — no new intraday aggregation needed), and top gainers/losers. The +1hr email reports
only the **delta** vs. the +30min snapshot (Redis-backed state, 24h TTL), and the whole email is
skipped when nothing meaningful changed. See `T232-POSTOPEN1`.

### 6.4 — Frontend regression found and fixed: Signal Filter market button

User reported the Signal Filter page breaking on the US market button. Root cause: a **three-layer
regression of a working Tier 128 (2026-06-22) feature** — the backend never selected/returned
`Stock.market`, and the frontend had regressed to client-side filtering on `(x as any).market`
(a field that never existed on the row type, hence the type-check-bypassing `as any`). Every
non-"ALL" market click silently returned zero rows. Restored server-side filtering per the
original design. See `T232-UI1`.

### 6.5 — HK regime improvements (user request: "any better indicators?")

User asked whether better indicators existed for the HK risk_off gate (which was correctly
blocking HK trading — HSI was genuinely ~9-11% below its 200-day average) and whether a bypass
button made sense.

- **HSI breadth confirmation** (`T232-HKBREADTH-CONFIRMATION`): added `_compute_hk_breadth()` —
  % of the tracked HK universe (31 stocks with ≥200 daily bars, no new external constituent
  list needed) trading above their own 200-day SMA. An index-level bear/risk_off call is
  downgraded one tier when breadth does NOT also confirm broad weakness (≥40%) — distinguishing
  "a few mega-caps dragging the index down" from genuine systemic decline. Verified against live
  production data: breadth read 32.3% on deploy day, correctly **confirming** (not spuriously
  relaxing) the existing `risk_off` call.
- **Time-boxed override** (`T232-RISKOFF-OVERRIDE`): rather than a permanent config flip or an
  always-visible bypass button (both rejected as too easy to misuse in the heat of the moment),
  added `POST /paper-portfolio/risk-off-override?hours=N` (max 24h) — self-expiring, checked on
  every gate evaluation, with a UI control on the Paper Portfolio Config Panel ("Allow trading
  for 4 hours" / "1 day" + live countdown + cancel).
- **VIX fully removed from HK-facing text** (`T232-POSTOPEN2`): gate-block messages, the morning
  digest, and the paper-portfolio Regime card all previously showed a US-shaped "VIX N/A"
  template for HK. HK genuinely has no VIX equivalent — all three now use HK's own descriptive
  regime note (e.g. "HSI -9.3% below SMA200") instead.

### 6.6 — Documentation pass + new architectural finding

While updating the `/paper-gates` reference page to describe the new breadth/override behavior
(`T232-GATEPAGE-DOCS-UPDATE`), discovered that **decision-engine runs a second, independent
regime classifier** (`services/decision-engine/src/api/core/regime.py`) that the entry-gate
page's live "Live Status" section actually reads from — NOT the `paper_trading_engine.py` copy
that gates real trading and was the one improved with breadth confirmation. See
`T232-REGIME-DUPLICATION` for the full writeup: decision-engine is a stateless HTTP service with
no DB access, so it independently re-fetches ^HSI via yfinance and hand-mirrors the
classification logic — a design tradeoff (avoid a cross-service call) that has already visibly
failed, since the very first regime enhancement after the mirror was written (breadth
confirmation) was not applied to both copies. **Not fixed in this session** — flagged as
real architectural debt requiring either a shared module or a market-data → decision-engine
HTTP call, not a quick patch.

---

## Part 7 — Deep Logic Review (2026-07-03): Full Gate/Pipeline/Cross-Service Audit

Following the fixes and features in Part 6, the user asked for a comprehensive, no-stone-unturned
review of "all the logics" across the system — not another targeted bug hunt, but an exhaustive
map of every gate, every silent-default, every swallowed exception, every US/HK asymmetry, and
every duplicated computation. Three parallel deep-read agents covered: (A) the paper trading
engine's full entry/exit gate stack, (B) the signal-generation and ranking pipeline, and
(C) decision-engine / strategy-engine / portfolio-optimizer. Nothing in this section was fixed
during the review — it is a read-only findings catalogue, prioritized below for follow-up.

### 7.1 — Confirmed live bugs (not just architectural debt)

**T232-DL1 — `ta_score` falsy-zero bug silently disables the TA gate.**
`paper_trading_engine.py` (`_scan_for_entries`, TA score gate) reads
`reasons.get("ta_score", 1.0) or 1.0`. A genuinely terrible TA score of exactly `0.0` is
falsy in Python, so `0.0 or 1.0` evaluates to `1.0` — the *most permissive* possible value.
A stock with the worst possible technical score passes the TA gate identically to a stock
with no TA score at all. Contrast with the correct pattern used two lines away for
`insider_score`/`congress_score`, which check `is not None`. **Fix:** change to
`v = reasons.get("ta_score"); ta = v if v is not None else 1.0`.

**T232-DL2 — `min_entry_score` stale fallback constant (3 vs actual default 4).**
`_DEFAULT_CONFIG["min_entry_score"]` is `4`, but seven separate `.get("min_entry_score", 3)`
call sites in `paper_trading_engine.py` (lines ~1407, 2674, 2678, 2688, 2694, 3216, 3303) hardcode
a stale fallback of `3`. Harmless today because `cfg` is always built by merging
`_DEFAULT_CONFIG` first — but if any future code path calls these functions with a raw/partial
config dict (e.g. a unit test, a new caller, a decision-engine-adjacent helper), the entry bar
silently drops to a weaker value with no error. **Fix:** import the real default once and reuse
it in every `.get()` fallback, or require `cfg` to always be pre-merged and assert that instead
of guessing a fallback per call site.

**T232-DL3 — `min_confidence` fallback disagrees with the real default by 17 points.**
Two call sites in `paper_trading_engine.py` — `_should_enter()` (line ~1190) and the
decision-engine payload builder (line ~2181) — use `.get("min_confidence", 62.0)`, while
`_DEFAULT_CONFIG["min_confidence"]` is `45.0` (with per-style overrides at 45/50/etc.). Same
failure mode as T232-DL2: currently masked by the config merge, latent otherwise.

**T232-DL4 — Documentation/code drift: `max_entries_per_day`.**
The module docstring in `paper_trading_engine.py` says daily entries are capped at "5"; the
actual `_DEFAULT_CONFIG["max_entries_per_day"]` is `3`. Cosmetic today, but exactly the kind
of comment that gets trusted during a future debugging session and sends someone down the
wrong path.

**T232-DL5 — `volume_z` absent-vs-zero conflation.**
The per-candidate low-volume gate reads `float((sig.reasons or {}).get("volume_z", 0))` — a
missing `volume_z` field (data gap) is silently treated as `0.0` (exactly average volume),
passing the gate, rather than being flagged as "no volume data available." A stock with a
genuine data gap looks identical to a stock with perfectly normal volume.

**T232-DL6 — Stale aspirational comment: HK-specific SWING thresholds that don't exist.**
`signals.py` (~line 1230) has a comment claiming "HK SWING thresholds: bull=0.74, bear=0.78 —
applied via market parameter check in `_apply_style_signal` when symbol ends in .HK" — no such
branch exists anywhere in `_decide_style`/`_apply_style_signal`. `_STYLE_PROFILES["SWING"]`
has only `bull/high_vol/bear/unknown` keys with no HK variant; the single hardcoded threshold
dict is applied identically to US and HK SWING signals today. Either the feature was removed
without removing the comment, or it was never actually implemented — either way the comment
actively misleads.

**T232-DL7 — Stale aspirational doc: portfolio-optimizer regime multiplier.**
`improvements.tsx` documents "Portfolio-optimizer fetches regime and applies position multiplier
(bull=1.0, choppy=0.75, bear=0.60, risk_off=0.50)" as shipped. No such code exists anywhere in
`services/portfolio-optimizer/src/` — no reference to `regime`, `decision_engine_url`, or
`/decide/regime`. The regime-multiplier logic that *does* exist (`_REGIME_MULT` in
decision-engine's `sizer.py`) is not reachable from portfolio-optimizer at all. The tracker
entry needs correcting to reflect reality, or the feature needs to actually be built.

### 7.2 — Regime computation: FIVE independent implementations (not two)

The existing `T232-REGIME-DUPLICATION` entry undercounts the problem — it names two engines
(decision-engine and paper_trading_engine). The full audit found **five**:

1. `paper_trading_engine._fetch_market_regime` (US) — SPY/QQQ/VIX/VIX9D/IWM/MDY, EMA20/50/200,
   4h cache-fallback, defaults to **`choppy`** on fetch failure (deliberately conservative).
2. `paper_trading_engine._fetch_hk_market_regime` (HK) — HSI dual-SMA(50/200) + the new breadth
   confirmation (T232-HKBREADTH-CONFIRMATION, this session).
3. `decision-engine/src/api/core/regime.py` — its own independent SPY/QQQ/VIX re-fetch (15-min
   cache, hardcoded `VIX_HIGH=25.0`/`VIX_FEAR=30.0`, **not configurable**), a *different* CHOPPY
   rule than #1, and defaults to **`neutral`** on failure — the opposite fail-safe from #1. Its
   HK classifier (`_compute_hk`) lacks the breadth confirmation from #2 entirely (already known).
4. `ml-prediction/src/api/hmm_regime.py` — a 4-state Gaussian HMM overlay (VIX, SPY 5d return,
   IWM vs EMA200), consumed only by #1 (as `hmm_bear_pressure`, an extra 30% size cut). Decision-
   engine never calls this endpoint, so the same moment can carry an HMM bear-pressure flag in
   paper trading with no analog in decision-engine's output.
5. `signal-engine/src/generators/signals.py._fetch_market_regime` — a *third* vocabulary
   entirely (`bull`/`high_vol`/`bear`/`unknown`, 3-4 states), derived from market-data's
   `/stocks/fear_greed` (SPY vs 200-day MA + Fear&Greed score), stamped into every signal's
   `reasons["market_regime"]` and separately consumed by `scheduler._get_current_regime()`
   for the BUY-alert conviction gate. Has its own separate HK classifier `_fetch_hsi_regime`
   (HSI vs 20-day SMA only) — a sixth implementation by strict count.

**These provably can and do disagree at the same instant** — different cache TTLs (15min vs 4h
vs 1h), different state vocabularies, different failure defaults (neutral vs choppy vs unknown),
different classification math for the same named state. A portfolio can be gated `risk_off` by
the live paper-trading regime while the BUY-alert emailer's conviction gate reads a `bull`
regime baked into the signal's `reasons` from hours earlier. **Recommendation:** this is too
large to fix as one patch (5 call sites, 3 services, 2 markets) — the actionable next step is
designating #1/#2 (paper_trading_engine's copies) as the single source of truth, having
decision-engine and signal-engine call `market-data`'s regime over HTTP instead of
re-implementing it, and removing the HMM/Fear&Greed vocabularies as separate published states
(fold them into #1/#2 as internal adjustments only, which is already how HMM is used).

**Fix applied 2026-07-04 (partial — #3 only):** Added `GET /stocks/regime?market=US|HK` to
market-data, exposing `_fetch_market_regime()`/`_fetch_hk_market_regime()` (#1/#2) directly over
HTTP via new `get_last_regime()`/`get_last_hk_regime()` wrapper functions. Rewrote
`decision-engine/src/api/core/regime.py` to call this endpoint instead of maintaining its own
from-scratch `_compute_us()`/`_compute_hk()` (now deleted — ~175 lines removed). Verified live in
production: `GET /decide/regime?market=HK` now returns `state=risk_off, breadth_pct=32.3` —
identical to market-data's own reading, including the breadth confirmation decision-engine never
had before this fix. Also folded the VIX position-size gradient formula (previously two
independently-written copies of `max(0.5, 1-max(0,(vix-20)/30))`, kept in sync only by a
cross-referencing comment) into a single computation inside the new `regime.py`, deriving it from
the shared `vix` field rather than re-implementing the formula a second time.

This reduces the regime-classifier count from 5 to 4. **#5 (signal-engine) was deliberately left
unmerged** — its `bull`/`high_vol`/`bear`/`unknown` vocabulary is load-bearing for every style's
outcome-calibrated buy/hold threshold tables (the SA-28/SA-31/SA-32 tuning rounds mentioned
elsewhere in this report tuned thresholds specifically against this 4-state classification).
Merging it into market-data's 5-state vocabulary would require re-deriving and re-validating
every threshold against live outcome data — a real project with its own risk profile, not a
same-day architectural cleanup. Tracked as an intentional, documented remaining divergence rather
than unaddressed debt. #4 (the HMM overlay) was already correctly scoped as an internal
adjustment consumed only by market-data's regime — no change needed there.

### 7.3 — Other duplicated computation with drift risk

- **Style game-plan parameters (`_STYLE_PARAMS`), triplicated, one copy has drifted.**
  `scheduler.py` (source of truth per its own header comment) and `paper_trading_engine.py`
  ("mirrors scheduler — inlined to avoid circular import") currently match. Decision-engine's
  independent third copy (`aggregator.py`) has **diverged for GROWTH**: stop `-16%`/target
  `+60%` vs the real engine's `-12%`/`+35%` — and invents `SCALP`/`INCOME` styles that don't
  exist anywhere in the actual trading engine, with parameters that have never been validated
  against real trading data.
- **`_should_enter()` (paper trading) and decision-engine's scorer/sizer/hard-rejects have
  diverged in both directions** since the latter was "extracted faithfully from" the former.
  `_should_enter()` has RL policy scoring, a calibrated logistic-regression entry model, a
  premarket gap filter, and a macro-calendar blackout that decision-engine lacks entirely.
  Decision-engine has a time-of-day gate, an extended-move/chasing guard, and a
  regime-dependent R:R floor (`regime_min_rr_ratio`) that `_should_enter()` lacks entirely.
  Neither is a superset of the other. The `/paper-portfolio/de-divergences` endpoint exists
  specifically because these two scorers disagree — a real, monitored, but unresolved drift.
- **K-Score/ranking fetched via three different query shapes**: `_scan_for_entries`'s
  correlated-subquery join, `_monitor_positions`'s separately-written "latest per stock"
  subquery, and `scheduler.check_signal_alerts`'s HTTP call to ranking-engine's own API. Any
  skew between ranking-engine's cache and the DB `Ranking` table's most recent row produces
  inconsistent K-Score reads between the trading engine and the alerting system.
- **NYSE holiday calendar duplicated in two files** with different types and different year
  coverage: `paper_trading_engine._NYSE_HOLIDAYS` (frozenset of `date`, through 2027) vs
  `scheduler._NYSE_HOLIDAYS` (frozenset of `(y,m,d)` tuples, only through 2026). A missed
  update in one but not the other silently desyncs whether the engine thinks the market is
  open vs whether the scheduler triggers that day's refresh.
- **RSI/MACD/ATR/Bollinger computed independently in `strategy-engine`'s backtest DSL**
  (`dsl/evaluator.py::compute_features`) instead of calling the dedicated `technical-analysis`
  service. The RSI implementations provably differ: technical-analysis has an explicit
  NaN-vs-zero disambiguation fix (T232-TA1) that the DSL's from-scratch reimplementation lacks.
  Backtests run through strategy-engine compute slightly different indicator values than the
  same symbol's live numbers used by signal-engine.
- **VIX position-size multiplier formula** — identical `max(0.5, 1-max(0,(vix-20)/30))` in both
  decision-engine's `sizer.py` and `paper_trading_engine.py`, kept in sync only by a comment
  ("Matches decision-engine formula") — no shared function, so a future tune to one silently
  stops matching the other.
- **Sector-relative-strength benchmark differs by data path for the same stock.** Ranking-
  engine's own `_stock_rs`/`_etf_20d_return` (feeds K-Score) is a separate implementation from
  market-data's `/stocks/{symbol}/relative-strength` (feeds signal generation's `kscore_boost`
  and RS-based compression). Signal-engine's own docstring claims market-data is "the single
  source of truth for RS data" — that claim is false for the ranking-engine/leaderboard path.

### 7.4 — Config values duplicated across services with drift risk (table)

| Config key | decision-engine | paper_trading_engine / scheduler | Kept in sync by |
|---|---|---|---|
| `min_confidence` | 62.0 flat | 45.0 base, per-style 45–50+ | Only when explicitly forwarded in `config_overrides`; any other caller gets 62.0 |
| `min_entry_score` | 4 default | 4 base, SWING override 5 | Same — only synced via explicit forwarding |
| `regime_vix_high` / `regime_vix_fear` | Hardcoded 25.0/30.0, **not configurable** | Configurable via portfolio config | Not synced at all — tuning one has zero effect on the other |
| `_STYLE_PARAMS` (per-style stop/target %) | Own copy, diverged for GROWTH | Two copies, kept matching by comment discipline | Manual — already failed once (see 7.3) |
| `regime_min_rr_ratio`, `max_breakout_extension_pct` | Present (3.0 / 6.0) | Absent entirely | N/A — decision-engine-only floor with no real-engine equivalent |
| `max_entry_gap_pct` (premarket gap filter) | Absent | Present, per-style overrides | N/A — paper-trading-only guard with no decision-engine equivalent |

### 7.5 — Silent defaults, swallowed exceptions, and missing freshness checks (representative, not exhaustive)

The full per-line inventory from all three research passes is long (60+ instances of bare
`except: pass` / `except Exception: pass` across `paper_trading_engine.py`, `scheduler.py`,
`signal-engine/api/routes.py`, and `decision-engine`). Rather than list all of them here, the
**pattern worth fixing structurally** is:

- **Most swallowed exceptions are deliberate fail-open design** (comments confirm this: macro
  blackout, regime-suspension Redis check, index-trend gate, research-gating) — appropriate for
  a trading system that should degrade to "trade normally" rather than "stop trading" on an
  ancillary data-source outage. This is a reasonable default posture, not a bug.
- **The actual gap is observability, not the fail-open behavior itself.** Nearly all of these
  are `except Exception: pass` with **zero logging** (`_apply_tuned_hold_days`, `_clear_gate_block`,
  `_write_gate_block`, `_write_no_entry_summary`, broker fill-check inner catches) — if Redis or
  a dependency degrades in a way that trips many of these simultaneously, there would be no log
  line anywhere to notice it happened, only silently-wrong behavior (stale gate-block messages,
  tuned params not applying, no-entry-summary not writing).
- **Two specific instances raise the stakes above "cosmetic":**
  - `scheduler._run_paper_trading_step`'s Redis lock acquire (bare `except: pass`) — if Redis is
    down, the distributed lock silently no-ops, **re-enabling the double-execution race the lock
    exists to prevent** (double-scanning/double-entering on the same cycle).
  - `scheduler.check_signal_alerts`'s Redis lock acquire — identical risk for duplicate alert
    emails.
  - Both should fail *closed* (skip the run and log an error) rather than fail open, since the
    lock's entire purpose is preventing a specific race, not being an optional nicety.
- **Cross-service reads with no freshness/staleness field surfaced to the caller:** signal
  confidence-calibration cache, ranking-engine's pattern cache (6h TTL, in-process — resets on
  every restart, not shared across replicas), and the ETF 20-day-return cache (1h TTL, same
  in-process/non-shared issue) all return data with no `as_of`/`cached_at` field, so a caller
  cannot distinguish "fresh" from "stale because the upstream dependency has been down for
  hours" from "never populated since the last container restart."

### 7.6 — Data-sufficiency threshold inconsistency (ranking vs signal pipeline)

**T232-DL8 — Ranking-engine (60 bars) and signal-engine (50 bars) disagree on "not enough
history," and only one of them tells you about it.** A stock with 55 daily bars gets a
(confidence-compressed but present) signal from signal-engine, yet gets **zero ranking row at
all** from ranking-engine (`_persist_rankings`/`_leaderboard_live` both hard-`continue` below
60 bars) — it silently vanishes from the leaderboard/screener with no persisted reason. Compare
to signal-engine, which persists `insufficient_history` directly into the signal's `reasons`
JSON — queryable, visible, explained. Ranking-engine has no equivalent per-stock flag; the only
visibility is an aggregate `skipped` counter in the batch-level log line. A trader looking at
a recent IPO or newly-tracked stock has no way to tell "not ranked because too new" from "not
ranked because something is broken" without grepping container logs for the day it happened.
**Fix (moderate effort):** either lower ranking-engine's threshold to match signal-engine's 50
(if the K-Score math tolerates it — the RS component already independently degrades gracefully
below 21 bars), or persist a `skip_reason` alongside `stock_id` in a lightweight table/Redis key
so the gap is queryable instead of log-only.

### 7.7 — US/HK asymmetries beyond regime (catalogue)

- Broker order routing is US-only — `_place_broker_entry`/`_place_broker_exit` hard-skip any
  `.HK` symbol; HK trades are always pure simulation even on a portfolio with a linked broker.
  (Expected/by design — no HK broker integration exists yet — but worth stating explicitly
  since it's not documented anywhere a new contributor would see it.)
- VIX-gradient position sizing and the HMM bear-pressure overlay are both silently no-ops for
  HK (regime dict never populates `vix`; HMM endpoint is never called for HK) — not a bug, but
  means two of the paper-trading engine's four size-adjustment mechanisms simply don't exist
  for HK portfolios, which isn't surfaced anywhere in the `/paper-gates` docs updated this
  session (those docs cover the regime *gate*, not the sizing asymmetry).
  Breadth-based sizing exists for both markets but is computed from **structurally different
  metrics** (US: IWM/MDY small/mid-cap ETFs; HK: % of DB-tracked stocks above their own 200-SMA)
  under the same field names (`breadth_weak`/`breadth_size_mult`) — same name, different
  meaning, easy to misread when comparing US and HK regime dicts side by side.
- Options flow / short-interest / analyst-momentum enrichment calls fire unconditionally for
  HK symbols even though `_fetch_options_flow`'s own docstring says it always returns
  `(None, None)` for HK — every HK signal-generation cycle makes a known-wasted HTTP round
  trip, and HK signals systematically lack a `+0.04`/`+0.02` confidence boost available to
  every US signal, for reasons unrelated to actual market conditions.
- The alert-emailer's conviction gate (`scheduler._is_conviction_buy`) applies identical
  RSI/MACD/ADX/K-Score/ML thresholds to HK and US signals with zero market-specific branching —
  the opposite asymmetry from the paper-trading engine, which tightens HK thresholds
  extensively via `_HK_MARKET_OVERRIDES`. Neither is necessarily wrong, but the two systems
  (alerts vs. actual trading) disagree on how much HK deserves stricter treatment.
- HK's `regime_suspension_days` override (7, more forgiving/slower-to-trip) coexists with a
  *stricter* `min_entry_score` override (6 vs US 4) and `min_confidence` override (65 vs 45) —
  an inconsistent risk posture (looser circuit breaker, tighter entry gate) with no comment
  explaining whether this combination was deliberately chosen or is itself drift.

### 7.8 — Scope note

This review deliberately did not re-litigate findings already fixed earlier in this session
(ranking staleness, watchlist tagging gaps, config silent-save, VIX-crash, wrong imports) or
already-tracked architectural debt (`T232-REGIME-DUPLICATION` as originally scoped). Everything
above is **net-new** from the 2026-07-03 deep-logic pass. See `improvements.tsx` Tier 232 entries
`T232-DL1` through `T232-DL8` and the corresponding duplication/asymmetry entries for tracking.

### 7.9 — Follow-up fixes shipped (2026-07-04)

The eight self-contained, low-risk findings from this review were fixed and deployed the
following day (the five architectural-debt items — 5-way regime duplication, triplicated style
params, dual scorer drift, ranking/signal history-threshold mismatch — remain open, correctly
tracked as debt requiring a real design decision, not a quick patch):

- **T232-DL1** (`ta_score` falsy-zero) — `paper_trading_engine.py` TA gate now checks
  `is not None` instead of `x or 1.0`.
- **T232-DL5** (`volume_z` absent-vs-zero) — same fix pattern; a missing `volume_z` now skips
  the low-volume gate (fail-open, consistent with the adjacent HK flow gate) instead of being
  read as exactly-average volume.
- **T232-DL2 / T232-DL3** (stale `min_entry_score`/`min_confidence` fallback constants) — all 9
  call sites across `paper_trading_engine.py` now reference `_DEFAULT_CONFIG[...]` instead of
  repeating independently-drifted magic numbers.
- **T232-DL4** (doc/code drift, "5" vs actual "3" daily entries) — docstring corrected to match
  the real, deliberately-tuned value.
- **T232-DL6** (stale HK SWING threshold comment) — replaced with an accurate comment; no HK-
  specific SWING threshold branch was built, since there's no evidence the described values were
  ever validated (HK outcome-tracking sample is thin — see `T232-DATA1`).
- **T232-DL-OBSERVABILITY** (Redis lock fail-open) — `_run_paper_trading_step`'s lock now fails
  **closed** (skip the cycle, log ERROR, next tick retries) since a missed lock here risks a real
  double cash-credit on exit. `check_signal_alerts`' lock deliberately stays fail-open (worst
  case is a duplicate email, and a DB-level dedup fallback already exists) but now logs a
  WARNING instead of silently swallowing the exception.

All changes compile-checked against production's actual Python 3.11 container (not just local
3.12) before deploy, per the standing lesson from the earlier f-string incident this session.

---

## Part 8 — Developer Documentation Audit (2026-07-04): all 14 `skill.md` files

Following the deep logic review and its fixes, the user asked for a separate pass: check every
`skill.md` file in the repo (one per service, plus `shared/`, `frontend/`, and `.claude/` — 14
files, ~1,700 lines total) against the actual current code, since these are the standing
development-practice references every future Claude Code session reads before touching a
service. Docs like these rot the same way the calibration/ranking bugs earlier in this report
did — silently, with no error to signal the drift — so the method was the same as the rest of
this audit: don't trust the doc, verify every checkable claim against source.

### 8.1 — Method

Two parallel research agents: one mechanically diffed every line-count/file-size/page-count claim
against `wc -l`/`ls -la` on the real files; one verified six specific behavioral/architectural
claims that looked suspicious on a manual read-through (wrong endpoint shapes, a scoring model
that didn't match the code, cross-service call graphs that seemed backwards). Findings were
fixed directly in all 14 files — this section documents what was wrong and why it mattered,
not just that something was fixed.

### 8.2 — Operationally dangerous findings (would misdirect a debugging session, not just read oddly)

**Port table was wrong for 6 of 11 services**, in both `.claude/skill.md` and
`services/api-gateway/skill.md`. Both files listed `technical-analysis:8009`, `ranking-
engine:8007`, `decision-engine:8006`, `strategy-engine:8010`, `portfolio-optimizer:8011`,
`event-intelligence:8012` — none of which match the real ports. Verified ground truth by
grepping every service's Dockerfile/`main.py` `uvicorn.run(port=...)` directly: the correct
values (already documented correctly in `CLAUDE.md`'s "System Port Map") are
`technical-analysis:8002`, `ranking-engine:8004`, `decision-engine:8009`, `strategy-
engine:8006`, `portfolio-optimizer:8007`, `event-intelligence:8010`. The actual proxy code
(`api-gateway/src/api/proxy.py`) was never affected — it reads ports from env-var-driven
settings objects, not hardcoded literals — so this was a pure documentation bug, but a
believable one: a future session trusting either skill.md and running
`docker exec stockai-ranking-engine-1 curl localhost:8007/...` would get a connection refused
against the wrong port and could easily misdiagnose it as a service outage.

**`frontend/skill.md` said "Current highest tier: 215. Next new tier: 216."** Actual highest
tier in `improvements.tsx` is 232 — tiers 216 through 232 already exist. Following that
instruction literally would have created a colliding/duplicate tier ID the next time someone
added a tier, silently merging two unrelated sets of tracker items under one number. Replaced
the hardcoded number with the one-line `grep` command to always check the live value instead of
re-encoding a snapshot that will be stale again within days (this file previously grew from
tier ~50 to 215 to 232 without ever being updated in between).

### 8.3 — Factually wrong claims (not merely stale — described behavior that never matched, or has since reversed)

- **`research-engine/skill.md`** said research divergence "does NOT block the trade — it's
  informational." False: `paper_trading_engine.py`'s `_scan_for_entries` has a real hard-reject
  gate (`research_gating_enabled`, default `true`) that `continue`s past any candidate with an
  AVOID/SELL research recommendation — skipping it entirely, not just penalizing its score. The
  identical gate exists in `decision-engine/api/core/hard_rejects.py`. This is the same class of
  error as an earlier finding in this report (T232-DL1/DL5's falsy-zero gates) in spirit if not
  mechanism: documentation asserting something is advisory when the code actually treats it as
  load-bearing is exactly the kind of gap that leads someone to "safely" change gating logic
  elsewhere while not realizing research recommendations are already blocking real entries.
- **`ranking-engine/skill.md`**'s K-score component list (Momentum/Technical quality/Volume
  confirmation/Signal strength/Relative performance) didn't match `compute_kscore()`'s actual
  6-component weighted formula (`technical`, `momentum`, `value`, `growth`, `volatility`,
  `relative_strength` — with `value`/`growth` being the fundamentals-based, sometimes-`None`
  components at the center of this session's T232-RANKSTALE-SCHEMA fix). The doc's endpoint
  table also claimed `GET /rankings/{symbol}` requires auth and referenced two endpoints
  (`/rankings/top`, `/rankings/sector/{sector}`) that don't exist — the real, unauthenticated
  endpoints are `/rankings/screen` and `/rankings/sector_rotation`. Only `POST /rankings/refresh`
  actually requires a JWT.
- **`portfolio-optimizer/skill.md`** said AI Allocation "Calls the research engine to get
  conviction scores per symbol." It calls **ranking-engine**'s `/rankings/{symbol}` for K-scores
  instead — there is no reference to research-engine anywhere in portfolio-optimizer's source.
  Also corrected: the real `method` literal is `"hierarchical_risk_parity"`, not `"hrp"`, and
  `/portfolio/frontier`/`/portfolio/correlation` don't exist — `POST /portfolio/optimize` is the
  only endpoint this service defines.
- **`technical-analysis/skill.md`**'s consumer-mapping table claimed ranking-engine calls it for
  RSI/MACD/BB inputs to K-score. It doesn't — `compute_kscore()` computes its own RSI/ADX/
  technical-quality independently from raw OHLCV (two separate, unrelated implementations of the
  same indicators exist in the codebase). The only call ranking-engine makes to
  technical-analysis is for a cosmetic `patterns` leaderboard column, never fed into the score.
- **`event-intelligence/skill.md`** described signal-engine's and decision-engine's consumption
  of event data as "planned" (T208 / "could check catalyst_score as a volatility gate"). Both
  are already live: signal-engine calls `/catalyst/{symbol}` in two code paths and nudges
  `fused_prob` from insider/congress scores; decision-engine's `scorer.py` already has a
  `catalyst` scoring layer. T208 itself (SEC 8-K flags) is also shipped, via a direct DB read of
  `sec_filings` rather than the HTTP path the doc implied.
- **`decision-engine/skill.md`** described a fixed "9 dimensions, each 0–1.33 points, total 12"
  scoring model and a bare `POST /decide` endpoint. Neither matches `scorer.py`/`routes.py`:
  the real endpoint is `POST /decide/{symbol}` (symbol is a path param, plus separate
  `/decide/batch` and `/decide/{symbol}/explain` routes), and scoring is a variable-length list
  of conditional integer-point layers (`price_zone`, `rr_quality`, `volume`, `earnings`,
  `ml_signal`, `conf_delta`, `freshness`, `catalyst`, `pre_regime`, `entry_drift`, `research`,
  `regime`) with an unbounded total, not a fixed 0–12 range. Also flagged: `SCALP` and `INCOME`
  are defined as valid styles in decision-engine's own schema (`models.py`, `aggregator.py`) but
  do not exist anywhere in the real trading engine (`paper_trading_engine.py` only implements
  SHORT/SWING/LONG/GROWTH) — dead, unvalidated speculative styles that could mislead a future
  feature built against decision-engine's schema into assuming they're live.

### 8.4 — Line-count / size staleness (20–53% off; not wrong, just old)

Mechanical `wc -l`/`ls -la` diff against every claimed figure. Most services
(`strategy-engine`, `portfolio-optimizer`, `technical-analysis`, `api-gateway`,
`event-intelligence` except one file) were exact or within a few lines — evidently refreshed
recently and a good model for the rest. The worst offenders, refreshed in this pass:

| File | Claimed | Actual | Drift |
|---|---|---|---|
| `ml-prediction/src/features/builder.py` | ~548 | 841 | +53% |
| `market-data/src/services/scheduler.py` | ~2,628 | 3,437 | +31% |
| `market-data/src/services/paper_trading_engine.py` | ~2,957 | 3,758 | +27% |
| `shared/db/session.py` | ~368 | 446 | +21% |
| `signal-engine/src/generators/signals.py` | ~1,989 | 2,359 | +19% |
| `ml-prediction/src/training/tuner.py` | ~170 | 199 | +17% |
| `event-intelligence/src/api/routes.py` | ~223 | 261 | +17% |
| `ml-prediction/src/training/trainer.py` | ~1,179 | 1,355 | +15% |
| `shared/db/models.py` | ~891 | 979 | +10% |
| `decision-engine/src/api/routes.py` | ~333 | 369 | +11% |
| `ranking-engine/src/api/routes.py` | ~788 | 838 | +6% |

`frontend/skill.md`'s page count (37 → 41 route files, missing the `research/` nested route and
`stock/[symbol]` dynamic route entirely from its tree diagram) and file sizes (`api.ts` 78KB →
82KB, `improvements.tsx` 1.2MB → 1.43MB) followed the same undercounting pattern — consistent
with active, ongoing development that these reference docs simply hadn't kept pace with.

### 8.5 — Fixes applied

All 14 files corrected in place (no code changes — documentation only, so no deployment or
container restart was required):
`.claude/skill.md`, `shared/skill.md`, `frontend/skill.md`, and `services/{market-data,
signal-engine, decision-engine, ranking-engine, research-engine, portfolio-optimizer,
technical-analysis, event-intelligence, ml-prediction, api-gateway, strategy-engine}/skill.md`.

Beyond correcting the specific claims above, each fix added a cross-reference back to the
relevant finding in this report (e.g. `technical-analysis/skill.md` now points at
`T232-DL-DUALSCORER`-adjacent context for the strategy-engine RSI-reimplementation divergence;
`market-data/skill.md`'s config-key table now flags every key that exists via `.get()` fallback
but has no entry in `_DEFAULT_CONFIG`, per T232-DL2/DL3) so a future reader lands on the current,
verified picture rather than rediscovering the same drift from scratch. Committed as `3496cf6`,
pushed to `prod`.

### 8.6 — Process note for future sessions

Two patterns emerged worth calling out explicitly, since they'll recur:

1. **Doc staleness compounds over time.** `frontend/skill.md`'s tier number was 17 tiers behind;
   line counts were off by as much as 53%. None of these files had "wrong day one" — they were
   accurate snapshots that nobody re-verified as the code kept moving. The fix (in a few places)
   was to replace a hardcoded fact with a one-line command to re-derive it live, rather than
   re-encoding another snapshot that will be stale again.
2. **A skill.md describing a "planned" feature is a trap if nobody re-checks it after the
   feature ships.** Three separate files (`event-intelligence`, and implicitly the aspirational
   `improvements.tsx` entries T232-DL7 already found for portfolio-optimizer) described
   already-shipped integrations as future work. Anyone reading only the doc — not the code —
   would conclude a real dependency doesn't exist and could duplicate work building it "for the
   first time," or conversely worry a gate isn't applying when it already is.

---

## Part 9 — Style-Params Consolidation Fix + Live HK Trading Diagnosis (2026-07-04)

### 9.1 — T232-DL-STYLEPARAMS3X fixed: a live SHORT/LONG bug, not just documentation drift

Following the regime consolidation (Part 7.2 follow-up), tackled the second triplicated-config
item: `_STYLE_PARAMS` (per-style entry/breakout/stop/target percentages), duplicated across
`scheduler.py` (source of truth), `paper_trading_engine.py` (mirror, runtime-mutated by Optuna
tuning via `_load_tuned_params()`), and decision-engine's `aggregator.py` (independent third
copy). Reading all three in full surfaced something worse than the drifted-GROWTH-values finding
already logged: decision-engine's copy was **missing SHORT and LONG entirely** — the two real
styles that exist in the trading engine — while `paper_trading_engine.py` forwards
`cfg["trading_style"]` verbatim to `POST /decide/{symbol}` for every portfolio, including SHORT
and LONG ones. Any such portfolio using decision-engine in `"primary"` mode (the default) was
silently falling back to `_STYLE_PARAMS.get(style.upper(), _STYLE_PARAMS["SWING"])` — getting
SWING's stop/target percentages for its game plan. This is a live bug affecting real (paper)
trading decisions, found while fixing what started as a documentation-level duplication.

**Fix:** added `GET /stocks/style-params` to market-data, returning the live in-memory
`_STYLE_PARAMS` dict (reflecting any Optuna-tuned overrides currently applied — not a static
snapshot). Rewrote `aggregator.py` to fetch this (15-min cache, hardcoded 4-style fallback for
market-data outages) instead of maintaining a separate dict, removed the dead `SCALP`/`INCOME`
styles, and corrected `models.py`'s docstring to the real 4 styles. Verified the endpoint returns
correct SHORT/SWING/LONG/GROWTH values post-deploy.

### 9.2 — Live diagnosis: why aren't HK portfolios trading (repeat investigation)

User asked again why HK portfolios weren't trading, this time pointing at a specific observation:
HK AI/semiconductor names (1347.HK, 3986.HK, 9903.HK, 6613.HK, 0669.HK, 0981.HK) were showing
BUY signals with 71-89% bull confidence on the Signal Filter Monitor, but paper trading wasn't
opening positions. Full live trace (Redis `no_entry_summary`, container logs, direct DB queries)
found:

- **HK GROWTH (portfolio 4):** every AI-name candidate genuinely failed a real per-candidate
  gate — 3986.HK/9903.HK on K-Score (47.8/40.9, both below the 48.0 floor), 1347.HK/0669.HK on
  low volume, 6613.HK on stop-cooldown (recently stopped out), 0981.HK on declining confidence
  (signal degrading since generation), 2513.HK/0117.HK on TA score. Working as designed — not a
  bug.
- **HK SWING (portfolio 2):** at the moment of the report, only 0-3 qualifying candidates existed
  (confirmed via direct query: 178 HK SWING BUY signals exist system-wide, but this portfolio's
  specific gate combination — regime override active, but starting from a much smaller
  post-filter candidate pool — left very few or zero to evaluate in a given cycle). Not a bug;
  genuinely thin candidate availability at that moment.
- **Real bug found in passing:** HK SWING Portfolio's config had `max_entries_per_day: 0`. This
  does NOT block trading — every gate reading this key checks `if x and x > 0:` before
  enforcing, so `0` (falsy) **disables** the gate rather than blocking every entry, the opposite
  of what the value suggests. No code path writes `0` here programmatically; most likely cause
  is an unvalidated edit in the Config Panel's plain number input (no `min` attribute, no
  backend range check on this or 7 other count-based keys). Reset to the default (3) and added
  validation (`_MIN_COUNT_CHECKS`, backend + frontend `min={1}`) so this can't recur silently.
- **WHYNOTRADE visibility gap found and fixed:** two rejection points were never tallied in
  `_skip_tally` — the alert-system's conviction-gate cross-check (`conv_gate:{symbol}:{style}`
  Redis hard-block) and the actual entry-qualifier rejection itself (DE score below threshold,
  or `_should_enter()`'s hard rejects). A candidate rejected only at these two points showed an
  empty `top_reasons` list with no explanation — now tallied as `conviction_gate` and
  `entry_score_below_threshold` respectively.

### 9.3 — K-Score cross-system consistency check (user-requested)

User asked to double-check whether 3986.HK's K-Score disagreed between the Signal Filter Monitor
(showing "K-Score 48 below 55") and paper trading's gate (logging `kscore: 47.82, min: 48.0`).
Traced both to source: the **value** is consistent (persisted `Ranking.score` for 2026-07-03 is
`47.83`; both systems read from the same table, just via different query paths — ranking-engine's
cached `GET /rankings` HTTP endpoint for the alert/conviction-gate system, a direct SQLAlchemy
join for paper trading). The **threshold** genuinely differs by design: paper trading's
`min_kscore` is 48.0 (a trade-entry floor), while the alert system's conviction gate
(`_is_conviction_buy`, Layer 2) requires ≥55 (a stricter bar for triggering a user-facing email).
Not a bug — two different systems intentionally applying two different bars to the same
consistent underlying number. Worth remembering as a standing "why do these two K-Score
thresholds disagree" answer if it comes up again.

---

## Part 10 — Dual-Scorer Tech Debt: `_should_enter()` vs Decision-Engine (2026-07-04)

**This section is the deliverable for a future full architecture/design pass, not a list of
things fixed today.** Two safe, non-verdict-changing bugs were fixed and shipped (10.6); the
substantial remainder is deliberately left as documented debt because closing it means changing
which real (paper) trades open or don't — a decision that needs either explicit product
sign-off per item or live outcome data to validate against, neither of which exists yet. Forcing
a resolution today would trade one unvalidated behavior for another, which does not build
trust in the system — it just moves the risk around blind. This section exists so that trust can
be built deliberately, later, with eyes open.

### 10.0 — Why this pair exists and why it drifted

`_should_enter()` (`paper_trading_engine.py:1174-1424`) is the original entry-scoring function.
Decision-engine's `scorer.py`/`hard_rejects.py`/`sizer.py` were built afterward, explicitly
described in their own docstrings as extracted "faithfully from" / "mirrors ... exactly" the
original. Decision-engine is now **primary** by default (`decision_engine_mode: "primary"` in
`_DEFAULT_CONFIG`) — `_should_enter()` only runs as a fallback when decision-engine is
unreachable, or in `"legacy"` shadow mode. In practice this means: **the system's default,
everyday entry decisions are made by the newer, "mirror" implementation, while the original is
now the rarely-exercised fallback path** — the opposite of what "mirror" implies once drift sets
in, since a change to the original after the mirror was written never propagates.

### 10.1 — Hard-reject conditions that exist ONLY in `_should_enter()`'s pipeline (absent from decision-engine)

These represent real risk exposure gaps when decision-engine is primary — decision-engine can
approve an entry that the fallback pipeline would have blocked, for the following reasons:

1. **Premarket/signal gap-up filter** (`max_entry_gap_pct`, 4%) — rejects if live price already
   gapped >4% above the signal's reference price. Decision-engine only has an *extended-move*
   guard measured from `breakout` (a different reference point, 6% threshold) — not equivalent.
2. **Macro/economic-calendar blackout** (FOMC/CPI/NFP/PCE within 2h) — zero equivalent in
   decision-engine.
3. **Stop-cooldown** (120h after a stop-out on the same symbol) — enforced upstream in
   `_scan_for_entries`, never forwarded to decision-engine's `config_overrides`.
4. **K-Score / Ranking gate** (`require_kscore`, `min_kscore`) — decision-engine never receives
   or checks `Ranking.score` as a gate at all.
5. **Signal staleness hard gate** (`max_signal_age_hours`=72h) — decision-engine's scorer only
   *scores* freshness (±1 pt), never hard-rejects on it; the pipeline hard-rejects before even
   calling `_should_enter()`, but decision-engine's standalone endpoint has no such pre-filter.
6. **Multi-timeframe confluence fail** (GROWTH/LONG BUY contradicted by SHORT SELL) — absent.
7. **Price drift from signal price** (`max_price_drift_pct`=3%) — distinct from the gap filter
   and the extended-move guard; absent from decision-engine.
8. **Low-volume hard skip** — decision-engine's scorer only scores `volume_z`, never hard-skips.
9. **HK Stock Connect flow gate** — zero Stock-Connect awareness in decision-engine.
10. **TA-score gate** — no TA-score check anywhere in decision-engine.
11. **Declining-confidence hard skip** — decision-engine only scores `confidence_delta`.
12. **Conviction-gate cross-check** (Redis `conv_gate:{symbol}:{style}` — must agree with the
    alert system's independent 5-layer conviction gate) — runs entirely upstream of both
    scorers; decision-engine never sees it.
13. **Regime risk_off hard gate + multi-day regime-suspension circuit breaker** — decision-engine
    hard-rejects `bear` but has no `risk_off` hard block (only a stiffened score floor) and no
    regime-suspension streak tracking at all.
14. **Equity-floor circuit breaker** (halt all entries below 80% of initial capital) — absent.
15. **Index trend-gate** (SPY/HSI down >1.5% intraday → block all entries) — absent.
16. **Heat-brake** (N stops within 48h → pause all entries) — absent.
17. **Cross-portfolio symbol cap + market-cluster cap** — absent.
18. **Sector concentration ($ exposure, `max_sector_pct`)** — partially fixed 2026-07-04 (see
    10.6); the count-based cap now works, the dollar-exposure cap still cannot be reconciled
    without decision-engine receiving live per-position prices it doesn't currently get.

### 10.2 — Hard-reject conditions that exist ONLY in decision-engine (absent from `_should_enter()`)

These make decision-engine, when used as primary, *more* conservative than the fallback would
be — asymmetric risk (fails toward fewer trades, not more):

19. **Market-closed / trading-session guard** (weekends, NYSE holidays, HK lunch) — `_should_enter()`
    itself has zero market-hours awareness (enforced elsewhere in the pipeline via a separate
    flag, but not inside the scoring function itself).
20. **Time-of-day gate** (blocks first 30 min / last 15 min of session) — absent from `_should_enter()`.
21. **Extended-move/chasing hard reject** (>6% above breakout) — `_should_enter()` only
    *penalizes* this (−3 score), never hard-blocks; a sufficiently bonus-heavy candidate could
    still pass `_should_enter()`'s score gate at 15%+ above breakout while decision-engine would
    unconditionally block it at 6%.
22. **Regime-based R:R stiffening as a hard reject** (3.0:1 minimum in choppy/risk_off) —
    `_should_enter()` only raises the *score threshold* for these regimes, never the R:R
    hard-reject floor itself (which stays flat at `min_rr_ratio`, default 2.0, regardless of
    regime).

### 10.3 — Scoring-layer / verdict-affecting differences (same input, different treatment)

These are the highest-risk items — both sides compute something related to the same signal, but
via different mechanisms, meaning the same candidate can score differently and cross the
ENTER/SKIP threshold differently depending on which system evaluates it:

23. **Calibrated logistic-regression decision boundary** — once ≥100 closed trades exist,
    `_should_enter()` abandons the additive `score >= min_entry_score` comparison entirely in
    favor of a sigmoid win-probability model (`_load_entry_weights`). Decision-engine has no
    equivalent and never adopts this regardless of how much trade history accumulates — meaning
    the "primary" system is permanently frozen on the original, cruder threshold logic even
    after the fallback has statistically progressed past it.
24. **Regime double-counting in decision-engine** — decision-engine both (a) raises the min-score
    floor for choppy/risk_off/low-win-rate (`min_score_for_regime`) AND (b) penalizes the
    additive score directly via a regime scoring layer (bull=+1 … risk_off=−2, bear=−99).
    `_should_enter()`/its pipeline only does (a) — raises the bar, never penalizes the score
    itself for regime. This means decision-engine's effective regime penalty in choppy/risk_off
    is compounded (stricter bar + lower score) versus `_should_enter()`'s single-lever approach.
    Can flip verdicts for candidates near the threshold.
25. **Research recommendation: scoring layer in decision-engine, sizing-only in `_should_enter()`**
    — decision-engine scores research recommendation directly (±2 pts, verdict-affecting).
    `_should_enter()` never scores it at all — research only affects position size and a
    separate, redundant hard AVOID/SELL gate that lives in the *caller*, not inside
    `_should_enter()` itself. Same signal, different mechanism, different verdict sensitivity.
26. **Cross-horizon consensus: verdict-affecting in `_should_enter()`, sizing-only in decision-engine**
    — the reverse of #25. `_should_enter()` scores `cross_style_buys` directly (±1, verdict-affecting);
    decision-engine's `compute_score()` never references it at all — only `sizer.py`'s
    `consensus_mult` (position size) uses it. A candidate with 0 cross-horizon BUYs in a choppy
    regime gets a verdict-affecting penalty under `_should_enter()` but none under decision-engine.
27. **Insider/congress catalyst scoring ceiling** — `_should_enter()` scores insider and congress
    signals as two independent fields, allowing up to **+2** if both fire simultaneously.
    Decision-engine collapses them into one `catalyst_score` field, capping the contribution at
    **+1 or −1**. Same underlying data, different point ceiling.
28. **Entry-zone drift scoring layer unique to decision-engine** — `scorer.py` has a distinct
    continuous drift-from-`entry2` scoring layer (−2 to +1) that `_should_enter()` never
    computes at all (its own price-zone scoring uses different, coarser cutoffs).
29. **Pre-regime early warning: score-threshold bump only vs. direct scoring layer** —
    `_should_enter()`'s caller raises `min_entry_score` when `is_pre_choppy`/`is_pre_risk_off`;
    decision-engine scores it as a direct −1 layer. Directionally similar, numerically different
    mechanism — not guaranteed to produce the same net effect at the margin.
30. **Recent win-rate floor bump** — decision-engine's `min_score_for_regime` adds +1 to the
    score floor when `recent_win_rate < 0.30`; no equivalent exists anywhere in `_should_enter()`
    or its caller.
31. **HMM bear-pressure sizing dampening** — exists only in `_scan_for_entries` (0.70× cap when
    `hmm_bear_pressure` is true); `sizer.py`'s `_REGIME_MULT` table has no HMM awareness at all.
32. **LLM (Claude) scoring layer** — decision-engine optionally applies an LLM-based score
    adjustment (`llm_scoring_enabled`) that `_should_enter()` has no equivalent for, meaning
    decision-engine's verdict can be swayed by an input the fallback path can never replicate
    even when it takes over.

### 10.4 — Numeric threshold mismatches (same concept, different default)

33. **`min_confidence` standalone defaults**: decision-engine's own default is 62.0
    (`routes.py`); `_should_enter()`'s is 45.0 (`_DEFAULT_CONFIG`). In practice the real caller
    (`_call_decision_engine`) always forwards the correct per-portfolio value, so this default
    mismatch is masked for the primary call path — but it means any OTHER caller of
    `/decide/{symbol}` (manual API test, a future consumer, `/decide/{symbol}/explain` which
    passes zero overrides) gets a materially stricter confidence floor (≈55.8 vs ≈40.5) than the
    real trading engine would apply.
34. **Confidence-sizing multiplier scale**: `_scan_for_entries`' inline sizing uses 50/30
    breakpoints; `sizer.py` uses 80/62 — a deliberate, acknowledged rescale (see the T232-DE2
    comment in `sizer.py`), not accidental drift, but it means the two "identical" position
    sizes are computed on entirely different confidence scales.
35. **Earnings multiplier does not compound into decision-engine's max-position-pct cap** —
    `_scan_for_entries` scales the position cap itself by the earnings de-risking multiplier;
    `sizer.py`'s cap is flat regardless of earnings proximity, making decision-engine's cap
    looser near earnings than the real engine's.

### 10.5 — Structural/architectural gap

36. **Nine of the pipeline-level hard gates in 10.1 (#3-12) run only inside `_scan_for_entries`,
    upstream of BOTH scorers, and are architecturally invisible to decision-engine's standalone
    `/decide/{symbol}` endpoint.** They currently "work" only because the one production caller
    (`_call_decision_engine`) happens to run them first and only calls decision-engine for
    candidates that already passed. Any *other* caller of `/decide/{symbol}` — manual testing,
    a future integration, the frontend's `decide.tsx` analysis tool — bypasses all nine
    protections entirely, since decision-engine has no way to know about stop-cooldowns, K-Score
    floors, staleness, confluence, price drift, volume, HK-flow, TA-score, or declining
    confidence unless they're explicitly threaded through `config_overrides` (currently, none
    are, except the sector-count fix from 10.6). This is the deepest issue in this section: it's
    not a value mismatch, it's a *pipeline topology* mismatch, and fixing it properly likely
    means either (a) moving these nine checks into `hard_rejects.py` so decision-engine is
    self-sufficient, or (b) formally documenting `/decide/{symbol}` as "only safe to call after
    the market-data pipeline's pre-filters," which is the de facto but undocumented contract today.

### 10.6 — Fixes actually applied 2026-07-04 (the safe subset only)

Two fixes were made, chosen specifically because they are **strictly safe** — they cannot cause
decision-engine to approve something it previously rejected, or vice versa, in a way that trades
one unvalidated risk for another:

- **Dead sector-cap input wired up** (`hard_rejects.py`): `paper_trading_engine._call_decision_engine`
  has always sent `open_sector_counts`/`candidate_sector` inside `config_overrides`, but
  `check_hard_rejects()` never read them — decision-engine had silently zero
  sector-concentration protection despite the caller believing it was providing that data. Added
  a count-based hard reject (`sector_count >= max_sector_positions`) using exactly the data
  already being sent, mirroring `paper_trading_engine.py`'s own count-based sector cap. The
  dollar-exposure cap (`max_sector_pct`) could NOT be reconciled the same way — it requires live
  per-position prices decision-engine's request payload doesn't carry — so that half of the gap
  (10.1 #18) remains open. Verified live: a candidate at 3/3 sector positions is now correctly
  blocked; one at 1/3 passes through.
- **`sizer.py`'s docstring corrected**: it claimed to mirror `_scan_for_entries()`'s sizing
  formula "exactly" — provably false per 10.3/10.4 above, and the file's own inline comments
  already acknowledged at least one deliberate divergence (the confidence-scale rescale). Fixed
  the docstring to describe it as a related-but-independent model with a pointer to this section,
  so a future reader doesn't trust a false "exact mirror" claim the way this session's earlier
  audit trusted (and then had to correct) several other stale claims in service `skill.md` files.

**Neither fix changes any ENTER/SKIP verdict for any currently-passing candidate** — the sector
fix can only newly *block* a candidate that was previously (incorrectly) approved despite being
over the sector cap; the docstring fix changes no runtime behavior at all. This is why they were
judged safe to ship without a design review, while everything in 10.1-10.5 was not.

### 10.7 — Recommended shape of a future design pass

Not prescribing the answer here — that's the point of leaving this as debt — but the audit
surfaced enough structure to scope the eventual work:

1. **Decide which system is the long-term source of truth.** Right now decision-engine is
   "primary" by config default but was originally the derivative/mirror implementation. Pick
   one direction: either decision-engine absorbs the missing nine pipeline gates (10.1 #3-12)
   and the macro/gap-filter protections (10.1 #1-2) so it's truly self-sufficient and
   `_should_enter()` can be retired to pure-fallback status, or `_should_enter()` is
   re-established as primary and decision-engine becomes the analysis/explain-only tool it's
   already used for in the frontend (`decide.tsx`).
2. **Resolve the verdict-affecting scoring differences (10.3) with outcome data, not judgment
   calls.** Items #23-32 each represent a real hypothesis about what should score higher or
   lower — the calibrated logistic model in particular is specifically designed to be validated
   empirically. Before porting any of these in either direction, gather a controlled comparison
   (e.g. run both scorers in shadow mode for a period, log both verdicts, compare against actual
   trade outcomes) rather than picking a side by inspection.
3. **Close the R:R/extended-move asymmetry (10.2 #21-22) as a standalone, low-risk follow-up.**
   These are both cases where decision-engine is MORE conservative — porting them to
   `_should_enter()` (fail-safe direction, same asymmetric-risk logic as 10.6) is a reasonable
   next increment once the sector-cap pattern from 10.6 is validated in production for a while.
4. **Fix the pipeline-topology gap (10.5 #36) regardless of which system wins in #1.** Whatever
   system ends up primary needs to be safe to call standalone — the current implicit contract
   ("only call `/decide/{symbol}` after market-data's pre-filters already ran") is fragile and
   undocumented anywhere except this report.

---

## Part 11 — Remaining Open High-Severity Bugs (2026-07-04): problem, fix, and expected improvement

Three of eight open high-severity items from the earlier verification pass were fixed and shipped
(`T232-ML2`/`ML3`/`ML4`, see Part 9 continuation / the `47c24e5` commit). The five below are
confirmed still live as of 2026-07-04 but not yet fixed — documented here with the specific
mechanism, the proposed fix, and what measurably improves once each ships, so a future session (or
a reviewer deciding what to prioritize) doesn't have to re-derive this from the tracker's terser
one-line summaries.

### 11.1 — T232-SIG10: SELL threshold asymmetry (SELL wins only 43.7% live) — PARTIALLY FIXED 2026-07-04

**Problem.** BUY and SELL signals are scored on fundamentally unequal footing.
`_STYLE_PROFILES[style]["buy_threshold"]` is a 4-way dict keyed by regime (`bull`/`high_vol`/
`bear`/`unknown`), tuned separately for each — e.g. SWING requires `fused > 0.72` in bull, `0.76`
in bear. The SELL side has no equivalent structure: `sell_t` falls back to a flat `0.35`
regardless of regime (`signals.py` ~line 1466). There is also no bearish-pillar gate symmetric to
`min_pillars_for_buy` (which requires ≥2 confirming bullish signals before a BUY fires) — a SELL
can fire off a single weak bearish nudge. Compounding this, the bullish-side nudges in the fusion
formula (breakout +0.05, options +0.04, pullback +0.07, K-Score +0.08) outnumber and outweigh the
bearish-side nudges, further tilting the system toward firing BUY less cautiously than SELL.

**Measured live impact:** SELL fires 2.3× as often as BUY and wins only 43.7% overall (US SHORT
SELL specifically: 33.3%, n=72) — worse than a coin flip for the highest-volume SELL cohort. SELL
accuracy also decays sharply with horizon: 70% correct at 5 days, down to 37% at 20 days, meaning
the signal has real short-term edge that the current scoring/horizon design throws away by
treating all SELL horizons the same way BUY horizons are treated.

**Proposed fix:** (1) regime-tier `sell_t` the same way `buy_threshold` is tiered — e.g. SWING
bull `0.32` (easier to SELL when the broader trend is against a long) / bear `0.38` (harder to
pile onto an already-bearish tape); (2) add a `min_pillars_for_sell` gate mirroring
`min_pillars_for_buy`; (3) shorten the SELL scoring/evaluation horizon to 5-10 days, where the
live-data accuracy decay curve shows the signal actually retains edge, instead of scoring it at
the same horizons as BUY.

**Expected improvement:** closing the asymmetry should reduce SELL fire rate toward BUY's rate
(fewer, higher-conviction SELL signals) and should lift win rate above the current 43.7% by
filtering out the single-pillar, wrong-horizon SELLs that are dragging the average down — the
70%-at-5-days number suggests real signal exists, it's just currently diluted by low-quality
long-horizon SELL calls sharing the same threshold.

**Re-investigation and partial fix (2026-07-04):** before implementing all three proposed fixes
blind, re-checked the live outcome data to see whether it actually supports specific regime-tier
threshold values. It does not, for two of the three items:

- **Horizon shortening (item 3): supported by data, implemented.** Re-measured SELL accuracy
  by window: 57.6% at 5 days, 58.6% at 10 days, dropping to 48.5% at 20 days (the direction
  matches the original 70%→37% claim; the magnitude differs, likely due to more outcome data
  having accumulated since the original measurement). Added `_SELL_OUTCOME_HOLD_DAYS`
  (SHORT=5, SWING=7, LONG=10, GROWTH=7 calendar days) alongside the existing `_OUTCOME_HOLD_DAYS`
  (unchanged for BUY), so SELL's primary `is_correct`/`pct_return`/`exit_date` fields — the ones
  `outcomes/calibrate` and every accuracy-reporting endpoint actually key off — are evaluated at
  the window where the data shows real signal, not diluted by a 14-28 day window where SELL's
  edge has already eroded. Does not touch BUY's windows, does not rewrite any historical
  `SignalOutcome` row (`signal_id` is `UNIQUE`, so only newly-generated SELL signals evaluate
  under the new windows going forward — a live-observed re-measurement, not a retroactive
  rewrite of the record). Verified live: triggered `outcomes/evaluate` against production
  (425 signals evaluated, zero errors) and confirmed the new table loads correctly in the
  running process.

- **Regime-tiered `sell_t` (item 1): investigated, found unsupported by current data, deferred.**
  A regime breakdown of SELL outcomes shows **96%+ of all SELL outcome rows are from `bull`-regime
  periods only** — bear/choppy/risk_off have single-digit or zero sample counts across every
  horizon. There is nothing to calibrate a bear-regime or risk_off-regime SELL threshold against
  yet. Additionally, sweeping `fused_prob` buckets within the bull-regime data (the only regime
  with real sample size) shows a noisy, non-monotonic relationship to win rate — no clean
  threshold value emerges. Inventing regime-tier numbers anyway (e.g. by mirroring BUY's
  bull/bear spread) would repeat the exact "overfit argmax on thin/absent data" failure mode
  already documented as `T232-OC3` in this same report — the opposite of what re-checking before
  acting is meant to prevent. Deferred until enough non-bull-regime SELL outcomes accumulate to
  calibrate against.

- **`min_pillars_for_sell` gate (item 2): investigated, found to require new feature engineering,
  deferred.** Reading the pillar-gate code directly surfaced a prior, deliberate decision
  (`T232-SIG3`, already in the codebase) that explicitly excludes SELL candidates from the
  existing pillar gate — because `independent_pillars_active` counts **bullish** evidence only
  (trend/momentum/volume/structure pillars scored `>= 0.5`), and applying a bullish-evidence gate
  to bearish candidates was found to erroneously compress genuine SELL signals back toward
  neutral. A real, symmetric `min_pillars_for_sell` needs its own bearish-evidence pillar count
  (e.g. counting pillars `<= 0.5` as bearish-confirming) — a genuine feature addition requiring
  its own validation against outcome data, not a parameter tweak to the existing gate. Deferred
  as a separate, larger piece of work rather than bolted on without validation.

### 11.2 — T232-SIG6: TA weight calibration never reaches the running process — FIXED 2026-07-04

**Problem.** `POST /calibrate_ta_weights` writes newly-calibrated weights to `ta_weights.json`
and to Redis (`stockai:ta_weights`, 90-day TTL) — but `_ta_weights`/`_ta_weights_calibrated`, the
actual module-level globals `_ta_score()` reads on every call, are only set once at import time
(`signals.py:247-248`). The endpoint updates two persistence layers and reports success, but the
values a live signal computation actually uses never change until the container is manually
restarted. An admin who runs weekly TA-weight calibration can be operating for days or weeks on
the belief that new weights are live when the process is silently still running the old ones.

**Proposed fix:** either (a) have `calibrate_ta_weights` reassign the module globals directly
under a lock immediately after writing to Redis/file, so the change is live for the current
process the moment calibration completes, or (b) change `_ta_score()` to read from a short-TTL
in-process cache backed by Redis (matching the pattern used elsewhere in this codebase for
similar "calibrated value read hot-path" problems) so a restart is never required for a
calibration to take effect.

**Expected improvement:** this doesn't change what the *correct* weights should be — it closes
the gap between "calibration ran and reported success" and "the running signal-generation process
is actually using the new weights." Without this fix, the entire TA-weight calibration mechanism
is only as good as how often the container happens to restart for unrelated reasons — a
mechanism this session's audit already found market-data restarts semi-regularly for (deploys,
crash-loops), so the staleness window in practice ranges from hours to potentially weeks.

**Fix applied 2026-07-04:** added `set_ta_weights()` to `signals.py`, mirroring the existing
`set_ml_weight_global_cap()` reassign-under-lock pattern already used for the analogous
ML-weight-cap calibration in the same file. `calibrate_ta_weights()` now calls
`set_ta_weights(new_weights)` immediately after persisting to file and Redis, updating the
in-process `_ta_weights`/`_ta_weights_calibrated` globals the running server process reads on
every `_ta_score()` call. Verified live end-to-end against production: triggered a real
calibration run (3,457 signals in the lookback window, 1,276 usable after price-lookup
filtering, 53.96% in-sample accuracy) and confirmed the returned weights differed materially
from the pre-calibration in-process values (`above_sma50`: 0.0 → 0.1808, `sma50_above_sma200`:
0.3137 → 0.0). Confirmed via container logs that the calibration request carried a real
`request_id` (i.e. executed inside the actual HTTP-serving FastAPI process, not a standalone
script) — proving the fix closes the gap for the exact process that matters, not just a fresh
reload that would have worked even with the old buggy code.

### 11.3 — T232-DE1: VIX double-counted in decision-engine's position sizing

**Problem.** `sizer.py`'s `compute_position()` composes 7 independent multipliers by straight
multiplication: `earnings_mult * regime_mult * confidence_mult * research_mult * consensus_mult *
breadth_size_mult * vix_size_mult`. Two of these — `regime_mult` and `vix_size_mult` — are not
independent: `regime_mult` is partly *derived from* VIX (VIX≥25 triggers `risk_off`, giving
`regime_mult=0.50`; VIX≥30 triggers `bear`, giving `regime_mult=0.00`), while `vix_size_mult` is a
separate continuous VIX gradient (`max(0.5, 1-(vix-20)/30)`) computed independently and multiplied
in again. At VIX=30 this produces `0.50 × 0.667 ≈ 0.335` combined — the same underlying market
signal (elevated volatility) is punishing the position size twice. Compare to
`paper_trading_engine.py`, which composes market-wide signals via `min()` (take the single most
conservative signal, don't compound them) rather than multiplying every one together. Worst
realistic stack across all 7 multipliers: `0.50 × 0.60 × 0.50 × 0.60 × 0.50 × 1.25 ≈ 0.056` — a
position sized at 5.6% of what the base formula intends, small enough to be pure noise (slippage
and commission could exceed the position's own expected value) while still consuming one of the
portfolio's limited `max_positions` slots.

**Proposed fix:** separate the 7 multipliers into two groups — market-wide/systemic signals
(`regime_mult`, `breadth_size_mult`, `vix_size_mult`, all three ultimately describing "how
dangerous is the broad market right now") composed via `market_mult = min(regime_mult,
breadth_size_mult, vix_size_mult)` instead of multiplication, and idiosyncratic/per-trade signals
(`research_mult`, `confidence_mult`, `consensus_mult`, `earnings_mult`) that remain multiplied
together since they genuinely are independent judgments about this specific trade. Also add an
explicit floor: skip the entry entirely (not just size it small) when the combined multiplier
drops below some threshold (e.g. 0.30) — a position too small to matter shouldn't occupy a
position-count slot other, better-sized candidates could use.

**Expected improvement:** eliminates a compounding-risk-signal bug that currently produces
economically-meaningless micro-positions specifically during the highest-volatility periods —
exactly when a trader would want either a normal-conservative position or no position at all, not
a position too small to be worth the slippage. Also frees up `max_positions` capacity during
volatile periods for candidates that clear a reasonable size floor.

### 11.4 — T232-OC3: signal threshold calibration has no holdout, applied straight to production

**Problem.** `POST /outcomes/calibrate/apply`'s threshold search sweeps 46 overlapping cumulative
subsets of the same sample and takes the argmax expected value — with `min_samples=15` and a
minimum EV-lift gate of just 0.1 percentage points (within typical sampling noise for n=15). At
n=15, the standard error on a win-rate estimate is roughly ±13 percentage points — an argmax
search over 46 correlated, overlapping subsets at that noise level is close to guaranteed to
surface an upward-biased fluke as the "optimal" threshold, and that threshold is then applied
directly to production with no independent validation. This is the textbook "overfit calibration"
scenario already documented elsewhere in this report as CAL-1's root cause pattern (Part 1) — this
finding shows the same failure mode still exists in a sibling endpoint. Notably, `calibrate_ml_weight`
(the ML-weight calibration endpoint, a close sibling in the same file) already does this correctly
— a genuine chronological 70/30 calibration/validation split — so the fix pattern already exists
in the codebase, it's just not applied consistently to every calibration endpoint.

**Proposed fix:** reuse `calibrate_ml_weight`'s existing 70/30 temporal split pattern for the
outcome-threshold sweep; raise the effective minimum sample size at the *winning* threshold to
≥50 (not just the `min_samples` floor for inclusion in the sweep at all); require the EV lift to
exceed some multiple (e.g. 2×) of its own bootstrap standard error before being considered a real
signal rather than noise, rather than a flat 0.1pp floor that doesn't scale with sample size.

**Expected improvement:** this is the same class of fix as the walk-forward split proposed in
`docs/DESIGN_SELF_IMPROVEMENT_LOOP_2026-07-04.md`'s Phase 1 — in fact, this finding and that
design's Phase 1 target the same underlying endpoint and the same underlying defect. Fixing this
should reduce the frequency of corrupted-threshold incidents like CAL-1 (Part 1), where a Sunday
calibration run silently loosened a production threshold for weeks before being caught by manual
inspection rather than any automated check.

### 11.5 — T232-PT4: CORRECTED 2026-07-04 — original premise was factually wrong, closed as working-as-designed

**Original claim (Part 11.5 as first written):** the alert system's conviction gate cross-check
hard-blocks paper entries using a confidence floor (`≥60`) stricter than portfolios' own
`min_confidence` (45 GROWTH / 50 SWING), invisibly suppressing candidates in the 45-59 band.

**Re-investigation (2026-07-04):** before implementing the proposed fix, read `_is_conviction_buy()`
in full and cross-checked live production Redis data. Both directly contradict the original
claim: `_is_conviction_buy()`'s BUY-path logic checks K-Score≥55, uptrend structure, RSI range,
MACD momentum, OBV volume confirmation, and ADX trend strength — **there is no confidence
threshold anywhere in this function's logic**. A separate confidence check (regime-tiered,
58-68) does exist in `scheduler.py`, but it lives in an entirely different code path — the
non-BUY "bullish improvement" alert case (e.g. WAIT→HOLD transitions) — and is never consulted
by the BUY-path gate that writes to `conv_gate:{symbol}:{style}`. Live data confirms this: a
sampled real Redis entry for a failed BUY candidate (PLTR, 2026-07-03) shows failed layers of
K-Score/uptrend/OBV/ADX/Stoch-RSI-overbought — no confidence-related failure appears anywhere.
The original audit conflated two unrelated code paths' thresholds into one incorrect claim.

**Also stale:** the "invisibly (no `_write_gate_block`)" half of the original complaint. This
exact block is already tallied as `_skip_tally["conviction_gate"]`, added earlier in this same
session as part of closing the T232-WHYNOTRADE visibility gap (see Part 9.2) — it surfaces in
the "Not trading: {reason}" UI display like every other per-candidate skip reason today.

**Disposition:** closed, no code change. What remains is a legitimate cross-check that a BUY
candidate's underlying technical quality (trend, momentum, volume, K-Score, trend strength) is
sound before paper trading commits capital to it — not an arbitrary, mismatched confidence bar
being silently imposed by an unrelated system. Downgraded from high to low severity in the
tracker. This entry is kept in the audit report specifically as a documented example of why
every finding in a large audit needs re-verification against current code and live data before
acting on it — exactly the discipline already applied elsewhere in this report (e.g. the
`rl_agent.py` correction in the service-architecture design doc's Part 2).
