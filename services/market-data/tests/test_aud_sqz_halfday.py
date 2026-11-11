"""AUD-SQZ-HALFDAY (2026-11-10): the US session closes at 13:00 ET on NYSE half-days."""
from datetime import datetime
from zoneinfo import ZoneInfo

from src.services.paper_trading_engine import _is_market_hours

ET = ZoneInfo("America/New_York")


def test_half_day_closes_at_one_pm():
    assert _is_market_hours("US", datetime(2026, 11, 27, 12, 59, tzinfo=ET)) is True
    assert _is_market_hours("US", datetime(2026, 11, 27, 13, 0, tzinfo=ET)) is False
    assert _is_market_hours("US", datetime(2026, 11, 27, 15, 30, tzinfo=ET)) is False
    assert _is_market_hours("US", datetime(2026, 12, 24, 14, 0, tzinfo=ET)) is False


def test_an_ordinary_day_still_closes_at_four():
    assert _is_market_hours("US", datetime(2026, 11, 25, 15, 59, tzinfo=ET)) is True
    assert _is_market_hours("US", datetime(2026, 11, 25, 16, 0, tzinfo=ET)) is False
