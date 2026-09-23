"""Tests for BUG-NEWSCLASSIFY-REPEATCOST — persist_news_items() must never re-classify a
headline whose URL is already stored for this source.

Confirmed live in production before this fix: every RSS/EDGAR poll cycle re-fetches the same
feed URL, which returns its most-recent N items regardless of what was already seen (RSS feeds
are not "since last poll" incremental). The DB's own ON CONFLICT dedup only prevented a
duplicate ROW — it did nothing to stop classify_in_batches() (a real, paid Claude call) from
running again on already-seen headlines every single cycle. Real production numbers at the
time this was caught: pr_newswire had 2,640 stored rows for only 22 distinct headlines (~120x
reclassification), businesswire 792-for-6 (~132x), sec_edgar 5,489-for-154 (~35x).

This test builds a REAL in-memory SQLite session with the REAL RealtimeNewsItem model (`db` is
stubbed wholesale by conftest.py for Docker-only dependencies, so this pops that stub and
restores it immediately after import, matching market-data's own established
test_broker_position_sync.py/test_correlation_preentry.py technique) — exercising the actual
dedup query and classify_in_batches() call, not a hand-copied reimplementation.
"""
import sys
from unittest.mock import MagicMock, patch

_STUBBED_MODULES = ("common", "common.config", "common.logging", "common.ai_keys", "common.redis_client", "db")
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util
import pathlib
from datetime import datetime, timezone

from sqlalchemy import Integer, create_engine
from sqlalchemy.orm import Session, sessionmaker

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test"] = _models
_spec.loader.exec_module(_models)

# RealtimeNewsItem.id is a BigInteger PK — real Postgres autoincrements this via a sequence,
# but SQLite only auto-increments a column declared exactly `INTEGER PRIMARY KEY` (BigInteger
# does not qualify), so the app's OWN real insert code (persist_news_items() never sets `id`
# explicitly — that's the whole point, it relies on the DB to assign one) would otherwise
# violate a NOT NULL constraint under this in-memory test engine. Swapping the compiled column
# type to plain Integer for this test's table (SQLite-only, does not affect the real model
# used against production Postgres) restores real autoincrement behavior.
_models.RealtimeNewsItem.__table__.c.id.type = Integer()

_engine = create_engine("sqlite:///:memory:")
_models.Base.metadata.create_all(_engine, tables=[_models.RealtimeNewsItem.__table__])
_SessionLocal = sessionmaker(bind=_engine)

# Restore the stubs for every OTHER test file collected in the same pytest run.
for _mod, _val in _saved_stubs.items():
    if _val is not None:
        sys.modules[_mod] = _val
    else:
        sys.modules.pop(_mod, None)

import sys as _sys  # noqa: E402
sys.modules.setdefault("common", MagicMock())
sys.modules.setdefault("common.config", MagicMock())
sys.modules.setdefault("common.logging", MagicMock())
sys.modules.setdefault("common.ai_keys", MagicMock())
sys.modules.setdefault("common.redis_client", MagicMock())
sys.modules.setdefault("db", MagicMock())

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from src.services import storage  # noqa: E402


def _item(headline, url, published=None):
    return {"headline": headline, "url": url, "published_at": published or datetime.now(timezone.utc)}


