# Deep Platform Audit — 2026-07-25

**Scope:** Full codebase audit across all 9 microservices, shared DB models, ML pipeline,
paper trading engine, scheduler, decision engine, ranking engine, frontend, and existing
known-limitations/fit-gap docs.
**Method:** Full file reads of every major service file + cross-reference against
`KNOWN_LIMITATIONS.md`, `FITGAP_AGENT_CATALOG_2026-07-18.md`, `improvements.tsx`, and
all prior audit reports (Tier 232/234/242).
**Date:** 2026-07-25
**Auditor:** Amazon Q (agentic deep read)

---

## Executive Summary

The platform is architecturally sound and significantly more complete than most comparable
open-source trading systems. The calibration loop, regime classifier, paper trading engine,
and ML pipeline are all production-grade. The primary risks are concentrated in four areas:

1. **Signal accuracy** — SWING BUY win rate ~27.5% at 10d, SHORT BUY ~16.2%. Both are
   well below the stated >70% target. The root causes are identified and partially addressed
   but not yet fully resolved.
2. **Data integrity** — `Stock.delisted` is structurally dead, the congress data sync is
   broken in production, and 4 ML features were NaN in all deployed models until the
   T232-ML1 fix (retraining still required).
3. **Silent failure surface** — ~60+ swallowed `except: pass` blocks across
   `paper_trading_engine.py` and `scheduler.py` leave no log trail during degraded
   conditions. Only 2 of the highest-risk sites were fixed.
4. **Architectural debt** — the dual-scorer divergence (decision-engine vs.
   `_should_enter()`) means the UI's "Decide" panel and the live trading engine are
   evaluating the same trade differently, with no reconciliation.

---

## Part 1 — Architecture

### 1.1 Service Map

| Service | Port | Role | Status |
|---|---|---|---|
| api-gateway | 8000 | Reverse proxy, auth, aggregation, AI proxy | ✅ Healthy |
| market-data | 8001 | Ingestion, live prices, paper trading, scheduler | ✅ Healthy |
| technical-analysis | 8002 | Indicators, patterns, trendlines | ✅ Healthy |
| ml-prediction | 8003 | XGBoost/LightGBM/RF/LSTM ensemble | ⚠️ Models need retrain (T232-ML1) |
| signal-engine | 8004 | 4-style signal generation, compression filters | ⚠️ Win rate below target |
| ranking-engine | 8005 | K-Score composite | ✅ Healthy |
| strategy-engine | 8006 | Rule DSL + backtester | ✅ Healthy |
| portfolio-optimizer | 8007 | MVO / Risk Parity / HRP / AI Allocation | ✅ Healthy |
| research-engine | 8008 | Planning Stage research reports | ✅ Healthy |
| decision-engine | 8009 | Entry scoring, sizing, LLM layer, risk agent | ⚠️ Dual-scorer debt |
| event-intelligence | 8010 | Earnings, insider, congress, macro | ❌ Congress sync broken |

### 1.2 Shared Layer

- PostgreSQL 16 + Redis 7 shared across all services via `shared/db/models.py`
- 30+ SQLAlchemy models covering the full trade lifecycle
- JWT auth (HS256, 30-day tokens) with per-user isolation
- structlog JSON logging on all services; `/health` on every service

### 1.3 Known Architectural Gaps

**T232-DL-DUALSCORER-DEBT** — The decision-engine's `scorer.py` and
`paper_trading_engine._should_enter()` are supposed to be mirrors but diverge on two
confirmed axes:
- No RL policy adjustment layer in `scorer.py` (AL-1 gap) — `_should_enter()` applies
  a Q-function adjustment; `scorer.py` never does.
- No calibrated logistic regression bypass in `scorer.py` — when ≥100 closed trades
  exist, `_should_enter()` can bypass the additive score entirely via `entry_weights.json`;
  `scorer.py` always uses the additive path.

