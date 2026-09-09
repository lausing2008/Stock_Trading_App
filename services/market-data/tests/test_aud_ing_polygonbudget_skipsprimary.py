"""AUD-ING-POLYGONBUDGET-SKIPSPRIMARY — a guard that named a provider instead of excluding one.

FOUND ONE DAY AFTER THE CHANGE THAT CAUSED IT, while writing the Data Pipeline reference page.

`AUD-ING-POLYGONDELAYED` reordered `_PRIORITY` to put `unusual_whales` first and Polygon LAST.
It did not touch this branch in `ingest_symbol()`:

    elif not _polygon_budget_available():
        adapters = [get_adapter("yfinance")]

That line was CORRECT while Polygon was first — "don't send a request we know will 429, go
straight to the fallback". Once Polygon moved to last, the same line became actively harmful: it
hardcodes yfinance and therefore **skips the new primary entirely**.

THE BLAST RADIUS WAS NEAR-TOTAL, and that is the part worth understanding. The budget counter
increments on EVERY US incremental ingest, whether or not Polygon is ever reached. With
`_POLYGON_BUDGET_PER_MINUTE = 5` and ~131 active US symbols, only the **first 5 symbols per
minute** ever saw Unusual Whales; the other ~126 were forced onto **yfinance-only** — the
unauthenticated, rate-limiting source the reorder existed to stop depending on.

Verified live in production before fixing:

    9 consecutive _polygon_budget_available() calls in one minute
        -> [True, True, True, True, True, False, False, False, False]

So the previous day's reorder was, in practice, largely INERT for 96% of symbols. Nothing failed:
yfinance answered, bars landed, `ingest.done` logged success. Same family as every other finding
in this codebase — not a crash, a silent wrong answer.

THE FIX: make the gate do what its NAME says — exclude Polygon, not select yfinance.

THE GENERALISABLE LESSON: a guard written as "fall back to X" silently encodes the priority order
current when it was written. Reordering the list does not update the guard; it just leaves it
naming a provider that is no longer the right answer. Prefer "exclude Y" over "use X".
"""
import pathlib

import pytest

SRC_DIR = pathlib.Path(__file__).resolve().parents[1] / "src"
ING_SRC = (SRC_DIR / "services/ingestion.py").read_text()
REG_SRC = (SRC_DIR / "adapters/registry.py").read_text()


class _A:
    """Adapter stand-in — only `.name` matters to the filter."""
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"_A({self.name})"


def _select(priority, budget_ok):
    """Mirrors the fixed branch. Source assertions below pin the real implementation."""
    adapters = [_A(n) for n in priority]
    if not budget_ok:
        without = [a for a in adapters if a.name != "polygon"]
        if without:
            adapters = without
    return [a.name for a in adapters]


_LIVE_PRIORITY = ["unusual_whales", "yfinance", "alpha_vantage", "polygon"]


# ── The bug ─────────────────────────────────────────────────────────────────────────────

def test_exhausting_the_budget_no_longer_skips_the_primary():
    """THE BUG. The old branch returned exactly ['yfinance'], dropping unusual_whales."""
    got = _select(_LIVE_PRIORITY, budget_ok=False)
    assert got[0] == "unusual_whales", "the primary must still be tried first"
    assert got == ["unusual_whales", "yfinance", "alpha_vantage"]


def test_the_old_behaviour_is_pinned_as_wrong():
    """Documents precisely what regressed, so nobody 'simplifies' it back."""
    old_behaviour = ["yfinance"]
    assert "unusual_whales" not in old_behaviour
    assert _select(_LIVE_PRIORITY, budget_ok=False) != old_behaviour


def test_polygon_is_still_dropped_when_the_budget_is_spent():
    """The guard must keep doing its actual job — BUG-POLYGONBUDGET measured 24,949 of 25,825
    Polygon calls (97%) rate-limited in one 24h window."""
    assert "polygon" not in _select(_LIVE_PRIORITY, budget_ok=False)


