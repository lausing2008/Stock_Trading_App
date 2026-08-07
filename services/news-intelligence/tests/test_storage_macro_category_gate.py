"""Tests for AUD264-NEWS-MACRO-CATEGORY-IGNORED: persist_news_items() must never set the
per-symbol hot-news flag for a headline classified "macro" — that classification means the
story is about the MARKET, not the specific company it happens to name (e.g. "Nasdaq slides as
AAPL, MSFT and NVDA drag megacaps lower" tags all three symbols but isn't news about any one
of them specifically).

Uses the exact same real-in-memory-SQLite-plus-real-RealtimeNewsItem-model technique already
established in test_storage_dedup.py (db is stubbed wholesale by conftest.py for Docker-only
dependencies — this pops that stub, builds a real engine, and restores it immediately after
import) — and symbol_mode="tagged" so each item's symbol is supplied directly (bypassing
extract_symbols(), which needs a real DB-backed universe this test doesn't set up), letting
_mark_hot()'s actual invocation be asserted on directly rather than via source-text checks.
"""
import sys
from unittest.mock import MagicMock, patch

_STUBBED_MODULES = ("common", "common.config", "common.logging", "common.ai_keys", "common.redis_client", "db")
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util
import pathlib
from datetime import datetime, timezone

from sqlalchemy import Integer, create_engine
from sqlalchemy.orm import sessionmaker

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_macro_gate", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_macro_gate"] = _models
_spec.loader.exec_module(_models)

# See test_storage_dedup.py's own comment for why this SQLite-only column-type swap is needed
# (RealtimeNewsItem.id is a BigInteger PK, which SQLite doesn't auto-increment).
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


class TestMacroCategoryNeverSetsHotFlag:
    def test_macro_classified_headline_does_not_mark_any_named_symbol_hot(self):
        with patch.object(storage, "SessionLocal", _SessionLocal), \
             patch.object(storage, "RealtimeNewsItem", _models.RealtimeNewsItem), \
             patch.object(storage, "get_admin_ai_key", return_value="fake-key"), \
             patch.object(storage, "classify_in_batches") as mock_classify, \
             patch.object(storage, "_mark_hot") as mock_mark_hot:
            mock_classify.return_value = [
                {"sentiment_score": 20, "sentiment_label": "negative", "is_material": True, "category": "macro"},
            ]
            storage.persist_news_items(
                [_item("Nasdaq slides as AAPL, MSFT and NVDA drag megacaps lower", "https://x/macro1", ["AAPL"])],
                source="pr_newswire", symbol_mode="tagged",
            )
            mock_mark_hot.assert_not_called()

    def test_company_specific_material_headline_still_marks_hot(self):
        """The fix must not accidentally suppress the real, intended case — a genuinely
        company-specific material headline (category != "macro") still sets the flag."""
        with patch.object(storage, "SessionLocal", _SessionLocal), \
             patch.object(storage, "RealtimeNewsItem", _models.RealtimeNewsItem), \
             patch.object(storage, "get_admin_ai_key", return_value="fake-key"), \
             patch.object(storage, "classify_in_batches") as mock_classify, \
             patch.object(storage, "_mark_hot") as mock_mark_hot:
            mock_classify.return_value = [
                {"sentiment_score": 10, "sentiment_label": "negative", "is_material": True, "category": "earnings"},
            ]
            storage.persist_news_items(
                [_item("Apple issues surprise profit warning", "https://x/earnings1", ["AAPL"])],
                source="pr_newswire", symbol_mode="tagged",
            )
            mock_mark_hot.assert_called_once_with("AAPL", "Apple issues surprise profit warning", "negative")

    def test_macro_headline_naming_multiple_symbols_marks_none_of_them(self):
        """The exact failure scenario from the tracker item — a single index-level story
        tagging 3 symbols must not set 3 separate hot-news flags."""
        with patch.object(storage, "SessionLocal", _SessionLocal), \
             patch.object(storage, "RealtimeNewsItem", _models.RealtimeNewsItem), \
             patch.object(storage, "get_admin_ai_key", return_value="fake-key"), \
             patch.object(storage, "classify_in_batches") as mock_classify, \
             patch.object(storage, "_mark_hot") as mock_mark_hot:
            mock_classify.return_value = [
                {"sentiment_score": 15, "sentiment_label": "negative", "is_material": True, "category": "macro"},
            ]
            storage.persist_news_items(
                [_item("Megacaps drag Nasdaq lower", "https://x/macro2", ["AAPL", "MSFT", "NVDA"])],
                source="pr_newswire", symbol_mode="tagged",
            )
            mock_mark_hot.assert_not_called()

    def test_non_material_macro_headline_also_does_not_mark_hot(self):
        """is_material=False already independently blocks the flag — confirm the two guards
        compose correctly rather than one silently overriding the other."""
        with patch.object(storage, "SessionLocal", _SessionLocal), \
             patch.object(storage, "RealtimeNewsItem", _models.RealtimeNewsItem), \
             patch.object(storage, "get_admin_ai_key", return_value="fake-key"), \
             patch.object(storage, "classify_in_batches") as mock_classify, \
             patch.object(storage, "_mark_hot") as mock_mark_hot:
            mock_classify.return_value = [
                {"sentiment_score": 50, "sentiment_label": "neutral", "is_material": False, "category": "macro"},
            ]
            storage.persist_news_items(
                [_item("Routine market commentary", "https://x/macro3", ["AAPL"])],
                source="pr_newswire", symbol_mode="tagged",
            )
            mock_mark_hot.assert_not_called()
