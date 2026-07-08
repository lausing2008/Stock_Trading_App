# Changelog

All significant changes to the Stock Trading App are documented here, grouped by session.
For full details on any item, see the linked docs or the git commit history.

---

## 2026-06-13 (Session 5) — Principal Architect + Financial Domain Dual Audit → Tier 14

### Overview

Two parallel audit workflows ran to completion: a 10-subsystem principal-architect technical audit and a 7-subsystem financial domain audit (quantitative analyst + CRO perspective). Combined: **5 CRITICAL · 27 HIGH · 29 MEDIUM** findings across the full stack.

### Top Issues by Blast Radius

| # | Severity | ID | Title | File(s) |
|---|----------|----|-------|---------|
| 1 | CRITICAL | aud14-adj-close | Unadjusted close prices corrupt SMA/ATR/MACD/ML system-wide | signals.py, builder.py, paper_trading_engine.py |
| 2 | CRITICAL | aud14-cv-leakage | CV folds overlap test set — AUC metrics inflated 0.05–0.15 | trainer.py:218-258 |
| 3 | CRITICAL | aud14-single-model | Single SWING-horizon model used for SHORT+SWING+LONG | scheduler.py:275, signals.py:1564,1640 |
| 4 | CRITICAL | aud14-paper-race | Double execution of paper_trading_step() in close burst window | scheduler.py:288-295,1278-1285 |
| 5 | CRITICAL | aud14-survivorship | ML training universe missing all delisted/acquired stocks | routes.py:88-89 |
| 6 | HIGH | aud14-backtest-fill | Entry at signal-bar close — look-ahead bias, rf=0 Sharpe | engine.py:51-61,80 |
| 7 | HIGH | aud14-momentum-max | Momentum pillar max() overrides overbought RSI+StochRSI | signals.py:858-876 |
| 8 | HIGH | aud14-rsi-div-dead | RSI divergence 0.18 dead weight stays in TA denominator | signals.py:778-785 |
| 9 | HIGH | aud14-obv-mislabeled | OBV MA crossover labeled as divergence in emails+reasons | signals.py:827-830 |
| 10 | HIGH | aud14-gbm-lstm-crash | GBM + LSTM fit() crash on sample_weight — never trained | gbm.py:20, lstm.py:48 |

### Systemic Patterns Identified

- **Unadjusted prices as systemic failure** — affects 6+ modules; adj_close standardization fixes all of them
- **Training/inference feature skew** — MACD adjust, label threshold lookahead, survivorship all degrade model quality silently
- **Regime detection split across 4 independent systems** — no single authoritative regime state
- **Financial metric labeling** — OBV, VWAP, K-Score Value/Growth all labeled incorrectly; misleads traders
- **Infrastructure defaults unsafe in production** — CORS wildcard, no Redis pool, Float financials, no correlation IDs

### Tier 14 Added to Improvements Tracker

31 items added across CRITICAL / HIGH / MEDIUM categories:
- 5 CRITICAL: adj-close, cv-leakage, single-model, paper-race, survivorship
- 22 HIGH: backtest/Sharpe/benchmark, signal pillar logic, ML model bugs, UX gaps (chart lines, exit button, broken links)
- 7 MEDIUM: Redis pool, CORS, Float schema, APScheduler, two migrations, global mutable state, no correlation IDs

---

## 2026-06-13 (Session 4) — Signal-Research Convergence Analysis + Tier 13 + Full System Audit

### ARMK Case Study: AI Signal vs Research Report Discrepancy

**Finding**: AI Signal = BUY (confidence 75, TA 88/100, GROWTH horizon) while Research Report AI Verdict = WAIT (overall score 66, confidence 58%).

**Root cause — the two systems answer different questions:**

| System | Question answered | Time horizon | Inputs |
|---|---|---|---|
| AI Signal | Is the price action right now warrant a momentum entry? | Short-term (days–weeks) | TA composite, VWAP, OBV, SMAs, ML model |
| Research Report | Is the full investment thesis verified enough to commit capital? | Medium-term (weeks–months) | Technical 25% + Fundamental 30% + Company 15% + Industry 15% + Economic 15% |

