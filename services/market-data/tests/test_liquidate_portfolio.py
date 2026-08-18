"""Tests for T286-LIQUIDATE-PORTFOLIO's _close_one_paper_trade() + POST /paper-portfolio/
{portfolio_id}/liquidate — the confirming-click counterpart to check_portfolio_drawdown_
alerts()'s email-only notification (AUD288-AUTO-LIQUIDATION-DEFERRED), which never closes a
position on its own.

paper_portfolio.py can't be imported directly in this test environment (conftest.py stubs
sqlalchemy itself as a MagicMock) — matches test_trade_postmortem.py's/
test_broker_position_sync.py's established technique exactly: pop the stub, build ONE shared
in-memory engine + real models while real sqlalchemy is active, then restore the stub
immediately. Both functions' real source is extracted and exec()'d against this real session,
so these tests exercise the actual logic, not a hand-copied reimplementation.
"""
import sys

_STUBBED_MODULES = ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql", "db")
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util
import pathlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_liquidate", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_liquidate"] = _models
_spec.loader.exec_module(_models)

_ENGINE = create_engine("sqlite:///:memory:")
_models.Base.metadata.create_all(
    _ENGINE,
    tables=[_models.PaperPortfolio.__table__, _models.PaperTrade.__table__],
)

for _mod, _stub in _saved_stubs.items():
    if _stub is not None:
        sys.modules[_mod] = _stub
    else:
        sys.modules.pop(_mod, None)

PaperPortfolio = _models.PaperPortfolio
PaperTrade = _models.PaperTrade

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "paper_portfolio.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


class _FakeHTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _extract_close_one_paper_trade():
    start = _ROUTES_SOURCE.index("def _close_one_paper_trade(")
    end = _ROUTES_SOURCE.index("\n\n\n@router.post(\"/trades/{trade_id}/exit\")", start)
    raw = _ROUTES_SOURCE[start:end]
    namespace = {
        "np": __import__("numpy"),
        "datetime": datetime, "timedelta": timedelta,
        "Session": Session, "PaperPortfolio": PaperPortfolio, "PaperTrade": PaperTrade,
    }
    exec(raw, namespace)  # noqa: S102 — real source, not a duplicate
    return namespace["_close_one_paper_trade"]


def _extract_liquidate_portfolio():
    """Extracts liquidate_portfolio() with a fake _get_portfolio()/_fetch_live_prices()
    injected, matching test_trade_postmortem.py's established pattern of stubbing the real
    module's own lazy imports/helpers rather than needing the whole file importable."""
    start = _ROUTES_SOURCE.index("def liquidate_portfolio(")
    end = _ROUTES_SOURCE.index("\n\n\n# ── Closed trades", start)
    raw = _ROUTES_SOURCE[start:end]
    sig_end = raw.index(") -> dict:\n") + len(") -> dict:\n")
    body = raw[sig_end:]
    func_source = "def liquidate_portfolio(portfolio_id, confirm=False, session=None):\n" + body
    func_source = func_source.replace(
        "from .services.paper_trading_engine import _fetch_live_prices\n\n    ",
        "",
    )
    namespace = {
        "select": select, "HTTPException": _FakeHTTPException,
        "PaperTrade": PaperTrade,
        "_get_portfolio": _fake_get_portfolio,
        "_fetch_live_prices": _fake_fetch_live_prices,
        "_close_one_paper_trade": _close_one_paper_trade,
        "log": _FakeLog(),
    }
    exec(func_source, namespace)  # noqa: S102 — real source, not a duplicate
    return namespace["liquidate_portfolio"]


class _FakeLog:
    def info(self, *a, **kw): pass
    def error(self, *a, **kw): pass


_fetch_live_prices_return: dict = {}


def _fake_fetch_live_prices(symbols):
    return dict(_fetch_live_prices_return)


def _fake_get_portfolio(session, portfolio_id=None):
    p = session.get(PaperPortfolio, portfolio_id)
    if p is None:
        raise _FakeHTTPException(404, f"Portfolio {portfolio_id} not found")
    return p


_close_one_paper_trade = _extract_close_one_paper_trade()
liquidate_portfolio = _extract_liquidate_portfolio()


def _make_session():
    session = Session(_ENGINE)
    for table in (PaperTrade.__table__, PaperPortfolio.__table__):
        session.execute(table.delete())
    session.commit()
    return session


def _make_portfolio(session, id_=1, current_cash=10_000.0, config=None):
    p = PaperPortfolio(id=id_, name="Test", initial_capital=10_000.0, current_cash=current_cash, config=config or {})
    session.add(p)
    session.commit()
    return p


