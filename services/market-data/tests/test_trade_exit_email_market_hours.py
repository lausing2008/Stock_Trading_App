"""Tests for PT-MONITOR-NO-MARKET-HOURS-GATE: send_trade_exit_email()'s new market_hours_open
param, and the _monitor_positions()/_send_exit_emails() wiring that feeds it.

_monitor_positions() has no market-hours gate (a genuinely breached stop should close promptly
even outside regular hours), but the resulting exit email previously read identically to a
live intraday trigger even when it reflects an already-final end-of-day close computed hours
into the overnight. This adds an explicit note when the market was closed at the moment of
exit, without changing whether/when the exit itself fires.

send_trade_exit_email() is pure string composition (no DB/network dependency), so it's tested
directly with real inputs. The scheduler.py-adjacent wiring in paper_trading_engine.py can't be
imported directly in this test environment (conftest.py stubs sqlalchemy as a MagicMock), so
that half is covered by source-text regression checks, matching this repo's established
pattern for this exact constraint.
"""
import pathlib
from unittest.mock import patch

from src.services.email_service import send_trade_exit_email

_ENGINE_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_ENGINE_SOURCE = _ENGINE_PATH.read_text()


def _capture_send():
    calls = []
    def _fake_send(to, subject, body_html, body_text):
        calls.append({"to": to, "subject": subject, "html": body_html, "text": body_text})
        return True
    return calls, _fake_send


def _base_kwargs(**overrides):
    kwargs = dict(
        to="user@example.com", symbol="DFNS", exit_reason="stop_hit",
        entry_price=27.5475, exit_price=27.1728, pnl_dollar=-71.16, pnl_pct=-1.36,
        hold_days=2, shares=100.0, style="SWING", signal_at_exit="BUY",
        highest_price=30.99, entry_notes=[],
    )
    kwargs.update(overrides)
    return kwargs


# ── send_trade_exit_email() — the after-hours note itself ──────────────────────────────────

def test_market_hours_open_true_omits_the_after_hours_note():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_trade_exit_email(**_base_kwargs(market_hours_open=True))
    html, text = calls[0]["html"], calls[0]["text"]
    assert "market was CLOSED" not in html
    assert "market was CLOSED" not in text


def test_market_hours_open_false_includes_the_after_hours_note_in_both_html_and_text():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_trade_exit_email(**_base_kwargs(market_hours_open=False))
    html, text = calls[0]["html"], calls[0]["text"]
    assert "market was CLOSED" in html
    assert "market was CLOSED" in text
    assert "prior session" in html
    assert "prior session" in text


def test_default_is_market_hours_open_true_no_regression_for_existing_callers():
    """Backward-compat: any pre-existing caller not passing market_hours_open at all must
    default to the market-open (no-note) case, not silently start claiming closed."""
    calls, fake = _capture_send()
    kwargs = _base_kwargs()
    with patch("src.services.email_service.send_email", fake):
        send_trade_exit_email(**kwargs)  # no market_hours_open kwarg at all
    html = calls[0]["html"]
    assert "market was CLOSED" not in html


def test_after_hours_note_does_not_change_pnl_price_or_subject_content():
    """The note is purely additive — everything else about the email must be identical
    regardless of market_hours_open."""
    calls_open, fake_open = _capture_send()
    with patch("src.services.email_service.send_email", fake_open):
        send_trade_exit_email(**_base_kwargs(market_hours_open=True))
    calls_closed, fake_closed = _capture_send()
    with patch("src.services.email_service.send_email", fake_closed):
        send_trade_exit_email(**_base_kwargs(market_hours_open=False))

    assert calls_open[0]["subject"] == calls_closed[0]["subject"]
    for frag in ("DFNS", "$71.16", "-1.36%", "27.5475", "27.1728"):
        assert frag in calls_open[0]["html"]
        assert frag in calls_closed[0]["html"]


def test_after_hours_note_explains_why_the_exit_still_correctly_fired():
    """The note must not just say 'market closed' — it should explain the exit was still the
    right call, matching the real design reasoning (a breached stop shouldn't sit unprotected
    until the next open), so a user doesn't read this as a bug report against the app itself."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_trade_exit_email(**_base_kwargs(market_hours_open=False))
    html = calls[0]["html"]
    assert "correctly closed" in html


# ── paper_trading_engine.py wiring — source-text regression checks ─────────────────────────

def test_monitor_positions_records_market_hours_open_on_every_closed_exit():
    """The exit dict appended to closed_exits must record market_hours_open, computed via the
    existing _is_market_hours() helper (not a new, second implementation) at the trade's own
    market — not the wall-clock 'now' of whenever the email happens to be sent."""
    start = _ENGINE_SOURCE.index("closed_exits.append({")
    end = _ENGINE_SOURCE.index("\n            continue", start)
    body = _ENGINE_SOURCE[start:end]
    assert '"market_hours_open": _is_market_hours(cfg.get("market"' in body


def test_send_exit_emails_forwards_market_hours_open_to_the_email_builder():
    start = _ENGINE_SOURCE.index("def _send_exit_emails(")
    end = _ENGINE_SOURCE.index("\n\n\ndef ", start)
    body = _ENGINE_SOURCE[start:end]
    assert "market_hours_open=exit_info.get(" in body
    # Missing key must default to True (market-open), never silently claim closed.
    assert 'exit_info.get("market_hours_open", True)' in body


def test_monitor_positions_gate_comment_still_documents_the_deliberate_no_gate_design():
    """Guards against a future well-intentioned 'fix' that adds a market-hours SUPPRESSION to
    _monitor_positions() itself — the design intent (exits must still fire promptly outside
    hours) is recorded so this isn't silently reverted."""
    assert "PT-MONITOR-NO-MARKET-HOURS-GATE" in _ENGINE_SOURCE
    assert "shouldn't sit" in _ENGINE_SOURCE or "should still close promptly" in _ENGINE_SOURCE
