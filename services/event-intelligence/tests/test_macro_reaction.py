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
from src.services.economic import _FOMC_DATES, _FRED_RELEASES, _FRED_SERIES

_macro_reaction_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "macro_reaction.py"
_source = _macro_reaction_path.read_text()


# ── _RELEASE_TO_FRED_SERIES integrity (AUD264-RELEASE-POLL-COVERS-4-OF-10) ────

def test_every_mapped_release_event_type_exists_in_fred_releases():
    """A typo'd or renamed event_type key here would silently mean check_release_day_fast_poll
    never matches any real economic_events row for that release, permanently skipping it."""
    release_event_types = {event_type for _, event_type, _, _ in _FRED_RELEASES}
    for event_type in _RELEASE_TO_FRED_SERIES:
        assert event_type in release_event_types, f"{event_type} not in _FRED_RELEASES"


def test_all_10_fred_releases_have_a_real_mapped_series_not_just_5_of_10():
    """The actual bug this fix closes: 6 of the 10 real release types (economic.py's
    _FRED_RELEASES) were never even SELECTed by check_release_day_fast_poll()'s own query,
    because _RELEASE_TO_FRED_SERIES only had 5 entries (one of which, pce_release, mapped to
    None and was skipped too) — confirmed in production as 113 fed_funds_release rows with
    zero actuals ever written. Every _FRED_RELEASES event_type must now resolve to a real,
    non-None series_id."""
    release_event_types = {event_type for _, event_type, _, _ in _FRED_RELEASES}
    for event_type in release_event_types:
        assert event_type in _RELEASE_TO_FRED_SERIES, f"{event_type} still missing from _RELEASE_TO_FRED_SERIES"
        assert _RELEASE_TO_FRED_SERIES[event_type] is not None, f"{event_type} still maps to None"


def test_every_mapped_series_id_is_a_real_series_in_fred_series():
    """Each _RELEASE_TO_FRED_SERIES value must correspond to a real entry in _FRED_SERIES (the
    reference-period series economic.py's own sync_fred() already polls) — a made-up or
    mistyped series_id would 404 against the real FRED API forever, silently, since the poll's
    own `if r.status_code != 200: continue` swallows it."""
    known_series_ids = {series_id for series_id, _, _, _ in _FRED_SERIES}
    for event_type, series_id in _RELEASE_TO_FRED_SERIES.items():
        assert series_id in known_series_ids, f"{event_type} -> {series_id} not in _FRED_SERIES"


def test_pce_release_now_maps_to_a_real_series_not_none():
    """PCE previously mapped to None because PCEPI wasn't in _FRED_SERIES at all — now that a
    real PCE Price Index series has been added, pce_release must resolve to it, not None."""
    assert _RELEASE_TO_FRED_SERIES["pce_release"] == "PCEPI"


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


def test_release_day_poll_is_armed_only_during_the_830am_to_1259pm_et_window():
    """Guards against a future edit accidentally widening this to run all day — the whole
    point of 'release-day-armed' is that it's a no-op outside a real BLS/BEA release morning,
    not just logically gated inside the function but ALSO cron-scheduled to a bounded window.

    BUG-CPIPOLL-WINDOWTOOSHORT (2026-08-12): the ORIGINAL window here was hour="8-9"
    (8:30-9:59am ET) — a real July 2026 CPI release still hadn't posted to FRED by the
    window's own 9:58am ET last check that day, leaving the release undetected until a manual
    backfill. Widened to hour="8-12" (through 12:59pm ET) for real headroom against a slow
    FRED mirror — still bounded (not "all day"), and still a cheap no-op on non-release days
    per the poll's own due_today DB gate."""
    start = _scheduler_source.index('id="check_release_day_fast_poll"')
    window = _scheduler_source[start - 300:start]
    assert 'hour="8-12"' in window
    assert "America/New_York" in window
    assert 'day_of_week="mon-fri"' in window


# ── T258-MACRO-SECTOR-IMPACT: _clean_sector_list() ─────────────────────────────

from src.services.macro_reaction import _clean_sector_list  # noqa: E402