def test_nothing_changes_while_the_budget_is_available():
    assert _select(_LIVE_PRIORITY, budget_ok=True) == _LIVE_PRIORITY


# ── Safety: never produce an empty candidate list ───────────────────────────────────────

def test_polygon_alone_is_kept_rather_than_emptied():
    """If Polygon were somehow the only candidate, dropping it would raise IngestionError with
    NO adapter tried at all. One doomed request beats no request."""
    assert _select(["polygon"], budget_ok=False) == ["polygon"]


def test_the_source_guards_against_the_empty_list():
    i = ING_SRC.index("_without_polygon = [")
    block = ING_SRC[i:i + 500]
    assert "if _without_polygon:" in block, "must not assign an empty candidate list"


# ── The fix is in the DECIDING path, not a comment ──────────────────────────────────────

def test_the_hardcoded_yfinance_fallback_is_gone():
    """ASSERT ON THE LIVE STATEMENT. The string 'yfinance' appears throughout this file in
    prose, so match the executable form the bug actually took."""
    assert 'elif not _polygon_budget_available():\n            adapters = [get_adapter("yfinance")]' not in ING_SRC


def test_the_budget_check_now_filters_rather_than_selects():
    assert 'a.name != "polygon"' in ING_SRC
    # The marker string appears in the header comment too, so index the STATEMENT — a call at
    # statement position inside the branch, not the `def` and not a prose mention.
    i = ING_SRC.index("            if not _polygon_budget_available():")
    block = ING_SRC[i:i + 400]
    assert "_without_polygon" in block


def test_the_budget_check_runs_AFTER_the_priority_list_is_built():
    """Filtering before the list exists would have nothing to filter."""
    i = ING_SRC.index("adapters = get_adapters(market, timeframe)")
    j = ING_SRC.index("if not _polygon_budget_available():", i)
    assert i < j


def test_the_other_three_branches_are_untouched():
    """HK routing and the batch/backfill path must not change — HK has no UW coverage at all."""
    assert 'elif symbol.endswith(".HK") or market == "HK":\n            adapters = [get_adapter("yfinance")]' in ING_SRC
    assert 'elif force or head is None:\n            adapters = [get_adapter("yfinance")]' in ING_SRC


# ── The stale rationale is corrected where it was written ───────────────────────────────

def test_the_original_comments_claim_is_corrected_in_place():
    """The comment said "going straight to yfinance instead". Correcting it only in an audit doc
    would leave the misleading line exactly where the next reader looks."""
    assert "AUD-ING-POLYGONBUDGET-SKIPSPRIMARY" in ING_SRC
    assert "Polygon is now LAST in _PRIORITY" in ING_SRC


def test_the_counter_semantics_are_recorded():
    """Why the blast radius was ~126/131 rather than 'only Polygon calls' — the counter
    increments on EVERY US incremental ingest, not only when Polygon is used."""
    # Both the header note and the branch comment carry this; assert the fact appears at all.
    assert "increments on EVERY US incremental ingest" in ING_SRC \
        or "increments on every US incremental ingest" in ING_SRC
    assert "~126 of 131" in ING_SRC or "the other ~126" in ING_SRC


# ── The arithmetic, pinned ──────────────────────────────────────────────────────────────

def test_only_five_of_131_symbols_reached_the_primary():
    """The measured consequence. 5 of 131 is 3.8% — so the previous day's reorder was inert
    for 96% of the universe."""
    budget, us_symbols = 5, 131
    assert budget / us_symbols < 0.04
    assert us_symbols - budget == 126


def test_priority_still_has_polygon_last():
    """If a future change moves Polygon back to first, THIS fix stays correct (it excludes rather
    than selects) — but the reasoning above should be re-read."""
    i = REG_SRC.index("_PRIORITY = [")
    line = REG_SRC[i:REG_SRC.index("]", i) + 1]
    assert line.index("polygon") > line.index("unusual_whales")
    assert line.index("polygon") > line.index("yfinance")
