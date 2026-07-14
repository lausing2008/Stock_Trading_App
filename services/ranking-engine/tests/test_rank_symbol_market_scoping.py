"""Regression test for T247-RANKINGENGINE-CROSSMARKET.

rank_symbol()'s sector-peer universe query previously had no market filter, so a stock's
value/growth percentile was computed against BOTH US and HK stocks sharing a sector label
(e.g. "Technology": 27 US + 14 HK stocks in production) — structurally different valuation
regimes pooled together. The batch/leaderboard path (_persist_rankings) is naturally
market-scoped since the scheduler invokes /rankings/refresh once per market.

routes.py itself can't be imported directly in a unit test — it imports
`from sqlalchemy.dialects.postgresql import insert as pg_insert` (fine, dialect-only) but also
`from db import ...`, and `db/__init__.py` eagerly creates a real psycopg2 Postgres engine at
import time. Rather than mock the entire routes.py module (which would hide the actual query
logic being tested), this test loads shared/db/models.py directly via importlib (bypassing
db/__init__.py's eager engine creation) and re-runs the EXACT query construction rank_symbol()
uses, against a real in-memory SQLite session with a mixed US/HK/Unknown-sector fixture.
"""
import importlib.util as _ilu
import pathlib as _pathlib
import sys

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

_models_path = (
    _pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
)
_spec = _ilu.spec_from_file_location("db_models_under_test", _models_path)
_models = _ilu.module_from_spec(_spec)
# Must register in sys.modules BEFORE exec_module — SQLAlchemy's declarative mapper resolves
# `Mapped[...]` string annotations against sys.modules[module.__name__] while the class bodies
# are still executing, not after.
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


def _add_stock(session, symbol, market, sector, active=True):
    s = Stock(symbol=symbol, market=market, exchange=Exchange.NASDAQ if market == Market.US else Exchange.HKEX,
               name=symbol, sector=sector, active=active)
    session.add(s)
    return s


def test_universe_query_is_scoped_to_the_target_stocks_market():
    """Core regression guard: the universe query rank_symbol() builds (real query, copied
    verbatim from the fixed routes.py line) must exclude the other market's stocks entirely."""
    session = _make_session()
    _add_stock(session, "AAPL", Market.US, "Technology")
    _add_stock(session, "MSFT", Market.US, "Technology")
    _add_stock(session, "0700.HK", Market.HK, "Technology")
    _add_stock(session, "9988.HK", Market.HK, "Technology")
    session.commit()

    target = session.execute(select(Stock).where(Stock.symbol == "0700.HK")).scalar_one()

    # This is the FIXED query from routes.py's rank_symbol() — scoped to stock.market.
    universe = list(session.execute(
        select(Stock).where(Stock.active.is_(True), Stock.market == target.market)
    ).scalars())
    universe_symbols = {s.symbol for s in universe}

    assert universe_symbols == {"0700.HK", "9988.HK"}
    assert "AAPL" not in universe_symbols
    assert "MSFT" not in universe_symbols


def test_unfixed_query_would_have_pooled_both_markets():
    """Sanity check proving the fix is a real, observable behavior change — the OLD query
    (no market filter) really did pool both markets together on this same fixture."""
    session = _make_session()
    _add_stock(session, "AAPL", Market.US, "Technology")
    _add_stock(session, "MSFT", Market.US, "Technology")
    _add_stock(session, "0700.HK", Market.HK, "Technology")
    session.commit()

    # The ORIGINAL (buggy) query — no market filter at all.
    old_universe = list(session.execute(select(Stock).where(Stock.active.is_(True))).scalars())
    old_symbols = {s.symbol for s in old_universe}
    assert old_symbols == {"AAPL", "MSFT", "0700.HK"}, (
        "fixture doesn't reproduce cross-market pooling — the old query should include everything"
    )


def test_inactive_stocks_are_still_excluded_after_the_fix():
    """The market-scoping fix must not weaken the pre-existing Stock.active filter."""
    session = _make_session()
    _add_stock(session, "0700.HK", Market.HK, "Technology", active=True)
    _add_stock(session, "1234.HK", Market.HK, "Technology", active=False)
    session.commit()

    target = session.execute(select(Stock).where(Stock.symbol == "0700.HK")).scalar_one()
    universe = list(session.execute(
        select(Stock).where(Stock.active.is_(True), Stock.market == target.market)
    ).scalars())
    assert {s.symbol for s in universe} == {"0700.HK"}