def test_clean_sector_list_passes_through_a_valid_list():
    assert _clean_sector_list(["Technology", "Financials"]) == ["Technology", "Financials"]


def test_clean_sector_list_returns_empty_for_non_list_input():
    assert _clean_sector_list("Technology") == []
    assert _clean_sector_list(None) == []
    assert _clean_sector_list(42) == []


def test_clean_sector_list_filters_non_string_entries():
    assert _clean_sector_list(["Technology", 42, None, "Energy"]) == ["Technology", "Energy"]


def test_clean_sector_list_filters_empty_and_whitespace_strings():
    assert _clean_sector_list(["Technology", "", "   ", "Energy"]) == ["Technology", "Energy"]


def test_clean_sector_list_strips_whitespace():
    assert _clean_sector_list(["  Technology  "]) == ["Technology"]


def test_clean_sector_list_caps_at_six_entries():
    long_list = [f"Sector{i}" for i in range(10)]
    result = _clean_sector_list(long_list)
    assert len(result) == 6


def test_clean_sector_list_handles_empty_list():
    assert _clean_sector_list([]) == []


# ── generate_reaction()'s new dict return shape ────────────────────────────────

def _mock_anthropic_response(sectors_helped=None, sectors_hurt=None, one_paragraph="Test reaction."):
    import json as _json
    from unittest.mock import MagicMock

    payload = {
        "surprise_direction": "above", "magnitude": "mild",
        "one_paragraph": one_paragraph,
        "sectors_helped": sectors_helped if sectors_helped is not None else [],
        "sectors_hurt": sectors_hurt if sectors_hurt is not None else [],
    }
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"content": [{"text": _json.dumps(payload)}]}
    return resp


class _FakeAsyncClient:
    def __init__(self, response, exc=None):
        self._response = response
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **kw):
        if self._exc:
            raise self._exc
        return self._response


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_generate_reaction_returns_dict_with_sector_lists(monkeypatch):
    import src.services.macro_reaction as mr

    monkeypatch.setattr(mr, "_api_key", lambda: "test-key")
    monkeypatch.setattr(mr, "_get_market_regime", lambda: {"state": "bull", "vix": 15.0})
    fake_client = _FakeAsyncClient(_mock_anthropic_response(
        sectors_helped=["Technology"], sectors_hurt=["Utilities"],
    ))
    monkeypatch.setattr(mr.httpx, "AsyncClient", lambda **kw: fake_client)

    result = _run(mr.generate_reaction("cpi_release", 3.2, 3.0, 3.1, "CPI"))
    assert result == {
        "reaction_text": "Test reaction.",
        "sectors_helped": ["Technology"],
        "sectors_hurt": ["Utilities"],
    }


def test_generate_reaction_returns_empty_sector_lists_when_llm_provides_none(monkeypatch):
    import src.services.macro_reaction as mr

    monkeypatch.setattr(mr, "_api_key", lambda: "test-key")
    monkeypatch.setattr(mr, "_get_market_regime", lambda: {})
    fake_client = _FakeAsyncClient(_mock_anthropic_response(sectors_helped=[], sectors_hurt=[]))
    monkeypatch.setattr(mr.httpx, "AsyncClient", lambda **kw: fake_client)

    result = _run(mr.generate_reaction("gdp_release", 2.5, 2.0, None, "GDP"))
    assert result["sectors_helped"] == []
    assert result["sectors_hurt"] == []


def test_generate_reaction_returns_none_when_reaction_text_is_missing(monkeypatch):
    """A response with sector lists but no usable one_paragraph must still degrade to None —
    the sector fields are additive to the existing reaction, never a substitute for it."""
    import json as _json
    from unittest.mock import MagicMock
    import src.services.macro_reaction as mr

    monkeypatch.setattr(mr, "_api_key", lambda: "test-key")
    monkeypatch.setattr(mr, "_get_market_regime", lambda: {})
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"content": [{"text": _json.dumps({
        "surprise_direction": "above", "magnitude": "mild", "one_paragraph": "",
        "sectors_helped": ["Technology"], "sectors_hurt": [],
    })}]}
    fake_client = _FakeAsyncClient(resp)
    monkeypatch.setattr(mr.httpx, "AsyncClient", lambda **kw: fake_client)

    result = _run(mr.generate_reaction("cpi_release", 3.2, 3.0, 3.1, "CPI"))
    assert result is None


