"""Tests for tickers.py — the universe-aware ticker matcher that replaces the abandoned
DESIGN_REALTIME_NEWS_FEED_2026-07-25.md design's bare regex (which matched "EPS"/"CEO"/"AI" as
if they were real stock symbols). Every test below constructs a fake, small universe directly
(bypassing the real DB-backed _load_universe()) and confirms extract_symbols() only ever
returns symbols that are ACTUALLY in that universe — the property the original design's regex
completely lacked.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services import tickers  # noqa: E402


def _fake_universe(monkeypatch, rows):
    """rows: list of (symbol, name_upper, market) tuples."""
    monkeypatch.setattr(tickers, "_load_universe", lambda: rows)


class TestExtractSymbols:
    def test_matches_real_ticker_as_standalone_word(self, monkeypatch):
        _fake_universe(monkeypatch, [("AAPL", "APPLE INC", "US")])
        assert tickers.extract_symbols("AAPL announces new product line") == ["AAPL"]

    def test_does_not_match_ticker_as_substring_of_longer_word(self, monkeypatch):
        """A real bug class this fixes: a 3-letter ticker like "AMD" must not match inside an
        unrelated all-caps word like "PYRAMDING" — matching case, since bare-ticker matching is
        already case-sensitive (a mixed-case company name like "Camden" never collides with the
        all-caps "AMD" pattern in the first place, which is itself a real, separate false-
        positive guard, not this test's target)."""
        _fake_universe(monkeypatch, [("AMD", "ADVANCED MICRO DEVICES", "US")])
        assert tickers.extract_symbols("The company reported strong PYRAMDING growth") == []

    def test_common_english_acronyms_never_match_unless_in_universe(self, monkeypatch):
        """The exact false-positive class that broke the original design's regex: EPS, CEO, AI,
        IPO, FDA are common headline words, not real tickers in this fake universe."""
        _fake_universe(monkeypatch, [("AAPL", "APPLE INC", "US")])
        headline = "CEO discusses EPS growth and AI strategy ahead of IPO, awaits FDA update"
        assert tickers.extract_symbols(headline) == []

    def test_matches_company_name_even_without_ticker_in_headline(self, monkeypatch):
        """The common real-world PR Newswire/Business Wire case: a headline names the company,
        never the ticker at all."""
        _fake_universe(monkeypatch, [("AAPL", "APPLE INC", "US")])
        assert tickers.extract_symbols("Apple Inc announces record quarterly revenue") == ["AAPL"]

    def test_short_symbols_1_2_chars_are_not_matched_by_bare_symbol(self, monkeypatch):
        """A 1-2 letter symbol (e.g. a real ticker "T" or "A") must not match a common short
        English word case-insensitively — only company-name matching applies to these."""
        _fake_universe(monkeypatch, [("T", "AT&T INC", "US")])
        assert tickers.extract_symbols("The company plans to expand operations") == []

    def test_hk_suffix_stripped_for_matching(self, monkeypatch):
        _fake_universe(monkeypatch, [("0700.HK", "TENCENT HOLDINGS", "HK")])
        assert tickers.extract_symbols("Tencent Holdings reports record profit") == ["0700.HK"]

    def test_empty_headline_returns_empty(self, monkeypatch):
        _fake_universe(monkeypatch, [("AAPL", "APPLE INC", "US")])
        assert tickers.extract_symbols("") == []

    def test_max_matches_caps_the_result(self, monkeypatch):
        _fake_universe(monkeypatch, [
            ("AAPL", "APPLE INC", "US"),
            ("MSFT", "MICROSOFT CORP", "US"),
            ("GOOGL", "ALPHABET INC", "US"),
        ])
        headline = "Apple Inc, Microsoft Corp, and Alphabet Inc all report earnings"
        assert tickers.extract_symbols(headline, max_matches=2) == ["AAPL", "MSFT"]

    def test_multiple_real_tickers_in_one_headline(self, monkeypatch):
        _fake_universe(monkeypatch, [
            ("AAPL", "APPLE INC", "US"),
            ("MSFT", "MICROSOFT CORP", "US"),
        ])
        assert set(tickers.extract_symbols("AAPL and MSFT both beat estimates")) == {"AAPL", "MSFT"}


class TestSymbolForCik:
    def test_resolves_real_cik(self, monkeypatch):
        monkeypatch.setattr(tickers, "_load_cik_map", lambda: {"320193": "AAPL"})
        assert tickers.symbol_for_cik("0000320193") == "AAPL"

    def test_unresolved_cik_returns_none(self, monkeypatch):
        monkeypatch.setattr(tickers, "_load_cik_map", lambda: {"320193": "AAPL"})
        assert tickers.symbol_for_cik("9999999999") is None

    def test_none_cik_returns_none(self, monkeypatch):
        monkeypatch.setattr(tickers, "_load_cik_map", lambda: {"320193": "AAPL"})
        assert tickers.symbol_for_cik(None) is None
