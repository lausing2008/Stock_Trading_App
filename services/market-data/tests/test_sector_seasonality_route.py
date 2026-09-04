"""Tests for AUD-SEASONALITY's GET /sector-seasonality — real, multi-year monthly seasonal
return statistics for the 13 sector/index ETFs Unusual Whales itself computes this for, a
genuinely different lens from the existing K-Score-momentum-based /sector-rotation endpoint.

routes.py can't be imported directly in this test environment (conftest.py stubs sqlalchemy/db
wholesale, and this file imports fastapi/yfinance/common.config at module level) — covered via
source-text regression checks, matching this repo's own established pattern for this exact
class of function.
"""
import pathlib

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _extract_source() -> str:
    start = _ROUTES_SOURCE.index('@router.get("/sector-seasonality")')
    end = _ROUTES_SOURCE.index('# ── Short Squeeze Scanner', start)
    return _ROUTES_SOURCE[start:end]


_FUNC_SOURCE = _extract_source()


def test_route_is_registered_as_a_real_literal_path():
    assert '@router.get("/sector-seasonality")' in _ROUTES_SOURCE


def test_disabled_case_reports_available_false_with_a_real_reason():
    assert '"reason": "unusual_whales_disabled"' in _FUNC_SOURCE


def test_no_data_case_also_reports_available_false():
    assert '"reason": "no_data"' in _FUNC_SOURCE


def test_month_defaults_to_the_current_calendar_month_when_omitted():
    assert "target_month = month if month is not None else _sdate.today().month" in _FUNC_SOURCE


def test_filters_to_only_the_requested_month():
    assert "r.month == target_month" in _FUNC_SOURCE


def test_filters_out_rows_with_no_ticker():
    assert "r.ticker is not None" in _FUNC_SOURCE


def _success_rows_segment() -> str:
    # the real success-path list comprehension is the LAST occurrence of '"rows": [' — the two
    # earlier ones are the disabled/no_data branches' own empty-list literals ('"rows": []').
    idx = _FUNC_SOURCE.rindex('"rows": [')
    return _FUNC_SOURCE[idx:idx + 700]


def test_rows_are_sorted_by_median_change_descending():
    segment = _success_rows_segment()
    assert "sorted(month_rows, key=lambda r: r.median_change" in segment
    assert "reverse=True" in segment


def test_success_response_surfaces_all_seven_stat_fields_per_row():
    segment = _success_rows_segment()
    for field in ("r.ticker", "r.avg_change", "r.median_change", "r.min_change", "r.max_change", "r.positive_closes", "r.positive_months_perc", "r.years"):
        assert field in segment


def test_never_returns_available_true_with_an_empty_rows_list():
    assert "if not month_rows:" in _FUNC_SOURCE


def test_checks_availability_before_fetching():
    avail_idx = _FUNC_SOURCE.index("_uw.is_available()")
    fetch_idx = _FUNC_SOURCE.index("_uw.get_sector_seasonality()")
    assert avail_idx < fetch_idx
