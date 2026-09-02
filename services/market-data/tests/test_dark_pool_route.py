"""Tests for T323-DARKPOOL's GET /{symbol}/dark-pool-prints — real off-exchange block trades via
Unusual Whales, genuinely new capability (no free-proxy fallback, unlike gamma-exposure).

Same source-text-extraction technique as test_gamma_exposure_route.py (routes.py can't be
imported directly in this test environment) — these are regression checks on the WIRING itself:
the availability gate is checked before any fetch, `source`/`available` are set honestly, and
the endpoint is registered as a real, literal path.
"""
import pathlib

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _extract_dark_pool_route_source() -> str:
    start = _ROUTES_SOURCE.index('@router.get("/{symbol}/dark-pool-prints")')
    end = _ROUTES_SOURCE.index('\n# ── T322-OPTIONS-GAMEPLAN', start)
    return _ROUTES_SOURCE[start:end]


_FUNC_SOURCE = _extract_dark_pool_route_source()


def test_route_is_registered_as_a_real_literal_path():
    assert '@router.get("/{symbol}/dark-pool-prints")' in _ROUTES_SOURCE


def test_no_catch_all_get_symbol_route_exists_in_this_file_to_shadow_it():
    """The BUG233-ROUTERORDER class this repo has hit before: a bare GET /{symbol} catch-all
    registered earlier in the same router would silently swallow a later literal-path route."""
    assert '@router.get("/{symbol}")' not in _ROUTES_SOURCE


def test_checks_availability_before_attempting_any_fetch():
    avail_idx = _FUNC_SOURCE.index("_uw.is_available()")
    fetch_idx = _FUNC_SOURCE.index("_uw.get_dark_pool_prints(")
    assert avail_idx < fetch_idx


def test_disabled_case_reports_source_none_not_a_fabricated_value():
    assert '"source": "none", "reason": "unusual_whales_disabled"' in _FUNC_SOURCE


def test_no_data_case_also_reports_source_none():
    assert '"source": "none", "reason": "no_data"' in _FUNC_SOURCE


def test_real_data_case_reports_source_unusual_whales():
    assert '"source": "unusual_whales"' in _FUNC_SOURCE


def test_never_returns_available_true_with_source_none():
    import re
    returns = re.findall(r"return \{[^}]*\}", _FUNC_SOURCE, re.DOTALL)
    assert len(returns) == 3
    for r in returns:
        if '"available": True' in r:
            assert '"source": "unusual_whales"' in r
        if '"source": "none"' in r:
            assert '"available": True' not in r


def test_disabled_and_no_data_cases_both_return_an_empty_prints_list_not_omit_the_key():
    """A caller (MarketPressurePanel) always destructures `.prints` — omitting the key entirely
    on the failure paths would be a real frontend crash risk, not just an inconsistency."""
    import re
    returns = re.findall(r"return \{[^}]*\}", _FUNC_SOURCE, re.DOTALL)
    for r in returns:
        if '"available": False' in r:
            assert '"prints": []' in r


def test_all_fields_come_from_the_real_row_object_not_hardcoded():
    for field in ("price", "size", "premium", "venue", "executed_at"):
        assert f"r.{field}" in _FUNC_SOURCE
