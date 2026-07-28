"""Tests for edgar_source.py — real feedparser against a fixture Atom feed shaped exactly like
SEC EDGAR's real `action=getcurrent` response (confirmed live during this rewrite; see the
module's own docstring). Covers the title-parsing regex that extracts company name + CIK from
EDGAR's own "FORM - Company Name (CIK) (Filer)" title convention."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services import edgar_source  # noqa: E402

_SAMPLE_ATOM = """<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Latest Filings</title>
<entry>
<title>8-K/A - RTB Digital, Inc. (0001419275) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/1419275/index.htm"/>
<updated>2026-07-27T17:30:53-04:00</updated>
</entry>
<entry>
<title>8-K - Huron Consulting Group Inc. (0001289848) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/1289848/index.htm"/>
<updated>2026-07-27T17:29:58-04:00</updated>
</entry>
</feed>"""


class TestFetchOne:
    def test_parses_company_and_cik_from_title(self, monkeypatch):
        _real_parse = edgar_source.feedparser.parse
        monkeypatch.setattr(
            edgar_source.feedparser, "parse",
            lambda url, request_headers=None: _real_parse(_SAMPLE_ATOM),
        )
        items = edgar_source._fetch_one("8-K")
        assert len(items) == 2
        assert items[0]["cik"] == "0001419275"
        assert "RTB Digital" in items[0]["headline"]
        assert items[0]["headline"].startswith("8-K/A filed")

    def test_headline_uses_actual_parsed_form_not_the_query_param(self, monkeypatch):
        """A real bug caught while writing this test: querying type=4 can still return an
        entry whose real title is "8-K/A" (EDGAR's type filter isn't perfectly exclusive across
        every entry) — the headline must reflect what was ACTUALLY filed, not silently relabel
        it using the query's own filing_type param, which would misrepresent an amendment as a
        fresh original filing."""
        _real_parse = edgar_source.feedparser.parse
        monkeypatch.setattr(
            edgar_source.feedparser, "parse",
            lambda url, request_headers=None: _real_parse(_SAMPLE_ATOM),
        )
        items = edgar_source._fetch_one("4")
        assert items[0]["headline"].startswith("8-K/A filed")

    def test_headline_falls_back_to_query_filing_type_when_title_unparseable(self, monkeypatch):
        atom = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
        <entry><title>Some Unparseable Title Format</title>
        <link rel="alternate" href="https://example.com"/>
        <updated>2026-07-27T17:30:53-04:00</updated></entry></feed>"""
        _real_parse = edgar_source.feedparser.parse
        monkeypatch.setattr(edgar_source.feedparser, "parse", lambda url, request_headers=None: _real_parse(atom))
        items = edgar_source._fetch_one("8-K")
        assert items[0]["headline"].startswith("8-K filed")

    def test_malformed_title_falls_back_to_raw_title_with_no_cik(self, monkeypatch):
        atom = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
        <entry><title>Some Unparseable Title Format</title>
        <link rel="alternate" href="https://example.com"/>
        <updated>2026-07-27T17:30:53-04:00</updated></entry></feed>"""
        _real_parse = edgar_source.feedparser.parse
        monkeypatch.setattr(edgar_source.feedparser, "parse", lambda url, request_headers=None: _real_parse(atom))
        items = edgar_source._fetch_one("8-K")
        assert items[0]["cik"] is None

    def test_parse_failure_returns_empty_list(self, monkeypatch):
        def _raise(url, request_headers=None):
            raise RuntimeError("network down")
        monkeypatch.setattr(edgar_source.feedparser, "parse", _raise)
        assert edgar_source._fetch_one("8-K") == []


class TestFetchEdgarRealtime:
    def test_polls_all_configured_filing_types(self, monkeypatch):
        calls = []

        def _fake_fetch_one(filing_type):
            calls.append(filing_type)
            return [{"headline": f"{filing_type} filed", "url": None, "published_at": None, "cik": None}]

        monkeypatch.setattr(edgar_source, "_fetch_one", _fake_fetch_one)
        items = edgar_source.fetch_edgar_realtime()
        assert calls == edgar_source._FILING_TYPES
        assert len(items) == len(edgar_source._FILING_TYPES)
