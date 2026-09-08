# Institutional Features Roadmap — 2026-08-16

**Date:** 2026-08-16
**Scope:** Features that bring StockAI from a sophisticated retail platform to one with
institutional-grade execution discipline, risk management, and analytics. Grounded in the
current codebase — every "already exists" claim is verified against actual code.

---

## What "Institutional" Means Here

Retail platforms show signals. Institutional platforms enforce discipline around those signals:
pre-trade risk checks, execution quality measurement, post-trade attribution, and a feedback
loop that actually closes. StockAI already has the signal layer. This roadmap covers the
discipline layer.

---

## Part 1 — Current Institutional-Grade Capabilities (Already Built)

These exist and should not be re-built. Documented here to avoid scope creep.

| Capability | Location | Notes |
|---|---|---|
| Kelly-fraction position sizing | `GET /paper-portfolio/kelly` | Quarter-Kelly, from real closed-trade history |
| Regime-aware entry dampening | `paper_trading_engine.py` | Sizing multipliers per regime state |
| Portfolio drawdown monitoring | `scheduler.py:6950` | `check_portfolio_drawdown_alerts()` |
| Trailing stop + breakeven trigger | `paper_trading_engine.py:2996` | Auto-trails, breakeven at configurable gain |
| Stop-loss execution | `paper_trading_engine.py:2312` | `_monitor_positions()`, intraday cycle |
| Scale-out partials | `paper_trading_engine.py` | T241 position-scaling gate |
| Sector concentration caps | `paper_trading_engine.py` | Pre-entry sector % and position caps |
| Heat brake | `paper_trading_engine.py:3946` | Blocks all entries after 3 stops in 48h |
| Walk-forward validation | `gate_harness.py` | Chronological 70/30 split, EV-lift margin check |
| Confidence calibration | `calibration.py` | Bucketed win rates, n≥30 floor |
| Outcome tracking | `SignalOutcome`, `SqueezeAlertOutcome` | Forward-return windows, is_correct flags |
| ConditionalOrder schema | `models.py:382` | Schema exists; execution not yet wired |
| Portfolio correlation + VaR | `/portfolio-risk/risk` | Pairwise correlation, beta, parametric VaR |
| Broker abstraction layer | `broker.py` | E*Trade sandbox + manual; real-money gates needed |

---

## Part 2 — Confirmed Gaps (Verified Against Codebase)

### INST-001: ConditionalOrder Execution Not Wired
**Status:** Schema exists (`models.py:382`), actions defined (`tighten_stop`, `sell_partial`,
etc.), but no scheduler job evaluates or executes them.
**Impact:** High — the entire conditional-order feature is inert.
**Effort:** M

```python
# scheduler.py — add check_conditional_orders()
def check_conditional_orders() -> None:
    with _get_session() as session:
        pending = session.query(ConditionalOrder).filter(
            ConditionalOrder.status == "pending",
            or_(ConditionalOrder.expires_at.is_(None),
                ConditionalOrder.expires_at > func.now())
        ).all()
        for order in pending:
            if _evaluate_conditions(order):
                _execute_action(order, session)
                order.status = "triggered"
                order.triggered_at = datetime.utcnow()
        session.commit()

def _execute_action(order: ConditionalOrder, session) -> None:
    if order.action_type == "tighten_stop":
        trade = session.query(PaperTrade).filter(
            PaperTrade.symbol == order.symbol,
            PaperTrade.stage == "open"
        ).first()
        if trade and order.action_value > trade.current_stop:
            trade.current_stop = order.action_value
    elif order.action_type == "sell_partial":
        _execute_partial_exit(session, order)
```

---

### INST-002: Portfolio Correlation Not Checked Pre-Entry
**Status:** `/portfolio-risk/risk` computes pairwise correlation and beta-weighted exposure
correctly, but `_scan_for_entries()` and `decision-engine` never call it.
**Impact:** High — a new entry can silently double-up on an existing correlated position.
**Effort:** M
**Reference:** T258-PORTFOLIO-CORRELATION-PREENTRY (FITGAP_AGENT_CATALOG_2026-07-18.md)

```python
# In paper_trading_engine.py _should_enter(), add:
async def _check_correlation_gate(symbol: str, portfolio_id: int, session) -> bool:
    """Reject entry if it would push portfolio correlation above threshold."""
    open_symbols = _get_open_symbols(session, portfolio_id)
    if not open_symbols:
        return True
    corr_matrix = _compute_rolling_correlations(symbol, open_symbols, window=60)
    max_corr = max(corr_matrix.values(), default=0.0)
    if max_corr > 0.80:
        log.info("entry.rejected.correlation", symbol=symbol, max_corr=max_corr)
        return False
    return True
```

