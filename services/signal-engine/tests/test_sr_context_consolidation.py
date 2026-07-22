"""Tests for AUD-DUPLOGIC's consolidation of _sr_context() onto technical-analysis's canonical
detect_sr_context() (GET /ta/{symbol}/levels' new sr_context field), instead of independently
reimplementing pivot detection with a different window/order in this file.

_sr_context() now fetches the classification from technical-analysis when a symbol is given,
falling back to the original local computation (unchanged, still present in this file) if the
HTTP call fails for any reason — technical-analysis being unreachable must never block signal
generation.
"""
import numpy as np
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch

from src.generators.signals import _sr_context, _fetch_sr_context_from_ta


def _make_df(n: int = 100, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + rng.normal(0, 1, n).cumsum() * 0.3
    close = np.maximum(close, 10.0)
    return pd.DataFrame({
        "close": close, "high": close + 0.3, "low": close - 0.3,
        "open": close, "volume": rng.integers(500_000, 2_000_000, n).astype(float),
    })


_FAKE_REMOTE_RESULT = {
    "sr_context": "breakout",
    "sr_nearest_resistance": 105.0,
    "sr_nearest_support": 95.0,
    "sr_52w_high": 106.0,
    "sr_52w_low": 90.0,
}


def test_uses_remote_result_when_symbol_provided_and_ta_reachable():
    df = _make_df()
    with patch("src.generators.signals._fetch_sr_context_from_ta", return_value=_FAKE_REMOTE_RESULT):
        result = _sr_context(df, symbol="AAPL")
    assert result == _FAKE_REMOTE_RESULT


def test_falls_back_to_local_computation_when_ta_unreachable():
    """The core fail-open guard: if technical-analysis is unreachable (fetch returns None),
    _sr_context() must fall through to its own local computation, not raise or return None."""
    df = _make_df()
    with patch("src.generators.signals._fetch_sr_context_from_ta", return_value=None):
        result = _sr_context(df, symbol="AAPL")
    assert result is not None
    assert result["sr_context"] in ("breakout", "at_resistance", "at_support", "neutral")
    assert "sr_nearest_resistance" in result


def test_skips_remote_fetch_entirely_when_symbol_omitted():
    """No symbol -> no HTTP call at all, straight to local computation — confirms the local
    fallback path is still fully reachable on its own for any caller that doesn't have a
    symbol handy."""
    df = _make_df()
    with patch("src.generators.signals._fetch_sr_context_from_ta") as mock_fetch:
        result = _sr_context(df, symbol=None)
    mock_fetch.assert_not_called()
    assert result["sr_context"] in ("breakout", "at_resistance", "at_support", "neutral")


def test_fetch_helper_returns_none_on_non_200():
    class _FakeResp:
        status_code = 500
        def json(self):
            return {}

    class _FakeClient:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def get(self, *a, **kw):
            return _FakeResp()

    with patch("src.generators.signals.httpx.Client", return_value=_FakeClient()):
        result = _fetch_sr_context_from_ta("AAPL")
    assert result is None


def test_fetch_helper_returns_none_on_network_exception():
    with patch("src.generators.signals.httpx.Client", side_effect=ConnectionError("unreachable")):
        result = _fetch_sr_context_from_ta("AAPL")
    assert result is None


def test_fetch_helper_returns_none_when_response_missing_sr_context_field():
    """A malformed/older /levels response with no sr_context field must degrade to None
    (triggering the local fallback), not crash trying to read a missing key."""
    class _FakeResp:
        status_code = 200
        def json(self):
            return {"symbol": "AAPL", "support_resistance": []}  # no sr_context field

    class _FakeClient:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def get(self, *a, **kw):
            return _FakeResp()

    with patch("src.generators.signals.httpx.Client", return_value=_FakeClient()):
        result = _fetch_sr_context_from_ta("AAPL")
    assert result is None


def test_fetch_helper_returns_the_real_dict_on_success():
    class _FakeResp:
        status_code = 200
        def json(self):
            return {"symbol": "AAPL", "sr_context": _FAKE_REMOTE_RESULT}

    class _FakeClient:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def get(self, *a, **kw):
            return _FakeResp()

    with patch("src.generators.signals.httpx.Client", return_value=_FakeClient()):
        result = _fetch_sr_context_from_ta("AAPL")
    assert result == _FAKE_REMOTE_RESULT
