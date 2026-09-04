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


# ── AUD-MINRR-MARKETBLIND: per-market override ───────────────────────────────
# regime_min_rr_ratio used to be a single pooled value applied to every market — a HK
# candidate was rejected against a threshold calibrated almost entirely off US trade volume,
# since HK trades far less often. by_market now lets a thin market's own capped value win.

def test_market_defaults_to_us_pooled_value_when_no_by_market_key_present(_reset_cache):
    """A calibration file written before this fix (no by_market key at all) must behave
    identically to before — every market falls back to the pooled top-level value."""
    _reset_cache.write_text(json.dumps({"min_rr_ratio": 1.75, "regime_min_rr_ratio": 3.38}))
    reload_min_rr_override()
    assert _default_min_rr_ratio("choppy", "US") == 3.38
    assert _default_min_rr_ratio("choppy", "HK") == 3.38


def test_market_specific_regime_min_rr_ratio_wins_over_pooled_value(_reset_cache):
    _reset_cache.write_text(json.dumps({
        "min_rr_ratio": 2.25,
        "regime_min_rr_ratio": 3.38,
        "by_market": {"HK": {"n_trades": 19, "regime_min_rr_ratio": 2.9}},
    }))
    reload_min_rr_override()
    assert _default_min_rr_ratio("choppy", "HK") == 2.9
    # US has no by_market entry in this file — falls through to the pooled value, unaffected.
    assert _default_min_rr_ratio("choppy", "US") == 3.38


def test_market_missing_from_by_market_falls_back_to_pooled_value(_reset_cache):
    """A market calibrate_min_rr_ratio() never saw any trades for at all (by_market has no key
    for it) must fall back to the pooled value, not error or return None."""
    _reset_cache.write_text(json.dumps({
        "min_rr_ratio": 2.25,
        "regime_min_rr_ratio": 3.38,
        "by_market": {"HK": {"n_trades": 19, "regime_min_rr_ratio": 2.9}},
    }))
    reload_min_rr_override()
    assert _default_min_rr_ratio("choppy", "JP") == 3.38


def test_market_param_defaults_to_us_when_not_passed(_reset_cache):
    """Every pre-existing call site that doesn't pass market at all must keep resolving
    exactly as before this fix — market defaults to "US"."""
    _reset_cache.write_text(json.dumps({
        "min_rr_ratio": 2.25,
        "regime_min_rr_ratio": 3.38,
        "by_market": {"HK": {"n_trades": 19, "regime_min_rr_ratio": 2.9}},
    }))
    reload_min_rr_override()
    assert _default_min_rr_ratio("choppy") == 3.38


def test_neutral_regime_still_ignores_by_market_regime_key(_reset_cache):
    """by_market only ever stores a regime_min_rr_ratio override (never re-sweeps the neutral-
    tier min_rr_ratio independently) — neutral regime must still resolve the pooled min_rr_ratio
    even when a by_market entry exists for that market."""
    _reset_cache.write_text(json.dumps({
        "min_rr_ratio": 2.25,
        "regime_min_rr_ratio": 3.38,
        "by_market": {"HK": {"n_trades": 19, "regime_min_rr_ratio": 2.9}},
    }))
    reload_min_rr_override()
    assert _default_min_rr_ratio("neutral", "HK") == 2.25
