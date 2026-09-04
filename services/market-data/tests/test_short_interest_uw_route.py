"""Tests for AUD-SIGCORROBORATE's GET /{symbol}/short-interest-uw — real Unusual Whales
short-interest (si_float), added specifically so signal-engine (a separate service/container
with no direct Python import path to unusual_whales.py) can cross-check its own yfinance-
derived short_pct_float, mirroring check_short_squeeze_alerts()'s existing
AUD-SQUEEZE3-UWSHORTINTERESTCORROBORATION corroboration pattern.

routes.py can't be imported directly in this test environment (see test_gamma_exposure_route.py's
own docstring for why) — same source-text-extraction technique, since this function's real work
is delegated entirely to unusual_whales.py (already independently tested in
test_unusual_whales.py).
"""
import pathlib

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _extract_short_interest_uw_source() -> str:
    start = _ROUTES_SOURCE.index('@router.get("/{symbol}/short-interest-uw")')
    end = _ROUTES_SOURCE.index('@router.get("/{symbol}/earnings-transcript")', start)
    return _ROUTES_SOURCE[start:end]


_FUNC_SOURCE = _extract_short_interest_uw_source()


def test_route_is_registered_as_a_real_literal_path():
    assert '@router.get("/{symbol}/short-interest-uw")' in _ROUTES_SOURCE


def test_checks_availability_before_attempting_any_fetch():
    avail_idx = _FUNC_SOURCE.index("_uw.is_available()")
    fetch_idx = _FUNC_SOURCE.index("_uw.get_short_interest(")
    assert avail_idx < fetch_idx


def test_disabled_case_reports_available_false():
    assert '"available": False, "reason": "unusual_whales_disabled"' in _FUNC_SOURCE


def test_no_data_case_also_reports_available_false():
    assert '"available": False, "reason": "no_data"' in _FUNC_SOURCE


def test_no_data_case_covers_both_none_and_missing_si_float():
    """get_short_interest() can return an object with si_float=None even when it doesn't
    return None outright — the no_data branch must catch both, not just the None case."""
    idx = _FUNC_SOURCE.index('"available": False, "reason": "no_data"')
    guard_segment = _FUNC_SOURCE[max(0, idx - 200):idx]
    assert "si is None" in guard_segment
    assert "si.si_float is None" in guard_segment


def test_success_case_returns_short_percent_of_float_scaled_to_a_percentage():
    """si_float from Unusual Whales is a fraction (e.g. 0.15), matching yfinance's own
    short_percent_of_float convention before THIS app's fundamentals route multiplies by 100 —
    this route must apply the same *100 scaling so callers get a directly comparable percentage,
    not a raw fraction."""
    assert "round(si.si_float * 100, 2)" in _FUNC_SOURCE


def test_never_returns_available_true_without_a_real_short_percent_of_float():
    """Structural sanity check on the 3 return dicts: 'available: True' must only ever pair
    with a real short_percent_of_float field, never appear alongside an available:False
    fallback dict — that combination would silently imply real data exists when it doesn't."""
    import re
    returns = re.findall(r"return \{[^}]*\}", _FUNC_SOURCE, re.DOTALL)
    assert len(returns) == 3
    for r in returns:
        if '"available": True' in r:
            assert "short_percent_of_float" in r
        else:
            assert '"available": False' in r


def test_symbol_is_uppercased_before_any_lookup():
    """Matches get_gamma_exposure()'s own convention — symbol.upper() before is_available()/
    get_short_interest() are ever called, so a lowercase request path still hits the right
    UW cache key."""
    upper_idx = _FUNC_SOURCE.index("symbol.upper()")
    avail_idx = _FUNC_SOURCE.index("_uw.is_available()")
    assert upper_idx < avail_idx
