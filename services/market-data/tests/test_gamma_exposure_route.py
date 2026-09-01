"""Tests for MPE-06's GET /{symbol}/gamma-exposure — real dealer gamma exposure via Unusual
Whales, distinct from check_gamma_unwind_alerts()'s existing free OI-concentration proxy.

routes.py can't be imported directly in this test environment (its import chain pulls in
common.config/db, none of which conftest.py stubs for real) — matching test_max_pain.py's/
test_options_chain.py's established source-text-extraction technique for functions in this
exact file. Since this function's real work is delegated entirely to unusual_whales.py (already
independently, behaviorally tested in test_unusual_whales.py), these are source-text regression
checks on the WIRING itself: the availability gate is checked before any fetch, `source`/
`available` are set honestly (never fabricated), and the endpoint is registered as a real,
literal path (not shadowed by a catch-all).
"""
import pathlib

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _extract_gamma_exposure_source() -> str:
    start = _ROUTES_SOURCE.index('@router.get("/{symbol}/gamma-exposure")')
    end = _ROUTES_SOURCE.index('\n# ── Per-symbol Relative Strength', start)
    return _ROUTES_SOURCE[start:end]


_FUNC_SOURCE = _extract_gamma_exposure_source()


def test_route_is_registered_as_a_real_literal_path():
    assert '@router.get("/{symbol}/gamma-exposure")' in _ROUTES_SOURCE


def test_no_catch_all_get_symbol_route_exists_in_this_file_to_shadow_it():
    """The BUG233-ROUTERORDER class this repo has hit before: a bare GET /{symbol} catch-all
    registered earlier in the same router would silently swallow a later literal-path route.
    This router has none — confirmed directly rather than assumed."""
    assert '@router.get("/{symbol}")' not in _ROUTES_SOURCE


def test_checks_availability_before_attempting_any_fetch():
    """is_available() must be the first real check — a disabled/unconfigured feature should
    never even call get_gex_levels(), matching every other opt-in-flag-gated feature's own
    "check both key and flag before doing any real work" convention in this codebase."""
    avail_idx = _FUNC_SOURCE.index("_uw.is_available()")
    fetch_idx = _FUNC_SOURCE.index("_uw.get_gex_levels(")
    assert avail_idx < fetch_idx


def test_disabled_case_reports_source_none_not_a_fabricated_value():
    assert '"source": "none", "reason": "unusual_whales_disabled"' in _FUNC_SOURCE


def test_no_data_case_also_reports_source_none():
    assert '"source": "none", "reason": "no_data"' in _FUNC_SOURCE


def test_real_data_case_reports_source_unusual_whales():
    """The one place a caller can actually tell 'this is real GEX' from 'the free proxy' —
    must be present and must be the string 'unusual_whales', not a generic truthy flag."""
    assert '"source": "unusual_whales"' in _FUNC_SOURCE


def test_never_returns_available_true_with_source_none():
    """A structural sanity check on the 3 return dicts in this function: 'available: True'
    must only ever pair with 'source: unusual_whales', never with 'source: none' — that
    combination would be a lie (claiming real data is available while also saying there's no
    real source for it)."""
    import re
    # Every return statement's dict literal in this function, as its own chunk.
    returns = re.findall(r"return \{[^}]*\}", _FUNC_SOURCE, re.DOTALL)
    assert len(returns) == 3
    for r in returns:
        if '"available": True' in r:
            assert '"source": "unusual_whales"' in r
        if '"source": "none"' in r:
            assert '"available": True' not in r


def test_all_gex_fields_come_from_the_real_levels_object_not_hardcoded():
    """Every numeric field in the success path must be read off the real GexLevels object
    returned by get_gex_levels(), never a literal placeholder value."""
    for field in ("call_wall", "put_wall", "gamma_flip", "gamma_magnet", "as_of_date"):
        assert f"levels.{field}" in _FUNC_SOURCE
