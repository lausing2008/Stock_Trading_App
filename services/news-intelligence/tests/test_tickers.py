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

    # ── AUD264-NEWS-COMPANY-NAME-UNBOUNDED-SUBSTRING: name matching now boundary-aware ───────

    def test_company_name_does_not_match_as_a_continuation_of_a_longer_word(self, monkeypatch):
        """The exact bug this closes: a company literally named "Target" (a common English
        word) used to match "targets"/"targeted" — a DIFFERENT word, not the same word with
        trailing punctuation — purely because the old code was a bare substring test."""
        _fake_universe(monkeypatch, [("TGT", "TARGET", "US")])
        assert tickers.extract_symbols("The company targets a new market segment") == []
        assert tickers.extract_symbols("Sales figures were targeted for review") == []

    def test_company_name_still_matches_as_its_own_standalone_word(self, monkeypatch):
        """The fix must not be so strict it breaks the legitimate, common case — a real mention
        of the company name on its own is still a real match."""
        _fake_universe(monkeypatch, [("TGT", "TARGET", "US")])
        assert tickers.extract_symbols("Target reports strong holiday sales") == ["TGT"]

    def test_company_name_with_apostrophe_s_possessive_still_matches(self, monkeypatch):
        """A possessive ("Target's") is the SAME word with trailing punctuation, not a
        different word — the boundary check (apostrophe isn't alphanumeric) must still match
        this, unlike the plural/continuation cases above."""
        _fake_universe(monkeypatch, [("TGT", "TARGET", "US")])
        assert tickers.extract_symbols("Target's stock surged after earnings") == ["TGT"]

    def test_company_named_block_matches_blocked_headline_but_not_a_different_word(self, monkeypatch):
        """Honest scope of this fix: the boundary regex correctly rejects "blocked"/"blockchain"
        (a DIFFERENT word than "Block"), but a headline genuinely using the standalone word
        "block" as a common verb ("Regulators block merger...") is, letter-for-letter, the SAME
        word as the company name — no boundary check can distinguish them without heavier NLP,
        and this fix does not claim to. What it DOES eliminate is the substring-continuation
        case (a different, longer word containing "block")."""
        _fake_universe(monkeypatch, [("SQ", "BLOCK", "US")])
        assert tickers.extract_symbols("The transaction was blocked by regulators") == []
        assert tickers.extract_symbols("Blockchain adoption continues to grow") == []
        assert tickers.extract_symbols("Block Inc announces new CEO") == ["SQ"]

    def test_datasets_matched_perfectly_does_not_match_company_named_match(self, monkeypatch):
        _fake_universe(monkeypatch, [("MTCH", "MATCH GROUP", "US")])
        assert tickers.extract_symbols("The two datasets matched perfectly") == []
        assert tickers.extract_symbols("Match Group reports Q3 subscriber growth") == ["MTCH"]

    # ── AUD264-NEWS-HK-NUMERIC-TICKER-FALSE-POSITIVE ──────────────────────────────────────────

    def test_numeric_ticker_does_not_match_an_unrelated_time_or_date_in_the_headline(self, monkeypatch):
        """The exact bug this closes: a purely-numeric HK ticker base ("0700") is a genuine
        standalone token in "Trading opens 0700 GMT" — the pre-existing alphanumeric-boundary
        check (which only excludes SUBSTRING continuations like "07001") does nothing to
        prevent this, since "0700" here really is its own word, just an unrelated one."""
        _fake_universe(monkeypatch, [("0700.HK", "TENCENT HOLDINGS", "HK")])
        assert tickers.extract_symbols("Trading opens 0700 GMT ahead of the open") == []
        assert tickers.extract_symbols("Markets close at 1600 with mixed results") == []

    def test_numeric_ticker_still_matches_when_the_hk_suffix_co_occurs(self, monkeypatch):
        """The real, common HK PR-headline convention this fix must not break: a headline that
        explicitly writes the .HK suffix alongside the bare numeric base is real context, not
        an accidental collision."""
        _fake_universe(monkeypatch, [("0700.HK", "TENCENT HOLDINGS", "HK")])
        assert tickers.extract_symbols("0700.HK reports record quarterly profit") == ["0700.HK"]

    def test_numeric_ticker_still_matches_when_the_company_name_co_occurs(self, monkeypatch):
        """A headline naming BOTH the bare numeric ticker and the real company is genuine
        context, not a coincidental number — must still match."""
        _fake_universe(monkeypatch, [("0700.HK", "TENCENT HOLDINGS", "HK")])
        assert tickers.extract_symbols("Tencent Holdings (0700) shares rally on earnings beat") == ["0700.HK"]

    def test_numeric_ticker_alone_with_neither_hk_suffix_nor_company_name_does_not_match(self, monkeypatch):
        """Direct regression guard for the fix's own gating condition — a bare numeric match
        with genuinely no corroborating context anywhere in the headline must be rejected,
        even outside the specific time-of-day example above."""
        _fake_universe(monkeypatch, [("0700.HK", "TENCENT HOLDINGS", "HK")])
        assert tickers.extract_symbols("The index rose 0700 points in early trading") == []

    def test_alphabetic_tickers_are_completely_unaffected_by_the_numeric_gate(self, monkeypatch):
        """Regression guard: the new numeric-only branch must never engage for a normal
        alphabetic ticker — AAPL still matches on its own, no co-occurring context required."""
        _fake_universe(monkeypatch, [("AAPL", "APPLE INC", "US")])
        assert tickers.extract_symbols("AAPL announces new product line") == ["AAPL"]


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
