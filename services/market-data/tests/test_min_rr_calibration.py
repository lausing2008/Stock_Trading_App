"""Tests for SELFIMPROVE-NEVER-CALIBRATED-PARAMS's min_rr_ratio calibration.

min_rr_ratio (2.0) and regime_min_rr_ratio (3.0) were permanently hardcoded literals with no
feedback loop from real trade outcomes. _default_min_rr_ratio() (paper_trading_engine.py) is
the read side — the calibrated fallback default _should_enter() consults whenever a portfolio's
own config doesn't explicitly set min_rr_ratio/regime_min_rr_ratio. calibrate_min_rr_ratio()
(paper_portfolio.py, not tested here — heavy DB/sklearn-adjacent dependencies) is the write
side; these tests cover the read side's file-cache/fallback/reload behavior directly, matching
the same pattern already used for _load_entry_weights()/reload_entry_weights() in this file.
"""
import json

import pytest

from src.services.paper_trading_engine import (
    _default_min_rr_ratio,
    _load_min_rr_override,
    reload_min_rr_override,
)
import src.services.paper_trading_engine as pte


@pytest.fixture(autouse=True)
def _reset_cache(tmp_path, monkeypatch):
    """Point the override file at a scratch path per test and force a fresh cache load."""
    scratch = tmp_path / "min_rr_calibration.json"
    monkeypatch.setattr(pte, "_MIN_RR_OVERRIDE_FILE", scratch)
    reload_min_rr_override()
    yield scratch
    reload_min_rr_override()


def test_falls_back_to_original_hardcoded_literals_when_never_calibrated(_reset_cache):
    assert _default_min_rr_ratio("neutral") == 2.0
    assert _default_min_rr_ratio("choppy") == 3.0
    assert _default_min_rr_ratio("risk_off") == 3.0


def test_uses_calibrated_value_once_a_calibration_file_exists(_reset_cache):
    _reset_cache.write_text(json.dumps({"min_rr_ratio": 1.75, "regime_min_rr_ratio": 2.5}))
    reload_min_rr_override()
    assert _default_min_rr_ratio("neutral") == 1.75
    assert _default_min_rr_ratio("choppy") == 2.5
    assert _default_min_rr_ratio("risk_off") == 2.5


def test_cache_is_not_reloaded_until_reload_min_rr_override_is_called(_reset_cache):
    assert _default_min_rr_ratio("neutral") == 2.0  # loads + caches "no file yet"
    _reset_cache.write_text(json.dumps({"min_rr_ratio": 1.5}))
    # Still the cached (pre-file) value — reload_min_rr_override() was not called
    assert _default_min_rr_ratio("neutral") == 2.0
    reload_min_rr_override()
    assert _default_min_rr_ratio("neutral") == 1.5


def test_load_min_rr_override_returns_empty_dict_when_no_file(_reset_cache):
    assert _load_min_rr_override() == {}


def test_malformed_calibration_file_falls_back_safely(_reset_cache):
    _reset_cache.write_text("{not valid json")
    reload_min_rr_override()
    assert _default_min_rr_ratio("neutral") == 2.0
    assert _default_min_rr_ratio("choppy") == 3.0
