# Changelog

All significant changes to the Stock Trading App are documented here, grouped by session.
For full details on any item, see the linked docs or the git commit history.

---

## 2026-06-13 — Paper Trading Deep Review + Fixes

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
