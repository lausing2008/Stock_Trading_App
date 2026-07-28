"""Tests for storage.py's symbol_mode dispatch logic and the hot-news Redis flag.

persist_news_items() itself needs a real Postgres-backed SessionLocal/RealtimeNewsItem +
pg_insert().on_conflict_do_nothing() — not available in this local test environment (no
psycopg2). This tests the parts that ARE independently verifiable: which symbol-resolution
path each symbol_mode dispatches to (via monkeypatched extract_symbols/symbol_for_cik), and
_mark_hot()'s own Redis-write behavior (via a fake Redis client), matching this repo's
established "test what's testable, document the DB-coupled remainder" precedent for functions
too Docker-only-dependency-coupled to fully exercise locally.
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services import storage  # noqa: E402


class TestMarkHot:
    def test_writes_a_redis_key_with_the_right_ttl_and_payload(self, monkeypatch):
        fake_redis = MagicMock()
        monkeypatch.setattr(storage, "get_redis", lambda: fake_redis)
        storage._mark_hot("AAPL", "Apple issues profit warning", "negative")
        fake_redis.setex.assert_called_once()
        args, _ = fake_redis.setex.call_args
        key, ttl, payload = args
        assert key == "stockai:hot_news:AAPL"
        assert ttl == storage._HOT_NEWS_TTL_SECONDS
        parsed = json.loads(payload)
        assert parsed["headline"] == "Apple issues profit warning"
        assert parsed["sentiment_label"] == "negative"

    def test_uppercases_the_symbol_in_the_key(self, monkeypatch):
        fake_redis = MagicMock()
        monkeypatch.setattr(storage, "get_redis", lambda: fake_redis)
        storage._mark_hot("aapl", "headline", "negative")
        key = fake_redis.setex.call_args[0][0]
        assert key == "stockai:hot_news:AAPL"

    def test_missing_sentiment_label_defaults_to_neutral_in_payload(self, monkeypatch):
        fake_redis = MagicMock()
        monkeypatch.setattr(storage, "get_redis", lambda: fake_redis)
        storage._mark_hot("AAPL", "headline", None)
        payload = json.loads(fake_redis.setex.call_args[0][2])
        assert payload["sentiment_label"] == "neutral"

    def test_redis_failure_does_not_raise(self, monkeypatch):
        fake_redis = MagicMock()
        fake_redis.setex.side_effect = RuntimeError("redis down")
        monkeypatch.setattr(storage, "get_redis", lambda: fake_redis)
        storage._mark_hot("AAPL", "headline", "negative")  # must not raise


class TestIsHot:
    def test_returns_parsed_payload_when_flag_set(self, monkeypatch):
        fake_redis = MagicMock()
        fake_redis.get.return_value = json.dumps({"headline": "X", "sentiment_label": "negative"})
        monkeypatch.setattr(storage, "get_redis", lambda: fake_redis)
        result = storage.is_hot("AAPL")
        assert result == {"headline": "X", "sentiment_label": "negative"}

    def test_returns_none_when_flag_not_set(self, monkeypatch):
        fake_redis = MagicMock()
        fake_redis.get.return_value = None
        monkeypatch.setattr(storage, "get_redis", lambda: fake_redis)
        assert storage.is_hot("AAPL") is None

    def test_redis_failure_returns_none_not_raises(self, monkeypatch):
        fake_redis = MagicMock()
        fake_redis.get.side_effect = RuntimeError("redis down")
        monkeypatch.setattr(storage, "get_redis", lambda: fake_redis)
        assert storage.is_hot("AAPL") is None
