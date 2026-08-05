"""Tests for T260-BEARISH-PUTS-WATCHLIST's routes.py additions:
  - GET /stocks/bearish_puts_watch — passthrough of the Redis cache
    check_gamma_unwind_alerts()'s _bearish_puts_watch_candidates() writes.
  - _squeeze_watch_out() — pure serialization of a SqueezeWatch row.
  - The squeeze-watch CRUD route registrations (source-text checks, matching this file's own
    established pattern for verifying route wiring without a full FastAPI TestClient).

routes.py imports directly in this test environment (conftest.py stubs `db` wholesale as a
MagicMock — confirmed via test_watchlist_delisted_badge.py's own docstring for the identical
pattern in watchlist.py).
"""
import pathlib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from src.api.routes import _squeeze_watch_out, bearish_puts_watch

_routes_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_routes_source = _routes_path.read_text()


def _watch(**overrides):
    defaults = dict(
        id=1, symbol="XYZ", watch_type="bearish_puts",
        added_at=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
        price_at_add=42.0, metric_at_add=60.0,
        reverted=False, reverted_at=None, revert_reason=None, note=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ── GET /stocks/bearish_puts_watch — pure Redis passthrough ─────────────────────────────────

def test_bearish_puts_watch_returns_cached_list():
    fake_redis = SimpleNamespace(get=lambda key: '[{"symbol": "XYZ", "dominant_side": "puts"}]')
    with patch("src.api.routes._get_redis", return_value=fake_redis):
        result = bearish_puts_watch()
    assert result == [{"symbol": "XYZ", "dominant_side": "puts"}]


def test_bearish_puts_watch_empty_cache_returns_empty_list():
    fake_redis = SimpleNamespace(get=lambda key: None)
    with patch("src.api.routes._get_redis", return_value=fake_redis):
        result = bearish_puts_watch()
    assert result == []


def test_bearish_puts_watch_malformed_json_degrades_to_empty_list_not_crash():
    fake_redis = SimpleNamespace(get=lambda key: "{not valid json")
    with patch("src.api.routes._get_redis", return_value=fake_redis):
        result = bearish_puts_watch()
    assert result == []


# ── _squeeze_watch_out() — pure serialization ────────────────────────────────────────────────

def test_active_watch_serializes_with_reverted_false_and_no_revert_fields():
    out = _squeeze_watch_out(_watch())
    assert out.reverted is False
    assert out.reverted_at is None
    assert out.revert_reason is None
    assert out.symbol == "XYZ"
    assert out.watch_type == "bearish_puts"
    assert out.price_at_add == 42.0
    assert out.metric_at_add == 60.0


def test_reverted_watch_serializes_reverted_fields():
    out = _squeeze_watch_out(_watch(
        reverted=True,
        reverted_at=datetime(2026, 8, 5, 9, 30, tzinfo=timezone.utc),
        revert_reason="price recovered to $45.00 (was $42.00 when added)",
    ))
    assert out.reverted is True
    assert out.reverted_at == "2026-08-05T09:30:00+00:00"
    assert "price recovered" in out.revert_reason


def test_note_and_short_squeeze_watch_type_serialize_correctly():
    out = _squeeze_watch_out(_watch(watch_type="short_squeeze", note="tracking the GME setup"))
    assert out.watch_type == "short_squeeze"
    assert out.note == "tracking the GME setup"


# ── Route registration — source-text checks ──────────────────────────────────────────────────

def test_list_endpoint_scopes_to_the_current_user():
    """A user must only ever see their OWN squeeze watches — never another user's."""
    start = _routes_source.index("def list_squeeze_watches(")
    end = _routes_source.index("\n\n\n", start)
    body = _routes_source[start:end]
    assert "SqueezeWatch.user_id == _user.id" in body


def test_create_endpoint_validates_watch_type():
    start = _routes_source.index("def add_squeeze_watch(")
    end = _routes_source.index("\n\n\n", start)
    body = _routes_source[start:end]
    assert '"short_squeeze", "bearish_puts"' in body


def test_create_endpoint_scopes_uniqueness_check_to_the_current_user():
    start = _routes_source.index("def add_squeeze_watch(")
    end = _routes_source.index("\n\n\n", start)
    body = _routes_source[start:end]
    assert "SqueezeWatch.user_id == _user.id" in body


def test_delete_endpoint_scopes_to_the_current_user_and_404s_if_not_found():
    start = _routes_source.index("def remove_squeeze_watch(")
    end = _routes_source.index("\n\n\n", start)
    body = _routes_source[start:end]
    assert "SqueezeWatch.user_id == _user.id" in body
    assert "404" in body
