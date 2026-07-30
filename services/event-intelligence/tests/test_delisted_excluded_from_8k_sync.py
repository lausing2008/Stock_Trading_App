"""Regression test for BUG-DELISTED-GENERATION-BLIND (sibling instance, event-intelligence).

Stock.delisted (aud14-survivorship) never flips Stock.active — a confirmed-delisted stock
stays "active" forever, so POST /events/sync/8k kept polling SEC EDGAR daily for a company
that can no longer file anything. Sibling instance of the exact bug class already fixed
across signal-engine/ranking-engine/market-data/technical-analysis (see
BUG-DELISTED-GENERATION-BLIND in .claude/CLAUDE.md) — found by re-grepping for `Stock.active`
across every OTHER service, since that fix was scoped only to those services.

routes.py imports Docker-only dependencies (db, sqlalchemy) that conftest.py stubs wholesale
as MagicMock — a real behavioral test of the query isn't possible in this environment, so this
is a source-text regression check, matching the established pattern used elsewhere in this
codebase for the identical constraint.
"""
import pathlib

_routes_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_source = _routes_path.read_text()


def _function_body(def_line: str) -> str:
    start = _source.index(def_line)
    end = _source.index("\n\n\n", start)
    return _source[start:end]


def test_sync_8k_excludes_delisted_stocks():
    body = _function_body("async def sync_8k(")
    assert "Stock.delisted.is_(False)" in body


def test_delisted_filter_is_combined_with_active_and_market_not_a_replacement():
    """The fix must ADD a delisted exclusion alongside the existing active + US-market
    filters, not accidentally replace either — an inactive (but not confirmed-delisted) stock,
    and any HK stock, must both still be excluded."""
    body = _function_body("async def sync_8k(")
    assert "Stock.active.is_(True)" in body
    assert "Stock.delisted.is_(False)" in body
    assert 'Stock.market == "US"' in body


def test_delisted_filter_is_on_the_same_query_as_the_other_filters():
    """Guards against the filter being added somewhere else in the function body rather than
    actually being part of the real symbol-selection query."""
    body = _function_body("async def sync_8k(")
    query_start = body.index("select(Stock.symbol).where(")
    query_end = body.index(")", body.index("Stock.market", query_start)) + 1
    query_block = body[query_start:query_end]
    assert "Stock.delisted.is_(False)" in query_block
