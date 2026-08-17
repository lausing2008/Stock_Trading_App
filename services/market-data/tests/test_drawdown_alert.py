"""Tests for T286-DRAWDOWN-ALERT: _compute_portfolio_drawdown() (paper_trading_engine.py),
send_portfolio_drawdown_alert_email() (email_service.py), and
check_portfolio_drawdown_alerts() (scheduler.py).

_compute_portfolio_drawdown() calls session.execute(select(func.max(...))) against a real
table, so — matching test_correlation_preentry.py's established technique exactly — this pops
the sqlalchemy/db stubs, builds ONE shared in-memory engine + the real PaperEquityCurve model,
then restores the stubs immediately so later-collected test files aren't affected.

send_portfolio_drawdown_alert_email() is pure string composition (no DB/network dependency),
tested directly with real inputs.

check_portfolio_drawdown_alerts() itself can't be imported in this test environment
(scheduler.py's import chain pulls in apscheduler and other unstubbed modules) — its wiring is
covered by source-text regression checks, matching test_earnings_beat_screener.py's own
established pattern for this exact constraint.
"""
import pathlib
import sys
from unittest.mock import patch

_STUBBED_MODULES = ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql", "db")
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util
from datetime import date

from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_drawdown", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_drawdown"] = _models
_spec.loader.exec_module(_models)

_ENGINE = create_engine("sqlite:///:memory:")
_models.Base.metadata.create_all(_ENGINE, tables=[_models.PaperEquityCurve.__table__])

for _mod, _stub in _saved_stubs.items():
    if _stub is not None:
        sys.modules[_mod] = _stub
    else:
        sys.modules.pop(_mod, None)

PaperEquityCurve = _models.PaperEquityCurve

# _compute_portfolio_drawdown() is extracted via exec() (matching
# test_correlation_preentry.py's established technique exactly) rather than imported directly
# — paper_trading_engine.py's own module-level `from sqlalchemy import select, func` would
# otherwise resolve to conftest.py's stubbed sqlalchemy if this module gets imported anywhere
# else in the same pytest session before or after this file, silently breaking real SQL calls.
_ENGINE_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_ENGINE_SOURCE = _ENGINE_PATH.read_text()


def _extract_compute_portfolio_drawdown():
    start = _ENGINE_SOURCE.index("def _compute_portfolio_drawdown(")
    end = _ENGINE_SOURCE.index("\n\ndef _write_gate_block(", start)
    func_source = _ENGINE_SOURCE[start:end]
    namespace = {
        "select": select,
        "func": func,
        "PaperEquityCurve": PaperEquityCurve,
    }
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_compute_portfolio_drawdown"]


_compute_portfolio_drawdown = _extract_compute_portfolio_drawdown()

from src.services.email_service import send_portfolio_drawdown_alert_email

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


def _session():
    return Session(_ENGINE)


def _add_curve_point(session, portfolio_id: int, day: int, equity: float):
    session.add(PaperEquityCurve(
        portfolio_id=portfolio_id, date=date(2026, 1, day), equity=equity,
        cash=equity, open_positions_value=0.0, open_positions_count=0,
    ))
    session.commit()


# ── _compute_portfolio_drawdown() ───────────────────────────────────────────────────────────

def test_no_curve_history_and_zero_current_equity_returns_none():
    with _session() as s:
        assert _compute_portfolio_drawdown(s, portfolio_id=9001, equity=0.0) is None


def test_no_drawdown_when_equity_at_or_above_peak():
    with _session() as s:
        _add_curve_point(s, 9002, 1, 100_000.0)
        assert _compute_portfolio_drawdown(s, 9002, equity=100_000.0) == 0.0
        assert _compute_portfolio_drawdown(s, 9002, equity=105_000.0) == 0.0


def test_computes_real_drawdown_fraction_from_peak():
    with _session() as s:
        _add_curve_point(s, 9003, 1, 100_000.0)
        dd = _compute_portfolio_drawdown(s, 9003, equity=80_000.0)
    assert dd == 0.20  # (100000 - 80000) / 100000


def test_uses_the_max_historical_peak_not_just_the_latest_snapshot():
    with _session() as s:
        _add_curve_point(s, 9004, 1, 100_000.0)
        _add_curve_point(s, 9004, 2, 90_000.0)  # a later, lower snapshot must not lower the peak
        dd = _compute_portfolio_drawdown(s, 9004, equity=85_000.0)
    assert dd == 0.15  # (100000 - 85000) / 100000, peak stays 100000


def test_current_intraday_equity_above_stored_peak_becomes_the_new_peak_pa_d2():
    """PA-D2: current intraday equity must be included in the peak computation so an
    all-time-high TODAY (not yet snapshotted to PaperEquityCurve) is correctly treated as the
    real peak — a drawdown must never be computed against a stale, lower historical peak."""
    with _session() as s:
        _add_curve_point(s, 9005, 1, 100_000.0)
        dd = _compute_portfolio_drawdown(s, 9005, equity=120_000.0)  # new all-time high, intraday
    assert dd == 0.0  # equity == peak now, zero drawdown, not a negative "gain" number


