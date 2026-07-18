"""Tests for T258-TRADE-POSTMORTEM's GET /paper-portfolio/trades/{id}/postmortem.

paper_portfolio.py can't be imported directly in this test environment (conftest.py stubs
sqlalchemy itself as a MagicMock) — matches test_broker_position_sync.py's and
test_correlation_preentry.py's established technique exactly: pop the stub, build ONE shared
in-memory engine + real models while real sqlalchemy is active, then restore the stub
immediately. get_trade_postmortem()'s real source is extracted and exec()'d against this real
session, so these tests exercise the actual logic, not a hand-copied reimplementation.
"""
import sys

_STUBBED_MODULES = ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql", "db")
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util
import pathlib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_postmortem", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_postmortem"] = _models
_spec.loader.exec_module(_models)

_ENGINE = create_engine("sqlite:///:memory:")
_models.Base.metadata.create_all(
    _ENGINE,
    tables=[_models.PaperPortfolio.__table__, _models.PaperTrade.__table__,
            _models.Stock.__table__, _models.Price.__table__],
)

for _mod, _stub in _saved_stubs.items():
    if _stub is not None:
        sys.modules[_mod] = _stub
    else:
        sys.modules.pop(_mod, None)

PaperPortfolio = _models.PaperPortfolio
PaperTrade = _models.PaperTrade
Stock = _models.Stock
Price = _models.Price
TimeFrame = _models.TimeFrame
Market = _models.Market
Exchange = _models.Exchange

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "paper_portfolio.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _extract_get_trade_postmortem():
    """Pulls get_trade_postmortem()'s real source out of paper_portfolio.py and exec()s it
    against real sqlalchemy/models — stubbing only the FastAPI decorator/Depends machinery
    and _STYLE_OVERRIDES (imported lazily inside the function in the real source; supplied
    here as a plain dict matching the real values so the test doesn't depend on
    paper_trading_engine.py being importable)."""
    # Extract the REAL _MECHANICAL_EXIT_REASONS constant from source too — a hardcoded
    # duplicate in this test file would silently stop reflecting a real change to the
    # constant (confirmed by adversarial testing: an earlier version of this extraction
    # hardcoded the set directly in the exec namespace, and emptying the real module-level
    # constant as a sabotage test was NOT caught, since the test never actually read the
    # sabotaged value).
    const_start = _ROUTES_SOURCE.index("_MECHANICAL_EXIT_REASONS = ")
    const_end = _ROUTES_SOURCE.index("\n", const_start)
    const_line = _ROUTES_SOURCE[const_start:const_end]

    start = _ROUTES_SOURCE.index("def get_trade_postmortem(")
    end = _ROUTES_SOURCE.index("\n\n\n# ── Trades CSV export", start)
    raw = _ROUTES_SOURCE[start:end]
    sig_end = raw.index(") -> dict:\n") + len(") -> dict:\n")
    body = raw[sig_end:]
    func_source = "def get_trade_postmortem(trade_id, session=None):\n" + body

    # Replace the real lazy import with a fixed, test-local stand-in for _STYLE_OVERRIDES.
    func_source = func_source.replace(
        "from ..services.paper_trading_engine import _STYLE_OVERRIDES",
        "_STYLE_OVERRIDES = _TEST_STYLE_OVERRIDES",
    )

    namespace = {
        "select": select,
        "func": func,
        "PaperTrade": PaperTrade,
        "Price": Price,
        "TimeFrame": TimeFrame,
        "HTTPException": _FakeHTTPException,
        "_TEST_STYLE_OVERRIDES": {
            "SHORT": {"max_hold_days": 10}, "GROWTH": {"max_hold_days": 60},
            "SWING": {"max_hold_days": 20}, "LONG": {"max_hold_days": 90},
        },
    }
    exec(const_line, namespace)  # noqa: S102 — real _MECHANICAL_EXIT_REASONS source, not a duplicate
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["get_trade_postmortem"]


class _FakeHTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


get_trade_postmortem = _extract_get_trade_postmortem()


def _make_session():
    session = Session(_ENGINE)
    for table in (Price.__table__, PaperTrade.__table__, PaperPortfolio.__table__, Stock.__table__):
        session.execute(table.delete())
    session.commit()
    return session


