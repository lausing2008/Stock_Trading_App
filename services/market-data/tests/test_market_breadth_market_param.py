"""Tests for T255-REPORTS-TAB — GET /stocks/market_breadth gained a `market` query param
(US|HK) instead of being hardcoded to Stock.market == Market.US, and the Redis cache key was
correspondingly namespaced per market so a US and an HK reading never overwrite each other.

routes.py can't be imported directly in this test environment (its import chain needs
common.config/db, neither for-real-installed here) — tested via source-text extraction,
matching this repo's established technique for this class of file.
"""
import pathlib

_routes_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_routes_source = _routes_path.read_text()


def _function_body():
    start = _routes_source.index("def market_breadth(")
    end = _routes_source.index('@router.get("/data_freshness")', start)
    return _routes_source[start:end]


_body = _function_body()


def test_market_breadth_accepts_a_market_query_param():
    """The route signature must accept a market param, not be hardcoded with no way for a
    caller to request HK breadth at all."""
    start = _routes_source.index('def market_breadth(')
    sig_end = _routes_source.index(")", start)
    sig = _routes_source[start:sig_end]
    assert "market:" in sig
    assert '"US"' in sig


def test_market_breadth_filters_stock_by_the_requested_market_not_hardcoded_us():
    """The DB query must filter on the requested market, not a hardcoded Market.US literal —
    confirms an HK request actually reads HK stocks, not silently reusing the US filter."""
    assert "Stock.market == _Market(market.upper())" in _body
    assert "Stock.market == _Market.US" not in _body


def test_redis_cache_key_is_namespaced_per_market():
    """The cache key must include the market so a US reading and an HK reading don't
    overwrite each other in Redis — a real bug if the key stayed global while the query
    became market-aware."""
    assert '_market_key = f"{_MARKET_BREADTH_KEY}:{market.upper()}"' in _body
    # Both the read (cache hit) and the write (cache-set) must use the namespaced key, not
    # the old bare _MARKET_BREADTH_KEY constant directly.
    assert "_get_redis().get(_market_key)" in _body
    assert "_get_redis().setex(_market_key," in _body
    assert "_get_redis().get(_MARKET_BREADTH_KEY)" not in _body
    assert "_get_redis().setex(_MARKET_BREADTH_KEY," not in _body


def test_hk_connect_flow_leaderboard_route_registered_before_symbol_catchall():
    """The new leaderboard route must be registered as a literal path
    (/hk-connect-flow/leaderboard/top), not something that could collide with the existing
    per-symbol /hk-connect-flow/{symbol} route — Starlette matches literal segments before
    param segments within the same router only if declared correctly; using a distinct
    literal sub-path (not a bare /{symbol} under the same prefix) sidesteps the ambiguity
    entirely, matching the BUG233-ROUTERORDER lesson documented in CLAUDE.md."""
    assert '@router.get("/hk-connect-flow/leaderboard/top")' in _routes_source
    assert "def hk_connect_flow_leaderboard(" in _routes_source


def test_leaderboard_route_delegates_to_get_flow_leaderboard():
    start = _routes_source.index("def hk_connect_flow_leaderboard(")
    end = _routes_source.index("@router.get", start + 1)
    body = _routes_source[start:end]
    assert "from ..services.hk_connect import get_flow_leaderboard" in body
    assert "return get_flow_leaderboard(session, days=days, limit=limit)" in body
