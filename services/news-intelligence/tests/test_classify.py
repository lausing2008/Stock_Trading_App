"""Tests for classify.py — real httpx.Client construction (mocked at the transport level via
a monkeypatched httpx.Client), matching risk_agent.py's/macro_reaction.py's own established
Claude-call testing pattern elsewhere in this repo."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services import classify  # noqa: E402


def _fake_response(status_code, text):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = {"content": [{"text": text}]}
    return r


def _patch_client(monkeypatch, response):
    fake_client = MagicMock()
    fake_client.__enter__ = MagicMock(return_value=fake_client)
    fake_client.__exit__ = MagicMock(return_value=False)
    fake_client.post.return_value = response
    monkeypatch.setattr(classify.httpx, "Client", MagicMock(return_value=fake_client))
    return fake_client


class TestClassifyHeadlines:
    def test_empty_headlines_returns_empty_list(self):
        assert classify.classify_headlines([], api_key="fake") == []

    def test_empty_api_key_returns_all_none_same_length(self):
        result = classify.classify_headlines(["A", "B"], api_key="")
        assert result == [None, None]

    def test_successful_parse_returns_real_classifications_in_order(self, monkeypatch):
        payload = json.dumps([
            {"sentiment_score": 80, "sentiment_label": "positive", "is_material": True, "category": "earnings"},
            {"sentiment_score": 20, "sentiment_label": "negative", "is_material": False, "category": "other"},
        ])
        _patch_client(monkeypatch, _fake_response(200, payload))
        result = classify.classify_headlines(["Good news", "Bad news"], api_key="fake-key")
        assert result[0]["sentiment_label"] == "positive"
        assert result[0]["is_material"] is True
        assert result[1]["sentiment_label"] == "negative"

    def test_strips_markdown_fence_before_parsing(self, monkeypatch):
        payload = "```json\n" + json.dumps([
            {"sentiment_score": 50, "sentiment_label": "neutral", "is_material": False, "category": "other"},
        ]) + "\n```"
        _patch_client(monkeypatch, _fake_response(200, payload))
        result = classify.classify_headlines(["Some headline"], api_key="fake-key")
        assert result[0] is not None
        assert result[0]["sentiment_label"] == "neutral"

    def test_non_200_response_degrades_to_all_none(self, monkeypatch):
        _patch_client(monkeypatch, _fake_response(500, ""))
        result = classify.classify_headlines(["A", "B"], api_key="fake-key")
        assert result == [None, None]

    def test_malformed_json_degrades_to_all_none(self, monkeypatch):
        _patch_client(monkeypatch, _fake_response(200, "not json at all"))
        result = classify.classify_headlines(["A"], api_key="fake-key")
        assert result == [None]

    def test_invalid_sentiment_label_falls_back_to_neutral(self, monkeypatch):
        payload = json.dumps([
            {"sentiment_score": 50, "sentiment_label": "bogus_label", "is_material": False, "category": "other"},
        ])
        _patch_client(monkeypatch, _fake_response(200, payload))
        result = classify.classify_headlines(["A"], api_key="fake-key")
        assert result[0]["sentiment_label"] == "neutral"

    def test_invalid_category_falls_back_to_other(self, monkeypatch):
        payload = json.dumps([
            {"sentiment_score": 50, "sentiment_label": "neutral", "is_material": False, "category": "bogus_category"},
        ])
        _patch_client(monkeypatch, _fake_response(200, payload))
        result = classify.classify_headlines(["A"], api_key="fake-key")
        assert result[0]["category"] == "other"

    def test_sentiment_score_clamped_to_0_100(self, monkeypatch):
        payload = json.dumps([
            {"sentiment_score": 500, "sentiment_label": "positive", "is_material": False, "category": "other"},
        ])
        _patch_client(monkeypatch, _fake_response(200, payload))
        result = classify.classify_headlines(["A"], api_key="fake-key")
        assert result[0]["sentiment_score"] == 100.0

    def test_fewer_parsed_items_than_headlines_pads_with_none(self, monkeypatch):
        payload = json.dumps([
            {"sentiment_score": 50, "sentiment_label": "neutral", "is_material": False, "category": "other"},
        ])
        _patch_client(monkeypatch, _fake_response(200, payload))
        result = classify.classify_headlines(["A", "B", "C"], api_key="fake-key")
        assert len(result) == 3
        assert result[0] is not None
        assert result[1] is None
        assert result[2] is None


class TestClassifyInBatches:
    def test_chunks_into_batch_size_groups(self, monkeypatch):
        calls = []

        def _fake_classify(headlines, api_key):
            calls.append(len(headlines))
            return [None] * len(headlines)

        monkeypatch.setattr(classify, "classify_headlines", _fake_classify)
        headlines = [f"h{i}" for i in range(20)]
        result = classify.classify_in_batches(headlines, api_key="fake")
        assert len(result) == 20
        assert calls == [8, 8, 4]  # _BATCH_SIZE = 8

    def test_one_failed_batch_does_not_lose_a_successful_batch(self, monkeypatch):
        def _fake_classify(headlines, api_key):
            if headlines[0] == "fail":
                return [None] * len(headlines)
            return [{"sentiment_score": 50, "sentiment_label": "neutral", "is_material": False, "category": "other"}] * len(headlines)

        monkeypatch.setattr(classify, "classify_headlines", _fake_classify)
        result = classify.classify_in_batches(["ok"], api_key="fake")
        assert result[0] is not None