def _make_portfolio(session, id_=1):
    p = PaperPortfolio(id=id_, name="Test", initial_capital=10000.0, current_cash=10000.0, config={})
    session.add(p)
    session.commit()
    return p


def _make_stock(session, symbol="TEST"):
    stock = Stock(symbol=symbol, market=Market.US, exchange=Exchange.NASDAQ, name=symbol)
    session.add(stock)
    session.commit()
    return stock


_next_trade_id = [1]


def _make_closed_trade(
    session, portfolio_id, stock_id=None, style="SWING",
    entry_price=100.0, exit_price=110.0, stop_loss=95.0, take_profit=120.0,
    hold_days=15, exit_reason="target_reached", entry_days_ago=20,
):
    entry_time = datetime.now(timezone.utc) - timedelta(days=entry_days_ago)
    exit_time = entry_time + timedelta(days=hold_days)
    trade = PaperTrade(
        id=_next_trade_id[0], portfolio_id=portfolio_id, symbol="TEST", stock_id=stock_id,
        trading_style=style, entry_date=entry_time.date(), entry_time=entry_time,
        entry_price=entry_price, shares=10.0, stop_loss=stop_loss, take_profit=take_profit,
        current_stop=stop_loss, stage="closed", hold_days=hold_days,
        exit_time=exit_time, exit_price=exit_price, exit_reason=exit_reason,
        pnl=(exit_price - entry_price) * 10.0, pct_return=(exit_price / entry_price - 1) * 100,
    )
    session.add(trade)
    session.commit()
    _next_trade_id[0] += 1
    return trade


_next_price_id = [1]  # BigInteger PK, same SQLite-autoincrement workaround as other test files


def _add_daily_price(session, stock_id, ts, high):
    session.add(Price(
        id=_next_price_id[0], stock_id=stock_id, ts=ts, timeframe=TimeFrame.D1,
        open=high, high=high, low=high, close=high, volume=1_000_000,
    ))
    _next_price_id[0] += 1
    session.commit()


# ── basic shape / not-found / stage guard ──────────────────────────────────────

def test_returns_404_for_a_nonexistent_trade():
    session = _make_session()
    try:
        get_trade_postmortem(999, session=session)
        assert False, "expected _FakeHTTPException"
    except _FakeHTTPException as exc:
        assert exc.status_code == 404


def test_returns_400_for_an_open_trade():
    session = _make_session()
    portfolio = _make_portfolio(session)
    trade = PaperTrade(
        id=_next_trade_id[0], portfolio_id=portfolio.id, symbol="TEST", trading_style="SWING",
        entry_date=datetime.now(timezone.utc).date(), entry_time=datetime.now(timezone.utc),
        entry_price=100.0, shares=10.0, stop_loss=95.0, current_stop=95.0, stage="open",
    )
    session.add(trade)
    session.commit()
    _next_trade_id[0] += 1
    try:
        get_trade_postmortem(trade.id, session=session)
        assert False, "expected _FakeHTTPException"
    except _FakeHTTPException as exc:
        assert exc.status_code == 400


# ── exit-reason classification ──────────────────────────────────────────────────

def test_mechanical_exit_reasons_are_classified_as_plan_consistent():
    session = _make_session()
    portfolio = _make_portfolio(session)
    for reason in ("stop_hit", "breakeven_stop", "target_reached", "time_stop"):
        trade = _make_closed_trade(session, portfolio.id, exit_reason=reason)
        result = get_trade_postmortem(trade.id, session=session)
        assert result["is_mechanical_exit"] is True, f"{reason} should be mechanical"


def test_discretionary_exit_reasons_are_classified_as_not_plan_consistent():
    session = _make_session()
    portfolio = _make_portfolio(session)
    for reason in ("signal_exit", "momentum_fade", "manual_exit"):
        trade = _make_closed_trade(session, portfolio.id, exit_reason=reason)
        result = get_trade_postmortem(trade.id, session=session)
        assert result["is_mechanical_exit"] is False, f"{reason} should NOT be mechanical"


def test_unknown_exit_reason_defaults_to_the_string_unknown():
    session = _make_session()
    portfolio = _make_portfolio(session)
    trade = _make_closed_trade(session, portfolio.id, exit_reason=None)
    result = get_trade_postmortem(trade.id, session=session)
    assert result["exit_reason"] == "unknown"
    assert result["is_mechanical_exit"] is False


