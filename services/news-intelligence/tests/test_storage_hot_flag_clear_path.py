"""Tests for AUD264-HOTNEWS-FLAG-STALE-NO-CLEAR-PATH: the hot-news flag previously had no
delete path anywhere — a stale NEGATIVE flag could only ever be overwritten by another
MATERIAL follow-up, never cleared by a genuine, non-material correction/retraction for the
same symbol. Also confirms _mark_hot() now stamps a real `ts` timestamp in the payload.

Uses the exact same real-in-memory-SQLite-plus-real-RealtimeNewsItem-model technique already
established in test_storage_macro_category_gate.py/test_storage_dedup.py.
"""
import sys
from unittest.mock import MagicMock, patch

_STUBBED_MODULES = ("common", "common.config", "common.logging", "common.ai_keys", "common.redis_client", "db")
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util
import json
import pathlib
from datetime import datetime, timezone

from sqlalchemy import Integer, create_engine
from sqlalchemy.orm import sessionmaker

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_hot_clear", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_hot_clear"] = _models
_spec.loader.exec_module(_models)

_models.RealtimeNewsItem.__table__.c.id.type = Integer()

_engine = create_engine("sqlite:///:memory:")
_models.Base.metadata.create_all(_engine, tables=[_models.RealtimeNewsItem.__table__])
_SessionLocal = sessionmaker(bind=_engine)

for _mod, _val in _saved_stubs.items():
    if _val is not None:
        sys.modules[_mod] = _val
    else:
        sys.modules.pop(_mod, None)

sys.modules.setdefault("common", MagicMock())
sys.modules.setdefault("common.config", MagicMock())
sys.modules.setdefault("common.logging", MagicMock())
sys.modules.setdefault("common.ai_keys", MagicMock())
sys.modules.setdefault("common.redis_client", MagicMock())
sys.modules.setdefault("db", MagicMock())

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from src.services import storage  # noqa: E402


def _item(headline, url, symbols):
    return {
        "headline": headline, "url": url, "symbols": symbols,
        "published_at": datetime.now(timezone.utc),
    }


class _FakeRedis:
    """A minimal in-memory stand-in for get_redis() — real setex/get/delete semantics,
    no TTL enforcement needed since these tests never sleep past one."""
    def __init__(self):
        self.store: dict[str, str] = {}

    def setex(self, key, ttl, value):
        self.store[key] = value

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        self.store.pop(key, None)


class TestMarkHotStampsARealTimestamp:
    def test_the_stored_payload_includes_a_parseable_ts_field(self):
        fake_redis = _FakeRedis()
        with patch.object(storage, "get_redis", return_value=fake_redis):
            storage._mark_hot("AAPL", "Apple issues profit warning", "negative")
        payload = json.loads(fake_redis.store["stockai:hot_news:AAPL"])
        assert "ts" in payload
        # Must be genuinely parseable and recent, not a placeholder/empty string.
        parsed = datetime.fromisoformat(payload["ts"])
        age_seconds = (datetime.now(timezone.utc) - parsed.replace(tzinfo=timezone.utc)).total_seconds()
        assert 0 <= age_seconds < 5

    def test_existing_headline_and_sentiment_label_fields_are_unaffected(self):
        """Regression guard: adding ts must not change the pre-existing payload shape."""
        fake_redis = _FakeRedis()
        with patch.object(storage, "get_redis", return_value=fake_redis):
            storage._mark_hot("MSFT", "Microsoft wins contract", "positive")
        payload = json.loads(fake_redis.store["stockai:hot_news:MSFT"])
        assert payload["headline"] == "Microsoft wins contract"
        assert payload["sentiment_label"] == "positive"


class TestClearHotAndCurrentHotSentiment:
    def test_clear_hot_deletes_the_key(self):
        fake_redis = _FakeRedis()
        with patch.object(storage, "get_redis", return_value=fake_redis):
            storage._mark_hot("NVDA", "bad news", "negative")
            assert "stockai:hot_news:NVDA" in fake_redis.store
            storage._clear_hot("NVDA")
            assert "stockai:hot_news:NVDA" not in fake_redis.store

    def test_current_hot_sentiment_reads_the_real_stored_value(self):
        fake_redis = _FakeRedis()
        with patch.object(storage, "get_redis", return_value=fake_redis):
            storage._mark_hot("TSLA", "bad news", "negative")
            assert storage._current_hot_sentiment("TSLA") == "negative"

    def test_current_hot_sentiment_returns_none_when_no_flag_exists(self):
        fake_redis = _FakeRedis()
        with patch.object(storage, "get_redis", return_value=fake_redis):
            assert storage._current_hot_sentiment("GOOG") is None


