"""Tests for AUD-EARNINGSCAL-MARKETESTIMATES.

User ask: "what're the estimates from the market for the stock before earning reports like
NVDA?" — events_calendar()'s earnings block already surfaced eps_estimate/trailing_eps/
revenue_growth/earnings_growth, but nothing about the stock's own history of beating/missing
estimates, or what analysts currently expect the price to do. Both were already computed
elsewhere in this codebase and simply never wired into this specific endpoint:
  - eps_beat_rate/eps_avg_surprise_pct already live on the SAME cached fundamentals blob this
    loop already reads (zero new fetch).
  - analyst_price_target_mean/_weighted/_n_firms come from _compute_weighted_analyst_consensus()
    (already built + tested in test_analyst_accuracy_weighting.py) — a real DB query, so it's
    only called for symbols that actually have a near-term earnings event in this window, never
    for the full active-stock universe events_calendar() otherwise iterates.

events_calendar() itself can't be imported in this test environment (conftest.py stubs
sqlalchemy/db wholesale, and routes.py imports fastapi/yfinance/common.config at module level)
— covered via source-text regression checks, matching test_fundamentals_cache_miss_logging.py's
established pattern for this exact import-constraint class.
"""
import pathlib

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _events_calendar_body() -> str:
    start = _ROUTES_SOURCE.index("def events_calendar(")
    end = _ROUTES_SOURCE.index("\n@router.get", start)
    return _ROUTES_SOURCE[start:end]


def _earnings_block() -> str:
    """The specific sub-block inside events_calendar() building an earnings-type event dict —
    narrower than the whole function, so a test checking "eps_beat_rate is read here" can't
    accidentally match some unrelated part of the function (e.g. the ex-dividend block a few
    lines below, which has no such field)."""
    body = _events_calendar_body()
    start = body.index("# Earnings")
    end = body.index("# Ex-dividend", start)
    return body[start:end]


def test_reads_eps_beat_rate_and_avg_surprise_from_the_same_cached_blob():
    """These two fields must come from the SAME `data` dict every other field in this block
    already reads (data.get(...)) — a fresh Redis GET or DB query here would be a real,
    unnecessary per-symbol cost this endpoint doesn't need."""
    block = _earnings_block()
    assert '"eps_beat_rate": data.get("eps_beat_rate")' in block
    assert '"eps_avg_surprise_pct": data.get("eps_avg_surprise_pct")' in block


def test_computes_analyst_consensus_only_inside_the_earnings_block():
    """_compute_weighted_analyst_consensus() is a real DB query — it must only be called for
    symbols that actually have a near-term earnings event (inside this specific block), never
    once per stock in the outer loop that iterates the WHOLE active-stock universe."""
    full_body = _events_calendar_body()
    assert full_body.count("_compute_weighted_analyst_consensus(") == 1
    block = _earnings_block()
    assert "_compute_weighted_analyst_consensus(session, stock.symbol)" in block


def test_analyst_consensus_call_happens_before_the_events_append():
    """The computed _consensus dict must exist before it's read into the event dict — a call
    site accidentally placed AFTER the .append() would silently produce a NameError (or, if
    hoisted incorrectly, read a stale/wrong variable)."""
    block = _earnings_block()
    call_idx = block.index("_compute_weighted_analyst_consensus(session, stock.symbol)")
    append_idx = block.index("events.append(")
    assert call_idx < append_idx


def test_reads_all_three_consensus_fields_from_the_computed_dict_not_hardcoded():
    block = _earnings_block()
    assert '"analyst_price_target_mean": _consensus.get("simple_mean")' in block
    assert '"analyst_price_target_weighted": _consensus.get("weighted_mean")' in block
    assert '"analyst_n_firms": _consensus.get("n_firms")' in block


def test_preexisting_fields_are_still_present_not_accidentally_removed():
    """A refactor mistake here could silently drop one of the fields this block already had
    before this change — confirm all 5 pre-existing fields are still read."""
    block = _earnings_block()
    for field in ("eps_estimate", "trailing_eps", "revenue_growth", "earnings_growth", "market_cap"):
        assert f'"{field}": data.get(' in block, f"missing pre-existing field: {field}"
