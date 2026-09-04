"""Regression tests for AUD-SIGCORROBORATE.

_check_uw_short_interest_disagreement() cross-checks the free yfinance-derived
short_pct_float (the sole driver of the SWING/GROWTH squeeze-boost gate in
_apply_style_signal()) against Unusual Whales' real si_float, mirroring market-data's own
check_short_squeeze_alerts() AUD-SQUEEZE3-UWSHORTINTERESTCORROBORATION fix. It must only ever
ANNOTATE (via a reasons flag) — never overwrite short_pct_float or otherwise alter the boost
gate's own threshold decision.
"""
from unittest.mock import MagicMock

import src.generators.signals as signals_mod
from src.generators.signals import _check_uw_short_interest_disagreement


def _mock_response(status_code=200, payload=None):
    return MagicMock(status_code=status_code, json=lambda: payload or {})


def _set_response(monkeypatch, response):
    client = MagicMock()
    client.__enter__.return_value.get.return_value = response
    monkeypatch.setattr(signals_mod.httpx, "Client", lambda **kw: client)


def test_no_flag_when_short_pct_float_is_none(monkeypatch):
    """Nothing to corroborate if the free reading itself is missing — must not even attempt
    the HTTP call."""
    client = MagicMock()
    monkeypatch.setattr(signals_mod.httpx, "Client", lambda **kw: client)
    result = _check_uw_short_interest_disagreement("AAPL", None)
    assert result is None
    client.__enter__.return_value.get.assert_not_called()


def test_no_flag_when_uw_unavailable(monkeypatch):
    _set_response(monkeypatch, _mock_response(200, {"symbol": "AAPL", "available": False, "reason": "unusual_whales_disabled"}))
    result = _check_uw_short_interest_disagreement("AAPL", 0.15)
    assert result is None


def test_no_flag_when_http_call_fails(monkeypatch):
    _set_response(monkeypatch, _mock_response(500, {}))
    result = _check_uw_short_interest_disagreement("AAPL", 0.15)
    assert result is None


def test_no_flag_when_http_client_raises(monkeypatch):
    client = MagicMock()
    client.__enter__.return_value.get.side_effect = Exception("connection refused")
    monkeypatch.setattr(signals_mod.httpx, "Client", lambda **kw: client)
    result = _check_uw_short_interest_disagreement("AAPL", 0.15)
    assert result is None


def test_no_flag_when_readings_agree(monkeypatch):
    """15% free vs 16% UW — well within the 20% relative threshold, no disagreement."""
    _set_response(monkeypatch, _mock_response(200, {"symbol": "AAPL", "available": True, "short_percent_of_float": 16.0}))
    result = _check_uw_short_interest_disagreement("AAPL", 0.15)
    assert result is None


def test_flag_set_when_uw_reading_diverges_materially(monkeypatch):
    """15% free vs 30% UW — a 100% relative difference, well past the 20% threshold."""
    _set_response(monkeypatch, _mock_response(200, {"symbol": "AAPL", "available": True, "short_percent_of_float": 30.0}))
    result = _check_uw_short_interest_disagreement("AAPL", 0.15)
    assert result is not None
    assert result["short_interest_uw_disagrees"] is True
    assert result["short_interest_uw_short_percent_of_float"] == 30.0


def test_flag_never_overwrites_the_free_reading_itself(monkeypatch):
    """The whole point of corroboration, not replacement: the function's return value must
    only ever contain the uw_* keys, never short_percent_of_float or short_pct_float — the
    caller's own free reading must remain untouched regardless of disagreement."""
    _set_response(monkeypatch, _mock_response(200, {"symbol": "AAPL", "available": True, "short_percent_of_float": 30.0}))
    result = _check_uw_short_interest_disagreement("AAPL", 0.15)
    assert "short_percent_of_float" not in result
    assert "short_pct_float" not in result


def test_no_flag_when_uw_short_percent_of_float_missing(monkeypatch):
    _set_response(monkeypatch, _mock_response(200, {"symbol": "AAPL", "available": True, "short_percent_of_float": None}))
    result = _check_uw_short_interest_disagreement("AAPL", 0.15)
    assert result is None


def test_boundary_just_under_threshold_no_flag(monkeypatch):
    """15% free vs 17.9% UW — a ~19.3% relative difference, just under the 20% threshold."""
    _set_response(monkeypatch, _mock_response(200, {"symbol": "AAPL", "available": True, "short_percent_of_float": 17.9}))
    result = _check_uw_short_interest_disagreement("AAPL", 0.15)
    assert result is None


def test_boundary_just_over_threshold_flags(monkeypatch):
    """15% free vs 18.5% UW — a ~23.3% relative difference, just over the 20% threshold."""
    _set_response(monkeypatch, _mock_response(200, {"symbol": "AAPL", "available": True, "short_percent_of_float": 18.5}))
    result = _check_uw_short_interest_disagreement("AAPL", 0.15)
    assert result is not None
    assert result["short_interest_uw_disagrees"] is True
