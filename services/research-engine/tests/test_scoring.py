"""Unit tests for research-engine scoring functions.

Tests pure Python functions — no network calls, no DB, no Claude API.
"""
import numpy as np
import pandas as pd
import pytest

from src.api.routes import (
    _atr,
    _compute_yf_indicators,
    _fmt_cap,
    _last,
    _second_last,
    _score_fundamental,
    _score_technical,
)


# ── _last / _second_last ──────────────────────────────────────────────────────

def test_last_returns_final_non_none():
    assert _last([1, 2, None, 3, None]) == 3


def test_last_all_none_returns_default():
    assert _last([None, None]) is None
    assert _last([None, None], default=0.0) == 0.0


def test_last_empty_returns_default():
    assert _last([], default=-1) == -1


def test_second_last_basic():
    assert _second_last([1, 2, None, 3, None, 4]) == 3


def test_second_last_only_one_non_none():
    assert _second_last([None, None, 5]) is None


# ── _atr ─────────────────────────────────────────────────────────────────────

def _make_prices(n: int = 50, base: float = 100.0) -> list[dict]:
    rng = np.random.default_rng(0)
    prices = []
    c = base
    for _ in range(n):
        c += rng.normal(0, 1)
        c = max(c, 1.0)
        prices.append({"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1_000_000})
    return prices


def test_atr_returns_positive():
    prices = _make_prices(50)
    atr = _atr(prices)
    assert atr is not None
    assert atr > 0


def test_atr_insufficient_data_returns_none():
    assert _atr(_make_prices(5), period=14) is None


def test_atr_period_respected():
    prices = _make_prices(30)
    atr14 = _atr(prices, period=14)
    atr7 = _atr(prices, period=7)
    assert atr14 is not None and atr7 is not None


# ── _fmt_cap ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cap,expected", [
    (None, "N/A"),
    (2.5e12, "$2.50T"),
    (500e9, "$500.0B"),
    (150e6, "$150.0M"),
    (50_000, "$50,000"),
])
def test_fmt_cap(cap, expected):
    assert _fmt_cap(cap) == expected


# ── _compute_yf_indicators ────────────────────────────────────────────────────

def _make_hist(n: int = 260) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    close = 100 + rng.normal(0.05, 1.0, n).cumsum()
    close = np.maximum(close, 1.0)
    return pd.DataFrame({"Close": close})


def test_compute_yf_indicators_keys():
    hist = _make_hist(260)
    result = _compute_yf_indicators(hist)
    assert "values" in result
    assert set(result["values"].keys()) == {
        "sma_50", "sma_200", "rsi_14", "macd_line", "signal_line", "macd_histogram"
    }


def test_compute_yf_indicators_length_matches():
    n = 260
    hist = _make_hist(n)
    result = _compute_yf_indicators(hist)
    for key, arr in result["values"].items():
        assert len(arr) == n, f"{key} length mismatch: expected {n}, got {len(arr)}"


def test_compute_yf_indicators_rsi_in_range():
    hist = _make_hist(260)
    rsi_vals = [v for v in _compute_yf_indicators(hist)["values"]["rsi_14"] if v is not None]
    assert all(0 <= v <= 100 for v in rsi_vals)


def test_compute_yf_indicators_sma_200_first_199_are_none():
    hist = _make_hist(260)
    sma200 = _compute_yf_indicators(hist)["values"]["sma_200"]
    assert all(v is None for v in sma200[:199])
    assert sma200[199] is not None


# ── _score_technical ──────────────────────────────────────────────────────────

def _make_indicators(n: int = 260, price: float = 150.0) -> dict:
    """Build a minimal indicator dict compatible with _score_technical."""
    rng = np.random.default_rng(1)
    sma50 = [round(price - 10 + i * 0.1, 2) for i in range(n)]
    sma200 = [round(price - 20 + i * 0.08, 2) for i in range(n)]
    rsi_vals = [round(55 + rng.uniform(-5, 5), 1) for _ in range(n)]
    macd = [round(rng.uniform(-0.5, 0.5), 4) for _ in range(n)]
    signal = [round(rng.uniform(-0.4, 0.4), 4) for _ in range(n)]
    hist = [round(macd[i] - signal[i], 4) for i in range(n)]
    return {"values": {
        "sma_50": sma50,
        "sma_200": sma200,
        "rsi_14": rsi_vals,
        "macd_line": macd,
        "signal_line": signal,
        "macd_histogram": hist,
    }}


