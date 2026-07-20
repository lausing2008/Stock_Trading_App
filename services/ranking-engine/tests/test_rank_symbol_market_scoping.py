"""Regression test for T247-RANKINGENGINE-CROSSMARKET.

rank_symbol()'s sector-peer universe query previously had no market filter, so a stock's
value/growth percentile was computed against BOTH US and HK stocks sharing a sector label
(e.g. "Technology": 27 US + 14 HK stocks in production) — structurally different valuation
regimes pooled together. The batch/leaderboard path (_persist_rankings) is naturally
market-scoped since the scheduler invokes /rankings/refresh once per market.

AUD250-RANKINGENGINE-TEST-HAND-DUPLICATED-QUERY: the original version of this test file
hand-copied the query text into the test itself ("This is the FIXED query from routes.py's
rank_symbol()") instead of reading it from the real source. That meant the test could keep
passing even if rank_symbol() itself regressed (e.g. someone accidentally dropped the
Stock.market filter) — the hand-copied duplicate would happily stay "fixed" forever,
independent of whatever the real function actually does. Rewritten to extract the real
universe-query line directly out of routes.py's source text and exec() it into the test's own
namespace, matching the established source-text-extraction technique used elsewhere in this
repo for functions with heavy runtime dependencies (test_llm_scoring_config_wiring.py,
test_min_kscore_config_wiring.py) — a change to the real query is now guaranteed to be
reflected here, not silently bypassed by a hardcoded duplicate.
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


def _real_universe_query_source() -> str:
    """Extracts the real `universe = list(session.execute(...))` statement directly out of
    rank_symbol() in routes.py's source text — the exact multi-line block, not a hand-copied
    duplicate. Fails loudly (raises) if the anchor text has moved or been rewritten, which is
    itself useful signal that this test needs to be re-anchored to match a real code change."""
    routes_path = (
        _pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
    )
    source = routes_path.read_text()
    start = source.index("universe = list(session.execute(", source.index("def rank_symbol("))
    end = source.index(").scalars())", start) + len(").scalars())")
    return source[start:end]


def _run_real_universe_query(session, stock):
    """Executes the REAL universe-query statement extracted from routes.py against a fake
    `session`/`stock` bound in this local scope — proves the test tracks the actual source,
    not an independently-maintained copy that could silently drift from it."""
    namespace = {"session": session, "select": select, "Stock": Stock, "stock": stock}
    exec(_real_universe_query_source(), namespace)
    return namespace["universe"]


def test_real_source_actually_scopes_the_universe_query_to_the_targets_market():
    """Core regression guard, now against the REAL routes.py source rather than a hand-copied
    duplicate — a future regression that drops the Stock.market filter from rank_symbol()
    itself will make this test fail, since it re-executes the real extracted statement."""
    session = _make_session()
    _add_stock(session, "AAPL", Market.US, "Technology")
    _add_stock(session, "MSFT", Market.US, "Technology")
    _add_stock(session, "0700.HK", Market.HK, "Technology")
    _add_stock(session, "9988.HK", Market.HK, "Technology")
    session.commit()

    target = session.execute(select(Stock).where(Stock.symbol == "0700.HK")).scalar_one()

    universe = _run_real_universe_query(session, target)
    universe_symbols = {s.symbol for s in universe}

    assert universe_symbols == {"0700.HK", "9988.HK"}
    assert "AAPL" not in universe_symbols
    assert "MSFT" not in universe_symbols


def test_unfixed_query_would_have_pooled_both_markets():
    """Sanity check proving the fix is a real, observable behavior change — the OLD query
    (no market filter) really did pool both markets together on this same fixture. This one
    intentionally stays a hand-written comparison query, since it represents the ALREADY-FIXED
    historical bug, not the current, still-changeable real behavior under test above."""
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
    """The market-scoping fix must not weaken the pre-existing Stock.active filter — checked
    against the real extracted source, same as the primary regression guard above."""
    session = _make_session()
    _add_stock(session, "0700.HK", Market.HK, "Technology", active=True)
    _add_stock(session, "1234.HK", Market.HK, "Technology", active=False)
    session.commit()

    target = session.execute(select(Stock).where(Stock.symbol == "0700.HK")).scalar_one()
    universe = _run_real_universe_query(session, target)
    assert {s.symbol for s in universe} == {"0700.HK"}
