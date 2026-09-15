# Backtesting Framework Audit

**Created:** 2025-08-22
**Updated:** 2025-08-22 (verified against production)
**Status:** Audit Document (No Changes Made)
**Scope:** `services/strategy-engine/` + `frontend/src/pages/strategies.tsx`

---

## Executive Summary

The backtesting framework is a **well-designed, production-quality MVP** with solid foundations:
- Vectorized execution with proper look-ahead bias prevention
- Comprehensive metric calculation (Sharpe, Sortino, Calmar, profit factor)
- Safe DSL evaluation (no `eval()`)
- Proper NaN handling during indicator warmup
- Survivorship bias flag (advisory, not blocking)
- SPY benchmark comparison with alpha calculation

**However**, it is explicitly a **single-asset, long-only, rule-based backtester** — not a full simulation engine. The paper trading engine (`paper_trading_engine.py`) has significantly more advanced features that are NOT available in the strategy backtester.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    Frontend (strategies.tsx)                     │
│  - 13 preset templates (RSI Bounce, Golden Cross, AI Signal...)  │
│  - Custom condition builder                                      │
│  - Equity curve visualization                                    │
│  - Compare up to 3 runs side-by-side                            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    API Routes (routes.py)                        │
│  - POST /backtest — run backtest                                │
│  - GET /backtests — list saved runs                             │
│  - Strategy CRUD                                                │
│  - Fetches prices from market-data service                      │
│  - Adds SPY benchmark comparison                                │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Backtest Engine (engine.py)                     │
│  - Vectorized execution                                         │
│  - 1-bar fill lag (look-ahead prevention)                       │
│  - Fee/slippage modeling (5 bps + 2 bps default)               │
│  - Metric calculation (Sharpe, Sortino, Calmar, etc.)          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                  DSL Evaluator (evaluator.py)                    │
│  - Safe boolean tree evaluation (no eval())                     │
│  - Operators: >, >=, <, <=, ==, crosses_above, crosses_below   │
│  - Logical: and, or, not                                        │
│  - Nullable boolean propagation for NaN handling               │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Feature Computation (evaluator.py)              │
│  - SMA (20, 50, 200)                                            │
│  - EMA (12, 26)                                                 │
│  - RSI (14)                                                     │
│  - MACD (12, 26, 9)                                             │
│  - ATR (14)                                                     │
│  - Bollinger Bands (20, 2σ)                                     │
│  - Volume ratio                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## What's Working Well

### 1. Look-Ahead Bias Prevention ✓

```python
# Signal detected at bar i-1 close, fill at bar i close (1-bar lag)
for i in range(1, len(feat)):
    if not in_pos and entries.iloc[i - 1]:  # ← signal from PREVIOUS bar
        entry_p = feat["close"].iloc[i] * (1 + self.slippage + self.fee)
```

**Verdict:** Correctly implemented. Cannot trade on information not yet available.

### 2. Fee/Slippage Modeling ✓

```python
def __init__(self, fee_bps: float = 5.0, slippage_bps: float = 2.0):
    self.fee = fee_bps / 1e4      # 0.05%
    self.slippage = slippage_bps / 1e4  # 0.02%
```

Entry: `close * (1 + slippage + fee)` — pays more
Exit: `close * (1 - slippage - fee)` — receives less

**Verdict:** Reasonable defaults. Total round-trip cost = 14 bps.

### 3. NaN Handling During Warmup ✓

The DSL evaluator uses nullable boolean logic to propagate "unknown" during indicator warmup:

```python
def _mask_nan_operands(result: pd.Series, *operands) -> pd.Series:
    """Convert result to nullable-boolean dtype and set positions to <NA> 
    wherever ANY operand was NaN."""
    result = result.astype("boolean")
    nan_mask = pd.Series(False, index=result.index)
    for op_ in operands:
        if isinstance(op_, pd.Series):
            nan_mask = nan_mask | op_.isna()
    result[nan_mask] = pd.NA
    return result
```

**Verdict:** Excellent. Prevents false signals during warmup period. Fixed in T247-STRATEGYENGINE-NOT-NAN.

### 4. Overflow Protection ✓

CAGR calculation guards against infinity:

```python
# T247-STRATEGYENGINE-CAGR-OVERFLOW
years = max((feat["ts"].iloc[-1] - feat["ts"].iloc[0]).days / 365.25, 1 / 365.25)
cagr = (equity.iloc[-1]) ** (1 / years) - 1 if equity.iloc[-1] > 0 else -1.0
cagr = float(cagr) if np.isfinite(cagr) else None
```

