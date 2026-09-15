"""T395-LEAPS-SYMBOLS — the LEAPS panel offered 4 symbols while 29 were captured.

The T384 backfill completed 2026-09-15: **29 symbols, 16,443 symbol-days, 23.19M rows, zero
errors**. The panel still listed only the original QQQ family, so none of it was reachable.

Same shape as T383 and T386 — data captured correctly and never surfaced is, from the UI,
indistinguishable from never having been captured. Third occurrence, hence the explicit test.

SELECTION IS BY MEASURED USABILITY, not by row count. "Usable" = a day carrying a
delta-selectable 0.60-0.80 call at >=330 DTE with a real NBBO quote. Greeks are sparse BY DESIGN
(UW returns delta only where volume > 0), so raw rows overstate what is testable:

    724 days, ~100% usable : META TSM GOOG JPM AVGO CRWD NET MU GLD TSLA SMH XLK CAT RTX DELL
    93-96%                 : HPE (93), GEV (96)
    80%                    : SOXX
    549 days               : SPY QQQ TQQQ (100%), QLD (82%), QQQM (50%)
    130 days, 100%         : AAPL MSFT NVDA AMZN AMD PLTR
"""
import pathlib
import re

import pytest

PANEL = (
    pathlib.Path(__file__).resolve().parents[3]
    / "frontend/src/components/LeapsBacktestPanel.tsx"
).read_text()


def _symbols() -> list[str]:
    i = PANEL.index("const SYMBOLS = [")
    return re.findall(r"'([A-Z]+)'", PANEL[i:PANEL.index("] as const;", i)])


def test_the_original_qqq_family_is_retained():
    """The qqq-leaps-playbook page is built around this set; dropping one orphans its history."""
    for s in ("QQQ", "QQQM", "QLD", "TQQQ"):
        assert s in _symbols(), s


def test_the_high_usability_symbols_are_all_offered():
    """Every symbol at ~100% usable over 724 captured days."""
    for s in ("META", "TSM", "GOOG", "JPM", "AVGO", "CRWD", "NET", "MU",
              "GLD", "TSLA", "SMH", "XLK", "CAT", "RTX", "DELL"):
        assert s in _symbols(), s


def test_the_shorter_history_symbols_are_offered_too():
    """130 days is short but 100% usable — a valid 6-month backtest window."""
    for s in ("AAPL", "MSFT", "NVDA", "AMZN", "AMD", "PLTR"):
        assert s in _symbols(), s


def test_no_duplicates():
    syms = _symbols()
    assert sorted(set(syms)) == sorted(syms), "a duplicate would render two identical chips"


def test_every_offered_symbol_is_actually_captured():
    """Offering a symbol with no archive guarantees a 404 on every run. The captured set is
    fixed by _OPTHIST_SYMBOLS, so the panel must be a SUBSET of it."""
    import ast
    sched = (
        pathlib.Path(__file__).resolve().parents[1] / "src/services/scheduler.py"
    ).read_text()
    i = sched.index("_OPTHIST_SYMBOLS = [")
    captured = set(ast.literal_eval(sched[i + len("_OPTHIST_SYMBOLS = "):sched.index("\n]", i) + 2]))
    offered = set(_symbols())
    assert offered <= captured, f"offered but never captured: {sorted(offered - captured)}"


# ── Cost guards: 29 chips must not mean 29 backtests per click ──────────────────────────

def test_the_default_selection_is_small():
    """Defaulting to all 29 would fire 29 sequential option-chain backtests on one click, each
    scanning a 23M-row table. T375-LEAPS-PERF already had to fix a 47-second coverage query
    that surfaced as a browser NetworkError."""
    i = PANEL.index("useState<string[]>(")
    default = re.findall(r"'([A-Z]+)'", PANEL[i:i + 200])
    assert 0 < len(default) <= 6, f"default selection is {len(default)} symbols"


def test_coverage_is_fetched_for_the_SELECTION_not_all_symbols():
    """The coverage panel loads on page render. Requesting all 29 would reintroduce exactly the
    T375-LEAPS-PERF slow-query problem."""
    assert "api.leapsCoverage(selected.join(',')" in PANEL
    assert "api.leapsCoverage(SYMBOLS.join(','))" not in PANEL


def test_the_coverage_cache_key_varies_with_the_selection():
    """A key that ignores `selected` would serve one symbol's coverage while showing another's
    chips — the kind of stale-render mismatch T380 was reported for."""
    i = PANEL.index("leaps-coverage-")
    assert "${selected.join(',')}" in PANEL[i:i + 120]


def test_the_thin_etfs_are_kept_but_are_not_the_default():
    """QQQM (50% usable) and QLD (82%) are the counter-example that produced T380's 'no usable
    quote' failure. Reachable, but not what a user lands on."""
    syms, i = _symbols(), PANEL.index("useState<string[]>(")
    default = re.findall(r"'([A-Z]+)'", PANEL[i:i + 200])
    assert "QQQM" in syms and "QLD" in syms
    assert "QQQM" not in default and "QLD" not in default


def test_the_panel_is_no_longer_titled_qqq_family():
    """It offers 29 symbols across semis, mega-cap tech, financials, industrials and gold."""
    assert "QQQ Family" not in PANEL


def test_the_usability_rationale_is_recorded():
    """So the next person extending this list applies the same measured bar rather than adding
    whatever was captured most recently."""
    i = PANEL.index("const SYMBOLS = [")
    block = PANEL[max(0, i - 1400):i]
    assert "usable" in block.lower()
    assert "delta only where volume" in block, "the reason raw row counts mislead"
