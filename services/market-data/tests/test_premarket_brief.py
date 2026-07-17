"""Tests for T249-MARKETMOVER-P3's pre-market brief.

send_premarket_brief_email() composes three already-shipped MarketMover pieces (P0's macro
calendar, P1's earnings day-of data, P2's macro reactions) into one email — pure string
composition, no DB/network dependency, so it's tested directly rather than via the
exec()-from-source technique other scheduler.py-adjacent tests use (see
test_price_alert_price_check.py's docstring for why scheduler.py itself can't be imported here
— its import chain pulls in apscheduler/ingestion.py/paper_trading_engine.py, none of which are
stubbed). email_service.py has none of those problematic imports, so it imports directly.

send_premarket_brief() itself (the DB-querying job function in scheduler.py) is covered only by
the source-text regression check below, matching test_scheduler_static_names.py's established
pattern for this exact risk class: conftest.py stubs sqlalchemy/db as MagicMock(), and a
MagicMock() attribute/name access never raises, so an actual NameError or missing import in a
new scheduler.py function would NOT be caught by importing and running it under the stubbed
test harness — only by reading the source directly, or by a live call against a real deployed
container (done once, manually, before considering this feature "verified" — see
.claude/CLAUDE.md's Feature Reference entry for this tier).
"""
from types import SimpleNamespace
from unittest.mock import patch
import pathlib

from src.services.email_service import send_premarket_brief_email


# ── send_premarket_brief_email() — pure composition, tested directly ────────────────────────

def _capture_send():
    """Patch send_email to capture (to, subject, body_html, body_text) instead of sending."""
    calls = []
    def _fake_send(to, subject, body_html, body_text):
        calls.append({"to": to, "subject": subject, "html": body_html, "text": body_text})
        return True
    return calls, _fake_send


def test_empty_brief_still_sends_with_explicit_none_sections():
    """No macro events, no earnings, no reactions — every section must show its explicit
    empty-state note, not a blank/missing section (a user should never wonder if the email
    failed to load data vs. genuinely having nothing to report today)."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        ok = send_premarket_brief_email(
            to="user@example.com", date_str="Fri, Jul 17", market="US",
            macro_events=[], my_earnings=[], recent_reactions=[],
        )
    assert ok is True
    assert len(calls) == 1
    html, text = calls[0]["html"], calls[0]["text"]
    assert "No high/critical-importance releases scheduled today." in html
    assert "None of your watched symbols report earnings today." in html
    assert "No macro reactions generated in the last 18 hours." in html
    assert "None scheduled today." in text
    assert "None." in text


def test_macro_event_renders_impact_badge_and_title():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_premarket_brief_email(
            to="user@example.com", date_str="Fri, Jul 17", market="US",
            macro_events=[{"type": "cpi", "title": "CPI Release", "description": "Consumer Price Index", "impact": "high"}],
            my_earnings=[], recent_reactions=[],
        )
    html, text = calls[0]["html"], calls[0]["text"]
    assert "CPI Release" in html
    # the badge is uppercased visually via CSS text-transform, not in the actual markup text
    assert ">high<" in html
    assert "[HIGH] CPI Release" in text


def test_critical_impact_gets_distinct_color_from_high():
    """critical and high must not silently collapse to the same badge color — a critical FOMC
    day should visually stand out from a routine high-importance release."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_premarket_brief_email(
            to="a@example.com", date_str="d", market="US",
            macro_events=[{"title": "FOMC", "description": "x", "impact": "critical"}],
            my_earnings=[], recent_reactions=[],
        )
    critical_html = calls[0]["html"]
    calls2, fake2 = _capture_send()
    with patch("src.services.email_service.send_email", fake2):
        send_premarket_brief_email(
            to="a@example.com", date_str="d", market="US",
            macro_events=[{"title": "PPI", "description": "x", "impact": "high"}],
            my_earnings=[], recent_reactions=[],
        )
    high_html = calls2[0]["html"]
    # extract the color used in each badge span
    assert "#ef4444" in critical_html  # critical -> red
    assert "#f97316" in high_html      # high -> orange
    assert "#ef4444" not in high_html


def test_my_earnings_renders_symbol_and_eps_estimate():
    ev = SimpleNamespace(eps_estimate=1.23)
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_premarket_brief_email(
            to="user@example.com", date_str="Fri, Jul 17", market="US",
            macro_events=[], my_earnings=[{"symbol": "AAPL", "event": ev}], recent_reactions=[],
        )
    html, text = calls[0]["html"], calls[0]["text"]
    assert "AAPL" in html and "$1.23" in html
    assert "AAPL reports today (EPS est. $1.23)" in text


def test_my_earnings_missing_eps_estimate_shows_em_dash_not_none_or_crash():
    """A genuine missing estimate (eps_estimate=None) must render as '—', not the literal
    string 'None' and not raise a formatting exception."""
    ev = SimpleNamespace(eps_estimate=None)
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_premarket_brief_email(
            to="user@example.com", date_str="d", market="US",
            macro_events=[], my_earnings=[{"symbol": "XYZ", "event": ev}], recent_reactions=[],
        )
    html = calls[0]["html"]
    assert "XYZ" in html
    assert "None" not in html
    assert "—" in html


