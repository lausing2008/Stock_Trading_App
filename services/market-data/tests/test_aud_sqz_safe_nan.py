"""AUD-SQZ-NAN (2026-11-10): routes._safe must treat NaN as missing.

NaN is not caught by `v in (...)` because NaN != NaN. A NaN short_percent_of_float then passed
every `spf * 100 < 15` gate (every NaN comparison is False) and rendered as "nan%".
"""
import math

from src.api.routes import _safe


def test_nan_is_missing():
    assert _safe({"x": float("nan")}, "x") is None


def test_infinities_and_placeholders_stay_missing():
    for v in (float("inf"), float("-inf"), None, "N/A", "", "Infinity"):
        assert _safe({"x": v}, "x") is None


def test_real_values_including_zero_are_kept():
    assert _safe({"x": 0.0}, "x") == 0.0
    assert _safe({"x": 0.27}, "x") == 0.27
    assert _safe({"x": "NASDAQ"}, "x") == "NASDAQ"
    assert not math.isnan(_safe({"x": 1e-9}, "x"))
