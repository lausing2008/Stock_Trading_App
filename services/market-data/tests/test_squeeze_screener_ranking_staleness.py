"""Tests for AUD265-SQUEEZE-MOMENTUM-NULL-ON-STALE-RANKINGS.

short_squeeze()'s ranking lookup used to filter `Ranking.as_of >= today - timedelta(days=7)`
BEFORE taking the latest-per-stock row — a stock whose newest ranking predated that window
was excluded from rank_rows entirely, silently nulling momentum_score/k_score for every stock
caught by a lapsed ranking refresh (this repo's own history documents rankings going stale 7+
days at a time). Fixed to widen the window to 90 days (bounding the query against the
unbounded-growth `rankings` history table, since Ranking has no unique(stock_id, as_of)
constraint — removing the filter entirely would pull every row ever written) and surface how
old the newest available ranking actually is via ranking_as_of/ranking_is_stale, matching
short_interest()'s own established staleness-surfacing convention.

routes.py can't be imported directly in this test environment (conftest.py stubs sqlalchemy
itself as a MagicMock) — matches test_correlation_preentry.py's/test_broker_position_sync.py's
established technique: pop the stub, build ONE shared in-memory engine + real models while
real sqlalchemy is active, then restore the stub immediately. The ranking query + the
rank_map/ranking_is_stale computation are extracted from the real source via exec() and run
against this real session.
"""
import sys

_STUBBED_MODULES = ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql", "db")
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util
import pathlib
from datetime import date, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_squeeze_stale", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_squeeze_stale"] = _models
_spec.loader.exec_module(_models)

_ENGINE = create_engine("sqlite:///:memory:")
_models.Base.metadata.create_all(_ENGINE, tables=[_models.Stock.__table__, _models.Ranking.__table__])

for _mod, _stub in _saved_stubs.items():
    if _stub is not None:
        sys.modules[_mod] = _stub
    else:
        sys.modules.pop(_mod, None)

Stock = _models.Stock
Market = _models.Market
Exchange = _models.Exchange
Ranking = _models.Ranking

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _fetch_rank_map_and_staleness(session, today: date):
    """Pulls the real ranking-fetch + staleness-computation block out of short_squeeze() and
    exec()s it against a real session — the exact statements between the AUD265 comment block
    and stock_id_map's own construction, isolated from the surrounding Redis/Stock/fundamentals
    machinery this test doesn't need."""
    start = _ROUTES_SOURCE.index('_ranking_stale_cutoff = today - _stimedelta(days=7)')
    end = _ROUTES_SOURCE.index('stock_id_map = {s.symbol: s.id for s in stocks}')
    body = _ROUTES_SOURCE[start:end]
    dedented = [ln[4:] if ln.startswith("    ") else ln for ln in body.splitlines()]
    func_source = (
        "def _run(session, today, select, Ranking, timedelta):\n"
        "    _stimedelta = timedelta\n"
        + "\n".join("    " + ln for ln in dedented)
        + "\n    return rank_map, _ranking_stale_cutoff\n"
    )
    namespace: dict = {}
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_run"](session, today, select, Ranking, timedelta)


def _make_session():
    return Session(_ENGINE)


def _seed_stock(session, stock_id=1):
    # A distinct symbol per stock_id — Stock has a real UniqueConstraint("symbol", "exchange"),
    # and every test in this file shares ONE module-level in-memory engine/table.
    session.add(Stock(id=stock_id, symbol=f"TEST{stock_id}", market=Market.US, exchange=Exchange.NASDAQ, name="Test Co"))
    session.commit()


def test_a_ranking_older_than_7_days_but_within_90_is_still_returned_not_nulled():
    """The exact regression this fix targets: a lapsed refresh leaves the newest ranking at,
    say, 20 days old — it must still be found (and marked stale), not silently excluded."""
    session = _make_session()
    _seed_stock(session)
    today = date(2026, 6, 1)
    stale_date = today - timedelta(days=20)
    session.add(Ranking(id=1, stock_id=1, as_of=stale_date, score=60.0, technical=55.0, momentum=52.0, volatility=15.0))
    session.commit()

    rank_map, cutoff = _fetch_rank_map_and_staleness(session, today)

    assert 1 in rank_map
    assert rank_map[1].momentum == 52.0
    assert rank_map[1].as_of < cutoff