class TestPersistNewsItemsDedup:
    def test_does_not_reclassify_an_already_stored_url(self):
        with patch.object(storage, "SessionLocal", _SessionLocal), \
             patch.object(storage, "RealtimeNewsItem", _models.RealtimeNewsItem), \
             patch.object(storage, "get_admin_ai_key", return_value="fake-key"), \
             patch.object(storage, "classify_in_batches") as mock_classify:
            mock_classify.return_value = [
                {"sentiment_score": 50, "sentiment_label": "neutral", "is_material": False, "category": "other"},
            ]
            storage.persist_news_items([_item("Acme reports earnings", "https://x/1")], source="pr_newswire")
            mock_classify.assert_called_once()
            assert mock_classify.call_args[0][0] == ["Acme reports earnings"]

            mock_classify.reset_mock()
            # Re-poll: the SAME url shows up again (the real RSS-feed-reserves-old-items case).
            storage.persist_news_items([_item("Acme reports earnings", "https://x/1")], source="pr_newswire")
            # AUD-NEWSCLASSIFY-ORDERING: previously asserted `assert_called_once_with([], ...)` —
            # the old code always called classify_in_batches, even with an empty list (a no-op
            # that still cost a function call). It is now skipped outright when there is nothing
            # to classify. The property this test exists for — a re-polled URL is never
            # reclassified — is satisfied more strongly, not less.
            mock_classify.assert_not_called()

    def test_classifies_only_the_genuinely_new_item_in_a_mixed_batch(self):
        with patch.object(storage, "SessionLocal", _SessionLocal), \
             patch.object(storage, "RealtimeNewsItem", _models.RealtimeNewsItem), \
             patch.object(storage, "get_admin_ai_key", return_value="fake-key"), \
             patch.object(storage, "classify_in_batches") as mock_classify:
            mock_classify.return_value = [
                {"sentiment_score": 50, "sentiment_label": "neutral", "is_material": False, "category": "other"},
            ]
            storage.persist_news_items([_item("Old headline", "https://x/2")], source="pr_newswire")

            mock_classify.reset_mock()
            mock_classify.return_value = [
                {"sentiment_score": 50, "sentiment_label": "neutral", "is_material": False, "category": "other"},
            ]
            storage.persist_news_items(
                [_item("Old headline", "https://x/2"), _item("Brand new headline", "https://x/3")],
                source="pr_newswire",
            )
            mock_classify.assert_called_once_with(["Brand new headline"], "fake-key")

    def test_same_url_different_source_is_treated_as_new(self):
        """Dedup must be scoped per-source — the same URL appearing under a different source
        (shouldn't normally happen, but the dedup query itself must not silently ignore the
        source filter) should still be classified."""
        with patch.object(storage, "SessionLocal", _SessionLocal), \
             patch.object(storage, "RealtimeNewsItem", _models.RealtimeNewsItem), \
             patch.object(storage, "get_admin_ai_key", return_value="fake-key"), \
             patch.object(storage, "classify_in_batches") as mock_classify:
            mock_classify.return_value = [
                {"sentiment_score": 50, "sentiment_label": "neutral", "is_material": False, "category": "other"},
            ]
            storage.persist_news_items([_item("Shared url headline", "https://shared/1")], source="pr_newswire")

            mock_classify.reset_mock()
            mock_classify.return_value = [
                {"sentiment_score": 50, "sentiment_label": "neutral", "is_material": False, "category": "other"},
            ]
            storage.persist_news_items([_item("Shared url headline", "https://shared/1")], source="businesswire")
            mock_classify.assert_called_once_with(["Shared url headline"], "fake-key")

    def test_items_with_no_url_are_always_classified(self):
        """A rare edge case (no url) can't be deduped by URL — must still be classified every
        time rather than silently dropped from classification forever."""
        with patch.object(storage, "SessionLocal", _SessionLocal), \
             patch.object(storage, "RealtimeNewsItem", _models.RealtimeNewsItem), \
             patch.object(storage, "get_admin_ai_key", return_value="fake-key"), \
             patch.object(storage, "classify_in_batches") as mock_classify:
            mock_classify.return_value = [
                {"sentiment_score": 50, "sentiment_label": "neutral", "is_material": False, "category": "other"},
            ]
            storage.persist_news_items([_item("No-url headline", None)], source="sec_edgar")
            mock_classify.assert_called_once_with(["No-url headline"], "fake-key")


