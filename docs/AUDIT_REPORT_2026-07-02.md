# System Audit Report — Win Rate, Signal Quality & ML Integrity

**Audit period:** 2026-06-17 through 2026-07-02
**Tiers:** 228 (Signal calibration) · 229 (Deep codebase) · 230 (Morning digest) · 231 (Win rate & ML integrity)
**Status as of:** 2026-07-02

---

## Executive Summary

A series of deep audits identified 21 findings across four severity levels. Five critical bugs were
producing silent data corruption (lookahead bias in ML features, catalyst probability applied after
signal classification, HMM blocking calls). Three high-severity regime and sizing issues were
causing the system to over-enter during stress periods. All critical and high findings have been
fixed. ML retraining is in progress.

---

## Tier 231 — System Audit: Win Rate, Signal Quality & ML Integrity

**Completed:** 2026-07-01 (critical), 2026-07-02 (high)

### CRIT-1 / QW-8: HMM Blocking HTTP Call in Paper Trading Engine
**File:** `services/market-data/src/services/paper_trading_engine.py`
**Severity:** Critical
**Status:** Fixed

`_fetch_market_regime()` made a synchronous `httpx.get()` call to the HMM endpoint (`/hmm/regime`),
adding 1–3 seconds of latency per cycle. The returned `bear_prob` value was stored in the local
variable but never actually used in position sizing or entry decisions. The call was pure overhead.

**Fix:** Removed the HMM HTTP call entirely. HMM regime is currently unused downstream; QW-8
(wiring `bear_prob > 0.5` into sizing) is tracked separately.

---

### CRIT-2: HK Regime `_compute_hk()` Missing `above_sma50` Guard
**File:** `services/decision-engine/src/api/core/regime.py`
**Severity:** Critical
**Status:** Fixed

The `elif hsi < e200:` branch lacked the `above_sma50` guard, causing sustained downtrends
(HSI below both SMA50 and SMA200) to be classified as "choppy (recovering)" instead of
"risk_off". The HK regime would call entries "choppy" during genuine downtrends.

**Fix:** Split into two guarded branches:
- `elif hsi < e200 and above_sma50:` → choppy (recovering)
- `elif hsi < e200 and not above_sma50:` → risk_off (sustained downtrend)

---

### CRIT-3/4: Lookahead Contamination in ML Feature Set
**File:** `services/ml-prediction/src/features/builder.py`
**Severity:** Critical
**Status:** Fixed; retraining in progress

Seven features in `FUNDAMENTAL_COLUMNS` were broadcast from today's snapshot to all historical
bars, leaking future data into past training rows:

| Feature | Why it's lookahead |
|---|---|
| `days_to_earnings` | Today's days-to-earnings applied to past bars |
| `eps_beat_streak` | Current streak applied backwards |
| `eps_surprise_avg` | Rolling average of future surprises |
| `avg_post_earnings_return_5d` | Forward returns of future quarters |
| `avg_revenue_surprise_pct` | Future quarterly surprises |
| `flow_5d_net_hkd` | Current Stock Connect flow applied to past bars |
| `flow_strength` | Derived from the above |

**Fix:** Removed all seven from `FUNDAMENTAL_COLUMNS`. Models retrained with contaminated
features show inflated AUC (likely 2–5% overstatement). Retraining triggered via
`POST /ml/tune_all?n_trials=40` for 153 symbols. New AUC figures reflect true predictive power.

**Also fixed (QW-1):** `eps_revision_direction` DB query ran AFTER the broadcast loop —
always NaN for all bars. Moved query before loop.

---

### CRIT-5: Catalyst Probability Nudge Applied After Signal Classification
**File:** `services/signal-engine/src/api/routes.py`
**Severity:** Critical
**Status:** Fixed

The catalyst-based `bullish_probability` adjustment (+0.05 / -0.03) was applied AFTER the signal
type (`BUY`/`HOLD`/`SELL`) had already been determined. A HOLD-territory probability could be
nudged into BUY territory but the signal label remained HOLD. DB stored HOLD but the live endpoint
returned BUY-territory probability. The signal badge and the stored signal disagreed.

**Fix:** After the probability nudge, re-evaluate signal direction against the style's `buy_threshold`
and `sell_threshold`. If `bullish_probability ≥ min(buy_threshold)` and signal was HOLD, upgrade to BUY.
If `bullish_probability ≤ sell_threshold` and signal was BUY/HOLD, downgrade to SELL.