def _make_levels(price: float = 150.0) -> dict:
    return {"support_resistance": [
        {"kind": "support", "price": price - 10},
        {"kind": "support", "price": price - 20},
        {"kind": "resistance", "price": price + 8},
        {"kind": "resistance", "price": price + 20},
    ]}


def test_score_technical_score_in_range():
    price = 150.0
    prices = _make_prices(60, base=price)
    indicators = _make_indicators(60, price=price)
    levels = _make_levels(price=price)
    result = _score_technical({}, prices, indicators, levels, live_price=price)
    assert 0 <= result["score"] <= 100


def test_score_technical_required_keys():
    price = 150.0
    prices = _make_prices(60, base=price)
    indicators = _make_indicators(60, price=price)
    levels = _make_levels(price=price)
    result = _score_technical({}, prices, indicators, levels, live_price=price)
    for key in ("score", "trend_verdict", "rsi", "macd", "volume", "atr",
                "support_resistance", "entry_planning"):
        assert key in result, f"Missing key: {key}"


def test_score_technical_trend_verdict_valid():
    price = 150.0
    prices = _make_prices(60, base=price)
    indicators = _make_indicators(60, price=price)
    levels = _make_levels(price=price)
    result = _score_technical({}, prices, indicators, levels, live_price=price)
    assert result["trend_verdict"] in (
        "Strong Bullish", "Bullish", "Neutral", "Bearish", "Strong Bearish"
    )


def test_score_technical_bullish_when_price_above_both_smas():
    """Price clearly above both SMAs → should score higher than 50."""
    price = 200.0
    sma50 = 160.0
    sma200 = 140.0
    indicators = {"values": {
        "sma_50": [sma50, sma50 + 0.1],
        "sma_200": [sma200, sma200 + 0.05],
        "rsi_14": [58.0, 58.0],
        "macd_line": [0.5, 0.6],
        "signal_line": [0.4, 0.5],
        "macd_histogram": [0.1, 0.1],
    }}
    prices = _make_prices(20, base=price)
    result = _score_technical({}, prices, indicators, {}, live_price=price)
    assert result["score"] > 50, f"Expected score > 50 for bullish setup, got {result['score']}"


def test_score_technical_bearish_when_price_below_both_smas():
    """Price well below both SMAs → should score below 50."""
    price = 100.0
    sma50 = 130.0
    sma200 = 150.0
    indicators = {"values": {
        "sma_50": [sma50, sma50],
        "sma_200": [sma200, sma200],
        "rsi_14": [38.0, 38.0],
        "macd_line": [-0.5, -0.6],
        "signal_line": [-0.3, -0.4],
        "macd_histogram": [-0.2, -0.2],
    }}
    prices = _make_prices(20, base=price)
    result = _score_technical({}, prices, indicators, {}, live_price=price)
    assert result["score"] < 50, f"Expected score < 50 for bearish setup, got {result['score']}"


def test_score_technical_empty_inputs_returns_valid():
    """All-empty inputs should not raise and should return a bounded score."""
    result = _score_technical({}, [], {}, {}, live_price=0.0)
    assert 0 <= result["score"] <= 100


def test_score_technical_live_price_used_over_stock_dict():
    """live_price=200 should override stock dict price=100."""
    price = 200.0
    sma50 = 180.0
    sma200 = 160.0
    indicators = {"values": {
        "sma_50": [sma50, sma50],
        "sma_200": [sma200, sma200],
        "rsi_14": [55.0, 55.0],
        "macd_line": [0.2, 0.2],
        "signal_line": [0.1, 0.1],
        "macd_histogram": [0.1, 0.1],
    }}
    result_correct = _score_technical(
        {"price": 100.0}, _make_prices(20, base=price), indicators, {}, live_price=price
    )
    result_wrong = _score_technical(
        {"price": 100.0}, _make_prices(20, base=100.0), {"values": {
            "sma_50": [sma50, sma50], "sma_200": [sma200, sma200],
            "rsi_14": [55.0, 55.0], "macd_line": [0.2, 0.2],
            "signal_line": [0.1, 0.1], "macd_histogram": [0.1, 0.1],
        }}, {}, live_price=0.0  # forces fallback to stock dict price=100
    )
    # price=200 above sma50=180 → bullish; price=100 below sma50=180 → bearish
    assert result_correct["score"] > result_wrong["score"]


