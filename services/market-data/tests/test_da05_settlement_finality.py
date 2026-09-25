"""DA-05: an options position must never settle against a session that is still trading.

THE DEFECT. `_settlement_close()` already refused to substitute a NEARBY session's close
(AUD-T400-SETTLESUBSTITUTE) — but it still accepted the CORRECT session's daily bar while that
session was open. This repo writes D1 bars DURING the session; that is the premise of
BUG-VOLANOM-STALEMARKET and of every other "mutable intraday D1" guard in the codebase.

WHY IT MATTERS MORE THAN AN ORDINARY STALENESS BUG: the mistake is PERMANENT. A $100 short put
read against an unfinished $101 print settles as expired-worthless (+$100 premium) and the
position is then excluded from every later retry — even if the session actually closes at $90
and the true outcome is an assignment worth -$900. There is no second chance to get it right.

The scheduled settlement run is in the evening and was always safe. The admin `/run-step` route
is callable at any hour and reaches the identical code, which is the reachable path.

16:15 ET is deliberately conservative: regular close is 16:00 and early closes are 13:00, so it
is final on BOTH kinds of day, plus an ingestion buffer. Waiting the extra hours on an
early-close day costs nothing — the only scheduled run is in the evening regardless. Being late
is recoverable; settling on a moving price is not.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from src.services.options_income_engine import (
    _SETTLEMENT_FINALITY_ET,
    settlement_session_is_final,
)

ET = ZoneInfo("America/New_York")
SESSION = date(2026, 9, 25)          # a Friday expiry


def _et(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


# ── The session still trading ────────────────────────────────────────────────

def test_a_session_still_trading_is_not_final():
    """09:35 ET on expiry day — the exact case the admin /run-step route could reach."""
    assert settlement_session_is_final(SESSION, _et(2026, 9, 25, 9, 35)) is False


def test_the_minutes_right_before_the_close_are_not_final():
    assert settlement_session_is_final(SESSION, _et(2026, 9, 25, 15, 59)) is False


def test_the_close_itself_is_not_yet_treated_as_final():
    """16:00 is the bell, not the moment the final bar is ingested and stable."""
    assert settlement_session_is_final(SESSION, _et(2026, 9, 25, 16, 0)) is False


def test_an_early_close_day_is_still_not_final_before_the_cutoff():
    """On a 13:00 early close the bar IS final at 13:05 — this deliberately waits anyway,
    because the cutoff has to hold on BOTH kinds of day and the only scheduled run is in the
    evening. Being conservative here costs hours; being wrong costs the position."""
    assert settlement_session_is_final(SESSION, _et(2026, 9, 25, 13, 5)) is False


# ── The session finished ─────────────────────────────────────────────────────

def test_after_the_cutoff_on_the_session_day_is_final():
    assert settlement_session_is_final(SESSION, _et(2026, 9, 25, 16, 15)) is True


def test_the_evening_scheduled_run_settles_normally():
    """The scheduled path was never broken and must not become broken by this guard."""
    assert settlement_session_is_final(SESSION, _et(2026, 9, 25, 20, 0)) is True


def test_any_earlier_session_is_final_at_any_hour():
    """A Friday expiry settled on Monday morning: the session is long over."""
    assert settlement_session_is_final(SESSION, _et(2026, 9, 28, 6, 0)) is True
    assert settlement_session_is_final(SESSION, _et(2026, 9, 26, 0, 1)) is True


# ── A session that has not happened ──────────────────────────────────────────

def test_a_future_session_is_never_final():
    """`expiry <= as_of` plus a clock skew, or a manual call with a wrong date, must not be
    able to settle a contract against a session that has not occurred."""
    assert settlement_session_is_final(SESSION, _et(2026, 9, 24, 23, 59)) is False
    assert settlement_session_is_final(SESSION, _et(2026, 9, 20, 12, 0)) is False


# ── The boundary is a time-of-day in ET, not UTC ─────────────────────────────

def test_finality_is_measured_in_exchange_time_not_utc():
    """20:30 UTC on expiry day is 16:30 ET — after the cutoff. The same instant read as a naive
    UTC clock would be 20:30, which would ALSO pass, so this pins the opposite direction: an
    instant that is late in UTC but still mid-session in ET must be refused."""
    utc_late_but_et_midsession = datetime(2026, 9, 25, 18, 0, tzinfo=ZoneInfo("UTC"))
    assert utc_late_but_et_midsession.astimezone(ET).hour == 14  # still trading
    assert settlement_session_is_final(SESSION, utc_late_but_et_midsession) is False


def test_the_cutoff_is_after_the_regular_close():
    """Pins the RELATIONSHIP, not the literal — the cutoff must sit past the 16:00 bell so an
    ingestion lag cannot slip an unfinished bar through."""
    assert (_SETTLEMENT_FINALITY_ET.hour, _SETTLEMENT_FINALITY_ET.minute) >= (16, 0)


def test_a_naive_datetime_is_accepted_without_raising():
    """Callers and tests pass tz-aware values, but a naive one must degrade rather than crash
    inside a settlement path."""
    assert settlement_session_is_final(SESSION, datetime(2026, 9, 26, 10, 0)) is True


# ── The guard must actually be WIRED IN ──────────────────────────────────────
#
# Everything above tests the predicate in isolation. A predicate that is never consulted is
# decoration: deleting the single `if not settlement_session_is_final(want)` line from
# _settlement_close left every test above green, because none of them went through it.

from types import SimpleNamespace          # noqa: E402
from unittest.mock import patch            # noqa: E402

from src.services import options_income_engine as OIE  # noqa: E402


class _FakeSession:
    """Returns a daily bar unconditionally — so any refusal to settle can only come from the
    finality guard, never from missing data."""

    def __init__(self):
        self.queried = False

    def execute(self, *_a, **_k):
        self.queried = True
        return SimpleNamespace(first=lambda: SimpleNamespace(close=101.0))


def test_settlement_close_defers_while_the_session_is_still_trading():
    sess = _FakeSession()
    with patch.object(OIE, "settlement_session_is_final", lambda *_a, **_k: False):
        assert OIE._settlement_close(sess, stock_id=1, expiry=SESSION) is None


def test_settlement_close_does_not_even_read_a_price_it_must_not_use():
    """Refusing AFTER reading the bar would still be correct, but refusing BEFORE makes it
    impossible for a later edit to accidentally use the value it just fetched."""
    sess = _FakeSession()
    with patch.object(OIE, "settlement_session_is_final", lambda *_a, **_k: False):
        OIE._settlement_close(sess, stock_id=1, expiry=SESSION)
    assert sess.queried is False


def test_settlement_close_returns_the_price_once_the_session_is_final():
    """The guard must not break the evening path that was always correct.

    R04 added a SECOND condition — the stored close must be corroborated by an independent
    reading — so this stubs that too. Without it this test would be asserting DA-05's guard
    while actually exercising R04's, and would fail for a reason unrelated to what it checks."""
    sess = _FakeSession()
    with patch.object(OIE, "settlement_session_is_final", lambda *_a, **_k: True), \
         patch.object(OIE, "_corroborate_settlement_close", lambda *_a, **_k: (True, "stub")):
        got = OIE._settlement_close(sess, stock_id=1, expiry=SESSION)
    assert got is not None
    price, session_date = got
    assert price == 101.0
    assert session_date == OIE.expected_settlement_session(SESSION)
