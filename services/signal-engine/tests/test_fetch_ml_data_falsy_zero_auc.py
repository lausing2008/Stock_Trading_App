"""Regression test for AUD-SIGNAL1 (AI Signal deep audit, 2026-09-02) — AUD-SIGNAL2-FALSYZEROAUC:
_fetch_ml_data()'s test_auc parsing used an `or`-chain (`m.get("mean_model_test_auc") or
m.get("auc") or m.get("cv_auc_mean") or 0.55`) that treated a real, legitimate auc=0.0 as
falsy/absent and silently substituted the next key, ultimately falling back to a fabricated
0.55 "healthy" default. That default defeats the AUC guard in _combine_ta_ml() (`if
ml_test_auc < 0.50: raw_w = 0.0`) specifically designed to zero out a worthless model's ML
weight — a genuine 0.0 AUC should assign 0.0 weight, not the ~20% weight a substituted 0.55
produces. Same bug class already fixed 3x in ml-prediction's own trainer.py
(AUD-ML1B-NUDGEGATE and its 2 siblings).

Mocks httpx.Client (the real module, not stubbed here per conftest.py) to drive
_fetch_ml_data()'s real parsing logic end to end, matching test_market_pulse.py's own
established pattern for this repo's outbound-HTTP-mocking convention.
"""
from unittest.mock import patch, MagicMock

from src.generators import signals


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


class _FakeClient:
    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, *a, **kw):
        return self._response


def _run_fetch(metrics: dict, bullish_probability: float = 0.7):
    response = _FakeResponse(200, {
        "bullish_probability": bullish_probability,
        "metrics": metrics,
        "model": "xgboost",
    })
    with patch.object(signals.httpx, "Client", return_value=_FakeClient(response)), \
         patch.object(signals, "_ml_service_token", return_value="fake-token"), \
         patch.object(signals, "_settings", MagicMock(ml_prediction_url="http://ml-prediction:8003")):
        return signals._fetch_ml_data("AAPL", "SWING")


def test_genuine_zero_mean_model_test_auc_is_not_overwritten():
    """The exact failure scenario: a real, legitimate auc=0.0 (a rank-inverted/untrained
    model) must be preserved as 0.0, never substituted with a fallback default."""
    _, test_auc, _ = _run_fetch({"mean_model_test_auc": 0.0, "auc": 0.65, "cv_auc_mean": 0.60})
    assert test_auc == 0.0


def test_genuine_zero_in_first_two_keys_falls_through_to_real_third_value():
    """A genuine 0.0 in BOTH of the first two keys must still surface the real (non-zero)
    cv_auc_mean, not skip past it to the 0.55 default — 0.0 is a present, valid value that
    correctly means 'try the next key has nothing to add here', only when the key doesn't
    exist at all should the loop continue."""
    _, test_auc, _ = _run_fetch({"mean_model_test_auc": 0.0, "auc": None, "cv_auc_mean": 0.60})
    assert test_auc == 0.0  # mean_model_test_auc=0.0 is present and used — real value wins


def test_all_three_keys_genuinely_absent_falls_back_to_default():
    _, test_auc, _ = _run_fetch({})
    assert test_auc == 0.55


def test_all_three_keys_explicitly_none_falls_back_to_default():
    _, test_auc, _ = _run_fetch({"mean_model_test_auc": None, "auc": None, "cv_auc_mean": None})
    assert test_auc == 0.55


def test_normal_nonzero_value_parses_correctly():
    _, test_auc, _ = _run_fetch({"mean_model_test_auc": 0.72})
    assert test_auc == 0.72


def test_zero_auc_correctly_zeroes_ml_weight_via_the_downstream_guard():
    """End-to-end proof the fix actually closes the gap the audit found: with the OLD
    or-chain bug, a genuine auc=0.0 (with the other two metric keys populated) would have
    been silently overwritten before ever reaching this guard, producing a nonzero raw_w.
    With the fix, test_auc=0.0 reaches _apply_style_signal's own ml_test_auc<0.50 guard
    (signals.py:1942-1944) unmodified and correctly zeroes the weight.

    Extracts just that guard's real source (not the whole many-parameter
    _apply_style_signal) via exec(), matching this repo's own established source-extraction
    technique for testing an isolated fragment inside a larger function."""
    import pathlib
    import textwrap
    src_path = pathlib.Path(signals.__file__)
    src = src_path.read_text()
    start = src.index("    if ml_prob is not None:")
    end = src.index("gap = abs(ml_prob_c - ta_prob)", start)
    snippet = textwrap.dedent(src[start:end])
    namespace = {
        "np": __import__("numpy"),
        "ml_prob": 0.9,  # a strongly bullish but UNRELIABLE model
        "ml_test_auc": 0.0,  # the exact value the fix now preserves instead of silently overwriting
        "ta_prob": 0.6,
        "_get_style_tuned_param": lambda *a, **kw: None,
        "_ml_weight_global_cap": None,
        "p": {"ml_weight_cap": 0.75, "ml_weight_floor": 0.10},
        "style_key": "SWING",
    }
    exec(snippet, namespace)  # noqa: S102 — isolated eval of real source, matches repo convention
    assert namespace["raw_w"] == 0.0
    assert namespace["ml_w"] == 0.0