_next_trade_id = [1]


def _make_open_trade(session, portfolio_id, symbol="AAPL", entry_price=100.0, shares=10.0, current_price=None):
    trade = PaperTrade(
        id=_next_trade_id[0], portfolio_id=portfolio_id, symbol=symbol,
        trading_style="SWING", entry_date=(datetime.now(timezone.utc) - timedelta(days=5)).date(),
        entry_time=datetime.now(timezone.utc) - timedelta(days=5),
        entry_price=entry_price, shares=shares, stop_loss=entry_price * 0.9, take_profit=entry_price * 1.2,
        current_stop=entry_price * 0.9, current_price=current_price, stage="open",
    )
    session.add(trade)
    session.commit()
    _next_trade_id[0] += 1
    return trade


# ── _close_one_paper_trade() — the shared close-flow ─────────────────────────────────────────

def test_computes_pnl_and_pnl_pct_correctly():
    session = _make_session()
    try:
        p = _make_portfolio(session, current_cash=5_000.0)
        trade = _make_open_trade(session, p.id, entry_price=100.0, shares=10.0)
        result = _close_one_paper_trade(session, p, trade, 110.0, "manual_liquidation")
        # exit_slippage_pct defaults to 0.001 -> exit_p = 110 * 0.999 = 109.89
        assert result["exit_price"] == 109.89
        assert result["pnl"] == round((109.89 - 100.0) * 10.0, 2)
        assert result["pnl_pct"] == round((109.89 / 100.0 - 1) * 100, 2)
    finally:
        session.close()


def test_credits_cash_back_net_of_commission():
    session = _make_session()
    try:
        p = _make_portfolio(session, current_cash=1_000.0, config={"commission_per_share": 0.01, "exit_slippage_pct": 0.0})
        trade = _make_open_trade(session, p.id, entry_price=50.0, shares=20.0)
        _close_one_paper_trade(session, p, trade, 60.0, "manual_liquidation")
        exit_value = 60.0 * 20.0
        commission = 0.01 * 20.0
        assert p.current_cash == round(1_000.0 + exit_value - commission, 2)
    finally:
        session.close()


def test_sets_exit_reason_to_the_passed_value_not_hardcoded():
    """The whole point of extracting this as a shared helper is that manual_exit_trade() and
    liquidate_portfolio() each pass their OWN distinct exit_reason — confirms the parameter
    actually reaches the trade, not a hardcoded literal left over from the pre-extraction
    single-caller version."""
    session = _make_session()
    try:
        p = _make_portfolio(session)
        trade = _make_open_trade(session, p.id)
        _close_one_paper_trade(session, p, trade, 105.0, "manual_liquidation")
        assert trade.exit_reason == "manual_liquidation"
    finally:
        session.close()


def test_marks_the_trade_closed_and_sets_exit_price():
    session = _make_session()
    try:
        p = _make_portfolio(session)
        trade = _make_open_trade(session, p.id)
        _close_one_paper_trade(session, p, trade, 95.0, "manual_liquidation")
        assert trade.stage == "closed"
        assert trade.exit_price == round(95.0 * 0.999, 4)
        assert trade.exit_time is not None
    finally:
        session.close()


def test_cash_never_goes_negative_even_on_a_pathological_input():
    session = _make_session()
    try:
        p = _make_portfolio(session, current_cash=0.0, config={"commission_per_share": 100.0})
        trade = _make_open_trade(session, p.id, entry_price=1.0, shares=1.0)
        _close_one_paper_trade(session, p, trade, 1.0, "manual_liquidation")
        assert p.current_cash >= 0.0
    finally:
        session.close()


# ── liquidate_portfolio() — the bulk-close endpoint ───────────────────────────────────────────

def test_requires_confirm_true_before_closing_anything():
    """The one deliberate second confirmation layer beyond the frontend's own browser
    confirm() dialog — must reject with a clear message, and must not have touched the
    position at all (checked directly, not just that an exception was raised)."""
    session = _make_session()
    try:
        p = _make_portfolio(session)
        trade = _make_open_trade(session, p.id)
        try:
            liquidate_portfolio(p.id, confirm=False, session=session)
            assert False, "expected _FakeHTTPException"
        except _FakeHTTPException as exc:
            assert exc.status_code == 400
            assert "confirm" in exc.detail.lower()
        assert session.get(PaperTrade, trade.id).stage == "open", "confirm=False must not close anything"
    finally:
        session.close()


