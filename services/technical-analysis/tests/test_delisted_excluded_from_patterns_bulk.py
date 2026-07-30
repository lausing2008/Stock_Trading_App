"""Regression test for BUG-DELISTED-GENERATION-BLIND (sibling instance, technical-analysis).

Stock.delisted (aud14-survivorship) never flips Stock.active — a confirmed-delisted stock
stays "active" forever, so GET /ta/patterns/bulk kept recomputing chart patterns for it every
6h cache cycle (real per-symbol Price fetch + pattern detection work), wasted on a stock that
can never be traded again. This is a sibling instance of the exact bug class already fixed
across signal-engine/ranking-engine/market-data (see BUG-DELISTED-GENERATION-BLIND in
.claude/CLAUDE.md) — found by re-grepping for `Stock.active` across every OTHER service, since
that fix was scoped only to those 3.

routes.py imports the Docker-only `db` package (not installed in this local test environment)
and can't be imported directly — this is a source-text regression check, matching the
established pattern used elsewhere in this codebase for the identical constraint.
"""
import pathlib

_routes_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_source = _routes_path.read_text()


def _function_body(def_line: str) -> str:
    start = _source.index(def_line)
    end = _source.index("\n\n\n", start)
    return _source[start:end]


def test_patterns_bulk_excludes_delisted_stocks():
    body = _function_body("def get_patterns_bulk(")
    assert "Stock.delisted.is_(False)" in body


def test_delisted_filter_is_combined_with_active_not_a_replacement_for_it():
    """The fix must ADD a delisted exclusion alongside the existing active filter, not
    accidentally replace it — an inactive (but not confirmed-delisted) stock must still be
    excluded."""
    body = _function_body("def get_patterns_bulk(")
    assert "Stock.active == True" in body
    assert "Stock.delisted.is_(False)" in body


def test_delisted_filter_is_on_the_same_stmt_line_as_active():
    """Guards against the filter being added somewhere else in the function body (e.g. a
    dead/unused variable) rather than actually being part of the real stock-selection stmt."""
    body = _function_body("def get_patterns_bulk(")
    stmt_line_start = body.index("stmt = select(Stock).where(")
    stmt_line_end = body.index("\n", stmt_line_start)
    stmt_line = body[stmt_line_start:stmt_line_end]
    assert "Stock.delisted.is_(False)" in stmt_line
