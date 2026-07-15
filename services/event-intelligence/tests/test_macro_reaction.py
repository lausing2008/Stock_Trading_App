"""Tests for T249-MARKETMOVER-P2's macro_reaction.py.

Two release-day-armed poll mechanisms:
1. check_release_day_fast_poll() — FRED series/observations for CPI/PPI/GDP/NFP, chosen over
   BLS's own v2 API after live research found BLS documents a ~1-day API lag from real release
   (disqualifying for same-day detection); FRED was confirmed live to have same-day
   availability (June 2026 CPI's realtime_start == its real July 14 release date).
2. check_fomc_statement_poll() — polls the Fed's own press_monetary.xml RSS feed (confirmed
   live and real) since FRED's rate series lag a day and have no "statement just posted"
   signal at all.

Both write into economic_events (actual_value/reaction_text), never directly to an alert
channel — market-data's check_macro_reaction_alerts() owns delivery (see its own test file).
"""
import pathlib

from src.services.macro_reaction import _is_fomc_day, _RELEASE_TO_FRED_SERIES
from src.services.economic import _FOMC_DATES, _FRED_RELEASES

_macro_reaction_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "macro_reaction.py"
_source = _macro_reaction_path.read_text()


# ── _RELEASE_TO_FRED_SERIES integrity ──────────────────────────────────────────

def test_every_mapped_release_event_type_exists_in_fred_releases():
    """A typo'd or renamed event_type key here would silently mean check_release_day_fast_poll
    never matches any real economic_events row for that release, permanently skipping it."""
    release_event_types = {event_type for _, event_type, _, _ in _FRED_RELEASES}
    for event_type in _RELEASE_TO_FRED_SERIES:
        assert event_type in release_event_types, f"{event_type} not in _FRED_RELEASES"


def test_pce_release_is_explicitly_none_not_silently_missing():
    """PCE price index isn't in _FRED_SERIES today — must be an explicit None (documented,
    skipped intentionally) rather than absent from the dict entirely, which would make the
    gap look like an oversight rather than a known, deliberate limitation."""
    assert "pce_release" in _RELEASE_TO_FRED_SERIES
    assert _RELEASE_TO_FRED_SERIES["pce_release"] is None


# ── _is_fomc_day() ──────────────────────────────────────────────────────────────

def test_is_fomc_day_true_for_a_real_seeded_fomc_date():
    from datetime import date
    real_date_str = _FOMC_DATES[0][0]
    d = date.fromisoformat(real_date_str)
    assert _is_fomc_day(d) is True


def test_is_fomc_day_false_for_a_non_meeting_date():
    from datetime import date
    assert _is_fomc_day(date(2026, 1, 1)) is False


# ── check_release_day_fast_poll() — armed-only-when-due discipline ────────────

def test_fast_poll_returns_no_api_key_skip_without_crashing_when_key_missing():
    """FRED_API_KEY absent must be a clean, logged no-op — not an exception that could crash
    the calling scheduler job."""
    import asyncio
    from unittest.mock import patch

    with patch("src.services.macro_reaction._settings") as mock_settings:
        mock_settings.fred_api_key = ""
        result = asyncio.run(
            __import__("src.services.macro_reaction", fromlist=["check_release_day_fast_poll"]).check_release_day_fast_poll()
        )
    assert result["skipped"] == "no_api_key"
    assert result["found"] == 0


# ── check_fomc_statement_poll() — armed-only-on-fomc-day discipline ───────────

def test_fomc_poll_is_a_noop_on_a_non_fomc_day():
    """The core safety property: on ~360 non-FOMC days/year this must do zero work (no feed
    fetch, no DB query) rather than running unconditionally every day."""
    import asyncio
    from unittest.mock import patch
    from datetime import datetime, timezone

    with patch("src.services.macro_reaction.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 1, 1, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = asyncio.run(
            __import__("src.services.macro_reaction", fromlist=["check_fomc_statement_poll"]).check_fomc_statement_poll()
        )
    assert result["skipped"] == "not_fomc_day"
    assert result["checked"] == 0


# ── source-text: fail-open discipline on generate_reaction() ──────────────────

def test_generate_reaction_never_raises_has_try_except_around_the_llm_call():
    """Source-text check matching decision-engine's llm_scorer.py fail-open discipline
    ('Never raises' in its own docstring) — the actual Anthropic API POST call must be
    wrapped so a network/parse failure returns None instead of propagating and crashing the
    calling poll function."""
    start = _source.index("async def generate_reaction")
    end = _source.index("\n\n\n", start)
    body = _source[start:end]
    assert "try:" in body
    assert "except Exception" in body
    assert "return None" in body


# ── scheduler wiring ────────────────────────────────────────────────────────────

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


def test_both_poll_jobs_are_registered_in_start_scheduler():
    assert 'id="check_release_day_fast_poll"' in _scheduler_source
    assert 'id="check_fomc_statement_poll"' in _scheduler_source


def test_release_day_poll_is_armed_only_during_the_830_to_1000_et_window():
    """Guards against a future edit accidentally widening this to run all day — the whole
    point of 'release-day-armed' is that it's a no-op outside the real BLS/BEA release
    window, not just logically gated inside the function but ALSO cron-scheduled tightly."""
    start = _scheduler_source.index('id="check_release_day_fast_poll"')
    window = _scheduler_source[start - 300:start]
    assert 'hour="8-9"' in window
    assert "America/New_York" in window
