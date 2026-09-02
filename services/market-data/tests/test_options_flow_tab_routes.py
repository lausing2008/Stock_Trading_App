"""Tests for T324-OPTIONSFLOW-TAB's 5 new routes: /options-screener, /option-trades,
/market-tide, /options-flow-alerts-recent, /dark-pool-alerts-recent.

Same source-text-extraction technique as test_gamma_exposure_route.py/test_dark_pool_route.py
(routes.py can't be imported directly in this test environment) — regression checks on the
WIRING itself: the availability gate is checked before any fetch for the 3 live-fetch routes,
`available` is set honestly, the routes are registered as real literal paths, and none are
shadowed by the file's own /{symbol} catch-all (confirmed registered later in the file).
"""
import pathlib

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _extract(start_marker: str, end_marker: str) -> str:
    start = _ROUTES_SOURCE.index(start_marker)
    end = _ROUTES_SOURCE.index(end_marker, start)
    return _ROUTES_SOURCE[start:end]


_SCREENER_SOURCE = _extract('@router.get("/options-screener")', '@router.get("/option-trades")')
_TRADES_SOURCE = _extract('@router.get("/option-trades")', '@router.get("/market-tide")')
_TIDE_SOURCE = _extract('@router.get("/market-tide")', '@router.get("/options-flow-alerts-recent")')
_FLOW_RECENT_SOURCE = _extract('@router.get("/options-flow-alerts-recent")', '@router.get("/dark-pool-alerts-recent")')
_DARKPOOL_RECENT_SOURCE = _extract('@router.get("/dark-pool-alerts-recent")', '\n# ── T322-OPTIONS-GAMEPLAN')


def test_all_5_routes_registered_as_real_literal_paths():
    for path in (
        '@router.get("/options-screener")', '@router.get("/option-trades")',
        '@router.get("/market-tide")', '@router.get("/options-flow-alerts-recent")',
        '@router.get("/dark-pool-alerts-recent")',
    ):
        assert path in _ROUTES_SOURCE


def test_catch_all_symbol_route_is_registered_strictly_after_all_5_new_routes():
    """BUG233-ROUTERORDER: a /{symbol} catch-all registered BEFORE these literal paths would
    silently shadow every one of them. This file genuinely has one (StockOut response_model) —
    confirm it comes later in the file, not earlier."""
    catch_all_idx = _ROUTES_SOURCE.index('@router.get("/{symbol}", response_model=StockOut)')
    for path in (
        '@router.get("/options-screener")', '@router.get("/option-trades")',
        '@router.get("/market-tide")', '@router.get("/options-flow-alerts-recent")',
        '@router.get("/dark-pool-alerts-recent")',
    ):
        assert _ROUTES_SOURCE.index(path) < catch_all_idx


# ── /options-screener ─────────────────────────────────────────────────────────────────

def test_screener_checks_availability_before_fetching():
    avail_idx = _SCREENER_SOURCE.index("_uw.is_available()")
    fetch_idx = _SCREENER_SOURCE.index("_uw.get_options_screener(")
    assert avail_idx < fetch_idx


def test_screener_disabled_case_reports_available_false():
    assert '"available": False, "reason": "unusual_whales_disabled"' in _SCREENER_SOURCE


def test_screener_disabled_case_still_returns_a_rows_key():
    assert '"rows": []' in _SCREENER_SOURCE


# ── /option-trades ─────────────────────────────────────────────────────────────────────

def test_trades_checks_availability_before_fetching():
    avail_idx = _TRADES_SOURCE.index("_uw.is_available()")
    fetch_idx = _TRADES_SOURCE.index("_uw.get_option_trades(")
    assert avail_idx < fetch_idx


def test_trades_disabled_case_reports_available_false():
    assert '"available": False, "reason": "unusual_whales_disabled"' in _TRADES_SOURCE


def test_trades_passes_max_dte_and_is_multi_leg_through():
    """The whole point of this route: 0DTE/multi-leg views must actually reach the client call."""
    assert "max_dte=max_dte" in _TRADES_SOURCE
    assert "is_multi_leg=is_multi_leg" in _TRADES_SOURCE


# ── /market-tide ──────────────────────────────────────────────────────────────────────

def test_tide_checks_availability_before_fetching():
    avail_idx = _TIDE_SOURCE.index("_uw.is_available()")
    fetch_idx = _TIDE_SOURCE.index("_uw.get_market_tide(")
    assert avail_idx < fetch_idx


def test_tide_disabled_case_reports_available_false():
    assert '"available": False, "reason": "unusual_whales_disabled"' in _TIDE_SOURCE


# ── /options-flow-alerts-recent (cached, DB-backed) ───────────────────────────────────

def test_flow_alerts_recent_queries_options_flow_alert_outcome_not_a_live_uw_call():
    """This route must read from the existing DB table, never call unusual_whales.py directly
    — the whole design point of this endpoint (bounded, zero extra API cost)."""
    assert "OptionsFlowAlertOutcome" in _FLOW_RECENT_SOURCE
    assert "_uw." not in _FLOW_RECENT_SOURCE
    assert "unusual_whales" not in _FLOW_RECENT_SOURCE


def test_flow_alerts_recent_discloses_its_bounded_scope():
    """The scope limitation (PriceAlert-subscribed + top-K symbols, not free-text search) must
    be an honest, visible field in the response, not silently hidden."""
    assert '"scope"' in _FLOW_RECENT_SOURCE
    assert "price_alert_subscribed_and_top_k_symbols" in _FLOW_RECENT_SOURCE


def test_flow_alerts_recent_orders_by_most_recent_first():
    assert "fired_date.desc()" in _FLOW_RECENT_SOURCE


# ── /dark-pool-alerts-recent (cached, DB-backed) ──────────────────────────────────────

def test_dark_pool_recent_queries_dark_pool_alert_outcome_not_a_live_uw_call():
    assert "DarkPoolAlertOutcome" in _DARKPOOL_RECENT_SOURCE
    assert "_uw." not in _DARKPOOL_RECENT_SOURCE
    assert "unusual_whales" not in _DARKPOOL_RECENT_SOURCE


def test_dark_pool_recent_discloses_its_bounded_scope():
    assert '"scope"' in _DARKPOOL_RECENT_SOURCE
    assert "price_alert_subscribed_and_top_k_symbols" in _DARKPOOL_RECENT_SOURCE


def test_dark_pool_recent_orders_by_most_recent_first():
    assert "fired_date.desc()" in _DARKPOOL_RECENT_SOURCE
