"""AUD-SQZ-SI-OVER100 (2026-11-10): short interest above 100% of float falls in the 30+ band."""
import importlib

sched = importlib.import_module("src.services.scheduler")


def test_over_100_percent_short_interest_gets_the_30_plus_rate():
    buckets = {"30+": {"win_rate": 0.4, "count": 33}}
    for metric in (30.0, 99.9, 100.0, 140.0):
        assert sched._squeeze_family_calibration_for_band(buckets, sched._PREBREAKOUT_CAL_BANDS, metric) == (0.4, 33), metric


def test_the_lower_bands_are_unchanged():
    buckets = {"15-20": {"win_rate": 0.3, "count": 31}}
    assert sched._squeeze_family_calibration_for_band(buckets, sched._PREBREAKOUT_CAL_BANDS, 17.0) == (0.3, 31)
    assert sched._squeeze_family_calibration_for_band(buckets, sched._PREBREAKOUT_CAL_BANDS, 14.9) == (None, None)