**Why Research said WAIT on ARMK:**
1. **Missing financial data** — yfinance returned `$0` for Total Cash, Total Debt, Revenue, EPS, FCF, P/E, EV/EBITDA. Fundamental score defaulted to 58 (not because fundamentals are bad, but because they couldn't be verified). This is a **data pipeline bug**, not an analytical disagreement.
2. **RSI 66.7 approaching overbought** without a confirmed catalyst to sustain momentum
3. **DCF fair value $41.00 vs price $54.27** = −24.5% overvalued by DCF

**Why AI Signal said BUY on ARMK:**
- Price +12.4% above 50-day SMA, +32.4% above 200-day SMA — strong trend structure
- OBV trending up (volume confirming price)
- 64 insider buy transactions (8.3% net purchase rate) — well above baseline
- ML model confirming directional bias
- GROWTH style designed for momentum setups (relaxed thresholds by design)

**Correct interpretation:**
- For GROWTH momentum trade: Signal is sound. Enter at standard sizing, stop at $44.30 support.
- For investment conviction: Research says wait for Aug 11 earnings to confirm margin recovery before sizing up.
- Both are right for their respective use cases. The conflict is not a bug — it's a feature that needs surfacing to the user.

**Next earnings**: 2026-08-11 (59 days). Catalysts that would flip to STRONG BUY: gross margin >15%, new contract wins in healthcare/education, debt paydown.

### Changes Made

| Item | File | What |
|---|---|---|
| PDF export button | `frontend/src/pages/research/[symbol].tsx` | "↓ Export PDF" button in header; all 9 tabs render simultaneously in print mode; `@media print` hides nav/tabs/chatbot; `window.print()` triggered via `useEffect` after `printMode` state change |
| Tier 13 tracker | `frontend/src/pages/improvements.tsx` | 12 new items: RES-FIX-1 (yfinance fallback), RES-FIX-2 (invalid date), INT-1 through INT-10 (signal-research integration layer) |
| Full system audit | workflow (background) | Principal-architect-grade parallel audit across 10 subsystems — findings will populate next tier when complete |

### Tier 13 — Signal-Research Intelligence Layer (12 items)

| ID | Severity | Title |
|---|---|---|
| RES-FIX-1 | high | Add fallback data source when yfinance returns empty financial statements |
| RES-FIX-2 | medium | Fix "Generated Invalid Date" in research report header |
| INT-1 | high | Research verdict badge on stock detail page |
| INT-2 | high | Signal-Research alignment indicator (ALIGNED / DIVERGENT) |
| INT-3 | high | Research-gated position sizing in paper trading |
| INT-4 | medium | Auto-trigger background research when BUY signal fires |
| INT-5 | medium | Research freshness warning (>7 days old) |
| INT-6 | medium | Composite conviction score (blend signal + research) |
| INT-7 | medium | Divergence alert — notify when signal/research disagree |
| INT-8 | feature | Forward return tracking (signal+research accuracy measurement) |
| INT-9 | feature | Research verdict in open paper positions panel |
| INT-10 | feature | Research chip on Opportunities signal cards |

### How to best use both systems together (reference)

**Decision framework:**
```
Signal fires BUY →
  ├── Research = BUY/STRONG BUY + score ≥ 75  → FULL SIZE entry (1.2× mult)
  ├── Research = WATCH + score 60–74           → STANDARD size (1.0×), monitor
  ├── Research = WAIT + score 50–65            → REDUCED size (0.6×), review before add
  ├── Research = AVOID                         → SKIP or very small probe only
  └── No research report                       → STANDARD size, generate report in background
```

**Planned implementation**: INT-3 (research-gated sizing) will wire this directly into the paper trading engine as a configurable multiplier. INT-2 (alignment indicator) will surface the conflict visually on the stock detail page. INT-7 (divergence alert) will send an email/notification when they disagree.

---

## 2026-06-13 (Session 3) — Tier 10 Audit Fixes + Research Network Error Fix

### Research Report Network Error (ARMK)

**Root cause**: Timeout mismatch across the stack.
- Frontend timeout: 90s — too short for a full AI analysis
- API gateway timeout: 120s — insufficient when data-gather (25s) + AI (120s) = 145s total
- Anthropic API: 120s limit left no margin for overhead

**Fixes applied:**

| Layer | Change |
|---|---|
| `api-gateway/src/api/proxy.py` | `POST /research/*` requests use 240s timeout (vs 120s for all others) |
| `research-engine/src/api/routes.py` | Anthropic AI call reduced from 120s to 90s — leaves margin under gateway timeout; added explicit 429 rate-limit handling |
| `frontend/src/lib/api.ts` | `generateResearch` timeout extended from 90s to 200s — prevents premature AbortError |
| `frontend/src/pages/research/[symbol].tsx` | Loading message updated to say "1–2 minutes" |

**New timeout chain**: frontend 200s → gateway 240s → AI call 90s + data-gather 25s = ~115s. No more chain breaks.

### Tier 10 Audit Fixes (remaining open items)

| Item | File | What |
|---|---|---|
| AUD-M10 | `services/ml-prediction/src/models/lgb.py` + `trainer.py` | LightGBM early stopping now uses LGB-native callbacks (`lgb.early_stopping(50)`, `lgb.log_evaluation(0)`); trainer.py passes `eval_set` for lightgbm branch |
| AUD-M13 | `services/market-data/src/services/paper_trading_engine.py` | `_NYSE_HOLIDAYS` frozenset (2024–2027 static dates); `_is_market_hours()` now checks holiday before opening |
| AUD-M19 | `scripts/migrations/005_paper_portfolio_unique_index.sql` | `CREATE UNIQUE INDEX uix_paper_portfolios_active_name ON paper_portfolios (name) WHERE is_active = TRUE` |
| AUD-M23 | `scripts/migrations/006_signal_unique_constraint.sql` | `CREATE UNIQUE INDEX uix_signals_stock_horizon_day ON signals (stock_id, horizon, date_trunc('day', ts))` |

Already-implemented items marked done in tracker (were duplicate entries):
AUD-M3-rsi40 (= M3-boundary done 6/12), AUD-M4-global-cap (= M4-floor done 6/12),
AUD-M5-detection (= M5-gap done 6/12), AUD-M6-latest-swing (= M6-default done 6/12),
AUD-M7, AUD-M8 (first_close_after already T+1), AUD-M21 (VALID_TRANSITIONS already in board.py).

### Tier 12 — All items now complete

PT-M5 (market breadth), PT-M3 (dashboard already done), PT-M2 (earnings stop freeze),
PT-M4 (VIX term structure), PT-M1 (sector RS lag), AUD-C1/C2 (confirmed done in ML-FIX-2).

---

## 2026-06-13 (Session 2) — Paper Trading Improvements: PT-M1, PT-M2, PT-M4

### Improvements implemented

| Item | File | What |
|---|---|---|
| PT-M2 | `paper_trading_engine.py` `_monitor_positions` | Earnings proximity stop freeze: if `signal.reasons.days_to_earnings ≤ 2`, all trail updates frozen (normal trail, double-top, K-score, OBV, sector RS). Hard stop still active. |
| PT-M4 | `paper_trading_engine.py` `_fetch_market_regime` | VIX term-structure: `^VIX9D` added to yfinance download. If `VIX9D/VIX > 1.10` AND state is bull/neutral → `is_pre_risk_off=True`. `vix9d` + `vix_term_inverted` logged. |
| PT-M1 | `paper_trading_engine.py` `_monitor_positions` + new helper | Sector RS lag: `_SECTOR_ETF_MAP` (12 sectors → XLK/XLV/XLF/etc.) + `_batch_sector_rs_lag()` helper. Single yfinance download for all stocks + ETFs. If stock lags sector ETF by >10pp over 5d AND trail armed → tighten to 1.5×ATR×regime_adj. |
| AUD-C1/C2 | Tracker only | Confirmed already implemented in ML-FIX-2 session. Marked done. |

### Trail tightening hierarchy (updated — most → least tight)

| Condition | Trail multiplier | Guard |
|---|---|---|
| Double-top neckline break | 1.2× ATR | `not earnings_near` |
| K-score deterioration (PT-H3) | 1.5× ATR | `not earnings_near` |
| OBV divergence (PT-H4) | 1.5× ATR | `not earnings_near` |
| Sector RS lag >10pp / 5d (PT-M1) | 1.5× ATR | `not earnings_near` |
| Normal trailing stop | 2.0× ATR | `not earnings_near` |
| All multipliers adjusted by regime | × regime_trail_adj | 0.70–1.00 |
| Earnings proximity (PT-M2) | — (freeze all updates) | DTE ≤ 2 from `signal.reasons` |

---

## 2026-06-13 (Session 1) — Paper Trading Deep Review + Fixes

### Root cause analysis: why paper trading never bought

| Root Cause | Detail |
|---|---|
| Weekend timing | CronTrigger jobs fire Mon–Fri only. Deployment happened Saturday. First real run is Monday. |
| Pre-AUD-H5 silent crash | Single try/except in `_refresh_market` — any yfinance error killed Stage 4 (paper trading step) |
| Portfolio config wrong scale | `risk_per_trade_pct=1` (was 100%, should be 0.01), `max_position_pct=5` (was 500%, should be 0.10), `max_hold_days=20` (should be 60 for GROWTH) — entered as % integers instead of decimal fractions |
| `stock_id` NULL on trades | `_scan_for_entries` didn't set `stock_id` on new trades → double-top mid-trade detection always queried `NULL`, silently skipped every cycle |

### DB migrations added (`scripts/migrations/`)

| File | What | Schema or Data |
|---|---|---|
| `001_add_sector_to_paper_trades.sql` | `sector` column on `paper_trades`, back-filled from stocks | Schema |
| `002_add_signal_at_exit_to_paper_trades.sql` | `signal_at_exit_id` + `signal_at_exit_type` on `paper_trades` | Schema |
| `003_fix_paper_portfolio_config.sql` | Fix wrong-scale config values; add missing safety params | Data (run every instance) |
| `004_add_stock_id_to_paper_trades.sql` | `stock_id` FK on `paper_trades`, back-filled from stocks table | Schema |
| `run_migrations.sh` | Runs 001–004 in order, idempotent (safe to re-run) | — |

**How to run on a new instance:**
```bash
bash scripts/migrations/run_migrations.sh
```

Fresh instances can skip 001, 002, 004 (create_all handles schema).
Migration 003 must run on every instance (data fix).

### Code changes

| Item | File | What was fixed |
|---|---|---|
| PT-C1 | DB (migration 003) | Portfolio config: `risk_per_trade_pct=0.01`, `max_position_pct=0.10`, `max_hold_days=60` |
| PT-H1 | `api/paper_portfolio.py` `/configure` | Range validation for all decimal-fraction params. Returns 400 with clear hint on bad input. |
| PT-H2 | `paper_trading_engine.py` + `models.py` | `stock_id` now set on `PaperTrade` at entry. Double-top trail tightening (1.2× ATR) is now active. |
| PT-H5 | `api/paper_portfolio.py` `/run-step` | Admin endpoint to trigger `paper_trading_step()` manually. `enforce_market_hours=false` for weekend testing. Rate-limited to 1/min. |
| PT-H3 | `paper_trading_engine.py` `_monitor_positions` | K-score deterioration exit: if K-score drops 15+ pts from entry, trail tightens to 1.5× ATR |
| PT-H4 | `paper_trading_engine.py` `_monitor_positions` | OBV divergence exit: price flat/up but OBV declining → trail tightens to 1.5× ATR |
| `/create` | `api/paper_portfolio.py` | New portfolios now seeded from `_DEFAULT_CONFIG + _STYLE_OVERRIDES` — always correct decimal values |
| Deploy guide | `docs/DEPLOY_EC2.md` | Added migration step to Section 10 |

### Improvements tracker

Tier 12 added (13 items). PT-C1, PT-H1, PT-H2, PT-H5 marked done.
PT-H3 and PT-H4 implemented this session (pending tracker update).

---

## 2026-06-12 — Adversarial System Audit (Tier 10)

Full findings in `docs/AUDIT_2026-06-12.md`. 27 issues fixed across 4 services.

Key fixes:
- AUD-H5: 4-stage isolation in `_refresh_market` — ingest failures no longer kill paper trading
- AUD-CB2/CB3: signal freshness window extended to 26h; CB-5 double-count fix
- AUD-M1: risk_off regime now requires BOTH legs (SPY < 50EMA AND VIX > 25)
- AUD-RE9: Early-warning flags `is_pre_choppy` / `is_pre_risk_off` added to regime engine
- AUD-PT-D6: Composite priority sort for entry candidates (confidence + K-score + breakout context)
- ML weight ramp extended; Optuna tuning added; 22-feature training

---

## 2026-06-11 — Signal Coherence & Breakthrough (Tier 8/9)

- Paper trading engine WF-2: full audit + regime engine
- GROWTH signal style added (high-volatility momentum)
- K-score gate, R:R gate, partial profit taking, ATR trailing stop
- ML/TA conflict dampening flag

---

## 2026-06-10 — Alert Intelligence & UX (Tier 7)

- Signal filter monitor with "Checked:" / "Sent:" timestamps
- Short interest tracker on stock detail
- Earnings This Week panel on Opportunities page

---

## 2026-06-09 — Security & Reliability Audit (Tier 6)

- JWT authentication, multi-user system, bcrypt passwords
- Admin settings section, namespaced localStorage per user
- Email price alerts via Gmail SMTP / AWS SES

---

## 2026-05-31 — Initial Feature Build (Tiers 1–5)

- ML calibration (isotonic regression, 3-way split)
- K-Score value sub-score with falling-knife gate
- Watchlist picker, move-between-lists
- Rankings prices, full refresh with force ingest
- HK timezone fix (UTC offset corrected in base.py + routes.py)

---

## Position Sizing Reference (GROWTH portfolio, $50k equity)

How the engine computes share count for a GROWTH trade:

```
risk_dollar    = equity × risk_per_trade_pct × size_multipliers
               = $50,000 × 0.01 × (regime_mult × earnings_mult × confidence_mult)
               = $500 (at 1.0 across all multipliers)

shares         = risk_dollar / stop_distance
               = $500 / (price × 0.12)  → for 12% stop

Cap 1: max_loss_per_trade_pct = 0.02
  max_loss_dollar = $1,000
  If shares × stop_distance > $1,000 → cap at 1,000 / stop_distance

Cap 2: max_position_pct = 0.10
  max_pos = equity × 0.10 = $5,000
  If shares × price > $5,000 → cap at 5,000 / price

Cap 3: Cash check
  position_value must be ≤ current_cash × 0.98
```

**Effect of each regime on size:**
| Regime | size_mult | trail_adj |
|---|---|---|
| bull | 1.00 | 1.00 |
| neutral | 1.00 | 1.00 |
| choppy | 0.75 | 1.00 |
| risk_off | 0.50 | 0.85 |
| bear | — (entries blocked) | 0.70 |

---

## Trail Tightening Hierarchy (most → least tight)

When multiple tightening conditions are true, each sets the stop independently —
the highest stop wins (current_stop only moves up, never down).

| Condition | Trail multiplier | When |
|---|---|---|
| Double-top neckline break | 1.2× ATR | `signal.reasons.double_top_breakdown = True` |
| K-score deterioration (PT-H3) | 1.5× ATR | K-score drops 15+ pts from entry value |
| OBV divergence (PT-H4) | 1.5× ATR | Price flat/up, OBV declining over 10 bars |
| Normal trailing stop | 2.0× ATR | Trail armed (position up 5%+) |
| All multipliers adjusted by regime | × regime_trail_adj | 0.70–1.00 depending on regime |

---

## Migration Quick Reference

```bash
# On EC2 — run all migrations (safe to re-run, idempotent)
cd /home/ec2-user/Stock_Trading_App
bash scripts/migrations/run_migrations.sh

# Run a specific migration manually
docker compose -f docker/docker-compose.yml exec -T postgres psql -U stockai -d stockai \
  < scripts/migrations/003_fix_paper_portfolio_config.sql

# Verify portfolio config is correct
docker compose -f docker/docker-compose.yml exec -T postgres psql -U stockai -d stockai -c "
SELECT name,
       config->>'risk_per_trade_pct' AS risk_pct,
       config->>'max_position_pct'   AS max_pos,
       config->>'max_hold_days'      AS hold_days,
       config->>'trading_style'      AS style
FROM paper_portfolios;"
```
