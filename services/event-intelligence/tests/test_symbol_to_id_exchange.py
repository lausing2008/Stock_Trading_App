"""Regression test for T247-EVENTINTELLIGENCE-SYMBOLEXCHANGE.

routes.py's _symbol_to_id() previously looked up Stock by symbol alone with
scalar_one_or_none() — but the DB's real uniqueness constraint is (symbol, exchange), so two
stocks sharing a ticker on different exchanges raised an unhandled MultipleResultsFound
(surfaced as an HTTP 500) instead of resolving deterministically.

routes.py itself can't be imported directly in a unit test — it imports
`from common.jwt_auth import get_current_username`, and conftest.py's blanket `common` stub
is a bare MagicMock (not a real package), so submodule imports fail. This test instead
re-runs the EXACT query construction _symbol_to_id() now uses (order_by active desc, id asc,
limit 1) against a real in-memory SQLite session with a fixture reproducing the duplicate-
symbol-different-exchange scenario — same technique as
ranking-engine's test_rank_symbol_market_scoping.py.
"""
import importlib
import importlib.util
import pathlib
import sys

# conftest.py stubs "sqlalchemy"/"sqlalchemy.orm" wholesale as MagicMock — reload the real
# modules here so this test runs against actual SQLAlchemy/SQLite, not a mock.
for _mod in ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql"):
    sys.modules.pop(_mod, None)
import sqlalchemy  # noqa: E402
importlib.reload(sqlalchemy)
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test"] = _models
_spec.loader.exec_module(_models)

Stock = _models.Stock
Market = _models.Market
Exchange = _models.Exchange
Base = _models.Base


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Stock.__table__])
    return Session(engine)


def _symbol_to_id_query(session, symbol: str):
    """Re-implements _symbol_to_id()'s query construction exactly (order_by active desc,
    id asc, limit 1) — the fix under test."""
    return session.execute(
        select(Stock.id)
        .where(Stock.symbol == symbol.upper())
        .order_by(Stock.active.desc(), Stock.id.asc())
        .limit(1)
    ).scalar_one_or_none()


def test_duplicate_symbol_across_exchanges_resolves_without_crashing():
    """The exact bug scenario: two Stock rows share the same symbol on different exchanges.
    The old scalar_one_or_none() (no order_by/limit) would raise MultipleResultsFound here;
    the fixed query must return exactly one id instead of crashing."""
    session = _make_session()
    session.add(Stock(symbol="ABC", market=Market.US, exchange=Exchange.NYSE, name="ABC Corp NYSE", active=True))
    session.add(Stock(symbol="ABC", market=Market.HK, exchange=Exchange.HKEX, name="ABC Corp HKEX", active=True))
    session.commit()

    result = _symbol_to_id_query(session, "ABC")
    assert result is not None  # must resolve, not raise MultipleResultsFound


def test_active_listing_is_preferred_over_inactive_duplicate():
    session = _make_session()
    session.add(Stock(symbol="XYZ", market=Market.US, exchange=Exchange.NYSE, name="XYZ Inactive", active=False))
    active = Stock(symbol="XYZ", market=Market.HK, exchange=Exchange.HKEX, name="XYZ Active", active=True)
    session.add(active)
    session.commit()

    result = _symbol_to_id_query(session, "XYZ")
    assert result == active.id


def test_single_matching_stock_still_resolves_normally():
    """No duplication — must behave exactly as before for the common case."""
    session = _make_session()
    stock = Stock(symbol="SOLO", market=Market.US, exchange=Exchange.NASDAQ, name="Solo Inc", active=True)
    session.add(stock)
    session.commit()

    result = _symbol_to_id_query(session, "SOLO")
    assert result == stock.id


def test_no_matching_stock_returns_none():
    session = _make_session()
    result = _symbol_to_id_query(session, "NOPE")
    assert result is None
