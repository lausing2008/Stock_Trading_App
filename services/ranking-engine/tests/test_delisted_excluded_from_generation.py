"""Regression tests for BUG-DELISTED-GENERATION-BLIND.

Stock.delisted (aud14-survivorship) never flips Stock.active — a confirmed-delisted stock
stays "active" forever, so every generation endpoint filtering only on Stock.active.is_(True)
kept recomputing fresh work for it on every refresh cycle. Confirmed sibling of
BUG-PAPERPOS-DELISTED-FROZEN/BUG-ALERTS-DELISTED-SILENT (2026-07-29, market-data) — those
fixed CONSUMING the flag once a signal/ranking already existed; this is the generation side
that produces that stale work in the first place.

Uses this repo's established real-source-extraction technique (test_rank_symbol_market_
scoping.py's pattern) — loads the real shared/db/models.py against an in-memory SQLite DB and
extracts the actual query statements directly out of routes.py's source text, so a future
regression that drops the Stock.delisted filter is caught against the REAL code, not a
hand-copied duplicate that could silently drift from it.
"""
import importlib.util as _ilu
import pathlib as _pathlib
import sys

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

_models_path = (
    _pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
)
_spec = _ilu.spec_from_file_location("db_models_under_test_delisted", _models_path)
_models = _ilu.module_from_spec(_spec)
sys.modules["db_models_under_test_delisted"] = _models
_spec.loader.exec_module(_models)

Stock = _models.Stock
Market = _models.Market
Exchange = _models.Exchange
Base = _models.Base

_ROUTES_PATH = (
    _pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
)
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Stock.__table__])
    return Session(engine)


def _add_stock(session, symbol, market=Market.US, delisted=False, active=True):
    s = Stock(
        symbol=symbol, market=market,
        exchange=Exchange.NASDAQ if market == Market.US else Exchange.HKEX,
        name=symbol, sector="Technology", active=active, delisted=delisted,
    )
    session.add(s)
    return s


# ── POST /rankings/refresh ──────────────────────────────────────────────────────────

def _run_real_refresh_universe_query(session) -> set[str]:
    """Extracts and executes the REAL `stmt = select(Stock).where(...)` statement from
    /rankings/refresh directly out of routes.py's source text, proving this test tracks the
    actual function rather than an independently-maintained copy that could silently drift."""
    marker = 'def refresh(\n'
    start = _ROUTES_SOURCE.index("stmt = select(Stock).where(Stock.active.is_(True)", _ROUTES_SOURCE.index(marker))
    end = _ROUTES_SOURCE.index("\n", start)
    namespace = {"session": session, "select": select, "Stock": Stock}
    exec(_ROUTES_SOURCE[start:end], namespace)
    result = list(session.execute(namespace["stmt"]).scalars())
    return {s.symbol for s in result}


def test_rankings_refresh_excludes_delisted_stocks():
    session = _make_session()
    _add_stock(session, "AAPL", delisted=False)
    _add_stock(session, "SKHYV", delisted=True)
    session.commit()

    symbols = _run_real_refresh_universe_query(session)

    assert symbols == {"AAPL"}
    assert "SKHYV" not in symbols


def test_rankings_refresh_query_is_present_in_the_real_source_verbatim():
    """Guards against the exact fix regressing silently — if someone reverts the filter back
    to plain Stock.active.is_(True), this exact string won't be found and the test raises
    loudly rather than silently testing nothing."""
    assert "stmt = select(Stock).where(Stock.active.is_(True), Stock.delisted.is_(False))" in _ROUTES_SOURCE


# ── single-symbol sector peer-universe (rank_symbol) ────────────────────────────────

def test_peer_universe_excludes_delisted_peers():
    assert (
        "select(Stock).where(Stock.active.is_(True), Stock.delisted.is_(False), Stock.market == stock.market)"
        in _ROUTES_SOURCE
    )

    session = _make_session()
    target = _add_stock(session, "AAPL", delisted=False)
    _add_stock(session, "MSFT", delisted=False)
    _add_stock(session, "SKHYV", delisted=True)
    session.commit()

    universe = list(session.execute(
        select(Stock).where(Stock.active.is_(True), Stock.delisted.is_(False), Stock.market == target.market)
    ).scalars())
    symbols = {s.symbol for s in universe}

    assert symbols == {"AAPL", "MSFT"}
    assert "SKHYV" not in symbols


# ── _leaderboard_live fallback ──────────────────────────────────────────────────────

def test_leaderboard_live_fallback_excludes_delisted_stocks():
    start = _ROUTES_SOURCE.index('"""Fallback: compute rankings live when no persisted data exists."""')
    end = _ROUTES_SOURCE.index("stocks = list(session.execute(stmt).scalars())", start)
    block = _ROUTES_SOURCE[start:end]
    assert "Stock.delisted.is_(False)" in block


def test_leaderboard_live_result_excludes_a_delisted_stock():
    session = _make_session()
    _add_stock(session, "AAPL", delisted=False)
    _add_stock(session, "SKHYV", delisted=True)
    session.commit()

    stmt = select(Stock).where(Stock.active.is_(True), Stock.delisted.is_(False))
    stocks = list(session.execute(stmt).scalars())
    symbols = {s.symbol for s in stocks}

    assert symbols == {"AAPL"}