def test_isolated_per_portfolio_id():
    with _session() as s:
        _add_curve_point(s, 9006, 1, 100_000.0)
        _add_curve_point(s, 9007, 1, 50_000.0)
        dd_a = _compute_portfolio_drawdown(s, 9006, equity=90_000.0)
        dd_b = _compute_portfolio_drawdown(s, 9007, equity=50_000.0)
    assert dd_a == 0.10
    assert dd_b == 0.0


# ── send_portfolio_drawdown_alert_email() — pure composition, tested directly ───────────────

def _capture_send():
    calls = []
    def _fake_send(to, subject, body_html, body_text):
        calls.append({"to": to, "subject": subject, "html": body_html, "text": body_text})
        return True
    return calls, _fake_send


def _breach(portfolio_name="US SWING Portfolio", current_dd_pct=24.3, limit_pct=20.0, equity=76_500.0):
    return {"portfolio_id": 3, "portfolio_name": portfolio_name, "current_dd_pct": current_dd_pct,
            "limit_pct": limit_pct, "equity": equity}


def test_single_breach_renders_portfolio_name_and_drawdown_pct():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_portfolio_drawdown_alert_email("user@example.com", [_breach()])
    html, text = calls[0]["html"], calls[0]["text"]
    assert "US SWING Portfolio" in html and "-24.3%" in html
    assert "US SWING Portfolio" in text


def test_subject_reflects_breach_count():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_portfolio_drawdown_alert_email("user@example.com", [_breach("A"), _breach("B")])
    assert "2 Portfolios" in calls[0]["subject"]


def test_singular_subject_and_body_for_one_breach():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_portfolio_drawdown_alert_email("user@example.com", [_breach()])
    assert "1 Portfolio " in calls[0]["subject"] or "1 Portfolio" in calls[0]["subject"]
    assert "2 Portfolios" not in calls[0]["subject"]
    assert " has" in calls[0]["html"]


def test_multiple_breaches_all_rendered():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_portfolio_drawdown_alert_email("user@example.com", [_breach("Alpha"), _breach("Bravo")])
    html = calls[0]["html"]
    assert "Alpha" in html and "Bravo" in html


def test_states_new_entries_already_paused_no_action_required():
    """The email must clearly state this already happened (entries are already paused) —
    never imply the user needs to take an action to stop trading."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_portfolio_drawdown_alert_email("user@example.com", [_breach()])
    html_lower = calls[0]["html"].lower()
    assert "paused" in html_lower
    assert "no action is required" in html_lower


# ── check_portfolio_drawdown_alerts() — source-text regression checks ───────────────────────

def _check_portfolio_drawdown_alerts_body() -> str:
    start = _scheduler_source.index("def check_portfolio_drawdown_alerts(")
    end = _scheduler_source.index("\ndef ", start + 1)
    return _scheduler_source[start:end]


def test_reuses_compute_portfolio_drawdown_not_a_second_derivation():
    """Must reuse the EXACT SAME peak-vs-current-equity helper the circuit breaker itself
    uses — never a fresh, second computation that could silently drift from it."""
    body = _check_portfolio_drawdown_alerts_body()
    assert "from .paper_trading_engine import _compute_portfolio_drawdown" in body
    assert "_compute_portfolio_drawdown(session, p.id, equity)" in body


def test_delivered_only_to_price_alert_subscribed_recipients():
    body = _check_portfolio_drawdown_alerts_body()
    assert "PriceAlert.triggered.is_(False)" in body


def test_only_scans_active_portfolios():
    body = _check_portfolio_drawdown_alerts_body()
    assert "PaperPortfolio.is_active.is_(True)" in body


def test_skips_portfolios_with_drawdown_gate_disabled():
    body = _check_portfolio_drawdown_alerts_body()
    assert "if not max_dd_cfg or max_dd_cfg <= 0:" in body
    assert "continue" in body


def test_state_key_is_cleared_on_recovery_not_left_stale():
    """A recovered portfolio must have its active-breach state cleared so a genuine FUTURE
    re-breach is treated as new, not silently suppressed by a stale key."""
    body = _check_portfolio_drawdown_alerts_body()
    assert "_rc.delete(state_key)" in body


def test_only_a_new_breach_not_an_already_active_one_triggers_a_send():
    """The whole point of the state-transition dedup: a portfolio that stays breached for many
    consecutive 1-minute cycles must not re-email on every single cycle."""
    body = _check_portfolio_drawdown_alerts_body()
    assert "already_active = bool(_rc.exists(state_key))" in body
    assert "if already_active:" in body
    assert "continue" in body


def test_state_key_is_set_only_after_a_new_breach_is_recorded():
    body = _check_portfolio_drawdown_alerts_body()
    assert '_rc.setex(state_key, 30 * 86400, "1")' in body


def test_per_recipient_send_is_isolated_from_the_rest_of_the_loop():
    """A single recipient's send exception must not abort the whole remaining recipient loop —
    matching check_earnings_beat_screener_alerts()'s own established isolation pattern."""
    body = _check_portfolio_drawdown_alerts_body()
    assert "for uid, user in recipients.items():" in body
    assert "except Exception as _send_exc:" in body


def test_state_key_is_namespaced_per_portfolio():
    body = _check_portfolio_drawdown_alerts_body()
    assert 'f"stockai:drawdown_alert_active:{p.id}"' in body
