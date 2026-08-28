"""Tests for SR-WATCH-PROXIMITY-ALERT's routes.py additions:
  - _sr_watch_out() — pure serialization of an SrWatch row.
  - The /sr-watch CRUD route registrations (source-text checks, matching
    test_squeeze_watch_routes.py's own established pattern for verifying route wiring
    without a full FastAPI TestClient).

routes.py imports directly in this test environment (conftest.py stubs `db` wholesale as a
MagicMock).
"""
import pathlib
from datetime import datetime, timezone
from types import SimpleNamespace

from src.api.routes import _sr_watch_out

_routes_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_routes_source = _routes_path.read_text()


def _watch(**overrides):
    defaults = dict(
        id=1, symbol="AAPL",
        added_at=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
        atr_multiplier=1.0, currently_near=False,
        last_alert_at=None, last_alert_level_kind=None, last_alert_level_price=None,
        note=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ── _sr_watch_out() — pure serialization ─────────────────────────────────────────────────────

def test_fresh_watch_serializes_with_currently_near_false_and_no_alert_fields():
    out = _sr_watch_out(_watch())
    assert out.symbol == "AAPL"
    assert out.currently_near is False
    assert out.last_alert_at is None
    assert out.last_alert_level_kind is None
    assert out.last_alert_level_price is None
    assert out.atr_multiplier == 1.0


def test_currently_near_watch_serializes_its_last_alert_fields():
    out = _sr_watch_out(_watch(
        currently_near=True,
        last_alert_at=datetime(2026, 8, 5, 9, 30, tzinfo=timezone.utc),
        last_alert_level_kind="support",
        last_alert_level_price=180.0,
    ))
    assert out.currently_near is True
    assert out.last_alert_at == "2026-08-05T09:30:00+00:00"
    assert out.last_alert_level_kind == "support"
    assert out.last_alert_level_price == 180.0


def test_custom_atr_multiplier_and_note_serialize_correctly():
    out = _sr_watch_out(_watch(atr_multiplier=2.5, note="watching for a breakout retest"))
    assert out.atr_multiplier == 2.5
    assert out.note == "watching for a breakout retest"


# ── Route registration — source-text checks ──────────────────────────────────────────────────

def test_list_endpoint_scopes_to_the_current_user():
    """A user must only ever see their OWN S/R watches — never another user's."""
    start = _routes_source.index("def list_sr_watches(")
    end = _routes_source.index("\n\n\n", start)
    body = _routes_source[start:end]
    assert "SrWatch.user_id == _user.id" in body


def test_create_endpoint_validates_atr_multiplier_is_positive():
    start = _routes_source.index("def add_sr_watch(")
    end = _routes_source.index("\n\n\n", start)
    body = _routes_source[start:end]
    assert "req.atr_multiplier <= 0" in body
    assert "400" in body


def test_create_endpoint_scopes_uniqueness_check_to_the_current_user():
    start = _routes_source.index("def add_sr_watch(")
    end = _routes_source.index("\n\n\n", start)
    body = _routes_source[start:end]
    assert "SrWatch.user_id == _user.id" in body


def test_create_endpoint_resets_currently_near_when_re_adding_an_existing_watch():
    """Re-adding a symbol already in the watch list must reset currently_near to False — a
    symbol re-added while already near a level should fire fresh, not silently inherit a stale
    "already alerted" state from before it was removed/re-added."""
    start = _routes_source.index("def add_sr_watch(")
    end = _routes_source.index("\n\n\n", start)
    body = _routes_source[start:end]
    existing_branch = body[body.index("if existing is not None:"):]
    assert "existing.currently_near = False" in existing_branch


def test_delete_endpoint_scopes_to_the_current_user_and_404s_if_not_found():
    start = _routes_source.index("def remove_sr_watch(")
    end = _routes_source.index("\n\n\n", start)
    body = _routes_source[start:end]
    assert "SrWatch.user_id == _user.id" in body
    assert "404" in body
