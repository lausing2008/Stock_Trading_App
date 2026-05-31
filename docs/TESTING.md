# Testing Guide

Unit tests for all backend services. Tests run locally without Docker — no database, no Redis, no live API calls.

---

## Running tests

```bash
# Run all services
make test

# Run a single service
cd services/signal-engine && python -m pytest -q

# Verbose output (shows each test name)
cd services/research-engine && python -m pytest -v

# Run a specific test file
cd services/research-engine && python -m pytest tests/test_scoring.py -v

# Run tests matching a keyword
cd services/signal-engine && python -m pytest -k "ta_score" -v
```

---

## Coverage by service

| Service | Tests | What is covered |
|---|---|---|
| **market-data** | 2 | OHLCV validation (bad high/low, negative prices) |
| **technical-analysis** | 6 | SMA, RSI, MACD, Bollinger Bands, VWAP, pattern detection |
| **ml-prediction** | 1 | Feature matrix shape, column names, label cardinality |
| **ranking-engine** | 1 | K-Score and all sub-scores bounded 0–100 |
| **signal-engine** | 28 | TA scoring, signal decision logic, Stochastic RSI, ADX, weekly alignment, pattern adjustment |
| **strategy-engine** | 3 | DSL rule evaluation (simple rule, AND combinator, crosses_above) |
| **portfolio-optimizer** | 2 | MVO and Risk Parity weights sum to 1, per-asset weight bounds |
| **research-engine** | 37 | Helper utilities, yfinance indicator computation, technical scoring, fundamental scoring |
| **api-gateway** | — | Proxy-only service; no unit-testable logic |
| **Total** | **80** | |

---

## signal-engine — `tests/test_signal_generator.py`

Tests the pure functions in `src/generators/signals.py`. No HTTP calls — all network fetches are bypassed by testing the functions in isolation with synthetic DataFrames.

### `_stoch_rsi`
| Test | Verifies |
|---|---|
| `test_stoch_rsi_range` | %K and %D are in [0, 1]; output series length matches input |
| `test_stoch_rsi_short_series_returns_midpoint` | < 14 bars does not raise; returns a valid float |

### `_adx`
| Test | Verifies |
|---|---|
| `test_adx_returns_three_floats` | Returns (ADX, +DI, −DI) as floats |
| `test_adx_non_negative` | All three values ≥ 0 |
| `test_adx_strong_trend` | Monotonically rising price produces ADX > 25 |

### `_ta_score`
| Test | Verifies |
|---|---|
| `test_ta_score_output_range` | Score in [0, 1] |
| `test_ta_score_returns_reasons_dict` | All expected reason keys are present |
| `test_ta_score_bullish_uptrend` | Strong uptrend (trend=+0.3) → score > 0.5 |
| `test_ta_score_bearish_downtrend` | Strong downtrend (trend=−0.3) → score < 0.5 |
| `test_ta_score_handles_short_data` | 30-bar DataFrame does not raise |

### `_weekly_ta_score`
| Test | Verifies |
|---|---|
| `test_weekly_ta_score_range` | Score in [0, 1] |
| `test_weekly_ta_score_too_few_bars_returns_neutral` | < 26 bars → exactly 0.5 |
| `test_weekly_ta_score_empty_returns_neutral` | Empty DataFrame → 0.5 |

### `_pattern_score_adjustment`
| Test | Verifies |
|---|---|
| `test_pattern_adjustment_no_patterns` | Empty list → 0.0 adjustment, empty active list |
| `test_pattern_adjustment_bullish` | `bull_flag` → positive adjustment |
| `test_pattern_adjustment_bearish` | `head_and_shoulders` → negative adjustment |
| `test_pattern_adjustment_clipped` | Four stacked bullish patterns → clipped at 0.15 |
| `test_pattern_adjustment_stale_pattern_ignored` | Pattern 200 bars old → 0.0 (recency < 0.1) |

### `_decide`
Parametrized over 8 `(probability, market_regime)` inputs:

| Prob | Regime | Expected |
|---|---|---|
| 0.80 | bull | BUY |
| 0.70 | bull | BUY |
| 0.60 | bull | HOLD |
| 0.45 | bull | WAIT |
| 0.20 | bull | SELL |
| 0.70 | bear | HOLD (bear raises BUY threshold to 0.73) |
| 0.75 | bear | BUY |
| 0.40 | bear | WAIT |

Additional boundary tests: exactly at the BUY threshold (0.651 → BUY), below SELL threshold (0.34 → SELL).

---

## research-engine — `tests/test_scoring.py`

Tests the pure scoring and utility functions in `src/api/routes.py`. No Claude API calls, no yfinance network calls, no upstream service calls.

### Helper utilities
| Test | Verifies |
|---|---|
| `test_last_returns_final_non_none` | Skips trailing None values |
| `test_last_all_none_returns_default` | Returns provided default when all None |
| `test_last_empty_returns_default` | Empty list → default |
| `test_second_last_basic` | Returns second non-None from end |
| `test_second_last_only_one_non_none` | Only one non-None → returns default (None) |

