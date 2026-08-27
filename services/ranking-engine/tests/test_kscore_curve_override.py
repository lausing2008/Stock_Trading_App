"""Tests for T234-CONFIG-UNJUSTIFIED-THRESHOLDS Group B's read-side Redis curve-shape override
(kscore.py's _load_active_curve_params()) and its wiring into compute_kscore()/_curve_params().

Mirrors test_kscore_weight_override.py's own established pattern exactly (same
_patched_get_redis() sys.modules-registration technique, since kscore.py does
`from common.redis_client import get_redis` INSIDE the function body against a common package
conftest.py stubs as a bare MagicMock() — patching via unittest.mock.patch does not reach a
fresh in-function import against a mocked parent).

_load_active_curve_params()/_curve_params()/compute_kscore() are all real, DB-independent
functions — no source-text extraction needed.
"""
import json
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock

import numpy as np
import pandas as pd

from src.scoring.kscore import (
    _CURVE_DEFAULTS,
    _curve_params,
    _load_active_curve_params,
    compute_kscore,
)


@contextmanager
def _patched_get_redis(return_value=None, raise_exc=None):
    fake_client = MagicMock()
    if raise_exc is not None:
        fake_client.get.side_effect = raise_exc
    else:
        fake_client.get.return_value = return_value
    fake_module = MagicMock()
    fake_module.get_redis = MagicMock(return_value=fake_client)
    previous = sys.modules.get("common.redis_client")
    sys.modules["common.redis_client"] = fake_module
    try:
        yield fake_client
    finally:
        if previous is not None:
            sys.modules["common.redis_client"] = previous
        else:
            del sys.modules["common.redis_client"]


def _price_df(n=300):
    rng = np.random.default_rng(9)
    close = 100 + rng.normal(0, 1, n).cumsum()
    return pd.DataFrame({
        "close": close,
        "high": close + rng.uniform(0.1, 1.0, n),
        "low": close - rng.uniform(0.1, 1.0, n),
        "volume": rng.integers(1000, 5000, n),
    })


def test_no_override_returns_the_hardcoded_defaults():
    with _patched_get_redis(return_value=None):
        assert _load_active_curve_params() == _CURVE_DEFAULTS


def test_the_fallback_path_never_returns_the_module_level_defaults_object_itself():
    """Same real bug class already caught once for _WEIGHTS — _load_active_curve_params()
    must always return a FRESH dict, not the module-level _CURVE_DEFAULTS object, since
    _curve_params() can be handed a merged dict a caller might mutate."""
    with _patched_get_redis(return_value=None):
        assert _load_active_curve_params() is not _CURVE_DEFAULTS
    with _patched_get_redis(return_value="not valid json{{{"):
        assert _load_active_curve_params() is not _CURVE_DEFAULTS
    with _patched_get_redis(raise_exc=ConnectionError("redis down")):
        assert _load_active_curve_params() is not _CURVE_DEFAULTS


def test_a_valid_full_override_is_merged_over_the_defaults():
    override = {k: v * 1.1 for k, v in _CURVE_DEFAULTS.items()}
    with _patched_get_redis(return_value=json.dumps(override)):
        result = _load_active_curve_params()
    assert result == override


def test_a_partial_override_is_allowed_unlike_the_weights_override():
    """Deliberately DIFFERENT from _load_active_weights()'s own all-or-nothing rule — each of
    #17/#18/#19's curve constants is independently meaningful (unlike weights, which only mean
    something as a complete set summing to 1.0), so a single promoted parameter should apply
    on its own."""
    partial = {"volatility_scale": 2000.0}
    with _patched_get_redis(return_value=json.dumps(partial)):
        result = _load_active_curve_params()
    assert result["volatility_scale"] == 2000.0
    # every other key falls back to the hardcoded default, unchanged
    for k, v in _CURVE_DEFAULTS.items():
        if k != "volatility_scale":
            assert result[k] == v


def test_an_unknown_key_in_the_override_is_silently_ignored_not_leaked():
    override = {"volatility_scale": 2000.0, "some_unrelated_stray_key": 999.0}
    with _patched_get_redis(return_value=json.dumps(override)):
        result = _load_active_curve_params()
    assert "some_unrelated_stray_key" not in result
    assert result["volatility_scale"] == 2000.0


def test_malformed_json_falls_back_to_hardcoded_not_a_crash():
    with _patched_get_redis(return_value="not valid json{{{"):
        assert _load_active_curve_params() == _CURVE_DEFAULTS


def test_a_redis_connection_failure_falls_back_to_hardcoded_not_a_crash():
    with _patched_get_redis(raise_exc=ConnectionError("redis down")):
        assert _load_active_curve_params() == _CURVE_DEFAULTS


def test_a_non_dict_json_value_falls_back_to_hardcoded():
    with _patched_get_redis(return_value=json.dumps([1, 2, 3])):
        assert _load_active_curve_params() == _CURVE_DEFAULTS


def test_curve_params_none_resolves_to_the_live_override_not_always_the_hardcoded_default():
    """The core semantic this whole read-side is built around: _curve_params(None) must
    reflect whatever is currently PROMOTED (matching _load_active_weights()'s own "None means
    live" convention) — a caller passing no cfg override at all must still see a real, already-
    promoted curve change, not silently ignore it."""
    override = {"volatility_scale": 3000.0}
    with _patched_get_redis(return_value=json.dumps(override)):
        result = _curve_params(None)
    assert result["volatility_scale"] == 3000.0


def test_curve_params_explicit_cfg_layers_on_top_of_the_live_override_not_the_hardcoded_default():
    """A sweep candidate's own cfg override must merge on top of whatever is ALREADY live
    (e.g. a previously-promoted volatility_scale), not silently discard it in favor of the
    hardcoded default — the two are independent layers: live-override, then candidate-on-top."""
    live_override = {"volatility_scale": 3000.0}
    with _patched_get_redis(return_value=json.dumps(live_override)):
        result = _curve_params({"rsi_low": 45.0})
    assert result["volatility_scale"] == 3000.0  # the live override survived
    assert result["rsi_low"] == 45.0             # the candidate's own override also applied


def test_compute_kscore_actually_uses_the_live_curve_override_not_the_hardcoded_default():
    """The core wiring guarantee: a promoted curve override must change compute_kscore()'s
    real output, not just be readable in isolation."""
    df = _price_df()
    with _patched_get_redis(return_value=None):
        baseline = compute_kscore(df)

    # A drastically higher volatility_scale severely penalizes ANY nonzero realized vol —
    # a real random-walk series always has nonzero vol, so this must pull the score down.
    with _patched_get_redis(return_value=json.dumps({"volatility_scale": 100000.0})):
        overridden = compute_kscore(df)

    assert overridden.score != baseline.score