def test_a_ranking_older_than_90_days_is_correctly_excluded():
    """The query's own bound: something MUST still limit how far back this looks, or every
    row ever written would be pulled on every request against the unbounded-growth table."""
    session = _make_session()
    _seed_stock(session, stock_id=2)
    today = date(2026, 6, 1)
    too_old = today - timedelta(days=200)
    session.add(Ranking(id=2, stock_id=2, as_of=too_old, score=60.0, technical=55.0, momentum=52.0, volatility=15.0))
    session.commit()

    rank_map, _ = _fetch_rank_map_and_staleness(session, today)

    assert 2 not in rank_map


def test_the_latest_ranking_per_stock_wins_when_multiple_exist():
    session = _make_session()
    _seed_stock(session, stock_id=3)
    today = date(2026, 6, 1)
    session.add(Ranking(id=3, stock_id=3, as_of=today - timedelta(days=10), score=40.0, technical=40.0, momentum=30.0, volatility=15.0))
    session.add(Ranking(id=4, stock_id=3, as_of=today - timedelta(days=1), score=70.0, technical=70.0, momentum=65.0, volatility=15.0))
    session.commit()

    rank_map, _ = _fetch_rank_map_and_staleness(session, today)

    assert rank_map[3].momentum == 65.0


def test_a_ranking_within_7_days_is_not_marked_stale():
    session = _make_session()
    _seed_stock(session, stock_id=4)
    today = date(2026, 6, 1)
    fresh_date = today - timedelta(days=2)
    session.add(Ranking(id=5, stock_id=4, as_of=fresh_date, score=60.0, technical=55.0, momentum=52.0, volatility=15.0))
    session.commit()

    rank_map, cutoff = _fetch_rank_map_and_staleness(session, today)

    assert rank_map[4].as_of >= cutoff


def test_short_squeeze_ranking_window_is_90_days_not_7():
    """Regression guard on the widened query bound itself — the real WHERE filter must use 90
    days, not the old 7-day value (which still legitimately appears in the surrounding
    explanatory comment describing the OLD, now-fixed behavior — checking only the real,
    executable WHERE clause avoids a false match against that comment text)."""
    start = _ROUTES_SOURCE.index("rank_rows = session.execute(")
    end = _ROUTES_SOURCE.index("rank_map = {rk.stock_id: rk for rk in rank_rows}", start)
    where_clause = _ROUTES_SOURCE[start:end]
    assert "Ranking.as_of >= today - timedelta(days=90)" in where_clause
    assert "Ranking.as_of >= today - timedelta(days=7)" not in where_clause


def test_response_surfaces_ranking_as_of_and_ranking_is_stale_fields():
    start = _ROUTES_SOURCE.index("def short_squeeze(")
    end = _ROUTES_SOURCE.index("results.sort(", start)
    body = _ROUTES_SOURCE[start:end]
    assert '"ranking_as_of":' in body
    assert '"ranking_is_stale":' in body


def _ranking_is_stale_expr(rank, cutoff):
    """Pulls the real ranking_is_stale expression out of short_squeeze() and eval()s it
    against a synthetic rank/_ranking_stale_cutoff pair — proves the ACTUAL comparison, not
    just that a key named ranking_is_stale is present somewhere in the response dict."""
    start = _ROUTES_SOURCE.index('"ranking_is_stale": (')
    end = _ROUTES_SOURCE.index("),", start) + 1
    expr = _ROUTES_SOURCE[start:end].split(":", 1)[1].strip().rstrip(",")
    namespace = {"rank": rank, "_ranking_stale_cutoff": cutoff}
    return eval(expr, namespace)  # noqa: S307 — isolated eval of one real expression


def test_ranking_is_stale_expression_true_when_rank_is_none():
    assert _ranking_is_stale_expr(rank=None, cutoff=date(2026, 5, 25)) is True


def test_ranking_is_stale_expression_true_when_rank_older_than_cutoff():
    class _FakeRank:
        as_of = date(2026, 5, 1)
    assert _ranking_is_stale_expr(rank=_FakeRank(), cutoff=date(2026, 5, 25)) is True


def test_ranking_is_stale_expression_false_when_rank_at_or_after_cutoff():
    class _FakeRank:
        as_of = date(2026, 5, 30)
    assert _ranking_is_stale_expr(rank=_FakeRank(), cutoff=date(2026, 5, 25)) is False
