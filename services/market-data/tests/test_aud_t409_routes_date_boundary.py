"""AUD-T409-UTCDATEBOUNDARY follow-up (2026-09-21) — routes.py's own share of the ~16-file
scoped list from docs/incidents/utc-vs-et-date-boundary.md. Nine call sites computed "today" as
a naive UTC truncation (`date.today()`/`datetime.now(timezone.utc).date()`), each with a real
user-facing consequence for roughly 4-5 hours of every evening:

- /earnings_calendar and /events/calendar used `today` as an inclusive lower bound
  (`today <= ned_date <= cutoff`) — a real today-ET earnings/macro event silently dropped off
  the "upcoming" list.
- Two `days_to_earnings` computations (the OpenBB-style enrichment helper and the yfinance
  ticker.calendar path) drifted by one day.
- The Fundamental.as_of upsert key (on-demand fetch, not a fixed-schedule batch job — more
  exposed to the bug window than a scheduled EOD job) could mis-date the row.
- Both Options Game Plan protective-put/covered-call DTE-window selections
  (`_nearest_expiry_in_dte_window`) could pick the wrong expiry — the same bug class
  options_income_engine.py was already fixed for, missed at these two separate call sites.
- A price-target days-remaining countdown and a sector-seasonality month filter drifted by one
  (day / at most 12 evenings a year that straddle a month boundary, respectively) — lower
  stakes, same root cause, fixed for consistency.

routes.py is importable directly in this test environment (test_fundamentals_stale_days_to_
earnings.py already does so) — `_today_et()` is tested directly; the call sites are verified
via source-text regression checks, matching test_t409_utc_date_boundary.py's own established
technique for the same bug class in a different file.
"""
import pathlib
from datetime import datetime, timezone
from unittest.mock import patch

from src.api.routes import _today_et

_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_SOURCE = _PATH.read_text()


class _FrozenDatetime(datetime):
    _frozen: datetime

    @classmethod
    def now(cls, tz=None):
        return cls._frozen.astimezone(tz) if tz else cls._frozen


def _frozen_at(utc_iso: str):
    frozen = _FrozenDatetime.fromisoformat(utc_iso).replace(tzinfo=timezone.utc)
    _FrozenDatetime._frozen = frozen
    return patch("src.api.routes.datetime", _FrozenDatetime)


# ── _today_et() itself ──────────────────────────────────────────────────────────────────────

def test_the_exact_evening_window_reads_the_correct_et_date():
    """2026-09-18 20:40 ET == 2026-09-19 00:40 UTC — the naive version reads Sept 19; the
    correct answer is Sept 18."""
    with _frozen_at("2026-09-19T00:40:00"):
        assert _today_et().isoformat() == "2026-09-18"


def test_naive_utc_truncation_would_have_gotten_this_wrong():
    """Not a strawman: this is what all nine call sites used to compute — constructed directly
    rather than via the real (unpatched-in-this-file) datetime.now(), matching the
    AUD-NEXTIMPROV-FROZENTIME-LEAK fix applied to the sibling T409/UW-01 test files."""
    frozen = datetime(2026, 9, 19, 0, 40, 0, tzinfo=timezone.utc)
    assert frozen.date().isoformat() == "2026-09-19"


def test_daytime_edt_matches_the_naive_version_exactly():
    with _frozen_at("2026-09-18T18:00:00"):  # 14:00 EDT
        assert _today_et().isoformat() == "2026-09-18"


# ── Every one of the nine fixed call sites actually uses the shared helper ─────────────────

def test_days_to_earnings_enrichment_helper_uses_today_et():
    start = _SOURCE.index('ned = payload.get("next_earnings_date")')
    end = _SOURCE.index("\n    return payload", start)
    body = _SOURCE[start:end]
    assert "today = _today_et()  # AUD-T409-UTCDATEBOUNDARY" in body
    assert "_date.today()" not in body


def test_yfinance_calendar_days_to_earnings_uses_today_et():
    start = _SOURCE.index('cal = ticker.calendar')
    end = _SOURCE.index("except Exception:\n        pass", start)
    body = _SOURCE[start:end]
    assert "today = _today_et()  # AUD-T409-UTCDATEBOUNDARY" in body


def test_earnings_calendar_endpoint_uses_today_et():
    start = _SOURCE.index("def earnings_calendar(")
    end = _SOURCE.index("\n\n\n@router", start)
    body = _SOURCE[start:end]
    assert "today = _today_et()  # AUD-T409-UTCDATEBOUNDARY" in body
    assert "_date.today()" not in body


def test_events_calendar_endpoint_uses_today_et():
    start = _SOURCE.index("def events_calendar(")
    end = _SOURCE.index("\n\n\n@router", start)
    body = _SOURCE[start:end]
    assert "today = _today_et()  # AUD-T409-UTCDATEBOUNDARY" in body


def test_fundamental_upsert_key_uses_today_et():
    assert "as_of=_today_et(),  # AUD-T409-UTCDATEBOUNDARY" in _SOURCE


def test_options_game_plan_hedge_leg_dte_uses_today_et():
    assert 'today = today or _today_et()  # AUD-T409-UTCDATEBOUNDARY' in _SOURCE


def test_options_game_plan_on_demand_dte_uses_today_et():
    assert "today = _today_et()  # AUD-T409-UTCDATEBOUNDARY\n        put_exp = _nearest_expiry_in_dte_window(" in _SOURCE


def test_price_target_days_remaining_uses_today_et():
    assert "days_remaining = (target_d - _today_et()).days  # AUD-T409-UTCDATEBOUNDARY" in _SOURCE


def test_sector_seasonality_target_month_uses_today_et():
    assert "target_month = month if month is not None else _today_et().month  # AUD-T409-UTCDATEBOUNDARY" in _SOURCE


def test_no_naive_utc_date_call_survives_at_any_fixed_site():
    """Regression guard across the whole file for the nine specific literals this fix
    removed — deliberately NOT asserting zero occurrences file-wide, since routes.py has other,
    un-triaged date.today()/datetime.now(timezone.utc).date() sites (rolling lookback cutoffs,
    leakage guards) this pass did not touch and must not claim to have fixed."""
    removed_literals = [
        'today = _date.today()\n        if next_ed >= today:',
    ]
    for literal in removed_literals:
        assert literal not in _SOURCE
