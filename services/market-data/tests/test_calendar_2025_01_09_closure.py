"""The 2025-01-09 national day of mourning was a full NYSE closure; the calendar must say so."""
from datetime import date, datetime, timedelta, timezone
from common.market_calendar import NYSE_HOLIDAYS, is_us_trading_day, session_bounds
from src.services.rzy_data import break_indices


def test_2025_01_09_is_not_a_us_session():
    assert date(2025, 1, 9) in NYSE_HOLIDAYS
    assert session_bounds('US', date(2025, 1, 9)) is None
    assert not is_us_trading_day(datetime(2025, 1, 9, 15, tzinfo=timezone.utc))
    # The sessions either side are ordinary ones.
    assert session_bounds('US', date(2025, 1, 8)) and session_bounds('US', date(2025, 1, 10))


def test_daily_bars_across_the_closure_are_one_contiguous_series():
    def bar(d):
        t = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        return dict(time=t.isoformat(), closed_at=(t + timedelta(hours=21)).isoformat())
    bars = [bar(date(2025, 1, 7)), bar(date(2025, 1, 8)), bar(date(2025, 1, 10)), bar(date(2025, 1, 13))]
    assert break_indices(bars, '1d', session_bounds) == []
    # A genuinely missing session still breaks the series.
    assert break_indices([bars[0], bars[2]], '1d', session_bounds) == [1]
