"""Regression tests for BUG-DELISTED-GENERATION-BLIND.

Stock.delisted (aud14-survivorship) never flips Stock.active — a confirmed-delisted stock
stays "active" forever, so POST /signals/refresh and /signals/reset kept regenerating fresh
BUY/SELL signals for it on every refresh cycle (called from market-data's _refresh_market(),
~77x/day for US alone), wasting real yfinance/ML work on a stock that can never be traded
again. Confirmed sibling of BUG-PAPERPOS-DELISTED-FROZEN/BUG-ALERTS-DELISTED-SILENT
(2026-07-29) — those fixed CONSUMING the flag once a signal already existed; this is the
generation side that produces those signals in the first place.

routes.py can't be imported directly in this test environment (its import chain pulls in
FastAPI/DB dependencies not fully stubbed for this file) — matching this repo's established
source-text-extraction technique used elsewhere in this service (e.g.
test_int4_research_trigger_gated.py).
"""
import pathlib

_ROUTES_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
)
_SOURCE = _ROUTES_PATH.read_text()


def _function_body(def_line: str) -> str:
    start = _SOURCE.index(def_line)
    end = _SOURCE.index("\n\n\n", start)
    return _SOURCE[start:end]


def test_refresh_signals_excludes_delisted_stocks():
    body = _function_body("def refresh_signals(")
    assert "Stock.delisted.is_(False)" in body
    assert 'q = select(Stock.symbol).where(Stock.active.is_(True), Stock.delisted.is_(False))' in body


def test_reset_signals_excludes_delisted_stocks():
    body = _function_body("def reset_signals(")
    assert "Stock.delisted.is_(False)" in body


def test_delisted_filter_is_combined_with_active_not_a_replacement_for_it():
    """The fix must ADD a delisted exclusion alongside the existing active filter, not
    accidentally replace Stock.active.is_(True) with only the new condition — an inactive
    (but not confirmed-delisted) stock must still be excluded."""
    for def_line in ("def refresh_signals(", "def reset_signals("):
        body = _function_body(def_line)
        assert "Stock.active.is_(True)" in body
        assert "Stock.delisted.is_(False)" in body


def test_read_only_signal_display_endpoints_are_unaffected():
    """The read/display endpoints (all_latest_signals, consensus, signal_for) intentionally
    do NOT need this fix — they only read already-generated signals, they don't generate new
    ones, so a stale delisted-stock signal already stored is harmless to still display. This
    guards against a future overzealous pass adding the filter everywhere and silently
    changing display behavior no one asked for."""
    all_latest_body = _function_body("def all_latest_signals(")
    assert "Stock.delisted.is_(False)" not in all_latest_body
