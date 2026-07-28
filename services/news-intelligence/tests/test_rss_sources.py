"""Tests for rss_sources.py — real feedparser against constructed fixture RSS XML (not a live
network call in a test). Business Wire's ASCII-only filter is the one real behavior specific
to this module (dropping the non-English noise from its generic international firehose feed —
see the module's own docstring for why GlobeNewswire was dropped in favor of this)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services import rss_sources  # noqa: E402

_SAMPLE_RSS = """<?xml version="1.0"?>
<rss version="2.0">
<channel>
<title>Sample Feed</title>
<item>
  <title>Acme Corp Reports Record Quarterly Revenue</title>
  <link>https://example.com/acme-earnings</link>
  <pubDate>Mon, 27 Jul 2026 22:24:00 GMT</pubDate>
</item>
<item>
  <title>Grindr Investit dans la Prévention du VIH</title>
  <link>https://example.com/non-ascii</link>
  <pubDate>Mon, 27 Jul 2026 21:56:00 GMT</pubDate>
</item>
<item>
  <title></title>
  <link>https://example.com/blank-title</link>
</item>
</channel>
</rss>"""


class TestParseFeed:
    def test_parses_real_entries_from_fixture_rss(self, monkeypatch):
        _real_parse = rss_sources.feedparser.parse
        monkeypatch.setattr(rss_sources.feedparser, "parse", lambda url: _real_parse(_SAMPLE_RSS))
        items = rss_sources._parse_feed("http://fake", "test_source")
        # blank-title entry is skipped; the other 2 survive at this layer (ASCII filtering is
        # fetch_businesswire()'s own job, not _parse_feed()'s).
        assert len(items) == 2
        assert items[0]["headline"] == "Acme Corp Reports Record Quarterly Revenue"
        assert items[0]["url"] == "https://example.com/acme-earnings"

    def test_publish_date_is_parsed_to_a_real_datetime(self, monkeypatch):
        _real_parse = rss_sources.feedparser.parse
        monkeypatch.setattr(rss_sources.feedparser, "parse", lambda url: _real_parse(_SAMPLE_RSS))
        items = rss_sources._parse_feed("http://fake", "test_source")
        assert items[0]["published_at"].year == 2026
        assert items[0]["published_at"].month == 7
        assert items[0]["published_at"].day == 27

    def test_parse_failure_returns_empty_list_not_raises(self, monkeypatch):
        def _raise(url):
            raise RuntimeError("network down")
        monkeypatch.setattr(rss_sources.feedparser, "parse", _raise)
        assert rss_sources._parse_feed("http://fake", "test_source") == []

    def test_bozo_with_no_entries_returns_empty(self, monkeypatch):
        class _FakeFeed:
            bozo = True
            bozo_exception = "malformed xml"
            entries = []
        monkeypatch.setattr(rss_sources.feedparser, "parse", lambda url: _FakeFeed())
        assert rss_sources._parse_feed("http://fake", "test_source") == []


class TestFetchBusinesswire:
    def test_ascii_only_filter_drops_non_english_titles(self, monkeypatch):
        """The real behavior specific to this source — a headline with non-ASCII characters
        (the multi-language noise from Business Wire's international firehose) must be dropped,
        while a genuine English financial headline survives."""
        monkeypatch.setattr(
            rss_sources, "_parse_feed",
            lambda url, name: [
                {"headline": "Turning Point Brands Declares Common Stock Dividend", "url": None, "published_at": None},
                {"headline": "Grindr Investit dans la Prévention du VIH", "url": None, "published_at": None},
            ],
        )
        items = rss_sources.fetch_businesswire()
        assert len(items) == 1
        assert items[0]["headline"] == "Turning Point Brands Declares Common Stock Dividend"