# ── plan-adherence math ──────────────────────────────────────────────────────────

def test_exit_vs_stop_and_target_percentages_are_computed_correctly():
    session = _make_session()
    portfolio = _make_portfolio(session)
    trade = _make_closed_trade(
        session, portfolio.id, exit_price=110.0, stop_loss=100.0, take_profit=120.0,
    )
    result = get_trade_postmortem(trade.id, session=session)
    assert result["plan_adherence"]["exit_vs_stop_pct"] == 10.0  # (110-100)/100*100
    assert round(result["plan_adherence"]["exit_vs_target_pct"], 2) == round((110 - 120) / 120 * 100, 2)


def test_exit_vs_target_is_none_when_no_take_profit_was_set():
    session = _make_session()
    portfolio = _make_portfolio(session)
    trade = _make_closed_trade(session, portfolio.id, take_profit=None)
    result = get_trade_postmortem(trade.id, session=session)
    assert result["plan_adherence"]["exit_vs_target_pct"] is None


# ── hold-window vs. style-specific expectation ──────────────────────────────────

def test_hold_days_vs_expected_uses_the_style_specific_max_hold_days():
    session = _make_session()
    portfolio = _make_portfolio(session)
    trade = _make_closed_trade(session, portfolio.id, style="SHORT", hold_days=15)
    result = get_trade_postmortem(trade.id, session=session)
    assert result["hold_window"]["expected_hold_days"] == 10  # SHORT's max_hold_days
    assert result["hold_window"]["hold_days_vs_expected"] == 5  # 15 - 10


def test_unknown_style_falls_back_to_the_60_day_default():
    session = _make_session()
    portfolio = _make_portfolio(session)
    trade = _make_closed_trade(session, portfolio.id, style="NOT_A_REAL_STYLE", hold_days=15)
    result = get_trade_postmortem(trade.id, session=session)
    assert result["hold_window"]["expected_hold_days"] == 60


# ── max favorable excursion ──────────────────────────────────────────────────────

def test_mfe_is_none_without_a_linked_stock_id():
    session = _make_session()
    portfolio = _make_portfolio(session)
    trade = _make_closed_trade(session, portfolio.id, stock_id=None)
    result = get_trade_postmortem(trade.id, session=session)
    assert result["max_favorable_excursion"]["price"] is None
    assert result["max_favorable_excursion"]["vs_exit_pct"] is None


def test_mfe_picks_the_highest_high_within_the_hold_window():
    session = _make_session()
    portfolio = _make_portfolio(session)
    stock = _make_stock(session)
    trade = _make_closed_trade(
        session, portfolio.id, stock_id=stock.id, exit_price=110.0, entry_days_ago=10, hold_days=5,
    )
    entry_time = trade.entry_time
    _add_daily_price(session, stock.id, entry_time + timedelta(days=1), high=108.0)
    _add_daily_price(session, stock.id, entry_time + timedelta(days=2), high=125.0)  # the real MFE
    _add_daily_price(session, stock.id, entry_time + timedelta(days=3), high=115.0)
    result = get_trade_postmortem(trade.id, session=session)
    assert result["max_favorable_excursion"]["price"] == 125.0
    assert round(result["max_favorable_excursion"]["vs_exit_pct"], 2) == round((125.0 - 110.0) / 110.0 * 100, 2)


def test_mfe_ignores_prices_outside_the_hold_window():
    session = _make_session()
    portfolio = _make_portfolio(session)
    stock = _make_stock(session)
    trade = _make_closed_trade(
        session, portfolio.id, stock_id=stock.id, exit_price=110.0, entry_days_ago=10, hold_days=5,
    )
    entry_time = trade.entry_time
    _add_daily_price(session, stock.id, entry_time - timedelta(days=5), high=999.0)  # before entry
    _add_daily_price(session, stock.id, entry_time + timedelta(days=1), high=112.0)
    result = get_trade_postmortem(trade.id, session=session)
    assert result["max_favorable_excursion"]["price"] == 112.0  # not 999.0


# ── source-text check ────────────────────────────────────────────────────────

def test_endpoint_is_registered():
    assert '@router.get("/trades/{trade_id}/postmortem")' in _ROUTES_SOURCE
    assert "def get_trade_postmortem(" in _ROUTES_SOURCE
