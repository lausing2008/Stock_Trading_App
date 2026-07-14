"""Regression test for T247-MLPREDICTION-PROMOTIONHISTORY-RACE (reader side).

meta_trainer._record_promotion_status() now writes meta_model:promotion_history as a native
Redis LIST (RPUSH/LTRIM) instead of a single JSON blob (SETEX), to eliminate a
read-modify-write race under concurrent retrains. _read_promotion_history() must correctly
read BOTH formats: the new list format (meta_model:promotion_history going forward) and the
old blob format (position_scaling_gate:promotion_history, unchanged by this fix).
"""
import json
from unittest.mock import MagicMock

from src.api.admin import _read_promotion_history


def test_reads_new_list_format():
    entries = [
        {"ts": "2026-07-01T00:00:00", "promoted": True, "auc": 0.72},
        {"ts": "2026-07-02T00:00:00", "promoted": False, "auc": 0.60},
    ]
    r = MagicMock()
    r.type.return_value = "list"
    r.lrange.return_value = [json.dumps(e) for e in entries]

    result = _read_promotion_history(r, "meta_model:promotion_history")
    assert result == entries


def test_reads_old_blob_format_unchanged():
    """position_scaling_gate:promotion_history still uses the pre-existing SETEX blob
    format — must keep working exactly as before this fix."""
    entries = [{"ts": "2026-07-01T00:00:00", "would_promote": True}]
    r = MagicMock()
    r.type.return_value = "string"
    r.get.return_value = json.dumps(entries)

    result = _read_promotion_history(r, "position_scaling_gate:promotion_history")
    assert result == entries


def test_missing_key_returns_empty_list():
    r = MagicMock()
    r.type.return_value = "none"
    r.get.return_value = None

    result = _read_promotion_history(r, "meta_model:promotion_history")
    assert result == []


def test_redis_type_call_failure_returns_empty_list():
    r = MagicMock()
    r.type.side_effect = ConnectionError("redis unreachable")

    result = _read_promotion_history(r, "meta_model:promotion_history")
    assert result == []


def test_malformed_list_entry_returns_empty_list_not_raises():
    r = MagicMock()
    r.type.return_value = "list"
    r.lrange.return_value = ["not valid json"]

    result = _read_promotion_history(r, "meta_model:promotion_history")
    assert result == []


def test_malformed_blob_returns_empty_list_not_raises():
    r = MagicMock()
    r.type.return_value = "string"
    r.get.return_value = "not valid json"

    result = _read_promotion_history(r, "position_scaling_gate:promotion_history")
    assert result == []