---

### INST-003: SignalOutcome Scale-Out Mislabeling (Production Bug)
**Status:** Confirmed bug — `paper_trading_engine.py:2578-2579` writes pre-blend `pnl_pct`
to `SignalOutcome` instead of the blended `total_pnl_pct` computed at lines 2530-2532.
**Impact:** Critical — corrupts the ruler used by every tuner and calibration step. Also:
`stop_hit` conflation triggers a 120-hour re-entry ban on profitable trailing-stop exits.
**Effort:** M — must be executed against production DB.
**Reference:** DESIGN_REVIEW_FORWARD_2026-08-05.md §R4 (full detail there)

Fix summary:
1. Replace `pnl_pct` with `total_pnl_pct` and `pnl_dollar > 0` with `total_pnl_dollar > 0`
   in the SignalOutcome writeback at lines 2578-2579.
2. Add a `trailing_stop` exit reason where the trail mutates `current_stop`, and repoint the
   120h cooldown and heat brake at `stop_hit` (protective) only, not trailing exits.
3. Decide which writer owns the `return_5d/10d/20d` window columns — `evaluate_signal_outcomes`
   or the paper writeback — and make the other stop writing them.

---

### INST-004: Broker Endpoint Admin Gate Missing
**Status:** Confirmed — every endpoint in `services/market-data/src/api/broker.py` uses
`Depends(get_current_user)`, not `Depends(get_admin_user)`. `PaperPortfolio` has no `user_id`
column, so any authenticated user can link any broker connection to any portfolio via direct
API call, bypassing the frontend's `isAdmin` UI guard.
**Impact:** High — capital-risk if a real E*Trade connection is ever linked.
**Effort:** S — add `Depends(get_admin_user)` to all `broker.py` routes.
**Reference:** DESIGN_SIX_ITEM_BATCH_2026-08-11.md §6

---

### INST-005: No Pre-Trade "What Could Go Wrong" Check
**Status:** Not built. Research reports have risk sections, but those are per-report (slow,
on-demand), not per-trade-decision. No adversarial pre-entry risk enumeration exists.
**Impact:** Medium — the one genuinely new agent in the FITGAP catalog.
**Effort:** M
**Reference:** T258-WHATCOULDGOWRONG-AGENT (FITGAP_AGENT_CATALOG_2026-07-18.md)

Design: one Claude Haiku call at decision time, given the signal, game plan, open book, and
regime. Returns 3-5 concrete failure modes (not a probability — that would be uncalibrated).
Displayed as a collapsible "Risk Check" panel on the Trade Board card and in the paper-trade
entry confirmation. Feature-flagged OFF by default (per the established pattern for LLM-spend
features).

---

### INST-006: No Per-Trade Post-Mortem (Plan vs. Actual)
**Status:** The data exists — `PaperTrade` stores both the plan (entry/stop/target at entry)
and actuals (exit price/reason/pnl). The per-trade review UI does not exist.
**Impact:** Medium — the aggregate learning loop is built; the per-trade review is missing.
**Effort:** S
**Reference:** T258-TRADE-POSTMORTEM (FITGAP_AGENT_CATALOG_2026-07-18.md)

```
Per-trade post-mortem panel (closed trades page):
- Entry plan vs. actual entry price (slippage)
- Stop plan vs. actual stop (was it moved? when?)
- Target plan vs. actual exit (early exit? target hit?)
- Exit reason (stop_hit / target / signal_decay / time_stop / trailing)
- MAE (maximum adverse excursion) — see INST-007
- Plan adherence score (0-100)
```

---

### INST-007: No Maximum Adverse Excursion (MAE) Tracking
**Status:** `max_favorable_excursion` exists in one admin endpoint only, with no aggregation.
`entry_slippage_pct` is a hardcoded `0.0` placeholder. No MAE field exists anywhere.
**Impact:** Medium — without MAE, entry timing cannot be measured or improved.
**Effort:** S — add `mae_pct` column to `PaperTrade`; populate in `_monitor_positions()`.
**Reference:** DESIGN_REVIEW_FORWARD_2026-08-05.md §R9

```python
# In _monitor_positions(), track MAE:
if current_price < trade.entry_price:
    adverse_excursion = (trade.entry_price - current_price) / trade.entry_price
    if trade.mae_pct is None or adverse_excursion > trade.mae_pct:
        trade.mae_pct = round(adverse_excursion, 4)
```

---

