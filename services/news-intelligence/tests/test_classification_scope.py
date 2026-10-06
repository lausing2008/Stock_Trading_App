"""AUD-NEWSSCOPE — a provider tag was being treated as relevance.

MEASURED IN PRODUCTION over 24h on 2026-10-05 (docs/audits/2026-10-05-news-classification-
volume-review.md): 941 classification calls, 255,643 tokens, 1,075 headlines. Of 968 classified
Alpaca articles, 834 — 86.2% — mentioned no active tracked stock, because Alpaca subscribes to
`news: ["*"]` and `persist_news_items` took its `symbols` list as-is.

The fix is a SCOPE, not a watchlist filter: an SPY or oil story is market context this platform
uses even though SPY is not a tracked position. Out-of-scope headlines are still STORED — they
keep their headline, source and timestamp — and simply are not paid to be labelled.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.scope import MARKET_CONTEXT_SYMBOLS, classification_scope  # noqa: E402

ACTIVE = {"MU", "NVDA", "AAPL", "0700.HK"}


def test_a_tracked_symbol_is_in_scope():
    assert classification_scope(["MU"], ACTIVE) == "tracked"
    assert classification_scope(["AAPL", "SOMETHINGELSE"], ACTIVE) == "tracked"


def test_an_untracked_tag_is_no_longer_treated_as_relevance():
    """The exact defect: a provider tag on any listed company counted as resolved."""
    assert classification_scope(["PCVX"], ACTIVE) is None
    assert classification_scope(["PTC"], ACTIVE) is None
    assert classification_scope(["GOOGL"], ACTIVE) is None, \
        "a megacap this platform does not track is still out of scope"


def test_named_market_context_is_kept_in_scope():
    """Out of universe is not the same as useless — the measured leaders were SPY and USO."""
    assert classification_scope(["SPY"], ACTIVE) == "market_context"
    assert classification_scope(["USO"], ACTIVE) == "market_context"
    assert classification_scope(["TLT"], ACTIVE) == "market_context"
    assert classification_scope(["SMH"], ACTIVE) == "market_context"


def test_tracked_wins_over_context_when_both_are_present():
    """An article naming both is about the tracked company first."""
    assert classification_scope(["SPY", "MU"], ACTIVE) == "tracked"


def test_hong_kong_context_is_present_so_hk_reports_are_not_under_served():
    assert classification_scope(["2800.HK"], ACTIVE) == "market_context"
    assert classification_scope(["0700.HK"], ACTIVE) == "tracked"


def test_no_symbols_is_out_of_scope():
    assert classification_scope(None, ACTIVE) is None
    assert classification_scope([], ACTIVE) is None
    assert classification_scope([None], ACTIVE) is None


def test_matching_is_case_insensitive_on_both_sides():
    assert classification_scope(["mu"], ACTIVE) == "tracked"
    assert classification_scope(["spy"], ACTIVE) == "market_context"
    assert classification_scope(["MU"], {"mu"}) == "tracked"


def test_the_context_list_is_membership_not_a_pattern():
    """A prefix rule would drift silently as new tickers appear; a named list is auditable."""
    assert "SPY" in MARKET_CONTEXT_SYMBOLS
    assert classification_scope(["SPYG"], ACTIVE) is None, \
        "a different fund that merely starts with SPY is not market context"
    assert classification_scope(["XLKQ"], ACTIVE) is None


def test_an_empty_universe_does_not_silently_make_everything_context():
    """With no active stocks loaded, a tracked symbol cannot be recognised — the caller's
    fail-open guard must handle that, not this function pretending the article is context."""
    assert classification_scope(["MU"], set()) is None


# ---------------------------------------------------------------- the caller's guard

_STORAGE = (Path(__file__).resolve().parents[1] / "src" / "services" / "storage.py").read_text()


def test_tagged_mode_now_depends_on_the_universe_and_keeps_the_fail_open():
    """Tagged mode was exempt from the resolver guard because it needed no resolver. Scoping
    its tags makes it depend on the universe, so an empty universe must classify everything
    loudly rather than classify nothing quietly."""
    seg = _STORAGE[_STORAGE.index('if symbol_mode == "cik":'):]
    seg = seg[:seg.index("api_key = get_admin_ai_key")]
    assert "_resolver_ok = True" not in seg, "tagged can no longer assume a working resolver"
    assert "_resolver_ok = bool(_active)" in seg
    assert "not _resolver_ok" in _STORAGE, "the fail-open path still exists"


def test_out_of_scope_headlines_are_still_stored():
    """Only the LABELLING is skipped. The insert loop runs over every new item."""
    seg = _STORAGE[_STORAGE.index("inserted = 0"):]
    assert "zip(_new_items, classifications, resolved_symbols)" in seg, \
        "every new item is inserted, classified or not"
    assert "classifications: list = [None] * len(_new_items)" in _STORAGE, \
        "unclassified items carry None and still persist"


def test_the_scope_counts_are_logged_by_reason():
    """"How many did we skip" cannot be answered from one number."""
    assert "tracked=_by_scope.get" in _STORAGE
    assert "market_context=_by_scope.get" in _STORAGE
    assert "out_of_scope=_by_scope.get" in _STORAGE