**Verdict:** Properly handles edge cases. Returns `None` instead of `inf`.

### 5. Survivorship Bias Flag ✓

```python
# AUD-BACKTEST-SURVIVORSHIP
_delisted = bool(session.execute(
    select(Stock.delisted).where(Stock.symbol == body.symbol.upper())
).scalar())
# ...
return {"backtest_id": bt.id, "symbol_delisted": _delisted, **asdict(result)}
```

**Verdict:** Advisory flag, not a hard block. Correct approach for a research tool.

### 6. Benchmark Comparison ✓

Fetches SPY for the same date range and calculates alpha:

```python
result.alpha = (
    round(result.cagr - spy_cagr, 4)
    if (result.cagr is not None and spy_cagr is not None)
    else None
)
```

**Verdict:** Useful for context. Simple alpha (not risk-adjusted).

### 7. Safe DSL Evaluation ✓

No `eval()` — walks the rule tree safely:

```python
def _evaluate_rule_nullable(rule: dict, df: pd.DataFrame) -> pd.Series:
    op = rule.get("op")
    if op in ("and", "or"):
        # ... recursive tree walk
    if op == "not":
        return ~_evaluate_rule_nullable(rule["node"], df)
    # ... comparison operators
```

**Verdict:** Secure. Cannot inject arbitrary code.

---

## Issues & Gaps

### P0: Critical — Indicator Formula Drift (VERIFIED)

**Problem:** `dsl/evaluator.py::compute_features()` reimplements RSI from scratch instead of using the canonical `shared/common/indicators.py`.

**Evidence:**
```python
# evaluator.py (strategy-engine)
rs = g / l.replace(0, np.nan)
out["rsi_14"] = 100 - 100 / (1 + rs)
# No masking for avg_loss==0 case

# indicators.py (shared/common)
rs = avg_gain / avg_loss.replace(0, np.nan)
rsi_val = 100 - (100 / (1 + rs))
return rsi_val.mask(avg_loss.notna() & avg_loss.eq(0), 100.0)  # ← explicit fix
```

**Measured Impact (production data):**

| Scenario | Bars Differing |
|----------|----------------|
| 200 random-walk trials, 500 bars each | 0 trials showed any difference |
| Unbroken 30-bar up-streak | 16 of 30 bars — canonical=100, backtest=NaN |

> ### ⚠️ CORRECTION — the "real-world occurrences" evidence below was WRONG
>
> The streak table in this section came from a SQL query counting **consecutive non-down
> days**. **That is not the trigger condition.** The mask fires only when `avg_loss` reaches
> **exactly 0**, and `ewm(alpha=1/14)` decays a prior loss by 13/14 per bar **without ever
> reaching zero** — after META's real 20-day up-streak `avg_loss` was still **`3.11e-01`**.
>
> Re-measured directly against the two formulas on **3,897 real production price bars** across
> the 8 named symbols (AAPL, HWM, META, MU, NVDA, SNDK, SOXL, XLK):
>
> | test | differing bars |
> |---|---|
> | 3,897 real production bars | **0** |
> | 200 random-walk trials × 500 bars | **0 trials** |
>
> **META, SOXL and XLK are NOT affected.** The streaks were real; the inference from them was
> not. Divergence requires a series with **no down bar since its very first bar**, which no real
> 500-bar window has.
>
> **Revised verdict:** not a P0, and not a parity bug. It is worth doing as **de-duplication**
> (one formula instead of two, removing the next chance to drift), which is how it shipped.

**Trigger condition:** `avg_loss` exactly 0 — i.e. no down bar since the start of the series.

**Verdict:** de-duplication with **no behaviour change on real data**. Not a correctness fix.

**Effort:** ~1 hour — ATR was already migrated to canonical, RSI followed the same pattern.

**STATUS: ✅ DONE (T389-RSI-CONSOLIDATE, 2026-09-15).** `compute_features()` now calls
`shared/common/indicators.py::rsi()`. 15 parity tests including five random-walk seeds asserting
bit-identical output, plus the converse case where they do differ (a no-loss window before any
pullback: canonical 100.0, old NaN).

---

### P1: Missing — Stop-Loss / Take-Profit

**Problem:** The backtester has NO stop-loss or take-profit logic. Positions exit only when the exit rule fires.

**Contrast with Paper Trading Engine:**
```python
# paper_trading_engine.py has:
"stop_loss_atr_mult": 2.0,
"target_atr_mult": 4.0,
"trail_atr_mult": 2.0,
"trail_trigger_pct": 0.05,
```

