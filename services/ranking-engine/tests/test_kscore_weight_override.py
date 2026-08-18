"""Tests for T288-KSCORE-WEIGHT-SWEEP's read-side Redis weight override
(kscore.py's _load_active_weights()) and its wiring into compute_kscore().

_load_active_weights() and compute_kscore() are both real, DB-independent functions
(kscore.py only imports common.indicators, which conftest.py loads for real) — no source-text
extraction needed here.
"""
import json
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock

import numpy as np
import pandas as pd

from src.scoring.kscore import _WEIGHTS, _load_active_weights, compute_kscore


@contextmanager
def _patched_get_redis(return_value=None, raise_exc=None):
    """kscore.py does `from common.redis_client import get_redis` INSIDE the function body,
    against a `common` package that conftest.py stubs as a bare MagicMock(). Per this repo's
    own documented gotcha (CLAUDE.md's Redis-connection-pooling audit): patching
    "common.redis_client.get_redis" via unittest.mock.patch does NOT work here, since a fresh
    `import common.redis_client` against a MagicMock-stubbed parent auto-vivifies a DIFFERENT
    child mock than whatever was patched — the function's own local import never sees the
    patch. The fix is to register the fake module directly in sys.modules, which every
    `import common.redis_client` statement in the same process actually shares."""
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
    rng = np.random.default_rng(7)
    close = 100 + rng.normal(0, 1, n).cumsum()
    return pd.DataFrame({
        "close": close,
        "high": close + rng.uniform(0.1, 1.0, n),
        "low": close - rng.uniform(0.1, 1.0, n),
        "volume": rng.integers(1000, 5000, n),
    })


def test_no_override_returns_the_hardcoded_weights():
    with _patched_get_redis(return_value=None):
        assert _load_active_weights() == _WEIGHTS


def test_the_fallback_path_never_returns_the_module_level_weights_object_itself():
    """The real bug this session caught: _load_active_weights() must always return a FRESH
    dict, never a reference to the module-level _WEIGHTS object — compute_kscore() mutates its
    own copy via `del`, and returning _WEIGHTS directly would let the FIRST call with any
    missing factor permanently corrupt the hardcoded default for every later call in the same
    process. Covers every fallback branch (no key set, malformed JSON, Redis exception) since
    each has its own independent `return` statement that could regress this individually."""
    with _patched_get_redis(return_value=None):
        assert _load_active_weights() is not _WEIGHTS
    with _patched_get_redis(return_value="not valid json{{{"):
        assert _load_active_weights() is not _WEIGHTS
    with _patched_get_redis(raise_exc=ConnectionError("redis down")):
        assert _load_active_weights() is not _WEIGHTS


def test_deleting_a_key_from_the_returned_weights_does_not_corrupt_the_hardcoded_default():
    """The exact end-to-end regression scenario: call _load_active_weights(), mutate the
    result the way compute_kscore() does, then confirm _WEIGHTS itself (and a fresh
    subsequent call) are both completely unaffected."""
    with _patched_get_redis(return_value=None):
        weights = _load_active_weights()
    del weights["value"]
    assert "value" in _WEIGHTS
    with _patched_get_redis(return_value=None):
        assert "value" in _load_active_weights()


def test_a_valid_full_override_is_used_verbatim():
    override = {
        "technical": 0.30, "momentum": 0.20, "value": 0.10,
        "growth": 0.10, "volatility": 0.20, "relative_strength": 0.10,
    }
    with _patched_get_redis(return_value=json.dumps(override)):
        assert _load_active_weights() == override


def test_a_partial_override_missing_a_key_falls_back_to_hardcoded():
    """A partial JSON blob (e.g. corrupted mid-write, or an older schema) must never be used —
    the 6 weights only mean something together as a complete set that sums to 1.0."""
    partial = {"technical": 0.30, "momentum": 0.20}
    with _patched_get_redis(return_value=json.dumps(partial)):
        assert _load_active_weights() == _WEIGHTS


def test_malformed_json_falls_back_to_hardcoded_not_a_crash():
    with _patched_get_redis(return_value="not valid json{{{"):
        assert _load_active_weights() == _WEIGHTS


def test_a_redis_connection_failure_falls_back_to_hardcoded_not_a_crash():
    with _patched_get_redis(raise_exc=ConnectionError("redis down")):
        assert _load_active_weights() == _WEIGHTS


def test_a_non_dict_json_value_falls_back_to_hardcoded():
    with _patched_get_redis(return_value=json.dumps([1, 2, 3])):
        assert _load_active_weights() == _WEIGHTS


def test_compute_kscore_actually_uses_the_override_not_the_hardcoded_default():
    """The core wiring guarantee: a promoted override must change compute_kscore()'s real
    output, not just be readable in isolation. Compares the SAME inputs under two different
    active weight sets and confirms the composite score genuinely differs."""
    df = _price_df()
    with _patched_get_redis(return_value=None):
        baseline = compute_kscore(df, rs_score=80.0, value_score=60.0, growth_score=40.0)

    # A weight set that puts everything on "growth" (a low input value here) must pull the
    # composite score DOWN relative to the baseline, which spreads weight more evenly.
    lopsided = {
        "technical": 0.01, "momentum": 0.01, "value": 0.01,
        "growth": 0.96, "volatility": 0.005, "relative_strength": 0.005,
    }
    with _patched_get_redis(return_value=json.dumps(lopsided)):
        overridden = compute_kscore(df, rs_score=80.0, value_score=60.0, growth_score=40.0)

    assert overridden.score != baseline.score
    assert overridden.score < baseline.score  # pulled toward growth_score=40, well below baseline


def test_redistribution_logic_is_unchanged_by_an_override_when_a_factor_is_none():
    """A promoted override must still correctly exclude/renormalize a None factor exactly as
    compute_kscore() always has — the override only changes WHICH weights apply, never HOW
    the None-exclusion redistribution itself works."""
    df = _price_df()
    override = {
        "technical": 0.30, "momentum": 0.20, "value": 0.10,
        "growth": 0.10, "volatility": 0.20, "relative_strength": 0.10,
    }
    with _patched_get_redis(return_value=json.dumps(override)):
        comp = compute_kscore(df, rs_score=None, value_score=None, growth_score=None)
    assert comp.value is None
    assert comp.growth is None
    assert comp.relative_strength is None
    # Composite score must still be a real, finite 0-100 value computed from only the 3
    # remaining factors (technical/momentum/volatility), renormalized to sum to 1.0 — not NaN
    # or a crash from dividing by a stale, non-renormalized weight sum.
    assert 0 <= comp.score <= 100
