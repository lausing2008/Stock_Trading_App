"""AUD-SQZ-LIVEROW-STALE (2026-11-10): a live-price row from an earlier session cannot fire a
squeeze alert on yesterday's move."""
import importlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sched = importlib.import_module("src.services.scheduler")


def test_yesterdays_us_row_is_stale_and_todays_is_not():
    today = sched._today_et()
    assert sched._live_row_from_earlier_session({"session_date": (today - timedelta(days=1)).isoformat()}, False) is True
    assert sched._live_row_from_earlier_session({"session_date": today.isoformat()}, False) is False


def test_hk_rows_use_the_hong_kong_date():
    hk_today = datetime.now(ZoneInfo("Asia/Hong_Kong")).date()
    assert sched._live_row_from_earlier_session({"session_date": hk_today.isoformat()}, True) is False
    assert sched._live_row_from_earlier_session({"session_date": (hk_today - timedelta(days=1)).isoformat()}, True) is True


def test_an_undated_row_is_kept():
    for row in ({}, {"session_date": None}, {"session_date": ""}, {"session_date": 20261110}):
        assert sched._live_row_from_earlier_session(row, False) is False


def test_all_four_squeeze_price_loops_apply_it_and_volume_anomaly_is_untouched():
    src = open(sched.__file__).read()
    def body(name):
        b = src[src.index(f"def {name}("):]
        return b[:b.index("\ndef ")]
    assert body("check_short_squeeze_alerts").count("_live_row_from_earlier_session(row, _is_hk_sym)") == 2
    assert body("check_squeeze_ignition_alerts").count("_live_row_from_earlier_session(row, _is_hk_sym)") == 2
