"""AUD-EARNIMPACT-AFTEROPEN: the after-open drift starts after the news, never before it."""
from datetime import date, timedelta
import pytest
from src.services.earnings import after_open_drift, _AFTER_OPEN_SESSIONS

D0 = date(2026, 1, 5)  # a Monday


def bars(opens_closes, start=D0):
    return [(start + timedelta(days=i), o, c) for i, (o, c) in enumerate(opens_closes)]


# Report day closes at 100. Overnight the stock jumps to 110 and then drifts to 112.
STOCK = bars([(99, 100), (110, 109), (109, 110), (110, 111), (111, 111), (111, 112), (112, 112)])
FLAT_MARKET = {d: (50.0, 50.0) for d, _, _ in STOCK}


def test_after_close_report_starts_at_the_next_open_and_excludes_the_jump():
    raw, excess, basis = after_open_drift(STOCK, FLAT_MARKET, D0, "postmarket")
    # Entry 110 (next open), exit the close 5 sessions later counting the entry session: 112.
    assert raw == pytest.approx(112 / 110 - 1)
    assert excess == pytest.approx(raw) and basis == "provider_time"
    # The old definition (report-day close to 5 bars later) would have reported the jump.
    assert 112 / 100 - 1 > 0.1 and raw < 0.02


def test_before_open_report_starts_at_that_mornings_open():
    raw, _, basis = after_open_drift(STOCK, FLAT_MARKET, D0, "premarket")
    assert raw == pytest.approx(STOCK[_AFTER_OPEN_SESSIONS - 1][2] / 99 - 1) and basis == "provider_time"


def test_unknown_time_never_includes_the_jump():
    raw, _, basis = after_open_drift(STOCK, FLAT_MARKET, D0, None)
    assert raw == pytest.approx(112 / 110 - 1) and basis == "assumed_after_close"
    assert after_open_drift(STOCK, FLAT_MARKET, D0, "unknown")[2] == "assumed_after_close"


def test_excess_subtracts_the_market_over_the_same_window():
    market = {d: (100.0, 100.0) for d, _, _ in STOCK}
    market[STOCK[1][0]] = (100.0, 101.0)
    market[STOCK[5][0]] = (101.0, 103.0)
    raw, excess, _ = after_open_drift(STOCK, market, D0, "postmarket")
    assert excess == pytest.approx(raw - (103 / 100 - 1))


def test_missing_market_bar_gives_no_excess_and_a_short_window_gives_nothing():
    raw, excess, _ = after_open_drift(STOCK, {}, D0, "postmarket")
    assert raw is not None and excess is None
    assert after_open_drift(STOCK[:5], FLAT_MARKET, D0, "postmarket") is None
    assert after_open_drift(STOCK, FLAT_MARKET, D0 + timedelta(days=30), "postmarket") is None
