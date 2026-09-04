"""Tests for AUD-TRANSCRIPT's GET /{symbol}/earnings-transcript — real earnings-call
transcript statements via Unusual Whales, the primary cross-service consumer being
event-intelligence's generate_earnings_impact().

routes.py can't be imported directly in this test environment (conftest.py stubs sqlalchemy/db
wholesale, and this file imports fastapi/yfinance/common.config at module level) — covered via
source-text regression checks, matching test_gamma_exposure_route.py's established pattern for
this exact class of function.
"""
import pathlib

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _extract_source() -> str:
    start = _ROUTES_SOURCE.index('@router.get("/{symbol}/earnings-transcript")')
    end = _ROUTES_SOURCE.index('@router.get("/{symbol}/dark-pool-prints")', start)
    return _ROUTES_SOURCE[start:end]


_FUNC_SOURCE = _extract_source()


def test_route_is_registered_as_a_real_literal_path():
    assert '@router.get("/{symbol}/earnings-transcript")' in _ROUTES_SOURCE


def test_checks_availability_before_deriving_the_quarter_or_fetching():
    avail_idx = _FUNC_SOURCE.index("_uw.is_available()")
    quarter_idx = _FUNC_SOURCE.index("_uw.earnings_quarter_from_report_date(")
    fetch_idx = _FUNC_SOURCE.index("_uw.get_earnings_transcript(")
    assert avail_idx < quarter_idx < fetch_idx


def test_derives_the_quarter_from_report_date_not_fiscal_fields():
    """AUD264 already established fiscal_year/fiscal_quarter as best-effort-only — this route
    must derive the quarter string from the real report_date param instead (never read a
    fiscal_year/fiscal_quarter attribute off any object in the actual code, docstring mentions
    of those field names aside)."""
    assert "_uw.earnings_quarter_from_report_date(report_date)" in _FUNC_SOURCE
    assert ".fiscal_year" not in _FUNC_SOURCE
    assert ".fiscal_quarter" not in _FUNC_SOURCE


def test_invalid_report_date_degrades_to_available_false_not_a_500():
    assert '"reason": "invalid_report_date"' in _FUNC_SOURCE
    idx = _FUNC_SOURCE.index("except Exception:")
    segment = _FUNC_SOURCE[idx:idx + 150]
    assert '"available": False' in segment


def test_disabled_case_reports_available_false_with_a_real_reason():
    assert '"reason": "unusual_whales_disabled"' in _FUNC_SOURCE


def test_no_data_case_also_reports_available_false():
    """Deliberately the SAME reason/shape whether the failure is 'no transcript published yet'
    or 'account not on UW's required Advanced+ tier' — this route cannot and must not pretend
    to distinguish those two real failure modes from each other."""
    assert '"reason": "no_data"' in _FUNC_SOURCE


def test_never_returns_available_true_with_an_empty_statements_list():
    """A real transcript with zero statements would be indistinguishable from 'no data' — the
    no_data branch must trigger whenever get_earnings_transcript() returns an empty list,
    never let an empty list through as available: True."""
    assert "if not statements:" in _FUNC_SOURCE


def test_real_data_case_surfaces_all_four_statement_fields():
    idx = _FUNC_SOURCE.index('"statements": [')
    # the second occurrence is the real success-path list comprehension (the first two
    # occurrences are the empty-list literals in the disabled/no_data/invalid branches)
    occurrences = [i for i in range(len(_FUNC_SOURCE)) if _FUNC_SOURCE.startswith('"statements": [', i)]
    success_idx = occurrences[-1]
    segment = _FUNC_SOURCE[success_idx:success_idx + 200]
    for field in ("s.speaker", "s.title", "s.content", "s.sentiment"):
        assert field in segment


def test_route_path_is_a_literal_segment_not_shadowed_by_the_symbol_path_param():
    """AUD-ROUTERORDER class regression guard, matching this repo's own established convention
    for every new route added under /stocks/{symbol}/..."""
    assert '@router.get("/{symbol}/earnings-transcript")' in _ROUTES_SOURCE