### INST-008: No Environment Gate on Alert Jobs (Local Dev Safety)
**Status:** `scheduler.py` registers all ~19 alert-emitting jobs unconditionally on startup.
`Settings.env` exists but the scheduler never reads it. Local dev with a prod DB dump would
send real emails to real users.
**Impact:** High operational risk — not a trading feature, but a prerequisite for safe
prod→local sync.
**Effort:** S
**Reference:** DESIGN_SIX_ITEM_BATCH_2026-08-11.md §1

```python
# In start_scheduler(), wrap all alert-emitting job registrations:
if Settings.env == "production":
    scheduler.add_job(check_price_alerts, ...)
    scheduler.add_job(check_signal_alerts, ...)
    # ... all 19 alert jobs
```

---

### INST-009: Regime Classifier Duplication
**Status:** Two incompatible regime classifiers exist. The `fear_greed`-derived one cannot
emit `choppy` or `risk_off`, so regime-tiered thresholds are fit against a degraded label.
**Impact:** Medium — any regime-conditional tuning is unreliable until resolved.
**Effort:** S — point all consumers at the canonical `get_last_regime()` classifier.
**Reference:** DESIGN_REVIEW_FORWARD_2026-08-05.md §R8

---

### INST-010: Sector Rotation Trajectory Not Persisted
**Status:** Only the latest sector-rotation snapshot exists (Redis, 3-day TTL). No historical
snapshots are persisted, so Emerging/Fading Leader classification is impossible.
**Impact:** Low — display-only today; blocks sector-rotation-based entry gates.
**Effort:** M
**Reference:** T258-SECTOR-ROTATION-TRAJECTORY (FITGAP_AGENT_CATALOG_2026-07-18.md)

---

## Part 3 — Broker Integration Readiness

The broker abstraction layer (`broker.py`) supports E*Trade sandbox and manual modes today.
Before connecting real capital, three gates must be built (in order):

| Gate | Status | Effort |
|---|---|---|
| Server-side admin-only on all `broker.py` endpoints | ❌ Missing (INST-004) | S |
| Real-money confirmation dialog (distinct from sandbox picker) | ❌ Missing | S |
| Pre-order buying-power check against real account | ❌ Missing | S |
| E*Trade production API credentials | ❓ Unknown — external prerequisite | — |

**Do not connect real capital until all four are resolved.** The code change is small; the
risk is not.

Broker interfaces to design for eventual live trading:

| Broker | Priority | Notes |
|---|---|---|
| Interactive Brokers (IBKR) | P1 | Best API for US + HK; TWS/IB Gateway |
| Alpaca | P1 | REST-native, US equities, paper trading built-in |
| Moomoo / Futu | P2 | HK market access; FUTU OpenAPI |
| E*Trade | P2 | Already partially implemented (sandbox) |

Abstraction interface already established in `broker.py` — new brokers implement
`place_order`, `cancel_order`, `get_order`, `get_account`, `get_positions`.

---

## Part 4 — Execution Quality Measurement

Institutional desks measure execution quality. This platform currently has none.

### 4.1 Implementation Shortfall Tracking

```python
class ExecutionQuality(Base):
    """Measure slippage vs. decision price for every paper trade."""
    __tablename__ = "execution_quality"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_id: Mapped[int] = mapped_column(ForeignKey("paper_trades.id"))
    decision_price: Mapped[float] = mapped_column(Float)   # price when signal fired
    execution_price: Mapped[float] = mapped_column(Float)  # actual fill price
    slippage_pct: Mapped[float] = mapped_column(Float)     # (exec - decision) / decision
    market_impact_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision_at: Mapped[datetime] = mapped_column(DateTime)
    executed_at: Mapped[datetime] = mapped_column(DateTime)
```

### 4.2 VWAP Benchmark

For each entry, record whether the fill was above or below the day's VWAP at execution time.
VWAP is already computed server-side — this is a one-field addition to `ExecutionQuality`.

---

## Part 5 — P&L Attribution

Currently the platform shows total P&L. Institutional attribution breaks it down by source.

```python
def compute_pnl_attribution(user_id: int, period_days: int = 90) -> dict:
    """Break portfolio return into attributable components."""
    return {
        "total_return_pct": ...,
        "benchmark_return_pct": ...,   # SPY for US, HSI for HK
        "alpha_pct": ...,              # total - benchmark
        "attribution": {
            "stock_selection": ...,    # alpha from picking stocks vs. sector
            "sector_allocation": ...,  # over/underweight sector vs. benchmark
            "entry_timing": ...,       # MAE-based: how much left on the table at entry
            "exit_timing": ...,        # MFE-based: how much left on the table at exit
            "position_sizing": ...,    # Kelly vs. actual size delta
        },
        "risk_adjusted": {
            "sharpe_30d": ...,
            "sharpe_90d": ...,
            "sortino_90d": ...,
            "calmar_90d": ...,
            "max_drawdown_pct": ...,
        }
    }
```

