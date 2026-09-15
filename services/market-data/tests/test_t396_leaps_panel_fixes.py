"""T396 — three real bugs the user found in screenshots of the extended LEAPS panel.

REPORTED: "error in the first pic, and how to read the last pic no stock info".

## 1. Coverage showed "0 days" for symbols that have 724

MY BUG, introduced by T395. To avoid a slow query the coverage request was scoped to the
SELECTED symbols — but the panel still rendered ALL 29, so every unselected one fell through to
a `0 days` default. META read "0 days" while the archive holds **724 usable days** for it;
verified directly:

    META 724   TSM 724   GOOG 724   JPM 721   CRWD 720   MU 723   (usable at delta 0.80)

The tell in the screenshot: the only symbols showing real numbers were exactly the selected
ones. **Showing a wrong number is worse than showing none** — a user would reasonably conclude
those symbols had no data and never select them.

## 2. NetworkError on Run — a 60-second query

Measured with EXPLAIN ANALYZE on the real table (now **49.4M rows / 12 GB**, tripled by the
T384 backfill):

    Index Scan using ix_och_leaps_cov ... actual rows=108485
    Execution Time: 60347 ms

Past the gateway timeout, which surfaces to the browser as NetworkError. T375-LEAPS-PERF fixed
this same query once at 7.5M rows (47s -> 13s); the table has since grown 6.5x.

**The index was already correct** — `(symbol, delta, ((expiry - as_of)), as_of)` with a partial
predicate, i.e. fully covering. But the plan showed `Index Scan`, not `Index ONLY Scan`, and
`pg_stat_user_tables` showed `last_vacuum` and `last_analyze` both NULL after a 23M-row bulk
load. A stale visibility map prevents index-only scans, so every matching row took a heap fetch.
VACUUM ANALYZE is the fix, not another index.

## 3. The roll result never said WHICH symbol it was for

A roll runs ONE symbol (`selected[0]`) and the per-cycle table has no symbol column, so the
entire result was unattributable — the user's "no stock info".
"""
import pathlib
import re

import pytest

PANEL = (
    pathlib.Path(__file__).resolve().parents[3]
    / "frontend/src/components/LeapsBacktestPanel.tsx"
).read_text()


# ── 1. Coverage must not render symbols it did not fetch ────────────────────────────────

def test_coverage_renders_only_the_selected_symbols():
    """Rendering all 29 while fetching only the selection is what produced the false 0 days."""
    i = PANEL.index("Data coverage at delta")
    block = PANEL[i:i + 900]
    assert "{selected.map(s => {" in block
    assert "{SYMBOLS.map(s => {" not in block


def test_the_fetch_and_the_render_use_the_SAME_list():
    """The actual invariant. If these ever diverge again the panel silently reports 0 days for
    real data — which is how this bug reached a screenshot."""
    assert "api.leapsCoverage(selected.join(',')" in PANEL
    i = PANEL.index("Data coverage at delta")
    assert "selected.map" in PANEL[i:i + 900]


def test_the_false_zero_is_explained_in_place():
    """So the next person does not 'simplify' it back to rendering every symbol."""
    i = PANEL.index("Data coverage at delta")
    block = PANEL[max(0, i - 700):i + 900]
    assert "724" in block, "the measured counter-example"
    assert "worse than showing none" in block


# ── 3. The roll result must identify its symbol ─────────────────────────────────────────

def test_the_roll_summary_shows_the_symbol():
    """A roll runs ONE symbol and the per-cycle table has no symbol column, so without this the
    result is unattributable."""
    assert "{rollResult.symbol}" in PANEL


def test_the_symbol_is_the_first_field_in_the_summary():
    """It identifies the whole result, so it must not be buried after Total/CAGR."""
    i = PANEL.index("{rollResult.symbol}")
    j = PANEL.index("rollResult.total_return_pct")
    assert i < j, "symbol must precede the numbers it labels"


def test_the_roll_toggle_says_it_runs_one_symbol():
    """The user selected six symbols and got a single-symbol result with no indication why."""
    i = PANEL.index("'hold', 'roll'")
    block = PANEL[i:i + 1400]
    assert "FIRST selected symbol only" in block


# ── Guards that must not regress ────────────────────────────────────────────────────────

def test_the_default_selection_is_still_small():
    """29 chips must not mean 29 backtests, and the coverage query is 60s cold."""
    i = PANEL.index("useState<string[]>(")
    default = re.findall(r"'([A-Z]+)'", PANEL[i:i + 200])
    assert 0 < len(default) <= 6


def test_the_coverage_cache_key_still_varies_with_the_selection():
    i = PANEL.index("leaps-coverage-")
    assert "${selected.join(',')}" in PANEL[i:i + 120]


def test_all_29_symbols_are_still_offered():
    i = PANEL.index("const SYMBOLS = [")
    syms = re.findall(r"'([A-Z]+)'", PANEL[i:PANEL.index("] as const;", i)])
    assert len(syms) == 29, f"expected 29 captured symbols, got {len(syms)}"
