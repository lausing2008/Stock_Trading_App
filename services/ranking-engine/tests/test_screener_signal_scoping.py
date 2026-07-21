"""Regression test for RK-D1-SCREENER-FULL-SCAN.

screen()'s sig_subq/sig_rows previously scanned the ENTIRE Signal table (filtered only by
horizon == "SWING", with no stock_id restriction at all) to build sig_map — even though the
main query (`rows`, already filtered by market/sector/score/etc.) only ever looks up a bounded
subset of stock_ids from that map. Fixed by scoping the signal subquery/query to
`Signal.stock_id.in_(_screen_stock_ids)`, where `_screen_stock_ids` are the stock ids already
present in `rows`.

routes.py can be imported directly in this test environment (db/sqlalchemy are stubbed as
MagicMock(), which never raises on attribute access) — but screen() itself has a large,
multi-branch body with heavy DB/session dependencies that make a full functional exercise
disproportionate to this fix's actual scope (a single added filter + an empty-list guard).
Matching the proportionate-testing precedent already established in this test suite
(test_rank_symbol_market_scoping.py), this is a source-text regression check on the specific
fix, not a full functional test of screen() end-to-end.
"""
import pathlib

_ROUTES_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
)
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _screen_body() -> str:
    start = _ROUTES_SOURCE.index("def screen(")
    end = _ROUTES_SOURCE.index("\ndef ", start + 1)
    return _ROUTES_SOURCE[start:end]


def test_screen_stock_ids_derived_from_the_already_filtered_rows():
    """_screen_stock_ids must be built from `rows` (the already market/sector/score-filtered
    result), not from a fresh, unfiltered query."""
    body = _screen_body()
    assert "_screen_stock_ids = [stock.id for stock, _ranking in rows]" in body
    rows_execute_idx = body.index("rows = session.execute(stmt).all()")
    screen_ids_idx = body.index("_screen_stock_ids = [stock.id for stock, _ranking in rows]")
    assert rows_execute_idx < screen_ids_idx, "_screen_stock_ids must be built AFTER rows is fetched"


def test_signal_subquery_is_scoped_to_the_screened_stock_ids():
    """The exact fix: both the subquery and the outer signal query must filter on
    Signal.stock_id.in_(_screen_stock_ids), not scan the whole Signal table."""
    body = _screen_body()
    assert body.count("Signal.stock_id.in_(_screen_stock_ids)") == 2, (
        "expected the stock_id.in_() filter on both the subquery and the outer signal query"
    )


def test_signal_queries_only_run_when_there_are_screened_stock_ids():
    """An empty `rows` result (no stocks matched the filters) must skip the signal queries
    entirely rather than running Signal.stock_id.in_([]) (a query IN sqlalchemy will still
    execute, but there's no reason to hit the DB at all when we already know the result is
    empty)."""
    body = _screen_body()
    if_guard_idx = body.index("if _screen_stock_ids:")
    else_idx = body.index("else:", if_guard_idx)
    sig_rows_empty_idx = body.index("sig_rows = []", else_idx)
    assert if_guard_idx < else_idx < sig_rows_empty_idx


def test_horizon_swing_filter_still_applied_alongside_the_new_stock_id_scoping():
    """The pre-existing horizon == "SWING" pin (so multiple horizons written in the same
    second don't produce arbitrary signal values) must not have been dropped while adding the
    new stock_id filter."""
    body = _screen_body()
    assert body.count('Signal.horizon == "SWING"') == 2
