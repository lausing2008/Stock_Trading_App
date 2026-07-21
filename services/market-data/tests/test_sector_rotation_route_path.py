"""Regression test for AUD-REDISAUDIT Phase 3's cross-service-wiring finding: T220-G's sector
K-Score rotation endpoint was registered as @router.get("/stocks/sector-rotation") on a router
ALREADY mounted with prefix="/stocks" (see the router = APIRouter(prefix="/stocks", ...) line
near the top of routes.py) — every sibling route in this file (e.g. /sector_rotation,
/fear_greed, /regime) correctly omits the /stocks prefix in its own decorator, since the router
mount already supplies it. This one route repeated it, resolving to the real, live-confirmed
path GET /stocks/stocks/sector-rotation instead of the intended GET /stocks/sector-rotation.

signal-engine's own caller (services/signal-engine/src/api/routes.py, T220-G's sector_momentum
enrichment) requests the single-prefixed {market_data_url}/stocks/sector-rotation — the
INTENDED path, not the actually-registered double-prefixed one — so every call 404'd, silently
swallowed by `if _rot_r.status_code == 200:` (never raises). Confirmed live against production
Postgres: 0 of the last 4,176 signals had reasons->>'sector_momentum' populated — this feature
has been silently non-functional since T220-G shipped (2026-06-xx per the tracker), caught only
by this cross-service-wiring audit pass, not by any prior functional test.

routes.py can't be imported directly in this test environment (it does `from common.config
import get_settings` at module level, and `common` isn't installed locally — matching the same
constraint documented for every other market-data route file), so this is a source-text
regression check rather than a live FastAPI TestClient route-resolution test.
"""
import pathlib

_routes_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_routes_source = _routes_path.read_text()


def test_router_is_mounted_with_stocks_prefix():
    """Confirms the assumption this whole test file depends on: the router is mounted with
    prefix="/stocks", so every route decorator in this file must NOT repeat it."""
    assert 'router = APIRouter(prefix="/stocks"' in _routes_source


def test_sector_rotation_kscore_route_does_not_double_prefix():
    """The T220-G sector K-Score rotation endpoint (get_sector_rotation) must be registered
    as /sector-rotation, not /stocks/sector-rotation — the router mount already supplies the
    /stocks prefix, matching every sibling route's own convention in this file."""
    assert '@router.get("/sector-rotation")' in _routes_source
    assert '@router.get("/stocks/sector-rotation")' not in _routes_source


def test_sector_rotation_kscore_and_etf_routes_remain_distinct():
    """Two different features share a similar name: sector_rotation() (RES-4, ETF-based,
    registered at /sector_rotation — underscore) and get_sector_rotation() (T220-G, K-Score
    momentum, registered at /sector-rotation — hyphen). Confirms the fix didn't accidentally
    collapse these into the same path."""
    assert '@router.get("/sector_rotation")' in _routes_source  # RES-4, ETF-based
    assert '@router.get("/sector-rotation")' in _routes_source  # T220-G, K-Score-based