Impact: the "Decide" UI panel and the live trading engine can reach different verdicts for
the same symbol at the same moment. The UI panel is the less accurate of the two.

**T234-CONFIG-DECIDE-DEFAULT-MISMATCH** — Fixed in routes.py (now fetches real gate params
via `aget_entry_gate_params()`), but the fix only applies when the caller sends no
`config_overrides`. The standalone `/decide/{symbol}/explain` endpoint (used by `decide.tsx`)
now gets real defaults; the batch endpoint still relies on the caller to supply them.

---

## Part 2 — Signal Engine

### 2.1 Win Rate Status

| Style | Direction | Horizon | Win Rate | Target |
|---|---|---|---|---|
| SWING | BUY | 10d | ~27.5% | >70% |
| SHORT | BUY | 5d | ~16.2% | >70% |
| LONG | BUY | 30d | Unknown | >70% |
| GROWTH | BUY | 60d | Unknown | >70% |

The SWING and SHORT win rates are critically below target. The platform has extensive
calibration infrastructure (OC3 EV-lift gate, OC5 confidence bands, tune_style_profiles,
T255 joint tuner) but the underlying signal quality issues have not been resolved by
calibration alone — calibration filters out bad signals but cannot improve the base rate
of the signal generator itself.

### 2.2 Root Cause Analysis

**ML overconfidence at high-confidence bands** — The ensemble (XGBoost/LightGBM/RF) is
overconfident in the 70–85% probability band. Platt/Isotonic calibration is applied but
the calibration dataset is small (n<200 for some style/horizon combos), making the
calibrated probabilities unreliable at the tails.

**4 PIT features were NaN in all deployed models** — `revenue_growth`, `earnings_growth`,
`return_on_equity`, `recommendation_mean` were silently NaN due to the T232-ML1 epoch-date
bug. The code fix shipped but no retrain has been confirmed. Every currently-deployed model
was trained without these features.

**T234-ML-FUND-BROADCAST-LEAKAGE** — `piotroski_score` and 8 of 12 `FUNDAMENTAL_COLUMNS`
are still broadcast from a single current snapshot across all historical training rows.
This is look-ahead leakage: the model sees today's fundamentals when training on 2-year-old
price bars. The T232-ML1 fix repaired 4 columns; the other 8 are still leaking.

**T234-SIG-INSAMPLE-GATE-TUNING** — `tune_style_profiles`'s `_ev_at` helper applies
in-sample-optimal gate parameters directly to Redis with no train/validation split. This
reproduces the exact failure mode that `outcomes_calibrate_apply` (which does have a proper
chronological split) was built to avoid. The EV double-count arithmetic bug was fixed
(T232-OC4) but the methodology gap in the same function was not.

### 2.3 Compression Filter Inventory

20+ compression filters are applied in `signals.py`. The filters are well-designed but
their interaction effects are not measured — it is unknown which filters are responsible
for the most false negatives (good trades filtered out) vs. true negatives (bad trades
correctly blocked). A filter attribution study would identify which filters to relax or
tighten.

### 2.4 Layer 3d Dead Code

`scorer.py` Layer 3d (`conf_delta`) was permanently dead code until recently — it read
`signal_data.get("confidence_delta")` but signal-engine writes this into
`reasons["confidence_delta"]`, not top-level. The fix is now in `scorer.py` (reads from
`reasons`), but this means every decision scored before the fix received zero
accelerating/decelerating signal adjustment. Historical score breakdowns in the DB are
incorrect for this layer.

---

## Part 3 — ML Pipeline

### 3.1 Trainer Architecture

- Walk-forward CV with `TimeSeriesSplit` (gap=horizon) — correct
- Probability calibration (Platt/Isotonic) — correct
- Precision-optimized threshold — correct
- Recency + class-balanced weights — correct
- Outcome augmentation (2× weight on closed trades) — correct
- OOS suppression when AUC < 0.52 — correct

### 3.2 Open Bugs