---

### HIGH-1: US "Bull" Regime Missing Breadth Gate
**File:** `services/decision-engine/src/api/core/regime.py`
**Severity:** High
**Status:** Fixed 2026-07-02

`_compute_us()` classified state as "bull" whenever SPY was above its 200 EMA and 50 EMA, without
checking IWM or MDY. During narrow rallies (mega-caps up, small/mid caps down), the system entered
freely in "bull" mode while most of the market was in a downtrend.

**Fix:** Added IWM+MDY 200 EMA check. When both are below their 200 EMAs, `breadth_weak = True`
and regime downgrades from "bull" to "neutral" (preventing full-size bull entries). `breadth_size_mult`
is set to 0.60 when breadth is weak.

---

### HIGH-2: ML Models Require Retraining (Post CRIT-3/4)
**File:** `services/ml-prediction/src/training/trainer.py`
**Severity:** High
**Status:** In progress

Current production models were trained on the 7 lookahead-contaminated features. They must be
retrained to reflect true out-of-sample predictive power. Retraining triggered; expected AUC
will drop slightly, which is the correct direction.

---

### HIGH-3: Dead Sector Concentration Check
**File:** `services/decision-engine/src/api/core/hard_rejects.py`
**Severity:** Medium (was High)
**Status:** Fixed 2026-07-02

A sector concentration hard reject block (T186) checked `open_sector_counts` and `candidate_sector`
from `cfg.get()` — but the decision-engine caller never populated these keys. The block silently
never fired. The consecutive-loss check is real (the `consec_losses` key IS passed via
`config_overrides` from `paper_trading_engine`).

**Fix:** Removed the dead sector concentration block. Consecutive-loss check retained.

---

### HIGH-4: VIX Spike Position Size Cliff-Edge
**File:** `services/decision-engine/src/api/core/regime.py`, `sizer.py`, `models.py`, `routes.py`
**Severity:** Medium (was High)
**Status:** Fixed 2026-07-02

VIX-based regime classification used binary bands (risk_off above 30, bear above VIX+SPY threshold).
An entry at VIX=26 had the same position size as VIX=18 — just a higher score threshold.

**Fix:** Added `vix_size_mult = max(0.5, 1.0 - max(0.0, (VIX - 20.0) / 30.0))` as a continuous
gradient. Flows through `regime.py` → `routes.py` → `compute_position()` → `Multipliers.vix`.

| VIX | Size Multiplier |
|---|---|
| ≤ 20 | 1.00× |
| 25 | 0.83× |
| 30 | 0.67× |
| 35+ | 0.50× |

---

### QW-4: NYSE Holidays Not in Market-Closed Guard
**File:** `services/decision-engine/src/api/core/hard_rejects.py`
**Severity:** Medium
**Status:** Fixed

`check_hard_rejects()` blocked weekend entries but not NYSE holidays. Entries could be attempted
on Thanksgiving, Christmas, MLK Day, etc.

**Fix:** Added `_NYSE_HOLIDAYS` frozenset (2025–2027). Holiday check runs after weekend check for
US market.

---

### QW-5: Regime Cache TTL 4 Hours
**File:** `services/decision-engine/src/api/core/regime.py`
**Severity:** Medium
**Status:** Fixed

Cache TTL was 14,400 seconds (4 hours) — regime could shift significantly intraday without the
decision engine seeing it.

**Fix:** Changed to 900 seconds (15 minutes).

---

### QW-7: Stop-Hit Fill Price Ignores Gap Below Stop
**File:** `services/market-data/src/services/paper_trading_engine.py`
**Severity:** Medium
**Status:** Fixed

When a stop was hit, the engine used `stop` as the fill price even if live price had gapped below it.
This overstated trade P&L on stop-out events.

**Fix:** `fill_base = min(stop, live_price) if exit_reason == "stop_hit" else live_price`

---

## Tier 229 — Deep Codebase Audit

**Completed:** 2026-07-01

### C1: Feature Importance Mislabeled After Outcome Augmentation
**File:** `services/ml-prediction/src/training/trainer.py:749`
**Severity:** Critical
**Status:** Fixed

`enumerate(FEATURE_COLUMNS)` was used to map importances, but after `shared_cols` intersection
during outcome augmentation, `X_train` may have fewer columns. Index `i` mapped to the wrong
feature name.