# ── _score_fundamental ────────────────────────────────────────────────────────

def _strong_fund() -> dict:
    return {
        "revenue_growth": 0.25,       # 25%
        "earnings_growth": 0.30,      # 30%
        "gross_margin": 0.60,
        "operating_margin": 0.25,
        "profit_margin": 0.20,
        "total_cash": 10_000_000_000,
        "total_debt": 2_000_000_000,
        "operating_cashflow": 5_000_000_000,
        "free_cashflow": 4_000_000_000,
        "total_revenue": 20_000_000_000,
        "trailing_pe": 20.0,
        "forward_pe": 16.0,
        "ev_to_revenue": 5.0,
        "ev_to_ebitda": 15.0,
        "return_on_equity": 0.25,
        "return_on_assets": 0.12,
        "trailing_eps": 5.0,
        "forward_eps": 6.0,
    }


def _weak_fund() -> dict:
    return {
        "revenue_growth": -0.10,
        "earnings_growth": -0.20,
        "gross_margin": 0.10,
        "operating_margin": 0.01,
        "profit_margin": -0.05,
        "total_cash": 500_000_000,
        "total_debt": 5_000_000_000,
        "operating_cashflow": -200_000_000,
        "free_cashflow": -500_000_000,
        "total_revenue": 2_000_000_000,
        "trailing_pe": 80.0,
        "forward_pe": 60.0,
        "return_on_equity": 0.02,
        "return_on_assets": 0.01,
    }


def test_fundamental_score_in_range():
    for fund in (_strong_fund(), _weak_fund(), {}):
        result = _score_fundamental(fund)
        assert 0 <= result["score"] <= 100


def test_fundamental_strong_scores_higher_than_weak():
    strong = _score_fundamental(_strong_fund())["score"]
    weak = _score_fundamental(_weak_fund())["score"]
    assert strong > weak, f"Strong ({strong}) should outscore weak ({weak})"


def test_fundamental_required_sections():
    result = _score_fundamental(_strong_fund())
    for section in ("score", "revenue", "eps", "margins", "balance_sheet",
                    "cash_flow", "valuation", "profitability"):
        assert section in result, f"Missing section: {section}"


def test_fundamental_empty_returns_neutral_50():
    result = _score_fundamental({})
    assert result["score"] == 50


def test_fundamental_excellent_revenue_growth():
    fund = {"revenue_growth": 0.30}
    result = _score_fundamental(fund)
    assert result["revenue"]["assessment"] == "Excellent"
    assert result["score"] > 50


def test_fundamental_weak_revenue_growth():
    fund = {"revenue_growth": -0.15}
    result = _score_fundamental(fund)
    assert result["revenue"]["assessment"] == "Weak"
    assert result["score"] < 50


def test_fundamental_undervalued_pe():
    fund = {"trailing_pe": 12.0}
    result = _score_fundamental(fund)
    assert result["valuation"]["assessment"] == "Undervalued"


def test_fundamental_overvalued_pe():
    fund = {"trailing_pe": 60.0}
    result = _score_fundamental(fund)
    assert result["valuation"]["assessment"] == "Overvalued"


def test_fundamental_excellent_roe():
    fund = {"return_on_equity": 0.25}
    result = _score_fundamental(fund)
    assert result["profitability"]["grade"] == "Excellent"


def test_fundamental_strong_balance_sheet():
    fund = {"total_cash": 10_000_000_000, "total_debt": 3_000_000_000}
    result = _score_fundamental(fund)
    assert "Strong" in result["balance_sheet"]["assessment"]


def test_fundamental_weak_balance_sheet():
    fund = {"total_cash": 1_000_000_000, "total_debt": 5_000_000_000}
    result = _score_fundamental(fund)
    assert "Weak" in result["balance_sheet"]["assessment"]


def test_fundamental_fcf_positive_excellent():
    fund = {"free_cashflow": 5_000_000_000, "total_revenue": 10_000_000_000}
    result = _score_fundamental(fund)
    assert result["cash_flow"]["assessment"] in ("Excellent", "Good")


def test_fundamental_fcf_negative_poor():
    fund = {"free_cashflow": -1_000_000_000, "total_revenue": 5_000_000_000}
    result = _score_fundamental(fund)
    assert result["cash_flow"]["assessment"] == "Poor"