class TestPersistNewsItemsClearsStaleNegativeFlags:
    def test_a_positive_material_followup_still_overwrites_via_mark_hot_not_clear(self):
        """A genuinely material positive follow-up already goes through _mark_hot's own
        overwrite (it's still material, non-macro) — _clear_hot must NOT also fire for the
        same item, since that would be a redundant/confusing double-write."""
        with patch.object(storage, "SessionLocal", _SessionLocal), \
             patch.object(storage, "RealtimeNewsItem", _models.RealtimeNewsItem), \
             patch.object(storage, "get_admin_ai_key", return_value="fake-key"), \
             patch.object(storage, "classify_in_batches") as mock_classify, \
             patch.object(storage, "_mark_hot") as mock_mark_hot, \
             patch.object(storage, "_clear_hot") as mock_clear_hot, \
             patch.object(storage, "_current_hot_sentiment", return_value="negative"):
            mock_classify.return_value = [
                {"sentiment_score": 80, "sentiment_label": "positive", "is_material": True, "category": "earnings"},
            ]
            storage.persist_news_items(
                [_item("Apple beats on earnings", "https://x/clear1", ["AAPL"])],
                source="pr_newswire", symbol_mode="tagged",
            )
            mock_mark_hot.assert_called_once_with("AAPL", "Apple beats on earnings", "positive")
            mock_clear_hot.assert_not_called()

    def test_a_non_material_followup_clears_an_existing_negative_flag(self):
        """The exact gap this fix closes: a non-material follow-up (e.g. "shares recover
        after selloff" style coverage that the LLM doesn't flag as independently material) for
        a symbol with a currently-negative flag must actively CLEAR it, not silently leave the
        stale warning to ride out its full TTL."""
        with patch.object(storage, "SessionLocal", _SessionLocal), \
             patch.object(storage, "RealtimeNewsItem", _models.RealtimeNewsItem), \
             patch.object(storage, "get_admin_ai_key", return_value="fake-key"), \
             patch.object(storage, "classify_in_batches") as mock_classify, \
             patch.object(storage, "_mark_hot") as mock_mark_hot, \
             patch.object(storage, "_clear_hot") as mock_clear_hot, \
             patch.object(storage, "_current_hot_sentiment", return_value="negative"):
            mock_classify.return_value = [
                {"sentiment_score": 55, "sentiment_label": "neutral", "is_material": False, "category": "other"},
            ]
            storage.persist_news_items(
                [_item("Apple shares recover in afternoon trading", "https://x/clear2", ["AAPL"])],
                source="pr_newswire", symbol_mode="tagged",
            )
            mock_mark_hot.assert_not_called()
            mock_clear_hot.assert_called_once_with("AAPL")

    def test_a_followup_does_not_clear_when_the_current_flag_is_not_negative(self):
        """No wasted Redis DELETE for a symbol whose current flag is already
        positive/neutral/absent — _clear_hot should only ever be called when there is
        genuinely something negative to clear."""
        with patch.object(storage, "SessionLocal", _SessionLocal), \
             patch.object(storage, "RealtimeNewsItem", _models.RealtimeNewsItem), \
             patch.object(storage, "get_admin_ai_key", return_value="fake-key"), \
             patch.object(storage, "classify_in_batches") as mock_classify, \
             patch.object(storage, "_mark_hot") as mock_mark_hot, \
             patch.object(storage, "_clear_hot") as mock_clear_hot, \
             patch.object(storage, "_current_hot_sentiment", return_value="positive"):
            mock_classify.return_value = [
                {"sentiment_score": 55, "sentiment_label": "neutral", "is_material": False, "category": "other"},
            ]
            storage.persist_news_items(
                [_item("Routine update", "https://x/clear3", ["AAPL"])],
                source="pr_newswire", symbol_mode="tagged",
            )
            mock_mark_hot.assert_not_called()
            mock_clear_hot.assert_not_called()

    def test_a_macro_followup_does_not_clear_a_company_specific_negative_flag(self):
        """The exact same reasoning AUD264-NEWS-MACRO-CATEGORY-IGNORED already established for
        SETTING the flag applies just as much to CLEARING it: an index-level story is not
        evidence about this specific company either way."""
        with patch.object(storage, "SessionLocal", _SessionLocal), \
             patch.object(storage, "RealtimeNewsItem", _models.RealtimeNewsItem), \
             patch.object(storage, "get_admin_ai_key", return_value="fake-key"), \
             patch.object(storage, "classify_in_batches") as mock_classify, \
             patch.object(storage, "_mark_hot") as mock_mark_hot, \
             patch.object(storage, "_clear_hot") as mock_clear_hot, \
             patch.object(storage, "_current_hot_sentiment", return_value="negative"):
            mock_classify.return_value = [
                {"sentiment_score": 60, "sentiment_label": "positive", "is_material": True, "category": "macro"},
            ]
            storage.persist_news_items(
                [_item("Nasdaq rallies as AAPL, MSFT lead megacaps higher", "https://x/clear4", ["AAPL"])],
                source="pr_newswire", symbol_mode="tagged",
            )
            mock_mark_hot.assert_not_called()
            mock_clear_hot.assert_not_called()