**Backend:** `GET /analytics/attribution?days=90`
**Frontend:** New tab on `/positions` page — "Attribution" alongside the existing allocation
donut and trade history.

---

## Part 6 — Implementation Priority

### Phase 1 — Safety & Correctness (do first, blocks everything else)

| ID | Item | Effort | Blocks |
|---|---|---|---|
| INST-003 | Fix SignalOutcome scale-out mislabeling | M | All tuners, calibration |
| INST-004 | Broker endpoint admin gate | S | Real-money connection |
| INST-008 | Environment gate on alert jobs | S | Safe prod→local sync |
| INST-009 | Retire duplicate regime classifier | S | Regime-conditional tuning |

### Phase 2 — Execution Discipline (highest trading impact)

| ID | Item | Effort | Impact |
|---|---|---|---|
| INST-001 | Wire ConditionalOrder execution | M | High — feature currently inert |
| INST-002 | Portfolio correlation pre-entry check | M | High — prevents silent doubling-up |
| INST-007 | MAE tracking on PaperTrade | S | Medium — enables entry timing measurement |
| INST-006 | Per-trade post-mortem UI | S | Medium — closes the plan-vs-actual loop |

### Phase 3 — Analytics & Attribution (after Phase 2 data accumulates)

| ID | Item | Effort | Impact |
|---|---|---|---|
| INST-005 | Pre-trade "What Could Go Wrong" | M | Medium — adversarial risk check |
| INST-010 | Sector rotation trajectory | M | Low-Medium — enables rotation-based gates |
| — | P&L attribution (§5) | M | Medium — requires MAE/MFE data from Phase 2 |
| — | Execution quality tracking (§4) | S | Medium — requires broker fills |

### Phase 4 — Live Broker Integration (after Phase 1-3 validated)

| Item | Prerequisite |
|---|---|
| E*Trade real-money gates (§3) | INST-004 + external API approval |
| IBKR adapter | Broker abstraction stable |
| Alpaca adapter | Broker abstraction stable |
| Moomoo/Futu adapter | HK market access confirmed |

---

## Part 7 — Database Migrations Required

| Migration | Table | Change |
|---|---|---|
| Add `mae_pct` | `paper_trades` | `FLOAT NULL` |
| Add `trailing_stop` exit reason | `paper_trades` | Enum extension |
| Add `execution_quality` | New table | See §4.1 |
| Add `sector_rotation_snapshots` | New table | Daily snapshot for trajectory |
| Add `user_id` to `PaperPortfolio` | `paper_portfolios` | Ownership boundary for broker gate |

---

## Part 8 — What NOT to Build (Scope Discipline)

Per the forward design review (DESIGN_REVIEW_FORWARD_2026-08-05.md §R10) and the FITGAP
analysis, the following are explicitly out of scope until cheaper items are validated:

| Item | Reason |
|---|---|
| True gamma exposure (GEX) modelling | Needs dealer positioning data — not available |
| Dark pool / block trade data | No source exists |
| Tick-level footprint charts | Requires paid Polygon upgrade |
| Earnings beat-probability model | Marginal over existing beat-rate %; highest hallucination risk |
| Squeeze-specific ML classifier | Only ~68 historical candidates — far below promotion-margin floor; revisit in 12+ months |
| Any new chart overlay | Platform's problem is not a shortage of indicators |

---

## Appendix — Related Documents

| Document | Relevance |
|---|---|
| `DESIGN_REVIEW_FORWARD_2026-08-05.md` | R4 (scale-out bug), R8 (regime), R9 (MAE), R12 (loop) |
| `FITGAP_AGENT_CATALOG_2026-07-18.md` | T258 gap items, agent-by-agent verdict |
| `COMPREHENSIVE_SYSTEM_AUDIT_2026-08-16.md` | BUG-007 (ConditionalOrder), CP-001 (auto-liquidation) |
| `DESIGN_SIX_ITEM_BATCH_2026-08-11.md` | Broker safety gates, environment kill switch |
| `SHORT_SELL_SIGNAL_ALERT_2026-08-15.md` | Squeeze alert architecture, open recommendations |
| `STRATEGIC_IMPROVEMENT_ROADMAP_2026-07-25.md` | Signal accuracy improvement roadmap |
| `FEATURE_ROADMAP_PYRAMID_GOALS_2026-08-16.md` | Pyramid trading, goals, trailing stop manager |

---

*Generated: 2026-08-16*
*Verified against codebase: 2026-08-16*
