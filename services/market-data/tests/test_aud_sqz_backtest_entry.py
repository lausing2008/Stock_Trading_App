"""AUD-SQZ-BACKTEST-ENTRY (2026-11-10): the admin squeeze backtest scores like the live tracker."""
from datetime import date

from src.api.admin import _squeeze_backtest_return
from src.services.scheduler import _squeeze_outcome_lookup_price as lookup

# Qualifying day Mon 2 Mar closes at 100; Tue 3 Mar at 110 (the entry); 10 days later at 121.
BUCKET = [(date(2026, 3, 2), 100.0), (date(2026, 3, 3), 110.0), (date(2026, 3, 13), 121.0)]


def test_entry_is_the_next_session_close_not_the_qualifying_day():
    ret = _squeeze_backtest_return(BUCKET, date(2026, 3, 2), 10, date(2026, 4, 1), lookup)
    assert round(ret, 6) == round(121.0 / 110.0 - 1, 6)       # not 121/100 - 1


def test_window_not_yet_closed_or_no_entry_is_none():
    assert _squeeze_backtest_return(BUCKET, date(2026, 3, 2), 10, date(2026, 3, 10), lookup) is None
    assert _squeeze_backtest_return(BUCKET[:1], date(2026, 3, 2), 10, date(2026, 4, 1), lookup) is None
