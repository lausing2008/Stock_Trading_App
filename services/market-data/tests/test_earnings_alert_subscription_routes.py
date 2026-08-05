"""Tests for the EarningsAlertSubscription CRUD endpoints in routes.py — the "pick which
stocks trigger an earnings result/impact email" feature (BUG-EARNINGS-IMPACT-UNSCOPED
follow-up), a durable per-symbol opt-in independent of PriceAlert's one-shot trigger.

routes.py imports directly in this test environment (conftest.py stubs `db` wholesale as a
MagicMock — confirmed via test_squeeze_watch_routes.py's own established pattern for the
identical constraint).
"""
import pathlib
from datetime import datetime, timezone
from types import SimpleNamespace

from src.api.routes import _earnings_alert_sub_out

_routes_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_routes_source = _routes_path.read_text()


def _sub(**overrides):
    defaults = dict(id=1, symbol="AAPL", created_at=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc))
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ── _earnings_alert_sub_out() — pure serialization ───────────────────────────────────────────

def test_serializes_id_symbol_and_created_at():
    out = _earnings_alert_sub_out(_sub())
    assert out.id == 1
    assert out.symbol == "AAPL"
    assert out.created_at == "2026-08-04T12:00:00+00:00"


def test_different_symbol_serializes_correctly():
    out = _earnings_alert_sub_out(_sub(symbol="DIS", id=42))
    assert out.symbol == "DIS"
    assert out.id == 42


# ── Route registration — source-text checks ──────────────────────────────────────────────────

def test_list_endpoint_scopes_to_the_current_user():
    start = _routes_source.index("def list_earnings_alert_subscriptions(")
    end = _routes_source.index("\n\n\n", start)
    body = _routes_source[start:end]
    assert "EarningsAlertSubscription.user_id == _user.id" in body


def test_create_endpoint_uppercases_and_strips_the_symbol():
    start = _routes_source.index("def add_earnings_alert_subscription(")
    end = _routes_source.index("\n\n\n", start)
    body = _routes_source[start:end]
    assert "req.symbol.upper().strip()" in body


def test_create_endpoint_rejects_an_empty_symbol():
    start = _routes_source.index("def add_earnings_alert_subscription(")
    end = _routes_source.index("\n\n\n", start)
    body = _routes_source[start:end]
    assert "if not symbol:" in body
    assert "400" in body


def test_create_endpoint_is_idempotent_not_a_duplicate_error():
    """Re-subscribing to an already-subscribed symbol must return the existing row, not a
    unique-constraint error surfacing as a 500 — a user clicking "Alert me" twice on the same
    stock shouldn't crash."""
    start = _routes_source.index("def add_earnings_alert_subscription(")
    end = _routes_source.index("\n\n\n", start)
    body = _routes_source[start:end]
    assert "if existing is not None:" in body
    assert "return _earnings_alert_sub_out(existing)" in body


def test_create_endpoint_scopes_uniqueness_check_to_the_current_user():
    start = _routes_source.index("def add_earnings_alert_subscription(")
    end = _routes_source.index("\n\n\n", start)
    body = _routes_source[start:end]
    assert "EarningsAlertSubscription.user_id == _user.id" in body


def test_delete_endpoint_scopes_to_the_current_user_and_404s_if_not_found():
    start = _routes_source.index("def remove_earnings_alert_subscription(")
    end = _routes_source.index("\n\n\n", start)
    body = _routes_source[start:end]
    assert "EarningsAlertSubscription.user_id == _user.id" in body
    assert "404" in body


def test_delete_endpoint_uppercases_the_symbol_path_param():
    """The symbol path param must be normalized the same way as create — otherwise
    DELETE /earnings-alert-subscriptions/aapl would silently fail to match a row created via
    POST with symbol="AAPL"."""
    start = _routes_source.index("def remove_earnings_alert_subscription(")
    end = _routes_source.index("\n\n\n", start)
    body = _routes_source[start:end]
    assert "symbol.upper().strip()" in body
