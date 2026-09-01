"""Regression tests for MPE-01/MPE-07's wiring inside short_squeeze() (GET /stocks/
short_squeeze) — compute_short_squeeze_score() itself is independently, behaviorally tested in
test_short_squeeze_score.py; these confirm the ROUTE actually calls it correctly and doesn't
hammer Unusual Whales once per stock in the whole universe.

short_squeeze() can't be exercised end-to-end in this test environment (needs a real DB
session), matching test_squeeze_screener_delisted_filter.py's established source-text-
extraction technique for this exact function.
"""
import pathlib

_routes_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_SOURCE = _routes_path.read_text()


def _function_body() -> str:
    start = _SOURCE.index("def short_squeeze(")
    end = _SOURCE.index('\n\n@router.get("/bearish_puts_watch")', start)
    return _SOURCE[start:end]


def test_squeeze_score_is_computed_for_each_result_row():
    body = _function_body()
    assert "compute_short_squeeze_score(" in body
    assert '"squeeze_score": compute_short_squeeze_score(' in body


def test_results_sort_by_squeeze_score_not_short_float_alone():
    """The pre-MPE-01 sort was `x["short_percent_of_float"]` — this must have genuinely
    changed to the composite score, not just gained a new field nobody sorts by."""
    body = _function_body()
    assert 'results.sort(key=lambda x: (x["squeeze_score"]' in body


def test_availability_is_checked_once_before_the_per_symbol_loop_not_inside_it():
    """_uw.is_available() must be called exactly once, BEFORE `for symbol, stock in
    stock_map.items():` — calling it inside the loop would re-check the same Redis flag once
    per candidate for no benefit."""
    body = _function_body()
    avail_idx = body.index("_uw.is_available()")
    loop_idx = body.index("for symbol, stock in stock_map.items():")
    assert avail_idx < loop_idx
    # And it must be assigned to a variable read inside the loop, not called again per-row.
    assert body.count("_uw.is_available()") == 1


def test_uw_short_interest_fetch_is_gated_behind_the_already_computed_flag():
    """The real per-symbol UW call must be conditional on _uw_on (computed once above), not an
    unconditional call — an unconditional call would hit Unusual Whales for every symbol in the
    whole active-stock universe, not just the ones that already cleared the short-float floor."""
    body = _function_body()
    assert "if _uw_on:" in body
    assert "_uw.get_short_interest(symbol)" in body


def test_uw_fetch_happens_after_the_short_float_floor_filter_not_before():
    """The UW enrichment call must sit AFTER `if spf is None or spf * 100 < min_short_float:
    continue` — fetching it before that filter would hit Unusual Whales for every active stock,
    defeating the whole point of scoping it to already-qualifying candidates."""
    body = _function_body()
    floor_idx = body.index("if spf is None or spf * 100 < min_short_float:")
    uw_fetch_idx = body.index("_uw.get_short_interest(symbol)")
    assert floor_idx < uw_fetch_idx


def test_score_inputs_use_the_same_values_already_computed_for_the_row_not_a_second_derivation():
    """The score's short_percent_of_float/days_to_cover/momentum_score/change_pct arguments
    must reuse the SAME local variables the row's own dict fields are built from — a second,
    independent re-derivation risks silently drifting from what's actually displayed."""
    body = _function_body()
    assert "short_percent_of_float=round(spf * 100, 2)," in body
    assert "days_to_cover=_dtc," in body
    assert "momentum_score=_momentum," in body
    assert "change_pct=_change_pct," in body