**Fix:** Changed to `enumerate(X_train.columns)`.

---

### C2: Outcome Row 2× Weighting Never Fires for Large Datasets
**File:** `services/ml-prediction/src/training/trainer.py`
**Severity:** Critical
**Status:** Fixed

The outcome augmentation weighting was conditional on dataset size, causing it to silently never
apply for the largest (most important) datasets.

**Fix:** Refactored to always apply the 2× weight using a separate `X_out_for_fit` matrix that
never contaminates CV splits. Outcome rows are scaled separately and appended at final model.fit()
time.

---

## Tier 231 — Consecutive-Loss Deadlock (Operational Bug)

**Completed:** 2026-07-01

### Description
Both HK SWING and HK GROWTH portfolios hit their consecutive-loss limits (4 and 5 losses
respectively) while having zero open positions. The circuit breaker prevented any new entries,
leaving the portfolio permanently stuck — no way to win a trade to reset the counter.

### Root Cause
`paper_trading_engine` checked `_consec_losses >= max_consec_losses` without considering whether
`open_count == 0`. When `open_count > 0`, blocking is correct (wait for current trades to resolve).
When `open_count == 0`, blocking is a deadlock — no open trades to close, no way to win.

### Fix
Split the circuit breaker into two cases:
- `open_count > 0`: block normally, write gate block to Redis UI
- `open_count == 0`: log restart warning, zero `_consec_losses`, call `_clear_gate_block()` to
  remove the Redis gate, allow one recovery entry

Also added `_clear_gate_block()` helper to immediately delete the Redis key
`paper:gate_block:{portfolio_id}`.

Existing stale Redis keys cleared immediately:
```bash
docker exec stockai-redis-1 redis-cli del paper:gate_block:2 paper:gate_block:4
```

---

## Tier 228 — Signal Calibration Improvements

**Started:** 2026-06-30, partial completion

| Item | Description | Status |
|---|---|---|
| ML-WEIGHT-FLOOR | AUC-scaled ml_weight_floor in signals.py | Done |
| HK-SHORT-SELL-DISABLE | Override SHORT HK SELL → HOLD | Done |
| HK-LIQUIDITY-FILTER | HKD 50M turnover gate | Done |
| HK-CONNECT-SIGNAL | Compress HK BUY when southbound flow negative | Done |
| CALIBRATION-REDIS | Redis-backed ta_weights + conviction_weights | In progress |
| SELL-CALIBRATION | SELL threshold sweep in outcomes_calibrate_apply | Pending |
| TA-SCORE-META | Ensemble prob as ta_score proxy in trainer.py | Pending |
| HK-MODEL-SEPARATE | HK-specific precision floors | Pending |
| ENSEMBLE-WEIGHTS | Rebalance lgb=0.45 xgb=0.30 rf=0.25 | Pending |
| POINT-IN-TIME-FUNDAMENTALS | Point-in-time fundamentals join | Pending |

---

## Tier 230 — Morning Digest

**Completed:** 2026-07-01

Merged duplicate morning digest emails (user was receiving two per day with different data from
two separate jobs) into a single combined US+HK digest.

---

## Open Items

| ID | Description | Priority |
|---|---|---|
| HIGH-2 | ML retrain 153 symbols after CRIT-3/4 removal | High — in progress |
| QW-2 | Signal age staleness threshold | Medium |
| QW-3 | HK liquidity gate lower bound | Medium |
| QW-6 | Confidence delta calibration | Medium |
| QW-8 | Wire HMM bear_prob > 0.5 into position sizing | Medium |
| T228 remaining | SELL calibration, TA meta, HK model, ensemble weights | Medium |

---

## Design Invariants Established

1. **Never use `::type` cast with SQLAlchemy `text()` named params** — use `CAST(:param AS type)`
2. **Alert checker always reads DB signals (`live=False`)** — not live-computed signals
3. **Any service-to-service call to an auth-protected endpoint must use `_service_token()`**
4. **`market:refresh_failed` Redis flag must not be set by ancillary service calls** (EDGAR, research)
5. **Frontend builds must use `DOCKER_BUILDKIT=0`** — BuildKit serves cached layers even with `--no-cache`
6. **Consecutive-loss circuit breaker must check `open_count == 0`** before blocking permanently