**T232-ML1-PIT-EPOCH-DATE-BUG** — Fixed in code, not yet in deployed models. Retrain
required. Confirm via `trained_at` field on model-info route + check that
`revenue_growth`/`earnings_growth`/`return_on_equity`/`recommendation_mean` have non-null
feature importance after retrain.

**T234-ML-FUND-BROADCAST-LEAKAGE** — 8 of 12 fundamental columns still use broadcast
(look-ahead) join. This inflates OOS AUC and makes the model appear more accurate than it
is on truly out-of-sample data. Fix: extend the `merge_asof` PIT mechanism from the 4
repaired columns to all 12.

**Survivorship bias in training universe** — `Stock.delisted` is never set to True
anywhere in the codebase. The `WHERE delisted = false` filter in `scheduler.py:2105` is
therefore a no-op — it never excludes any stock. Delisted stocks remain in the training
universe, inflating historical win rates for signals that would have been catastrophic
losses in practice.

### 3.3 Model Promotion Gates

`DESIGN_MODEL_PROMOTION_GATES_2026-07-12.md` exists. The promotion gate infrastructure
is built (T255 joint tuner, `TuneHistory`, retro-feedback `realized_ev_pct_after`
backfill). The gate correctly uses chronological walk-forward splits. No open bugs found
in the promotion gate itself.

---

## Part 4 — Paper Trading Engine

### 4.1 Regime Classifier

5-state classifier (bull/neutral/choppy/risk_off/bear) with hysteresis is well-implemented.
HMM overlay (QW-8), VIX term structure (PT-M4), and breadth via IWM/MDY (PT-M5) are all
integrated. Pre-regime early warnings (`is_pre_choppy`, `is_pre_risk_off`) are surfaced
to the decision engine.

### 4.2 Entry Circuit Breakers

All major circuit breakers are implemented:
- max_positions, equity floor, drawdown (20%), daily loss (4%), weekly loss (8%)
- weekly gain lock (6%), consecutive losses (3), daily entries (3)
- bear/risk_off gates, T258-PORTFOLIO-CORRELATION-PREENTRY (now done per fit-gap doc)

### 4.3 Open Bugs

**T234-PT-SCALEIN-COST-BASIS-BUG** — Scale-in (adding to a winning position) does not
update `entry_shares` or blend `entry_price`. The scale-out side was fixed (T232-PT6);
the scale-in side has the same class of cost-basis bug and is fully open. Effect: when a
scaled-in position closes, `pct_return` is computed against the wrong cost basis
(understated entry cost → overstated return).

**T232-PT6 backfill gap** — UPST (id=23) and IMVT (id=7) have incorrect `entry_shares`
and `realized_pnl=0` from the migration fallback. Their `pct_return` will be understated
when they close. Self-limiting to these 2 trades.

**PT-10 deferred** — Breakeven-stop exits (`|pct_return| < 0.3%`) are not treated as
streak-neutral. They count as losses in `_recent_win_rate`/`_consec_loss_streak`, which
can trigger the consecutive-loss circuit breaker on trades that were effectively flat.

### 4.4 Silent Failure Surface

~60+ swallowed `except: pass` blocks across `paper_trading_engine.py` and `scheduler.py`.
Only 2 of the highest-risk sites (Redis lock-acquire) were fixed (T232-DL-OBSERVABILITY).
The remaining ~58 sites log nothing on failure. During a Redis or upstream-service
degradation, multiple optional gates (macro gate, sector RS gate, OBV divergence check,
etc.) would silently fail open with no log trail.

Specific counts from last audit grep:
- `paper_trading_engine.py`: ~14/53 bare `except Exception: pass` blocks still silent
- `scheduler.py`: ~18/75 bare `except Exception: pass` blocks still silent

---

## Part 5 — Scheduler

### 5.1 Job Inventory