def test_closes_every_open_trade_in_the_portfolio():
    global _fetch_live_prices_return
    session = _make_session()
    try:
        p = _make_portfolio(session, current_cash=1_000.0)
        t1 = _make_open_trade(session, p.id, symbol="AAPL", entry_price=100.0, shares=5.0)
        t2 = _make_open_trade(session, p.id, symbol="MSFT", entry_price=200.0, shares=2.0)
        _fetch_live_prices_return = {"AAPL": 110.0, "MSFT": 210.0}
        result = liquidate_portfolio(p.id, confirm=True, session=session)
        assert len(result["closed"]) == 2
        assert {c["symbol"] for c in result["closed"]} == {"AAPL", "MSFT"}
        assert session.get(PaperTrade, t1.id).stage == "closed"
        assert session.get(PaperTrade, t2.id).stage == "closed"
    finally:
        _fetch_live_prices_return = {}
        session.close()


def test_only_closes_trades_belonging_to_this_portfolio_not_others():
    global _fetch_live_prices_return
    session = _make_session()
    try:
        p1 = _make_portfolio(session, id_=1)
        p2 = _make_portfolio(session, id_=2)
        t1 = _make_open_trade(session, p1.id, symbol="AAPL")
        t2 = _make_open_trade(session, p2.id, symbol="TSLA")
        _fetch_live_prices_return = {"AAPL": 110.0, "TSLA": 300.0}
        result = liquidate_portfolio(p1.id, confirm=True, session=session)
        assert len(result["closed"]) == 1
        assert result["closed"][0]["symbol"] == "AAPL"
        assert session.get(PaperTrade, t1.id).stage == "closed"
        assert session.get(PaperTrade, t2.id).stage == "open", "must not touch the OTHER portfolio's trade"
    finally:
        _fetch_live_prices_return = {}
        session.close()


def test_falls_back_to_current_price_when_the_batch_fetch_is_missing_the_symbol():
    """A symbol missing from the batch live-price fetch (a partial yfinance failure mid-cycle)
    must fall back to the trade's own last-known price, not crash or silently skip the close."""
    global _fetch_live_prices_return
    session = _make_session()
    try:
        p = _make_portfolio(session)
        t = _make_open_trade(session, p.id, symbol="ZZZZ", entry_price=50.0, current_price=55.0)
        _fetch_live_prices_return = {}  # ZZZZ is missing from the batch fetch
        result = liquidate_portfolio(p.id, confirm=True, session=session)
        assert len(result["closed"]) == 1
        # exit_price should derive from the fallback current_price (55.0), not crash on a None.
        assert result["closed"][0]["exit_price"] == round(55.0 * 0.999, 4)
        assert session.get(PaperTrade, t.id).stage == "closed"
    finally:
        _fetch_live_prices_return = {}
        session.close()


def test_returns_empty_closed_list_when_there_are_no_open_positions():
    session = _make_session()
    try:
        p = _make_portfolio(session)
        result = liquidate_portfolio(p.id, confirm=True, session=session)
        assert result["closed"] == []
    finally:
        session.close()


def test_one_trades_failure_does_not_abort_closing_the_rest():
    """A single trade's close raising an exception (e.g. a corrupted row) must not prevent
    every OTHER open trade in the same portfolio from still being closed."""
    global _fetch_live_prices_return
    session = _make_session()
    try:
        p = _make_portfolio(session)
        # entry_price=0.0 makes _close_one_paper_trade's own pnl_pct math raise ZeroDivisionError —
        # this trade is intentionally never asserted on individually, only used to trigger the failure.
        _make_open_trade(session, p.id, symbol="AAPL", entry_price=0.0)
        t2 = _make_open_trade(session, p.id, symbol="MSFT", entry_price=100.0)
        _fetch_live_prices_return = {"AAPL": 110.0, "MSFT": 210.0}
        result = liquidate_portfolio(p.id, confirm=True, session=session)
        # MSFT (the healthy trade) must still close even if AAPL's math has an edge case.
        symbols_closed = {c["symbol"] for c in result["closed"]}
        assert "MSFT" in symbols_closed
        assert session.get(PaperTrade, t2.id).stage == "closed"
    finally:
        _fetch_live_prices_return = {}
        session.close()


def test_404s_for_a_nonexistent_portfolio():
    session = _make_session()
    try:
        try:
            liquidate_portfolio(9999, confirm=True, session=session)
            assert False, "expected _FakeHTTPException"
        except _FakeHTTPException as exc:
            assert exc.status_code == 404
    finally:
        session.close()