def test_generate_reaction_returns_none_on_malformed_sector_field_types(monkeypatch):
    """The LLM returning sectors_helped as a bare string (not a list) must not crash the whole
    reaction — _clean_sector_list degrades it to [] and the reaction_text still comes through."""
    import json as _json
    from unittest.mock import MagicMock
    import src.services.macro_reaction as mr

    monkeypatch.setattr(mr, "_api_key", lambda: "test-key")
    monkeypatch.setattr(mr, "_get_market_regime", lambda: {})
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"content": [{"text": _json.dumps({
        "surprise_direction": "above", "magnitude": "mild", "one_paragraph": "Valid text.",
        "sectors_helped": "Technology",  # malformed: should be a list
        "sectors_hurt": [],
    })}]}
    fake_client = _FakeAsyncClient(resp)
    monkeypatch.setattr(mr.httpx, "AsyncClient", lambda **kw: fake_client)

    result = _run(mr.generate_reaction("cpi_release", 3.2, 3.0, 3.1, "CPI"))
    assert result is not None
    assert result["reaction_text"] == "Valid text."
    assert result["sectors_helped"] == []


# ── source-text: both call sites updated for the new dict shape ───────────────

def test_both_call_sites_write_sectors_helped_and_hurt_columns():
    fast_poll_start = _source.index("async def check_release_day_fast_poll")
    fast_poll_end = _source.index("\n\ndef _is_fomc_day", fast_poll_start)
    fast_poll_body = _source[fast_poll_start:fast_poll_end]
    assert "ev.sectors_helped" in fast_poll_body
    assert "ev.sectors_hurt" in fast_poll_body


# ── AUD264-RELEASE-POLL-COVERS-4-OF-10 (second root cause): realtime_start guard ──

def _fast_poll_body() -> str:
    start = _source.index("async def check_release_day_fast_poll")
    end = _source.index("\n\ndef _is_fomc_day", start)
    return _source[start:end]


def test_obs0_realtime_start_is_checked_against_today_before_accepting_the_value():
    """The core second-root-cause fix: obs[0] is just the NEWEST reference-period observation
    on the series — nothing previously confirmed it was actually published TODAY rather than
    some earlier date sync_fred() (the separate reference-period sync) had already captured.
    A same-day release has realtime_start == today (verified live, per this module's own
    docstring); anything else means nothing new has posted yet."""
    body = _fast_poll_body()
    assert 'obs[0].get("realtime_start") != today.isoformat()' in body


def test_realtime_start_check_happens_before_actual_value_is_extracted_and_written():
    """The guard must gate BEFORE `actual = float(obs[0]["value"])` and the ev.actual_value
    write — a guard added after extraction, or on a separate unreachable branch, would not
    actually prevent a stale value from being written."""
    body = _fast_poll_body()
    guard_idx = body.index('obs[0].get("realtime_start") != today.isoformat()')
    extract_idx = body.index('actual = float(obs[0]["value"])')
    write_idx = body.index("ev.actual_value = actual")
    assert guard_idx < extract_idx < write_idx


def test_realtime_start_mismatch_continues_the_loop_not_raises_or_returns():
    """A `continue` (not a raise or early `return`) is required — this poll processes
    MULTIPLE due-today events in one call (e.g. both cpi_release and ppi_release on the same
    morning), and one series not having a fresh observation yet must not abort checking the
    others."""
    body = _fast_poll_body()
    guard_idx = body.index('obs[0].get("realtime_start") != today.isoformat()')
    tail = body[guard_idx:guard_idx + 150]
    assert "continue" in tail

    fomc_start = _source.index("async def check_fomc_statement_poll")
    fomc_body = _source[fomc_start:]
    assert "ev.sectors_helped" in fomc_body
    assert "ev.sectors_hurt" in fomc_body