class TestClassifyOnlyTrackedSymbols:
    """AUD-NEWSCLASSIFY-ORDERING — resolve the symbol BEFORE spending a Claude call.

    persist_news_items() used to classify every new headline and only afterwards discover
    whether it mapped to anything. Measured over 263,457 classified rows: 186,461 (70.8%) had
    symbol IS NULL and could never influence a decision. By source — sec_edgar 148,860 items at
    97.0% unusable, pr_newswire 99.9%, businesswire 99.9% — while alpaca is 0%, precisely
    because it ships native ticker tags. news_classify is 87% of this platform's Claude spend.

    Nothing required the LLM to run first: the EDGAR CIK is on the raw item and
    symbol_for_cik() is a local lookup; extract_symbols() reads only the headline.
    """

    def test_an_untracked_headline_is_not_sent_to_claude(self):
        with patch.object(storage, "SessionLocal", _SessionLocal), \
             patch.object(storage, "RealtimeNewsItem", _models.RealtimeNewsItem), \
             patch.object(storage, "get_admin_ai_key", return_value="fake-key"), \
             patch.object(storage, "_load_universe", return_value=[("AAPL", "APPLE", "US")]), \
             patch.object(storage, "extract_symbols", return_value=[]), \
             patch.object(storage, "classify_in_batches") as mock_classify:
            storage.persist_news_items(
                [_item("Some private company files a prospectus", "https://x/untracked")],
                source="pr_newswire",
            )
            mock_classify.assert_not_called()

    def test_a_tracked_headline_is_still_classified(self):
        with patch.object(storage, "SessionLocal", _SessionLocal), \
             patch.object(storage, "RealtimeNewsItem", _models.RealtimeNewsItem), \
             patch.object(storage, "get_admin_ai_key", return_value="fake-key"), \
             patch.object(storage, "_load_universe", return_value=[("AAPL", "APPLE", "US")]), \
             patch.object(storage, "extract_symbols", return_value=["AAPL"]), \
             patch.object(storage, "classify_in_batches") as mock_classify:
            mock_classify.return_value = [
                {"sentiment_score": 20, "sentiment_label": "negative", "is_material": True, "category": "earnings"},
            ]
            storage.persist_news_items(
                [_item("AAPL cuts guidance", "https://x/tracked")], source="pr_newswire",
            )
            mock_classify.assert_called_once()
            assert mock_classify.call_args[0][0] == ["AAPL cuts guidance"]

    def test_only_the_tracked_half_of_a_mixed_batch_is_classified(self):
        def _fake_extract(headline, *a, **k):
            return ["AAPL"] if "AAPL" in headline else []

        with patch.object(storage, "SessionLocal", _SessionLocal), \
             patch.object(storage, "RealtimeNewsItem", _models.RealtimeNewsItem), \
             patch.object(storage, "get_admin_ai_key", return_value="fake-key"), \
             patch.object(storage, "_load_universe", return_value=[("AAPL", "APPLE", "US")]), \
             patch.object(storage, "extract_symbols", side_effect=_fake_extract), \
             patch.object(storage, "classify_in_batches") as mock_classify:
            mock_classify.return_value = [
                {"sentiment_score": 50, "sentiment_label": "neutral", "is_material": False, "category": "other"},
            ]
            storage.persist_news_items([
                _item("Unknown Co announces something", "https://x/m1"),
                _item("AAPL announces something", "https://x/m2"),
                _item("Another unknown filing", "https://x/m3"),
            ], source="pr_newswire")
            mock_classify.assert_called_once()
            assert mock_classify.call_args[0][0] == ["AAPL announces something"]

    def test_the_untracked_row_is_still_persisted_just_unlabelled(self):
        """The stated trade-off: the market-wide /news feed keeps the headline, source and
        timestamp and loses only its sentiment chip. Dropping the ROW would be a bigger,
        unannounced change."""
        with patch.object(storage, "SessionLocal", _SessionLocal), \
             patch.object(storage, "RealtimeNewsItem", _models.RealtimeNewsItem), \
             patch.object(storage, "get_admin_ai_key", return_value="fake-key"), \
             patch.object(storage, "_load_universe", return_value=[("AAPL", "APPLE", "US")]), \
             patch.object(storage, "extract_symbols", return_value=[]), \
             patch.object(storage, "classify_in_batches") as mock_classify:
            inserted = storage.persist_news_items(
                [_item("Untracked headline", "https://x/persist-me")], source="pr_newswire",
            )
            mock_classify.assert_not_called()
            assert inserted == 1, "the row must still be stored, just without labels"

    def test_a_broken_resolver_classifies_everything_rather_than_silently_disabling_the_gate(self):
        """THE failure mode this guard exists for. An EMPTY ticker universe means we cannot tell
        'untracked' from 'unknown'. Skipping classification there would silently stop every
        hot-news flag from ever being set — the same 'an empty result must say WHICH nothing'
        mistake as T403, where a dead feed and 'this stock has no options' were one string."""
        with patch.object(storage, "SessionLocal", _SessionLocal), \
             patch.object(storage, "RealtimeNewsItem", _models.RealtimeNewsItem), \
             patch.object(storage, "get_admin_ai_key", return_value="fake-key"), \
             patch.object(storage, "_load_universe", return_value=[]), \
             patch.object(storage, "extract_symbols", return_value=[]), \
             patch.object(storage, "classify_in_batches") as mock_classify:
            mock_classify.return_value = [
                {"sentiment_score": 50, "sentiment_label": "neutral", "is_material": False, "category": "other"},
            ]
            storage.persist_news_items(
                [_item("Headline during a resolver outage", "https://x/outage")],
                source="pr_newswire",
            )
            mock_classify.assert_called_once()

    def test_tagged_sources_are_unaffected(self):
        """Alpaca ships native ticker tags, needs no resolver, and was already 0% wasted."""
        with patch.object(storage, "SessionLocal", _SessionLocal), \
             patch.object(storage, "RealtimeNewsItem", _models.RealtimeNewsItem), \
             patch.object(storage, "get_admin_ai_key", return_value="fake-key"), \
             patch.object(storage, "classify_in_batches") as mock_classify:
            mock_classify.return_value = [
                {"sentiment_score": 80, "sentiment_label": "positive", "is_material": True, "category": "earnings"},
            ]
            it = _item("TSLA beats", "https://x/alpaca-1")
            it["symbols"] = ["TSLA"]
            storage.persist_news_items([it], source="alpaca", symbol_mode="tagged")
            mock_classify.assert_called_once()