### `_atr`
| Test | Verifies |
|---|---|
| `test_atr_returns_positive` | ATR > 0 on valid price history |
| `test_atr_insufficient_data_returns_none` | < 15 bars with period=14 → None |
| `test_atr_period_respected` | period=14 and period=7 both return non-None for 30 bars |

### `_fmt_cap`
Parametrized: `None → "N/A"`, `2.5T → "$2.50T"`, `500B → "$500.0B"`, `150M → "$150.0M"`, `50000 → "$50,000"`.

### `_compute_yf_indicators`
| Test | Verifies |
|---|---|
| `test_compute_yf_indicators_keys` | Returns `values` dict with exactly the 6 expected keys |
| `test_compute_yf_indicators_length_matches` | Every series has the same length as the input DataFrame |
| `test_compute_yf_indicators_rsi_in_range` | All non-None RSI values are in [0, 100] |
| `test_compute_yf_indicators_sma_200_first_199_are_none` | First 199 SMA-200 values are None; index 199 is not |

### `_score_technical`
| Test | Verifies |
|---|---|
| `test_score_technical_score_in_range` | Score in [0, 100] |
| `test_score_technical_required_keys` | All 8 top-level keys present in output |
| `test_score_technical_trend_verdict_valid` | Verdict is one of the 5 defined strings |
| `test_score_technical_bullish_when_price_above_both_smas` | Price > SMA50 > SMA200, healthy RSI → score > 50 |
| `test_score_technical_bearish_when_price_below_both_smas` | Price < SMA50 < SMA200, weak RSI → score < 50 |
| `test_score_technical_empty_inputs_returns_valid` | All-empty inputs do not raise; score is valid |
| `test_score_technical_live_price_used_over_stock_dict` | `live_price=200` produces higher score than `live_price=0` with `stock["price"]=100` |

The last test is the regression guard for the bug where price was always 0 because `_score_technical` was reading from the stock metadata dict (which has no price field) instead of the live price.

### `_score_fundamental`
| Test | Verifies |
|---|---|
| `test_fundamental_score_in_range` | Score in [0, 100] for strong, weak, and empty inputs |
| `test_fundamental_strong_scores_higher_than_weak` | Strong fundamentals outscore weak ones |
| `test_fundamental_required_sections` | All 8 output sections present |
| `test_fundamental_empty_returns_neutral_50` | `{}` → score exactly 50 |
| `test_fundamental_excellent_revenue_growth` | 30% YoY → assessment "Excellent", score > 50 |
| `test_fundamental_weak_revenue_growth` | −15% YoY → assessment "Weak", score < 50 |
| `test_fundamental_undervalued_pe` | P/E = 12 → "Undervalued" |
| `test_fundamental_overvalued_pe` | P/E = 60 → "Overvalued" |
| `test_fundamental_excellent_roe` | ROE = 25% → grade "Excellent" |
| `test_fundamental_strong_balance_sheet` | Cash $10B, Debt $3B → "Strong Balance Sheet" |
| `test_fundamental_weak_balance_sheet` | Cash $1B, Debt $5B → "Weak Balance Sheet" |
| `test_fundamental_fcf_positive_excellent` | FCF = $5B on $10B revenue → "Excellent" or "Good" |
| `test_fundamental_fcf_negative_poor` | FCF = −$1B → "Poor" |

---

## How tests avoid Docker dependencies

All services import from `shared/` (`common`, `db`) and from third-party packages (`structlog`, `yfinance`, `httpx`) that are only installed inside Docker containers. Each service that needs this has a `tests/conftest.py` that runs before any test module is imported:

```python
# services/<svc>/tests/conftest.py
import sys
from unittest.mock import MagicMock

_stubs = ["structlog", "common", "common.config", "common.logging", "db", "httpx", ...]
for _m in _stubs:
    sys.modules.setdefault(_m, MagicMock())

# Patch module-level calls that run at import time
import common.config as _cfg
_cfg.get_settings = MagicMock(return_value=MagicMock())
```

`sys.modules.setdefault` inserts a `MagicMock` for any module name that isn't already loaded, so all attribute lookups on it (`common.config.get_settings()`, `db.session.SessionLocal`, etc.) return further `MagicMock` objects instead of raising. Only services that test pure Python functions need this — technical-analysis, ranking-engine, strategy-engine, portfolio-optimizer, and ml-prediction all have self-contained dependencies and don't need the stub.

---

## Adding new tests

1. Create `services/<svc>/tests/test_<topic>.py`
2. If your service imports from `shared/` or has Docker-only deps, copy the `conftest.py` from an existing service (e.g. `signal-engine`) and adjust the `_stubs` list
3. Test only pure functions — any function that takes plain Python/pandas/numpy values and returns plain values. Functions that make HTTP calls or DB queries should be tested via integration tests (not covered here)
4. Run `make test` to confirm everything still passes
