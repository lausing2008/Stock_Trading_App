"""AUD-SQZ-SI-ROWORDER (2026-11-10): the newest short-interest row by date, under either order."""
from src.services.unusual_whales import _latest_short_interest_row as latest

ROWS = [{"market_date": "2026-10-15", "si_float": 0.10},
        {"market_date": "2026-10-31", "si_float": 0.22},
        {"market_date": "2026-10-24", "si_float": 0.15}]


def test_newest_row_whatever_the_order():
    assert latest(ROWS)["si_float"] == 0.22
    assert latest(list(reversed(ROWS)))["si_float"] == 0.22
    assert latest(sorted(ROWS, key=lambda r: r["market_date"]))["si_float"] == 0.22


def test_a_single_dict_and_undated_rows_keep_the_old_behaviour():
    assert latest({"si_float": 0.3}) == {"si_float": 0.3}
    assert latest([{"si_float": 0.1}, {"si_float": 0.2}]) == {"si_float": 0.1}


def test_empty_or_malformed_is_none():
    assert latest([]) is None and latest(None) is None and latest(["x"]) is None
