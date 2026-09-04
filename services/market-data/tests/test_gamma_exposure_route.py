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
    # AUD-SIGCORROBORATE: end marker moved again — get_short_interest_uw() now sits between
    # this function and get_earnings_transcript_route(), and it has no route of its own tested
    # in this file, so it must not leak into THIS function's extracted source.
    end = _ROUTES_SOURCE.index('@router.get("/{symbol}/short-interest-uw")', start)
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


# ── AUD-MAXPAIN: max_pain / oi_per_strike wiring ─────────────────────────────────────────────

def test_max_pain_and_oi_per_strike_are_fetched_via_the_real_unusual_whales_functions():
    assert "_uw.get_max_pain(sym)" in _FUNC_SOURCE
    assert "_uw.get_oi_per_strike(sym)" in _FUNC_SOURCE


def test_max_pain_and_oi_per_strike_fetched_only_after_the_gex_availability_gate():
    """Must not attempt these fetches when Unusual Whales is disabled/unconfigured — same
    availability-gate discipline as get_gex_levels() itself."""
    avail_idx = _FUNC_SOURCE.index("_uw.is_available()")
    max_pain_idx = _FUNC_SOURCE.index("_uw.get_max_pain(")
    oi_idx = _FUNC_SOURCE.index("_uw.get_oi_per_strike(")
    assert avail_idx < max_pain_idx
    assert avail_idx < oi_idx


def test_response_includes_max_pain_and_oi_per_strike_lists():
    assert '"max_pain": [' in _FUNC_SOURCE
    assert '"oi_per_strike": [' in _FUNC_SOURCE


def test_max_pain_rows_filter_out_null_max_pain_values():
    """A row with max_pain=None (a real, possible UW response for some expiries) must be
    dropped, not passed through as a fabricated-looking null entry in the array."""
    idx = _FUNC_SOURCE.index('"max_pain": [')
    segment = _FUNC_SOURCE[idx:idx + 200]
    assert "if r.max_pain is not None" in segment


def test_oi_per_strike_rows_filter_out_null_strike_values():
    idx = _FUNC_SOURCE.index('"oi_per_strike": [')
    segment = _FUNC_SOURCE[idx:idx + 250]
    assert "if r.strike is not None" in segment


# ── AUD-NOPE: nope wiring ────────────────────────────────────────────────────────────────────

def test_nope_is_fetched_via_the_real_unusual_whales_function():
    assert "_uw.get_nope(sym)" in _FUNC_SOURCE


def test_nope_fetched_only_after_the_gex_availability_gate():
    avail_idx = _FUNC_SOURCE.index("_uw.is_available()")
    nope_idx = _FUNC_SOURCE.index("_uw.get_nope(")
    assert avail_idx < nope_idx


def test_response_includes_a_nope_field():
    assert '"nope": (' in _FUNC_SOURCE


def test_nope_field_is_null_when_the_reading_itself_has_no_nope_value():
    """A None NopeReading OR a real reading whose own `nope` field is null must both degrade
    to a null nope in the response, never a fabricated/placeholder object."""
    idx = _FUNC_SOURCE.index('"nope": (')
    segment = _FUNC_SOURCE[idx:idx + 400]
    assert "nope is not None and nope.nope is not None" in segment


def test_nope_object_surfaces_both_variants_and_the_raw_deltas():
    """UW publishes both nope and nope_fill (volume-weighted vs. fill-weighted delta) — neither
    is documented as strictly superior, so both must be surfaced, plus the raw call/put delta
    and volume figures the metric itself is built from."""
    idx = _FUNC_SOURCE.index('"nope": (')
    segment = _FUNC_SOURCE[idx:idx + 400]
    for field in ("nope.nope", "nope.nope_fill", "nope.call_delta", "nope.put_delta", "nope.call_vol", "nope.put_vol", "nope.stock_vol", "nope.timestamp"):
        assert field in segment