The scheduler covers the full operational lifecycle:
- Ingest, rankings, signals, ML retrain, outcome evaluation, alerts
- Paper trading, equity curve snapshot, EDGAR 8-K, HK Connect flows
- Fundamentals snapshot, watchlist auto-rotation, sector rotation
- Value area levels, volume anomaly alerts, top-3 conviction alerts
- Premarket brief, earnings reactions, macro reactions, promotion gate
- RL training, meta model retrain, backfill realized EV

### 5.2 Congress Data Sync — Broken in Production

`sync_congress_trades()` in event-intelligence uses S3 source URLs that return HTTP 301.
The congress page shows stale/empty data. This has been confirmed broken since at least
2026-07-03 (T233-ARCH-CONGRESS-DEDUP re-scoping note) and was still unresolved as of
the Tier 234 audit (2026-07-04). `congress.py` has not been touched since before the
re-scoping note per `git log`.

Fix order (from KNOWN_LIMITATIONS.md):
1. Fix the S3 source URLs in `sync_congress_trades()` — verify `rows_upserted > 0`
2. Verify real data flows into the `congress_trades` table
3. Add a frontend adapter for the incompatible JSON shape
4. Delete the market-data duplicate endpoint

Do not attempt the "just repoint the frontend" shortcut without step 1 confirmed.

### 5.3 `Stock.delisted` — Structurally Dead Column

`Stock.delisted` is defined in `shared/db/models.py:111` (`default=False`) and read
exactly once as a `WHERE delisted = false` filter in `scheduler.py:2105`. Nothing
anywhere in the codebase ever sets it to `True`. The filter is a no-op.

Consequences:
- Delisted stocks remain in the active universe for signal generation, ML training,
  and outcome evaluation
- The T232-OC6 survivorship bias fix (censoring) cannot be upgraded to "score confirmed
  delistings as losses" because there is no reliable delisting signal
- ML training data includes stocks that went to zero, inflating apparent win rates

Fix options:
- Wire a real delisting data source (yfinance `info["delistingDate"]` or similar)
- Adopt the fixed-rule heuristic: >90 days with zero price bars and not a market holiday

---

## Part 6 — Decision Engine

### 6.1 Scoring Pipeline

7-layer additive scoring is well-structured. All known dead-code bugs have been fixed:
- Layer 3d `conf_delta` now correctly reads from `reasons` (was top-level miss)
- Layer 3f catalyst now reads `insider_score`/`congress_score` separately (was clamped
  to [0,100], making bearish-catalyst penalty unreachable)
- Layer 3h `entry_drift` double-count removed (was scoring price zone twice)
- Layer 6 K-Score added (AUD232-042)
- Layer 7 cross-horizon consensus added (AUD232-007)

### 6.2 Remaining Gaps

**No RL policy layer** — AL-1 gap. `_should_enter()` applies a Q-function adjustment;
`scorer.py` never does. Deferred due to cross-service coupling concern (would require
HTTP call back to market-data or duplicating RL model loading).

**No calibrated logistic bypass** — When ≥100 closed trades exist, `_should_enter()`
can bypass the additive score via `entry_weights.json`. `scorer.py` always uses the
additive path. This means the UI "Decide" panel is less accurate than the live engine
for mature portfolios.

**T258-WHATCOULDGOWRONG-AGENT** — Adversarial pre-trade risk enumeration is implemented
(`risk_agent.py`, `check_risks()`) and wired into the decision pipeline behind
`cfg.get("risk_check_enabled", False)`. It is advisory only (never affects score/verdict).
Status: built but disabled by default — needs to be enabled in production config.

---

## Part 7 — Ranking Engine (K-Score)

### 7.1 Architecture

K-Score 0–100 composite across 6 sub-scores: technical, momentum, value, growth,
volatility, relative strength. Sector-relative fundamental scoring. ADX boost with floor
fix. Proxy-mixing fix (T234).

### 7.2 Known Issues

