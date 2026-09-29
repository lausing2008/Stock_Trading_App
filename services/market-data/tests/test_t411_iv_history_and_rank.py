"""T411-IVRANK-OLDESTROW + T411-IVHV: which row of an iv-rank response is "now", and the
daily-history fetch the IV vs HV panel is built on.

THE DEFECT THIS PINS. `get_iv_rank()` took `rows[0]` and its docstring asserted the response was
"most recent first". Measured 2026-09-28 against the live Unusual Whales API on AAPL, NVDA and
SPY — all three identical — the response is ASCENDING: with no params it is 5 rows spanning
2026-09-22 to 2026-09-28, and `rows[0]` is the 22nd. Every consumer had been reading an IV
roughly a trading week stale, and the staleness scales with the window: at `timespan=1Y` the
response is 251 rows and `rows[0]` is a year old.

On AAPL that measured day the difference was `iv_rank_1y` 32.05 (the stale row) against 45.06
(the current one) — not a rounding artifact but a different answer to "are options cheap here",
which is precisely the question `OptionsGamePlanSnapshot.iv_rank_1y` feeds into the signal
alert email's "options relatively expensive/cheap" badge to answer.

These tests assert on the VALUE selected, never on the expression used to select it, so the
implementation is free to change as long as it keeps picking the newest day.
"""
from unittest.mock import MagicMock, patch

import pytest

import src.services.unusual_whales as uw


# Five days, ascending — the real shape measured from the live API.
_ASCENDING = [
    {"date": "2026-09-22", "volatility": "0.225", "iv_rank_1y": "32.052", "close": "339.75"},
    {"date": "2026-09-23", "volatility": "0.231", "iv_rank_1y": "35.10", "close": "340.10"},
    {"date": "2026-09-24", "volatility": "0.238", "iv_rank_1y": "38.40", "close": "341.00"},
    {"date": "2026-09-25", "volatility": "0.240", "iv_rank_1y": "41.00", "close": "337.02"},
    {"date": "2026-09-28", "volatility": "0.247", "iv_rank_1y": "45.0621", "close": "338.4"},
]


@pytest.fixture
def no_cache():
    """Neutralise Redis. The conftest stubs the `redis` module with a MagicMock, so an
    unpatched `_get_redis().get(key)` returns a truthy MagicMock and every one of these tests
    would silently exercise the cache-hit path instead of the parsing path under test."""
    rdb = MagicMock()
    rdb.get.return_value = None
    with patch.object(uw, "_get_redis", return_value=rdb):
        yield rdb


# ── the row selector itself ─────────────────────────────────────────────────────────────

def test_newest_row_is_chosen_from_an_ascending_response():
    """The real, measured ordering. This is the case the old `rows[0]` got wrong."""
    assert uw._newest_iv_row(_ASCENDING)["date"] == "2026-09-28"


def test_newest_row_is_chosen_from_a_descending_response():
    """The ordering the old docstring CLAIMED. Selecting by date rather than by position means
    a change on UW's side cannot silently reintroduce the bug in either direction."""
    assert uw._newest_iv_row(list(reversed(_ASCENDING)))["date"] == "2026-09-28"


def test_newest_row_is_chosen_from_a_shuffled_response():
    shuffled = [_ASCENDING[2], _ASCENDING[0], _ASCENDING[4], _ASCENDING[1], _ASCENDING[3]]
    assert uw._newest_iv_row(shuffled)["date"] == "2026-09-28"


def test_a_row_with_no_date_cannot_win_by_accident():
    """A dateless row must not outrank a real one — that would be the same class of failure as
    the original bug: a plausible number from the wrong day."""
    rows = _ASCENDING + [{"volatility": "9.99", "iv_rank_1y": "99.9"}]
    assert uw._newest_iv_row(rows)["date"] == "2026-09-28"


def test_an_empty_response_yields_an_empty_row_not_an_exception():
    assert uw._newest_iv_row([]) == {}


# ── get_iv_rank end to end ──────────────────────────────────────────────────────────────

def test_get_iv_rank_returns_the_newest_day_not_the_first_row(no_cache):
    """THE REGRESSION TEST. With the ascending response the API really sends, the returned
    reading must be the 28th's — 0.247 / 45.06 — not the 22nd's 0.225 / 32.05."""
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", return_value=list(_ASCENDING)):
        out = uw.get_iv_rank("AAPL")

    assert out is not None
    assert out.as_of_date == "2026-09-28"
    assert out.volatility == pytest.approx(0.247)
    assert out.iv_rank_1y == pytest.approx(45.0621)
    assert out.close == pytest.approx(338.4)


def test_get_iv_rank_is_still_None_on_an_empty_response(no_cache):
    """Fail-open contract preserved: callers branch on None, never on an exception."""
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", return_value=[]):
        assert uw.get_iv_rank("AAPL") is None


def test_get_iv_rank_is_None_when_uw_is_unavailable():
    with patch.object(uw, "is_available", return_value=False):
        assert uw.get_iv_rank("AAPL") is None


# ── get_iv_history ──────────────────────────────────────────────────────────────────────

def test_history_returns_every_day_ascending(no_cache):
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", return_value=list(reversed(_ASCENDING))):
        rows = uw.get_iv_history("AAPL")

    assert [r.as_of_date for r in rows] == [
        "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-28",
    ]
    assert rows[-1].volatility == pytest.approx(0.247)


def test_history_asks_for_the_full_year_window(no_cache):
    """The measurement this feature rests on: one request buys 251 daily rows, so a caller
    wanting three months costs exactly one request. Sending no `timespan` would return 5 rows
    and quietly reduce the chart to a single week."""
    captured = {}

    def _fake_get(path, params=None, **kw):
        captured["params"] = params
        return list(_ASCENDING)

    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", side_effect=_fake_get):
        uw.get_iv_history("AAPL")

    assert captured["params"]["timespan"] == "1Y"


def test_history_drops_rows_with_no_date_rather_than_inventing_one(no_cache):
    """A point with no date cannot be placed on a time axis. Dropping it is the only honest
    option; defaulting it to today would put a stale reading at the right-hand edge."""
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", return_value=_ASCENDING + [{"volatility": "0.5"}]):
        rows = uw.get_iv_history("AAPL")
    assert len(rows) == len(_ASCENDING)


def test_history_preserves_a_missing_volatility_as_None(no_cache):
    """A gap in the series must stay a gap. A 0.0 would draw the IV line down to the floor and
    read as a real collapse in implied volatility."""
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", return_value=[{"date": "2026-09-22", "iv_rank_1y": "30"}]):
        rows = uw.get_iv_history("AAPL")
    assert len(rows) == 1
    assert rows[0].volatility is None


def test_history_is_empty_on_failure_never_raising(no_cache):
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", side_effect=RuntimeError("controlled network failure")):
        assert uw.get_iv_history("AAPL") == []


def test_history_is_empty_when_uw_is_unavailable():
    with patch.object(uw, "is_available", return_value=False):
        assert uw.get_iv_history("AAPL") == []