def test_recent_reactions_renders_title_and_reaction_text():
    ev = SimpleNamespace(title="CPI came in hot", reaction_text="Markets sold off on the print.")
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_premarket_brief_email(
            to="user@example.com", date_str="d", market="US",
            macro_events=[], my_earnings=[], recent_reactions=[ev],
        )
    html, text = calls[0]["html"], calls[0]["text"]
    assert "CPI came in hot" in html
    assert "Markets sold off on the print." in html
    assert "CPI came in hot: Markets sold off on the print." in text


def test_recent_reactions_capped_at_five():
    """recent_reactions can carry more than 5 rows (18h window with a busy news day) — the
    email must cap display at 5, not render an unbounded list."""
    events = [SimpleNamespace(title=f"Event {i}", reaction_text=f"Reaction {i}") for i in range(8)]
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_premarket_brief_email(
            to="user@example.com", date_str="d", market="US",
            macro_events=[], my_earnings=[], recent_reactions=events,
        )
    html = calls[0]["html"]
    assert "Event 0" in html and "Event 4" in html
    assert "Event 5" not in html and "Event 7" not in html


def test_subject_includes_market_and_date():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_premarket_brief_email(
            to="user@example.com", date_str="Fri, Jul 17", market="HK",
            macro_events=[], my_earnings=[], recent_reactions=[],
        )
    assert calls[0]["subject"] == "🔔 Pre-Market Brief — HK — Fri, Jul 17"


def test_not_financial_advice_disclaimer_present_in_both_html_and_text():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_premarket_brief_email(
            to="user@example.com", date_str="d", market="US",
            macro_events=[], my_earnings=[], recent_reactions=[],
        )
    html, text = calls[0]["html"], calls[0]["text"]
    assert "not a prediction" in html.lower()
    assert "not a prediction" in text.lower()
    assert "not financial advice" in html.lower()
    assert "not financial advice" in text.lower()


# ── send_premarket_brief() — source-text regression checks (scheduler.py can't be imported) ──

_SCHEDULER_SOURCE = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
).read_text()


def test_premarket_brief_function_uses_only_module_level_imported_models():
    """send_premarket_brief() references EarningsEvent/Stock/EconomicEvent/PriceAlert/User —
    all must already be in the module-level `from db import ...` line (confirmed at time of
    writing), matching the exact NameError bug class test_scheduler_static_names.py guards
    against for a different function. A MagicMock()-stubbed test run would NOT catch a
    regression here, so this is a source-text check, not an import-and-call test."""
    start = _SCHEDULER_SOURCE.index("def send_premarket_brief(")
    end = _SCHEDULER_SOURCE.index("\ndef ", start + 1)
    body = _SCHEDULER_SOURCE[start:end]
    for name in ("EarningsEvent", "Stock", "EconomicEvent", "PriceAlert"):
        assert name in body, f"{name} used in send_premarket_brief() body"
    import_line = next(line for line in _SCHEDULER_SOURCE.splitlines() if line.startswith("from db import"))
    module_level_names = {n.strip() for n in import_line.removeprefix("from db import").split(",")}
    for name in ("EarningsEvent", "Stock", "EconomicEvent", "PriceAlert", "User"):
        assert name in module_level_names, f"{name} missing from module-level db import: {import_line!r}"


def test_premarket_brief_us_job_is_registered_as_a_scheduled_job():
    assert 'id="premarket_brief_us"' in _SCHEDULER_SOURCE
    assert 'send_premarket_brief(["US"])' in _SCHEDULER_SOURCE


def test_premarket_brief_hk_job_is_registered_as_a_scheduled_job():
    assert 'id="premarket_brief_hk"' in _SCHEDULER_SOURCE
    assert 'send_premarket_brief(["HK"])' in _SCHEDULER_SOURCE


def test_premarket_brief_registered_before_morning_digest_in_the_schedule():
    """Runs at 8:00 local, ahead of the morning digest's 8:50 — catalyst context should arrive
    before the opportunities digest, not after. Checks the CronTrigger immediately preceding
    the premarket_brief_us add_job call uses hour=8, minute=0."""
    brief_start = _SCHEDULER_SOURCE.index('id="premarket_brief_us"')
    brief_block = _SCHEDULER_SOURCE[max(0, brief_start - 300):brief_start]
    assert "hour=8, minute=0" in brief_block


def test_premarket_brief_imports_macro_events_from_db_locally():
    """Reuses P0's own DB query function rather than re-implementing the same query — a
    regression here (e.g. a typo'd import path) would silently duplicate query logic instead
    of failing loudly, since it's a local import inside a try/except-wrapped function."""
    start = _SCHEDULER_SOURCE.index("def send_premarket_brief(")
    end = _SCHEDULER_SOURCE.index("\ndef ", start + 1)
    body = _SCHEDULER_SOURCE[start:end]
    assert "from ..api.routes import _macro_events_from_db" in body
    assert "from .email_service import send_premarket_brief_email" in body