No open bugs found in `kscore.py` itself. The K-Score is correctly used as a gate in
`check_signal_alerts()` (≥55 conviction threshold) and is now also a layer in
`scorer.py` (Layer 6, AUD232-042).

The K-Score's RS sub-score uses a different window granularity than the catalog's
5/20/60/120d spec, but covers the same function. Not a bug — a deliberate simplification.

---

## Part 8 — T258 Open Gaps (from Fit-Gap Catalog)

These are the 6 genuine gaps identified in `FITGAP_AGENT_CATALOG_2026-07-18.md`.
Status updated as of this audit:

| ID | Gap | Priority | Status |
|---|---|---|---|
| T258-WHATCOULDGOWRONG-AGENT | Adversarial pre-trade risk enumeration | Medium | ✅ Built, disabled by default |
| T258-PORTFOLIO-CORRELATION-PREENTRY | Wire portfolio-risk math into pre-entry gate | Medium | ✅ Done per fit-gap doc |
| T258-MACRO-SECTOR-IMPACT | Structured sectors_helped/hurt on macro reactions | Medium | ❌ Open — narrative only |
| T258-SECTOR-ROTATION-TRAJECTORY | Persist rotation snapshots, classify Emerging/Fading | Low | ❌ Open |
| T258-ACCUM-DIST-BREAKOUT-QUALITY | A/D classifier + breakout follow-through | Low | ❌ Open |
| T258-TRADE-POSTMORTEM | Per-closed-trade plan-vs-actual review in UI | Low | ❌ Open |

---

## Part 9 — Data Integrity

### 9.1 Summary of Data Integrity Issues

| Issue | Severity | Status |
|---|---|---|
| `Stock.delisted` never set — survivorship bias in universe | High | ❌ Open |
| Congress sync broken (S3 301) — stale data in production | High | ❌ Open |
| 4 ML PIT features NaN in deployed models (T232-ML1) | High | ⚠️ Code fixed, retrain pending |
| 8 fundamental columns broadcast-leaking in ML training | High | ❌ Open (T234-ML-FUND-BROADCAST-LEAKAGE) |
| Scale-in cost basis bug (T234-PT-SCALEIN-COST-BASIS-BUG) | Medium | ❌ Open |
| UPST/IMVT backfill gap (T232-PT6) | Low | ⚠️ Self-limiting to 2 trades |
| PT-10 breakeven streak-neutral deferred | Low | ❌ Open |
| `_ev_at` in-sample gate tuning (T234-SIG-INSAMPLE-GATE-TUNING) | Medium | ❌ Open |

### 9.2 Real-Time News Feed

`DESIGN_REALTIME_NEWS_FEED_2026-07-25.md` exists. Implementation not yet started.
Current news is polled (Yahoo Finance + Google News RSS). The design doc covers WebSocket
streaming, Redis pub/sub fan-out, and frontend integration.

---

## Part 10 — Frontend

### 10.1 improvements.tsx Status

~160+ improvement items tracked. Nearly all marked `done`. ~20 still `todo`/`in-progress`.
The tracker is the authoritative source of truth for feature status.

### 10.2 Known Frontend Gaps

- Real-time news feed not implemented (design doc exists)
- T258-TRADE-POSTMORTEM: per-closed-trade plan-vs-actual review not in UI
- T258-SECTOR-ROTATION-TRAJECTORY: rotation trajectory not surfaced in Reports → Money Flow
- Congress page shows stale/empty data (upstream sync broken)

---

## Part 11 — Prioritized Remediation Plan

### P0 — Fix immediately (data integrity / financial correctness)

1. **Retrain all ML models** — T232-ML1 code fix is deployed but models are stale.
   Trigger `tune_all`. Verify `revenue_growth`/`earnings_growth`/`return_on_equity`/
   `recommendation_mean` have non-null feature importance in the new models.

2. **Fix congress sync** — Update S3 source URLs in `sync_congress_trades()`. Verify
   `rows_upserted > 0` before touching the frontend. Follow the 4-step plan in
   KNOWN_LIMITATIONS.md.

