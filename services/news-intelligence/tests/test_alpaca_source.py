"""Tests for alpaca_source.py's pure message-parsing logic (_parse_news_message) — the
connection/reconnect loop itself (_run_once/run_alpaca_stream) is integration-level WebSocket
I/O not covered by unit tests here, matching this repo's established precedent of testing the
pure logic directly and documenting the untested integration surface rather than mocking an
entire WebSocket protocol round-trip."""
import sys
from datetime import timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services import alpaca_source  # noqa: E402


class TestParseNewsMessage:
    def test_parses_a_real_shaped_alpaca_news_message(self):
        msg = {
            "T": "n", "id": 12345,
            "headline": "Acme Corp Reports Record Quarterly Revenue",
            "summary": "...", "author": "Alpaca",
            "created_at": "2026-07-27T22:24:00Z",
            "updated_at": "2026-07-27T22:24:00Z",
            "url": "https://example.com/acme-earnings",
            "symbols": ["ACME", "MSFT"],
        }
        item = alpaca_source._parse_news_message(msg)
        assert item is not None
        assert item["headline"] == "Acme Corp Reports Record Quarterly Revenue"
        assert item["url"] == "https://example.com/acme-earnings"
        assert item["symbols"] == ["ACME", "MSFT"]
        assert item["published_at"].tzinfo == timezone.utc

    def test_missing_headline_returns_none(self):
        assert alpaca_source._parse_news_message({"T": "n", "symbols": ["AAPL"]}) is None

    def test_malformed_created_at_falls_back_to_now_not_crash(self):
        msg = {"headline": "X", "created_at": "not-a-real-timestamp", "symbols": []}
        item = alpaca_source._parse_news_message(msg)
        assert item is not None
        assert item["headline"] == "X"

    def test_missing_symbols_defaults_to_empty_list(self):
        item = alpaca_source._parse_news_message({"headline": "X", "created_at": "2026-07-27T22:24:00Z"})
        assert item["symbols"] == []

    def test_non_string_entries_in_symbols_are_filtered(self):
        msg = {"headline": "X", "created_at": "2026-07-27T22:24:00Z", "symbols": ["AAPL", None, 123, "MSFT"]}
        item = alpaca_source._parse_news_message(msg)
        assert item["symbols"] == ["AAPL", "MSFT"]