**Impact:** Backtests cannot model risk management. A strategy with good entry/exit rules but no stops will show unrealistic results.

**Recommendation:** Add optional stop-loss/take-profit parameters:
```python
class BacktestEngine:
    def __init__(
        self,
        fee_bps: float = 5.0,
        slippage_bps: float = 2.0,
        stop_loss_pct: float | None = None,  # e.g., 0.05 = 5% stop
        take_profit_pct: float | None = None,
    ):
```

---

### P1: Missing — Position Sizing

**Problem:** 100% allocation on every trade. No position sizing logic.

**Contrast with Paper Trading Engine:**
```python
"risk_per_trade_pct": 0.01,  # risk 1% of equity per trade
```

**Impact:** Backtests don't reflect realistic portfolio management. A 10-trade backtest with 100% allocation per trade is not how anyone actually trades.

**Recommendation:** Add position sizing options:
- Fixed fraction (e.g., 10% per trade)
- Risk-based (e.g., 1% equity at risk, sized by stop distance)
- Kelly criterion (optional, advanced)

---

### P1: Missing — Multi-Asset / Portfolio Backtest

**Problem:** Single-asset only. Cannot backtest a strategy across multiple symbols simultaneously.

From `engine.py` docstring:
> This is intentional simplicity for MVP — portfolio-level and multi-asset testing is a future extension.

**Impact:** Cannot test diversification effects, sector rotation strategies, or portfolio-level risk.

**Recommendation:** Future extension. Lower priority than P0/P1 items above.

---

### P2: Missing — Walk-Forward Validation

**Problem:** No walk-forward or out-of-sample testing. The entire date range is used for both rule development and evaluation.

From `skill.md`:
> Walk-forward backtest not yet implemented (deferred — 2+ weeks of work per improvement tracker)

**Impact:** Overfitting risk. A strategy optimized on historical data may not generalize.

**Note:** The ML service (`ml-prediction`) DOES have walk-forward validation:
```python
# trainer.py
"""True walk-forward validation: retrain per window, evaluate on held-out test slice."""
```

**Recommendation:** Add optional walk-forward mode that splits data into train/test windows.

---

### P2: Missing — Regime Awareness

**Problem:** No market regime filtering. Backtests run the same rules regardless of bull/bear/sideways conditions.

**Contrast with Paper Trading Engine:**
```python
# Has full regime integration
if regime == "BEAR":
    # Different behavior
```

**Impact:** A strategy that works in bull markets may fail in bear markets, but the backtest won't show this.

**Recommendation:** Add optional regime filter parameter.

---

### P3: Missing — Intraday Timeframes

**Problem:** Daily bars only. Cannot backtest intraday strategies.

**Impact:** Limited to swing/position trading strategies. Day trading strategies cannot be tested.

**Recommendation:** Lower priority. Would require intraday data ingestion first.

---

### P3: Missing — Short Selling

**Problem:** Long-only. Cannot backtest short strategies.

From `engine.py` docstring:
> single-asset, long-only, next-bar fill

**Impact:** Cannot test bearish strategies or hedging.

**Recommendation:** Add `direction` parameter to rules (BUY/SELL).

---

## Test Coverage Assessment

| Test File | Coverage |
|-----------|----------|
| `test_backtest_engine.py` | CAGR overflow, JSON compliance, multi-year sanity |
| `test_strategy_backtest_cascade.py` | ORM cascade delete |
| `test_backtest_survivorship_flag.py` | Delisting flag presence |
| `test_dsl.py` | DSL evaluation, NaN handling |
| `test_atr_consolidation.py` | ATR canonical migration |

**Total:** 5 test files, 23 tests passing.

**Still Missing (verified):**
- ❌ No tests for fee/slippage calculation correctness
- ❌ No tests for look-ahead bias prevention

These guard the engine's two most important correctness properties.

**Recommendation:** Add fee/slippage and look-ahead tests (~2 hours).

---

## Frontend Assessment

The `strategies.tsx` page is **well-designed**:

**Strengths:**
- 13 preset templates with clear descriptions
- AI Signal variants for SHORT/SWING/LONG styles
- Custom condition builder with all available features
- Equity curve visualization with SPY overlay
- Compare up to 3 runs side-by-side with analysis
- Saved runs with load/delete functionality
- LEAPS backtester integration (separate engine)

**Minor Issues:**
- No input validation for date ranges (handled server-side)
- No loading state for initial stock list fetch
- Compare analysis is basic (could add statistical significance)

---

## Comparison: Strategy Backtester vs Paper Trading Engine