3. **Fix T234-ML-FUND-BROADCAST-LEAKAGE** — Extend the `merge_asof` PIT mechanism to
   all 12 fundamental columns (not just the 4 repaired by T232-ML1). This is look-ahead
   leakage that inflates OOS AUC and makes the model appear more accurate than it is.

4. **Fix T234-PT-SCALEIN-COST-BASIS-BUG** — Scale-in does not update `entry_shares` or
   blend `entry_price`. Mirror the scale-out fix (T232-PT6) for the scale-in path.

### P1 — Fix soon (signal accuracy / win rate)

5. **Fix T234-SIG-INSAMPLE-GATE-TUNING** — Add a chronological train/validation split
   to `tune_style_profiles`'s `_ev_at` helper before writing gate params to Redis.
   Mirror the split logic from `outcomes_calibrate_apply`.

6. **Wire `Stock.delisted`** — Either connect a real delisting data source or implement
   the fixed-rule heuristic (>90 days zero price bars). Without this, survivorship bias
   affects the entire ML training universe and outcome evaluation.

7. **Filter attribution study** — Measure which of the 20+ compression filters in
   `signals.py` are responsible for the most false negatives. Relax over-aggressive
   filters to improve signal recall without sacrificing precision.

### P2 — Fix when capacity allows (observability / debt)

8. **Batch-triage the ~60 silent `except: pass` blocks** — Use the Tier 232 audit
   catalog (Part 7 of `AUDIT_REPORT_TIER232_2026-07-02.md`) as the starting list.
   Add at minimum a `log.warning()` to each site. Fail closed on any site that guards
   a financial decision.

9. **Resolve T232-DL-DUALSCORER-DEBT** — Either port the RL policy layer and calibrated
   logistic bypass into `scorer.py` (accepting the cross-service coupling), or document
   explicitly that the UI "Decide" panel is a lower-fidelity approximation and should
   not be used for live trading decisions.

10. **Enable T258-WHATCOULDGOWRONG-AGENT in production** — `risk_agent.py` is built and
    wired. Set `risk_check_enabled: true` in the production paper-trading config. The
    agent is advisory only and cannot affect verdicts.

### P3 — New features (when P0–P2 are clear)

11. **T258-MACRO-SECTOR-IMPACT** — Add structured `sectors_helped`/`sectors_hurt` output
    to macro reaction events (finishes what T249-P2 explicitly deferred).

12. **T258-TRADE-POSTMORTEM** — Surface per-closed-trade plan-vs-actual review in the UI.
    `PaperTrade` already stores all required data (entry plan + exit actuals).

13. **Real-time news feed** — Implement per `DESIGN_REALTIME_NEWS_FEED_2026-07-25.md`.

14. **T258-SECTOR-ROTATION-TRAJECTORY** — Persist rotation snapshots, classify
    Emerging/Established/Fading Leader, surface in Reports → Money Flow.

15. **MAE-aware outcome scoring** — Deferred from T232-OC4. Natural home is the Backtest
    Harness (T233-SELFIMPROVE-PHASE2) when it exists, not `evaluate_signal_outcomes()`.

---

## Part 12 — What Is Working Well

These areas are production-grade and should not be touched without strong justification:

- **Calibration loop** — The most built-out area of the platform. Confidence calibration
  (real bucket win rates, n≥30), `outcomes/calibrate/apply` with chronological splits,
  `tune_style_profiles`, T255 joint tuner, `TuneHistory`, promotion gates, signal watchdog,
  retro-feedback realized-EV checks. This is the highest-leverage area per the fit-gap
  catalog's own closing section, and it is done extensively.

- **Regime classifier** — 5-state with hysteresis, HMM overlay, VIX term structure,
  breadth via IWM/MDY, pre-regime early warnings. Mechanically enforced (sizing dampeners,
  min_entry_score raises, entry blocks) — not just narrated.

