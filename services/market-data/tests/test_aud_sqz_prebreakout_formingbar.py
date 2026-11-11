"""AUD-SQZ-PREBREAKOUT-FORMINGBAR (2026-11-10): pre-breakout compression uses settled bars only,
and refuses a symbol whose newest settled bar is old."""
import importlib
from datetime import date, datetime

sched = importlib.import_module("src.services.scheduler")


def test_recent_and_old_newest_bars():
    today = date(2026, 11, 10)
    assert sched._prebreakout_bars_recent(datetime(2026, 11, 9), today) is True
    assert sched._prebreakout_bars_recent(datetime(2026, 11, 3), today) is True     # 7 days
    assert sched._prebreakout_bars_recent(datetime(2026, 11, 2), today) is False
    assert sched._prebreakout_bars_recent(None, today) is False


def test_the_query_excludes_todays_forming_bar_and_checks_recency():
    src = open(sched.__file__).read()
    body = src[src.index("def check_prebreakout_alerts("):]
    body = body[:body.index("\ndef ")]
    assert "Price.ts < _pb_today_start" in body
    assert body.index("_prebreakout_bars_recent(price_rows[0].ts, _today_et())") < body.index("compression = detect_price_compression(")