| Feature | Strategy Backtester | Paper Trading Engine |
|---------|--------------------|--------------------|
| Stop-loss | ❌ | ✓ ATR-based |
| Take-profit | ❌ | ✓ ATR-based |
| Trailing stop | ❌ | ✓ Multiple types |
| Position sizing | ❌ 100% allocation | ✓ Risk-based |
| Regime awareness | ❌ | ✓ Full integration |
| Multi-asset | ❌ | ✓ Portfolio-level |
| Walk-forward | ❌ | ✓ (via decision-engine) |
| Circuit breakers | ❌ | ✓ Drawdown, daily loss |
| Signal decay | ❌ | ✓ Time-based exit |
| Fee modeling | ✓ Basic | ✓ Basic |
| Look-ahead prevention | ✓ | ✓ |

**Conclusion:** The strategy backtester is a **rule-testing tool**, not a full trading simulator. The paper trading engine is the production-grade simulation.

---

## Recommendations Summary (Revised)

| Priority | Issue | Effort | Impact | Status |
|----------|-------|--------|--------|--------|
| ~~P0~~ → P3 | RSI canonical migration | ~1h | **Downgraded** — 0 differing bars on 3,897 real rows; de-duplication only | ✅ **DONE** (T389) |
| **P1** | Fee/slippage + look-ahead tests | ~2h | High — guards core correctness | ✅ **DONE** (T388) |
| **P2** | No stop-loss/take-profit | 4-6h | Medium — backtester is rule-testing tool; paper engine has both |
| **P2** | No position sizing | 3-4h | Medium — same rationale |
| **P3** | No walk-forward | 2+ weeks | Low — ML service already has this |
| **P3** | No regime awareness | 2-3h | Low — advanced feature |
| **P3** | No intraday | 1+ week | Low — requires data |
| **P3** | No short selling | 2-3h | Low — limited use case |

---

## Recommended Implementation Order (Revised)

1. ~~**RSI canonical migration**~~ — ✅ **DONE (T389-RSI-CONSOLIDATE, 2026-09-15).** Downgraded
   from P0 first: measured at **0 differing bars on 3,897 real production rows**, so it shipped
   as de-duplication, not a parity fix. See the correction box in the P0 section above.
2. ~~**Fee/slippage + look-ahead tests**~~ — ✅ **DONE (T388-ENGINE-COSTS, 2026-09-15).** 17
   **behavioural** tests driving the real engine with synthetic prices where the answer is
   computable by hand. **8 engine sabotages all caught:** same-bar entry fill, same-bar exit
   fill, equity counting the fill bar's own return, flipped cost sign, bps/percent confusion
   (`x/1e2`), fees dropped from the equity curve only, stacked positions, unclosed final
   position. `strategy-engine` went **23 → 55 tests**.
3. **Defer stop-loss/position sizing** — still the right call. Real gaps, but the backtester is a
   rule-testing tool and the paper engine already has stops, sizing and regime integration.

**Nothing in this document is now unaddressed except the deliberately-deferred P2/P3 items.**

---

## Files Reviewed

| File | Lines | Purpose |
|------|-------|---------|
| `services/strategy-engine/src/backtest/engine.py` | ~130 | Core backtest engine |
| `services/strategy-engine/src/dsl/evaluator.py` | ~130 | DSL evaluation + features |
| `services/strategy-engine/src/api/routes.py` | ~220 | API endpoints |
| `services/strategy-engine/skill.md` | ~90 | Service documentation |
| `services/strategy-engine/tests/test_backtest_engine.py` | ~60 | CAGR overflow tests |
| `services/strategy-engine/tests/test_strategy_backtest_cascade.py` | ~40 | ORM cascade tests |
| `services/strategy-engine/tests/test_backtest_survivorship_flag.py` | ~50 | Survivorship flag tests |
| `frontend/src/pages/strategies.tsx` | ~700 | UI for backtesting |
| `frontend/src/pages/backtest-results.tsx` | ~200 | Gate replay results (different) |
| `shared/common/indicators.py` | ~70 | Canonical indicators |

---

## Conclusion

The backtesting framework is a **solid MVP** with correct fundamentals (look-ahead prevention, fee modeling, NaN handling). 

**Immediate action items:**
1. RSI canonical migration (~1h) — narrow trigger but hits exactly the momentum names traded
2. Fee/slippage + look-ahead tests (~2h) — guards core correctness, genuinely absent

**Defer:** Stop-loss/position sizing — the backtester is intentionally a rule-testing tool; the paper trading engine is the production-grade simulator with all advanced features.

No changes were made. This document is for review and prioritization.