- **Paper trading engine circuit breakers** — All major financial risk controls are
  implemented and tested. The engine is conservative by design.

- **ML trainer walk-forward CV** — TimeSeriesSplit with gap=horizon, OOS suppression at
  AUC < 0.52, recency weighting, outcome augmentation. Correct methodology.

- **Decision engine hard rejects** — `hard_rejects.py` correctly blocks bear regime,
  low confidence, bad R:R, earnings proximity, max positions, daily loss limit, and
  research AVOID/SELL. These are the right gates.

- **K-Score composite** — Well-designed 6-sub-score composite with sector-relative
  fundamentals. Correctly used as a gate and as a scoring layer.

---

## Appendix A — File Reference

| File | Lines | Key finding |
|---|---|---|
| `shared/db/models.py` | ~1200 | `Stock.delisted` never set |
| `services/signal-engine/src/generators/signals.py` | ~2500 | Win rate below target; 20+ filters |
| `services/decision-engine/src/api/core/scorer.py` | ~220 | Layer 3d dead code fixed; RL/logistic gaps remain |
| `services/decision-engine/src/api/routes.py` | ~280 | Full async pipeline; micro-position guard correct |
| `services/market-data/src/services/paper_trading_engine.py` | ~3500 | Scale-in cost basis bug open |
| `services/market-data/src/services/scheduler.py` | ~3000 | Congress sync broken; 18/75 silent exceptions |
| `services/ml-prediction/src/training/trainer.py` | ~800 | PIT fix deployed; broadcast leakage open |
| `services/ranking-engine/src/scoring/kscore.py` | ~600 | No open bugs |
| `frontend/src/pages/improvements.tsx` | ~160+ items | ~20 still todo/in-progress |
| `docs/KNOWN_LIMITATIONS.md` | — | 6 partial fixes documented |
| `docs/FITGAP_AGENT_CATALOG_2026-07-18.md` | — | 6 genuine gaps; 4 still open |

---

## Appendix B — Tracker Cross-Reference

| Tracker ID | Description | Status |
|---|---|---|
| T232-PT6 | Scale-out P&L backfill | ✅ Done (2 trades imprecise) |
| T232-OC6 | Survivorship bias censoring | ✅ Partial (delisted=losses deferred) |
| T232-OC4 | Win definition cost hurdle + EV double-count | ✅ Done (MAE deferred) |
| T232-ML1 | PIT epoch-date bug | ✅ Code fixed, retrain pending |
| T232-DL-OBSERVABILITY | Swallowed exceptions | ✅ 2/60 sites fixed |
| T232-DL-DUALSCORER-DEBT | Dual scorer divergence | ❌ Open |
| T233-ARCH-CONGRESS-DEDUP | Congress sync broken | ❌ Open |
| T234-PT-SCALEIN-COST-BASIS-BUG | Scale-in cost basis | ❌ Open |
| T234-ML-FUND-BROADCAST-LEAKAGE | Fundamental broadcast leakage | ❌ Open |
| T234-SIG-INSAMPLE-GATE-TUNING | In-sample gate tuning | ❌ Open |
| T258-WHATCOULDGOWRONG-AGENT | Adversarial risk agent | ✅ Built, disabled |
| T258-PORTFOLIO-CORRELATION-PREENTRY | Pre-entry correlation gate | ✅ Done |
| T258-MACRO-SECTOR-IMPACT | Structured sector impact | ❌ Open |
| T258-SECTOR-ROTATION-TRAJECTORY | Rotation trajectory | ❌ Open |
| T258-ACCUM-DIST-BREAKOUT-QUALITY | A/D + breakout quality | ❌ Open |
| T258-TRADE-POSTMORTEM | Per-trade plan-vs-actual | ❌ Open |
| AL-1 | RL policy layer in scorer.py | ❌ Open |
| PT-10 | Breakeven streak-neutral | ❌ Open |
