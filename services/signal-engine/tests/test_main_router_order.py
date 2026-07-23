"""Regression test for a real router-ordering bug found live in production 2026-07-22.

T233-ARCH-INSERVICE-SPLITS split signal-engine's single routes.py (35 routes, one APIRouter)
into 3 files: routes.py (9 hot-path routes, including the catch-all GET /{symbol}),
calibration.py (13 literal-path routes), outcomes.py (13 literal-path routes). main.py mounts
all 3 via create_app(routers=[...]) — FastAPI/Starlette matches routes in registration order,
so if the router containing GET /{symbol} is registered before the routers containing literal
paths like /confidence-calibration or /tune_status, the catch-all silently shadows them.

This is exactly what happened on first deploy: GET /signals/confidence-calibration resolved to
routes.py's signal_for("confidence-calibration") instead of calibration.py's dedicated route,
crashing with a 404 from market-data (fetching prices for the "symbol" confidence-calibration).

main.py can't be imported directly in this test environment (conftest.py stubs the `common`
package — including common.service — wholesale), so this is a source-text regression check,
matching this repo's established pattern for files with this exact import constraint.
"""
import pathlib

_MAIN_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "main.py"
_MAIN_SOURCE = _MAIN_PATH.read_text()

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _routers_list_order():
    """Extracts the literal order of the routers=[...] argument to create_app()."""
    start = _MAIN_SOURCE.index("routers=[")
    end = _MAIN_SOURCE.index("]", start)
    inner = _MAIN_SOURCE[start + len("routers=[") : end]
    return [name.strip() for name in inner.split(",") if name.strip()]


def test_routes_py_still_contains_the_catch_all_symbol_route():
    """Sanity check that the hazard this test guards against still exists in routes.py —
    if /{symbol} is ever removed from routes.py, this test's premise no longer applies."""
    assert '@router.get("/{symbol}")' in _ROUTES_SOURCE


def test_router_containing_catch_all_is_registered_last():
    """The router bound to the name `router` (routes.py's, containing GET /{symbol}) must be
    the LAST element in create_app's routers=[...] list — every literal-path router
    (calibration_router, outcomes_router) must register before it, so their literal paths are
    matched before the catch-all ever gets a chance to shadow them."""
    order = _routers_list_order()
    assert order[-1] == "router", (
        f"expected `router` (routes.py, contains catch-all /{{symbol}}) to be last in "
        f"routers=[...], got order: {order}"
    )
    assert "calibration_router" in order[:-1]
    assert "outcomes_router" in order[:-1]


def test_calibration_and_outcomes_routers_are_imported():
    """Confirms main.py still imports both non-catch-all routers under the exact names this
    test's ordering check depends on."""
    assert "from .api.calibration import router as calibration_router" in _MAIN_SOURCE
    assert "from .api.outcomes import router as outcomes_router" in _MAIN_SOURCE
