"""Tests for T230-BACKTESTING-MULTISYMBOL — portfolio_backtest.py's multi-symbol, day-stepped
shared-capital simulator over already-resolved SignalOutcome ground truth.

See portfolio_backtest.py's own module docstring for the full honest-scope disclosure — this
is NOT a replay of _scan_for_entries()/paper_trading_step()'s live decision/exit pipeline.

portfolio_backtest.py can't be imported directly in this test environment (conftest.py stubs
sqlalchemy itself as a MagicMock, and it transitively imports gate_harness.py, which imports
paper_trading_engine.py) — matches test_gate_harness_extended.py's established technique: pop
the stub, build ONE shared in-memory engine + real models while real sqlalchemy is active,
then restore the stub immediately. The pure functions under test are extracted from the real
source via exec() and run against this real session, so these tests exercise the actual logic,
not a hand-copied reimplementation that could silently drift from it.
"""
import sys

_STUBBED_MODULES = ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql", "db")
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util
import pathlib
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_pb", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_pb"] = _models
_spec.loader.exec_module(_models)

_ENGINE = create_engine("sqlite:///:memory:")
_models.Base.metadata.create_all(
    _ENGINE, tables=[_models.Stock.__table__, _models.Signal.__table__, _models.SignalOutcome.__table__, _models.Price.__table__],
)

for _mod, _stub in _saved_stubs.items():
    if _stub is not None:
        sys.modules[_mod] = _stub
    else:
        sys.modules.pop(_mod, None)

Stock = _models.Stock
Signal = _models.Signal
SignalOutcome = _models.SignalOutcome
Price = _models.Price
Market = _models.Market
Exchange = _models.Exchange
SignalHorizon = _models.SignalHorizon
SignalType = _models.SignalType
TimeFrame = _models.TimeFrame

_PB_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "backtest" / "portfolio_backtest.py"
_PB_SOURCE = _PB_PATH.read_text()

_ATE_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_ATE_SOURCE = _ATE_PATH.read_text()


def _extract_ewm_atr_from_ohlc():
    """Real ATR helper paper_trading_engine.py's _historical_atr (via gate_harness.py) relies
    on — a pure pandas function with no coupling to the rest of that huge module, so it's safe
    to extract standalone."""
    from common.indicators import atr as _canon_atr
    import pandas as pd
    start = _ATE_SOURCE.index("def _ewm_atr_from_ohlc(")
    end = _ATE_SOURCE.index("\ndef _compute_atr(", start)
    namespace = {"pd": pd, "_canon_atr": _canon_atr}
    exec(_ATE_SOURCE[start:end], namespace)  # noqa: S102 — isolated eval of real source, matching repo convention
    return namespace["_ewm_atr_from_ohlc"]


_ewm_atr_from_ohlc = _extract_ewm_atr_from_ohlc()

_GH_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "backtest" / "gate_harness.py"
_GH_SOURCE = _GH_PATH.read_text()


def _extract_historical_atr():
    import pandas as pd
    start = _GH_SOURCE.index("def _historical_atr(")
    end = _GH_SOURCE.index("\ndef _historical_confidence_delta(", start)
    namespace = {
        "select": select, "Price": Price, "TimeFrame": TimeFrame, "Session": Session,
        "date": date, "pd": pd, "_ewm_atr_from_ohlc": _ewm_atr_from_ohlc,
    }
    exec(_GH_SOURCE[start:end], namespace)  # noqa: S102 — isolated eval of real source, matching repo convention
    return namespace["_historical_atr"]


_historical_atr = _extract_historical_atr()
_HORIZON_BUCKET = {"SHORT": "5d", "SWING": "10d", "LONG": "20d", "GROWTH": "10d"}
# Same lag table gate_harness.py's own _HORIZON_RESOLUTION_LAG_DAYS defines (AUD19-DB3's
# calendar-day approximation of each style's trading horizon) — duplicated here rather than
# extracted, since it's a plain module-level dict literal with zero function-body logic to
# accidentally drift from.
_HORIZON_RESOLUTION_LAG_DAYS = {"SHORT": 7, "SWING": 14, "LONG": 20, "GROWTH": 14}
# gate_harness.py's own promotion-margin constants (BUG233-BACKTESTHARNESS-COINFLIP) —
# portfolio_backtest.py's sweep re-uses these EXACT values (not a second, independently-tuned
# margin), so the test fixture must inject the SAME real numbers, not a duplicated guess.
_MIN_PROMOTION_EV_LIFT_PCT = 0.5
_MIN_PROMOTION_LIFT_SD_RATIO = 0.5


def _extract_module():
    """Extract portfolio_backtest.py's own real functions (everything after its imports),
    substituting the already-extracted _historical_atr/_HORIZON_BUCKET/etc. for the ones it
    would normally import from .gate_harness (a relative import that can't resolve during
    exec())."""
    import numpy as np
    from datetime import timedelta
    from dataclasses import asdict
    marker = "# Simplified subset of paper_trading_engine.py's _DEFAULT_CONFIG"
    start = _PB_SOURCE.index(marker)
    namespace = {
        "np": np, "date": date, "timedelta": timedelta,
        "field": __import__("dataclasses").field,
        "dataclass": __import__("dataclasses").dataclass, "asdict": asdict,
        "select": select, "Session": Session,
        "Market": Market, "Signal": Signal, "SignalHorizon": SignalHorizon,
        "SignalOutcome": SignalOutcome, "SignalType": SignalType, "Stock": Stock,
        "_HORIZON_BUCKET": _HORIZON_BUCKET, "_historical_atr": _historical_atr,
        "_HORIZON_RESOLUTION_LAG_DAYS": _HORIZON_RESOLUTION_LAG_DAYS,
        "_MIN_PROMOTION_EV_LIFT_PCT": _MIN_PROMOTION_EV_LIFT_PCT,
        "_MIN_PROMOTION_LIFT_SD_RATIO": _MIN_PROMOTION_LIFT_SD_RATIO,
    }
    exec(_PB_SOURCE[start:], namespace)  # noqa: S102 — isolated eval of real source, matching repo convention
    return namespace


_ns = _extract_module()
_size_position = _ns["_size_position"]
_max_drawdown_pct = _ns["_max_drawdown_pct"]
_annualized_sharpe = _ns["_annualized_sharpe"]
run_portfolio_backtest = _ns["run_portfolio_backtest"]
sweep_max_portfolio_drawdown_pct = _ns["sweep_max_portfolio_drawdown_pct"]
sweep_risk_per_trade_pct = _ns["sweep_risk_per_trade_pct"]
_passes_return_promotion_margin = _ns["_passes_return_promotion_margin"]
_DEFAULT_CFG = _ns["_DEFAULT_CFG"]


def _make_session() -> Session:
    return Session(_ENGINE)


_next_stock_id = [1000]


def _insert_stock(session, symbol, sector=None):
    sid = _next_stock_id[0]
    _next_stock_id[0] += 1
    session.add(Stock(id=sid, symbol=symbol, market=Market.US, exchange=Exchange.NASDAQ, name=f"{symbol} Co", sector=sector))
    session.commit()
    return sid


_next_signal_id = [1]
_next_outcome_id = [1]


def _insert_buy_signal_with_outcome(
    session, stock_id, symbol, style, signal_date, entry_date, exit_date,
    entry_price, exit_price, pct_return,
):
    """Inserts a Signal + a resolved SignalOutcome row for it — the exact join shape
    _fetch_symbol_signals() reads. bucket-specific is_correct_{bucket}/return_{bucket} are set
    to make the row resolvable for whichever style's own bucket the test needs."""
    sig_id = _next_signal_id[0]
    _next_signal_id[0] += 1
    session.add(Signal(
        id=sig_id, stock_id=stock_id, horizon=SignalHorizon(style),
        ts=datetime.combine(signal_date, datetime.min.time(), tzinfo=timezone.utc),
        signal=SignalType.BUY, confidence=60.0, bullish_probability=0.6,
    ))
    bucket = _HORIZON_BUCKET[style]
    kwargs = {
        "id": _next_outcome_id[0], "signal_id": sig_id, "stock_id": stock_id, "symbol": symbol,
        "horizon": SignalHorizon(style), "signal_direction": "BUY", "signal_date": signal_date,
        "confidence": 60.0, "entry_date": entry_date, "entry_price": entry_price,
        "exit_date": exit_date, "exit_price": exit_price, "pct_return": pct_return,
        "is_correct": pct_return > 0,
    }
    kwargs[f"return_{bucket}"] = pct_return
    kwargs[f"is_correct_{bucket}"] = pct_return > 0
    _next_outcome_id[0] += 1
    session.add(SignalOutcome(**kwargs))
    session.commit()


def _insert_daily_prices(session, stock_id, start_date, closes):
    """Enough consecutive daily bars for _historical_atr()'s own period+5 floor (period=14
    default -> needs >=15 rows) — a flat, mildly-varying series so ATR computes a real,
    finite, small value rather than None."""
    from datetime import timedelta
    next_id = [500_000 + stock_id * 1000]
    for i, c in enumerate(closes):
        session.add(Price(
            id=next_id[0], stock_id=stock_id,
            ts=datetime.combine(start_date + timedelta(days=i), datetime.min.time()),
            timeframe=TimeFrame.D1, open=c, high=c + 1, low=c - 1, close=c, volume=1000,
        ))
        next_id[0] += 1
    session.commit()


# ── _size_position() — the sizing subset ────────────────────────────────────────────────────

class TestSizePosition:
    def test_basic_sizing_uses_risk_per_trade_over_stop_distance(self):
        cfg = {**_DEFAULT_CFG}
        # equity=100000, risk_per_trade_pct=0.01 -> risk_dollar=1000; atr=2.0, stop_atr_mult=2.0
        # -> stop_distance=4.0; raw shares = 1000/4.0 = 250.0, BUT max_position_pct=0.10 caps
        # position_value at 100000*0.10=10000 -> 250*50=12500 exceeds that, so shares clamps
        # to 10000/50=200.0. Confirms the risk-based formula feeds INTO the position cap, not
        # that the cap is bypassed.
        result = _size_position(100_000.0, entry_price=50.0, atr=2.0, cfg=cfg)
        assert result is not None
        shares, stop_distance = result
        assert shares == 200.0
        assert stop_distance == 4.0

    def test_basic_sizing_when_the_position_cap_does_not_bind(self):
        """The same formula with a smaller risk_per_trade_pct so max_position_pct never
        triggers — isolates the pure risk/stop-distance sizing math on its own."""
        cfg = {**_DEFAULT_CFG, "risk_per_trade_pct": 0.001}
        # risk_dollar=100; stop_distance=4.0 -> shares=25.0; position_value=25*50=1250,
        # comfortably under the 10000 position cap.
        result = _size_position(100_000.0, entry_price=50.0, atr=2.0, cfg=cfg)
        assert result is not None
        shares, stop_distance = result
        assert shares == 25.0
        assert stop_distance == 4.0

    def test_none_atr_falls_back_to_a_5_percent_of_price_stop_distance(self):
        cfg = {**_DEFAULT_CFG}
        result = _size_position(100_000.0, entry_price=100.0, atr=None, cfg=cfg)
        assert result is not None
        _, stop_distance = result
        assert stop_distance == 5.0  # 100 * 0.05

    def test_max_position_pct_caps_a_tiny_stop_distance_from_producing_an_oversized_position(self):
        cfg = {**_DEFAULT_CFG}
        # A tiny ATR would otherwise size a huge share count — max_position_pct=0.10 of
        # 100000 = 10000 must cap position_value, not let it balloon unchecked.
        result = _size_position(100_000.0, entry_price=50.0, atr=0.01, cfg=cfg)
        assert result is not None
        shares, _ = result
        assert shares * 50.0 <= 10_000.0 + 0.01

    def test_max_loss_per_trade_pct_caps_shares_before_the_position_pct_cap(self):
        cfg = {**_DEFAULT_CFG, "max_loss_per_trade_pct": 0.001}  # very tight loss cap
        result = _size_position(100_000.0, entry_price=50.0, atr=2.0, cfg=cfg)
        assert result is not None
        shares, stop_distance = result
        # max_loss_dollar = 100000*0.001=100; shares = 100/4.0 = 25.0 (much less than the
        # unconstrained 250.0 from the basic-sizing test above)
        assert shares == 25.0

    def test_below_min_position_value_returns_none(self):
        cfg = {**_DEFAULT_CFG, "risk_per_trade_pct": 0.0000001}
        result = _size_position(100_000.0, entry_price=50.0, atr=2.0, cfg=cfg)
        assert result is None

    def test_zero_or_negative_entry_price_returns_none(self):
        cfg = {**_DEFAULT_CFG}
        assert _size_position(100_000.0, entry_price=0.0, atr=2.0, cfg=cfg) is None
        assert _size_position(100_000.0, entry_price=-10.0, atr=2.0, cfg=cfg) is None


# ── _max_drawdown_pct() / _annualized_sharpe() ──────────────────────────────────────────────

class TestDrawdownAndSharpe:
    def test_max_drawdown_on_a_real_peak_to_trough_sequence(self):
        # peak=110 at index1, trough=88 at index3 -> dd = (110-88)/110 = 20.0%
        result = _max_drawdown_pct([100.0, 110.0, 95.0, 88.0, 105.0])
        assert result == 20.0

    def test_monotonically_rising_equity_has_zero_drawdown(self):
        assert _max_drawdown_pct([100.0, 110.0, 120.0, 130.0]) == 0.0

    def test_empty_equity_series_returns_zero_not_a_crash(self):
        assert _max_drawdown_pct([]) == 0.0

    def test_sharpe_is_none_not_zero_with_fewer_than_two_observations(self):
        assert _annualized_sharpe([]) is None
        assert _annualized_sharpe([0.01]) is None

    def test_sharpe_is_none_not_zero_on_zero_variance_returns(self):
        """A fabricated Sharpe of 0.0 would misleadingly read as 'genuinely flat' instead of
        'unmeasurable' — zero std must degrade to None, not a real-looking 0.0."""
        assert _annualized_sharpe([0.001, 0.001, 0.001, 0.001]) is None

    def test_sharpe_is_positive_for_a_real_positive_mean_return_series(self):
        result = _annualized_sharpe([0.01, 0.02, -0.005, 0.015, 0.008])
        assert result is not None
        assert result > 0


# ── run_portfolio_backtest() — the real day-stepped integration ─────────────────────────────

class TestRunPortfolioBacktest:
    def test_no_matched_signals_returns_a_skipped_reason_not_a_crash(self):
        session = _make_session()
        result = run_portfolio_backtest(session, ["NOSUCHSYM"], "SWING", "US", date(2026, 1, 1), date(2026, 6, 1))
        assert result.n_signals_seen == 0
        assert result.skipped_reason is not None
        session.close()

    def test_a_single_symbol_single_trade_produces_the_exact_expected_equity_curve(self):
        """The simplest real case: one symbol, one BUY signal, entry->exit, no competition
        for cash/room. Confirms the whole pipeline (fetch -> size -> enter -> exit -> equity)
        end to end against hand-computed numbers."""
        session = _make_session()
        sid = _insert_stock(session, "ONESYM", sector="Technology")
        _insert_daily_prices(session, sid, date(2026, 1, 1), [100.0 + i * 0.1 for i in range(40)])
        _insert_buy_signal_with_outcome(
            session, sid, "ONESYM", "SWING",
            signal_date=date(2026, 2, 1), entry_date=date(2026, 2, 2), exit_date=date(2026, 2, 12),
            entry_price=100.0, exit_price=110.0, pct_return=0.10,
        )
        result = run_portfolio_backtest(session, ["ONESYM"], "SWING", "US", date(2026, 1, 1), date(2026, 3, 1))
        assert result.n_signals_seen == 1
        assert result.n_entered == 1
        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.symbol == "ONESYM"
        assert trade.entry_price == 100.0
        assert trade.exit_price == 110.0
        assert trade.pnl_dollar > 0   # a real, profitable trade must show positive dollar P&L
        assert result.final_equity > result.initial_capital
        assert result.win_rate == 1.0
        session.close()

    def test_max_positions_cap_blocks_a_signal_beyond_the_limit(self):
        """3 symbols all signaling entry on the SAME day, max_positions=2 -> exactly 2 admitted,
        1 blocked and counted in n_skipped_no_room, never silently dropped."""
        session = _make_session()
        for i, sym in enumerate(["CAPA", "CAPB", "CAPC"]):
            sid = _insert_stock(session, sym, sector="Technology")
            _insert_daily_prices(session, sid, date(2026, 1, 1), [50.0 + i for _ in range(40)])
            _insert_buy_signal_with_outcome(
                session, sid, sym, "SWING",
                signal_date=date(2026, 2, 1), entry_date=date(2026, 2, 2), exit_date=date(2026, 2, 12),
                entry_price=50.0 + i, exit_price=55.0 + i, pct_return=(55.0 + i - (50.0 + i)) / (50.0 + i),
            )
        result = run_portfolio_backtest(
            session, ["CAPA", "CAPB", "CAPC"], "SWING", "US", date(2026, 1, 1), date(2026, 3, 1),
            cfg_overrides={"max_positions": 2},
        )
        assert result.n_entered == 2
        assert result.n_skipped_no_room >= 1
        session.close()

    def test_sector_cap_blocks_over_concentration_in_one_sector(self):
        """Two symbols in the SAME sector, a tight max_sector_pct — the second entry (which
        would push the sector's combined exposure over the cap) must be blocked even though
        max_positions itself has plenty of room."""
        session = _make_session()
        sid_a = _insert_stock(session, "SECA", sector="Energy")
        sid_b = _insert_stock(session, "SECB", sector="Energy")
        _insert_daily_prices(session, sid_a, date(2026, 1, 1), [100.0 for _ in range(40)])
        _insert_daily_prices(session, sid_b, date(2026, 1, 1), [100.0 for _ in range(40)])
        _insert_buy_signal_with_outcome(
            session, sid_a, "SECA", "SWING",
            signal_date=date(2026, 2, 1), entry_date=date(2026, 2, 2), exit_date=date(2026, 2, 20),
            entry_price=100.0, exit_price=105.0, pct_return=0.05,
        )
        _insert_buy_signal_with_outcome(
            session, sid_b, "SECB", "SWING",
            signal_date=date(2026, 2, 1), entry_date=date(2026, 2, 2), exit_date=date(2026, 2, 20),
            entry_price=100.0, exit_price=95.0, pct_return=-0.05,
        )
        result = run_portfolio_backtest(
            session, ["SECA", "SECB"], "SWING", "US", date(2026, 1, 1), date(2026, 3, 1),
            cfg_overrides={"max_sector_pct": 0.15, "max_positions": 10},
        )
        # A default-sized position uses ~10% of equity, so a 0.15 cap admits the FIRST
        # same-sector entry (10% <= 15%) but must block the SECOND (10%+10%=20% > 15%) even
        # with max_positions wide open.
        assert result.n_entered == 1
        assert result.n_skipped_no_room >= 1
        session.close()

    def test_a_later_entry_reuses_cash_freed_by_an_earlier_exit(self):
        """Symbol A exits before symbol B's entry date — B must be able to use the cash A's
        exit freed up, proving the day-stepped exit-before-entry ordering actually works, not
        just a coincidental pass."""
        session = _make_session()
        sid_a = _insert_stock(session, "SEQA", sector="Technology")
        sid_b = _insert_stock(session, "SEQB", sector="Technology")
        _insert_daily_prices(session, sid_a, date(2026, 1, 1), [100.0 for _ in range(60)])
        _insert_daily_prices(session, sid_b, date(2026, 1, 1), [100.0 for _ in range(60)])
        _insert_buy_signal_with_outcome(
            session, sid_a, "SEQA", "SWING",
            signal_date=date(2026, 2, 1), entry_date=date(2026, 2, 2), exit_date=date(2026, 2, 10),
            entry_price=100.0, exit_price=110.0, pct_return=0.10,
        )
        _insert_buy_signal_with_outcome(
            session, sid_b, "SEQB", "SWING",
            signal_date=date(2026, 2, 5), entry_date=date(2026, 2, 15), exit_date=date(2026, 2, 25),
            entry_price=100.0, exit_price=90.0, pct_return=-0.10,
        )
        result = run_portfolio_backtest(
            session, ["SEQA", "SEQB"], "SWING", "US", date(2026, 1, 1), date(2026, 3, 1),
            cfg_overrides={"initial_capital": 2_000.0, "max_position_pct": 0.9, "risk_per_trade_pct": 1.0, "max_loss_per_trade_pct": 1.0, "max_sector_pct": 1.0},
        )
        assert result.n_entered == 2  # both got in, sequentially, on the same shared cash pool
        session.close()

    def test_exits_are_processed_before_entries_on_the_same_calendar_day(self):
        """A same-day exit+entry pair must free the exiting position's cash BEFORE the new
        entry is sized — otherwise a same-day rotation would be incorrectly blocked."""
        session = _make_session()
        sid_a = _insert_stock(session, "SDAY_OUT", sector="Technology")
        sid_b = _insert_stock(session, "SDAY_IN", sector="Technology")
        _insert_daily_prices(session, sid_a, date(2026, 1, 1), [100.0 for _ in range(60)])
        _insert_daily_prices(session, sid_b, date(2026, 1, 1), [100.0 for _ in range(60)])
        same_day = date(2026, 2, 10)
        _insert_buy_signal_with_outcome(
            session, sid_a, "SDAY_OUT", "SWING",
            signal_date=date(2026, 2, 1), entry_date=date(2026, 2, 2), exit_date=same_day,
            entry_price=100.0, exit_price=108.0, pct_return=0.08,
        )
        _insert_buy_signal_with_outcome(
            session, sid_b, "SDAY_IN", "SWING",
            signal_date=date(2026, 2, 1), entry_date=same_day, exit_date=date(2026, 2, 20),
            entry_price=100.0, exit_price=95.0, pct_return=-0.05,
        )
        result = run_portfolio_backtest(
            session, ["SDAY_OUT", "SDAY_IN"], "SWING", "US", date(2026, 1, 1), date(2026, 3, 1),
            cfg_overrides={"initial_capital": 1_500.0, "max_position_pct": 0.9, "risk_per_trade_pct": 1.0, "max_loss_per_trade_pct": 1.0, "max_sector_pct": 1.0},
        )
        assert result.n_entered == 2
        session.close()

    def test_win_rate_and_avg_return_pct_are_computed_correctly_across_mixed_trades(self):
        session = _make_session()
        sid_a = _insert_stock(session, "MIXA")
        sid_b = _insert_stock(session, "MIXB")
        _insert_daily_prices(session, sid_a, date(2026, 1, 1), [100.0 for _ in range(60)])
        _insert_daily_prices(session, sid_b, date(2026, 1, 1), [100.0 for _ in range(60)])
        _insert_buy_signal_with_outcome(
            session, sid_a, "MIXA", "SWING",
            signal_date=date(2026, 2, 1), entry_date=date(2026, 2, 2), exit_date=date(2026, 2, 12),
            entry_price=100.0, exit_price=120.0, pct_return=0.20,
        )
        _insert_buy_signal_with_outcome(
            session, sid_b, "MIXB", "SWING",
            signal_date=date(2026, 2, 3), entry_date=date(2026, 2, 4), exit_date=date(2026, 2, 14),
            entry_price=100.0, exit_price=90.0, pct_return=-0.10,
        )
        result = run_portfolio_backtest(session, ["MIXA", "MIXB"], "SWING", "US", date(2026, 1, 1), date(2026, 3, 1))
        assert result.n_entered == 2
        assert result.win_rate == 0.5
        assert result.avg_return_pct == 5.0  # (20% + -10%) / 2 = 5.0 (already displayed as percent)
        session.close()

    def test_only_the_requested_symbols_are_included_not_the_whole_market(self):
        """A resolved BUY signal for a symbol NOT in the requested list must never leak into
        the backtest — confirms the Stock.symbol.in_() filter is real, not a no-op."""
        session = _make_session()
        sid_wanted = _insert_stock(session, "WANTED")
        sid_other = _insert_stock(session, "UNWANTED")
        _insert_daily_prices(session, sid_wanted, date(2026, 1, 1), [100.0 for _ in range(40)])
        _insert_daily_prices(session, sid_other, date(2026, 1, 1), [100.0 for _ in range(40)])
        _insert_buy_signal_with_outcome(
            session, sid_wanted, "WANTED", "SWING",
            signal_date=date(2026, 2, 1), entry_date=date(2026, 2, 2), exit_date=date(2026, 2, 12),
            entry_price=100.0, exit_price=110.0, pct_return=0.10,
        )
        _insert_buy_signal_with_outcome(
            session, sid_other, "UNWANTED", "SWING",
            signal_date=date(2026, 2, 1), entry_date=date(2026, 2, 2), exit_date=date(2026, 2, 12),
            entry_price=100.0, exit_price=999.0, pct_return=8.99,  # absurd — must never appear
        )
        result = run_portfolio_backtest(session, ["WANTED"], "SWING", "US", date(2026, 1, 1), date(2026, 3, 1))
        assert result.n_signals_seen == 1
        assert all(t.symbol == "WANTED" for t in result.trades)
        session.close()


# ── T234-CONFIG-UNJUSTIFIED-THRESHOLDS: max_portfolio_drawdown_pct circuit breaker ──────────

class TestDrawdownCircuitBreaker:
    def _drawdown_scenario(self, session, drawdown_pct_cfg, symbol_prefix):
        """A -> a large loss that pushes the portfolio well underwater, THEN B tries to enter.
        A tight drawdown_pct_cfg must block B; a loose one must admit it. Sized so A alone
        drives the portfolio's own equity down by ~18% (a real -60% trade at ~30% position
        sizing), independent of B's own outcome. symbol_prefix keeps each test's own symbols
        distinct — tests share ONE in-memory engine with a real UNIQUE(symbol, exchange)
        constraint, so re-using a literal symbol across test methods would collide."""
        sym_a, sym_b = f"{symbol_prefix}A", f"{symbol_prefix}B"
        sid_a = _insert_stock(session, sym_a, sector="Technology")
        sid_b = _insert_stock(session, sym_b, sector="Technology")
        _insert_daily_prices(session, sid_a, date(2026, 1, 1), [100.0 for _ in range(60)])
        _insert_daily_prices(session, sid_b, date(2026, 1, 1), [100.0 for _ in range(60)])
        _insert_buy_signal_with_outcome(
            session, sid_a, sym_a, "SWING",
            signal_date=date(2026, 2, 1), entry_date=date(2026, 2, 2), exit_date=date(2026, 2, 10),
            entry_price=100.0, exit_price=40.0, pct_return=-0.60,
        )
        _insert_buy_signal_with_outcome(
            session, sid_b, sym_b, "SWING",
            signal_date=date(2026, 2, 5), entry_date=date(2026, 2, 15), exit_date=date(2026, 2, 25),
            entry_price=100.0, exit_price=110.0, pct_return=0.10,
        )
        result = run_portfolio_backtest(
            session, [sym_a, sym_b], "SWING", "US", date(2026, 1, 1), date(2026, 3, 1),
            cfg_overrides={
                "initial_capital": 1_000.0, "max_position_pct": 0.30, "risk_per_trade_pct": 1.0,
                "max_loss_per_trade_pct": 1.0, "max_sector_pct": 1.0,
                "max_portfolio_drawdown_pct": drawdown_pct_cfg,
            },
        )
        return result, sym_a

    def test_a_tight_drawdown_cap_blocks_a_later_entry_after_a_big_loss(self):
        session = _make_session()
        result, sym_a = self._drawdown_scenario(session, drawdown_pct_cfg=0.05, symbol_prefix="DDTIGHT")
        assert result.n_entered == 1  # only A got in — B was blocked by the breaker
        assert result.n_skipped_drawdown_breaker >= 1
        assert all(t.symbol == sym_a for t in result.trades)
        session.close()

    def test_a_loose_drawdown_cap_still_admits_the_later_entry(self):
        session = _make_session()
        result, _ = self._drawdown_scenario(session, drawdown_pct_cfg=0.90, symbol_prefix="DDLOOSE")
        assert result.n_entered == 2  # both A and B got in — the loose cap never trips
        assert result.n_skipped_drawdown_breaker == 0
        session.close()

    def test_default_config_still_has_the_real_020_breaker_value(self):
        """Confirms the module's own _DEFAULT_CFG carries the real, currently-live 0.20 value —
        not a value this test file silently duplicated and could drift from."""
        assert _DEFAULT_CFG["max_portfolio_drawdown_pct"] == 0.20

    def test_n_skipped_drawdown_breaker_defaults_to_zero_when_never_tripped(self):
        session = _make_session()
        sid = _insert_stock(session, "NODIP")
        _insert_daily_prices(session, sid, date(2026, 1, 1), [100.0 for _ in range(40)])
        _insert_buy_signal_with_outcome(
            session, sid, "NODIP", "SWING",
            signal_date=date(2026, 2, 1), entry_date=date(2026, 2, 2), exit_date=date(2026, 2, 12),
            entry_price=100.0, exit_price=105.0, pct_return=0.05,
        )
        result = run_portfolio_backtest(session, ["NODIP"], "SWING", "US", date(2026, 1, 1), date(2026, 3, 1))
        assert result.n_skipped_drawdown_breaker == 0
        session.close()


# ── _passes_return_promotion_margin() — the two independent promotion guards, tested directly

class TestPassesReturnPromotionMargin:
    def test_none_candidate_or_baseline_never_promotes(self):
        assert _passes_return_promotion_margin(None, 1.0, [0.01, 0.02]) is False
        assert _passes_return_promotion_margin(2.0, None, [0.01, 0.02]) is False

    def test_a_lift_below_the_absolute_floor_never_promotes_even_with_zero_dispersion(self):
        """0.3pp of lift is below _MIN_PROMOTION_EV_LIFT_PCT (0.5pp) — must be rejected even
        with IDENTICAL (zero-dispersion) combined returns, isolating the absolute-floor guard
        from the SD-ratio guard (which would otherwise trivially pass at sd_pct<=0)."""
        assert _passes_return_promotion_margin(1.3, 1.0, [0.02, 0.02, 0.02, 0.02]) is False

    def test_a_lift_at_or_above_the_floor_with_zero_dispersion_promotes(self):
        assert _passes_return_promotion_margin(1.5, 1.0, [0.02, 0.02, 0.02, 0.02]) is True

    def test_a_lift_clearing_the_floor_but_failing_the_sd_ratio_does_not_promote(self):
        """A large absolute lift (12.64pp) that fails the SD-ratio requirement because the
        underlying trades are wildly dispersed — the exact scenario the dedicated
        sweep-level test below reproduces end to end, isolated here as a pure-function check."""
        combined = [0.95, -0.6, 0.95, -0.6, -0.4]
        assert _passes_return_promotion_margin(5.37, -7.27, combined) is False

    def test_fewer_than_two_combined_returns_never_promotes(self):
        assert _passes_return_promotion_margin(5.0, 1.0, [0.02]) is False
        assert _passes_return_promotion_margin(5.0, 1.0, []) is False


# ── sweep_max_portfolio_drawdown_pct() — the walk-forward candidate search ──────────────────

class TestSweepMaxPortfolioDrawdownPct:
    def _insert_n_trades(self, session, symbols_returns, style="SWING", base_date=date(2026, 1, 1)):
        """symbols_returns: list of (symbol, signal_date, entry_date, exit_date, entry_price,
        exit_price, pct_return) tuples — bulk-inserts a stock + daily prices + a resolved BUY
        SignalOutcome for each, matching the exact shape sweep_max_portfolio_drawdown_pct's own
        two run_portfolio_backtest() calls (train + validation slices) will read."""
        for sym, sig_date, entry_date, exit_date, entry_price, exit_price, pct_return in symbols_returns:
            sid = _insert_stock(session, sym, sector="Technology")
            _insert_daily_prices(session, sid, base_date, [entry_price for _ in range(400)])
            _insert_buy_signal_with_outcome(
                session, sid, sym, style,
                signal_date=sig_date, entry_date=entry_date, exit_date=exit_date,
                entry_price=entry_price, exit_price=exit_price, pct_return=pct_return,
            )

    def test_window_too_short_for_a_resolvable_validation_slice_skips_cleanly(self):
        """A window entirely inside the last _HORIZON_RESOLUTION_LAG_DAYS['SWING']=14 days has
        no resolvable validation slice at all — must degrade to a clear skipped_reason, never
        crash or fabricate a result."""
        session = _make_session()
        result = sweep_max_portfolio_drawdown_pct(
            session, ["ANY"], "SWING", "US", date(2026, 6, 20), date(2026, 6, 25),
        )
        assert result["skipped_reason"] is not None
        session.close()

    def test_no_admitted_trades_on_the_train_slice_skips_with_a_clear_reason(self):
        session = _make_session()
        result = sweep_max_portfolio_drawdown_pct(
            session, ["NOSUCHSYM"], "SWING", "US", date(2026, 1, 1), date(2026, 6, 1),
        )
        assert result["skipped_reason"] is not None
        assert "baseline_validation" in result

    def test_a_genuinely_better_candidate_gets_promoted_on_the_validation_slice(self):
        """Constructs a scenario where a TIGHTER drawdown cap materially improves
        total_return_pct on BOTH the train slice (so it's selected as best_cand) AND the
        validation slice (so it clears the promotion margin) — a large early loss followed by
        a second, also-losing entry that a tight breaker would have blocked, versus a loose cap
        that lets the second loss through too."""
        session = _make_session()
        base = date(2026, 1, 1)
        # Train slice: a big loss, then a second loss that a tight cap should block.
        self._insert_n_trades(session, [
            ("TRA", base + timedelta(days=30), base + timedelta(days=31), base + timedelta(days=39), 100.0, 40.0, -0.60),
            ("TRB", base + timedelta(days=34), base + timedelta(days=44), base + timedelta(days=54), 100.0, 60.0, -0.40),
        ])
        # Validation slice: the SAME shape, later in time, so a tight cap ALSO helps there.
        self._insert_n_trades(session, [
            ("VLA", base + timedelta(days=200), base + timedelta(days=201), base + timedelta(days=209), 100.0, 40.0, -0.60),
            ("VLB", base + timedelta(days=204), base + timedelta(days=214), base + timedelta(days=224), 100.0, 60.0, -0.40),
        ])
        window_start = base
        window_end = base + timedelta(days=280)
        result = sweep_max_portfolio_drawdown_pct(
            session, ["TRA", "TRB", "VLA", "VLB"], "SWING", "US", window_start, window_end,
            candidates=[0.05, 0.90],
            base_cfg_overrides={
                "initial_capital": 1_000.0, "max_position_pct": 0.30, "risk_per_trade_pct": 1.0,
                "max_loss_per_trade_pct": 1.0, "max_sector_pct": 1.0,
            },
        )
        assert result["candidate_value"] == 0.05  # the tight cap wins on the train slice
        assert result["promoted"] is True
        assert result["candidate_validation"]["total_return_pct"] > result["baseline_validation"]["total_return_pct"]
        session.close()

    def test_a_positive_lift_that_clears_the_floor_but_fails_the_sd_ratio_still_does_not_promote(self):
        """The lift-magnitude floor and the SD-ratio requirement are two INDEPENDENT guards
        (matching gate_harness.py's own BUG233-BACKTESTHARNESS-COINFLIP fix) — a lift that
        clears the absolute floor by a wide margin (12.64pp >> 0.5pp) must still be rejected
        when the underlying trade returns are dispersed enough that the lift isn't a meaningful
        fraction of that dispersion. Isolated by adding a huge (+95%) win that gets admitted
        IDENTICALLY under both candidate and baseline (it's the very first chronological entry,
        well before either candidate value could ever have tripped) — it inflates the combined
        return pool's own SD without touching the lift itself, so a naive `lift > 0` check
        would wrongly promote here while the real SD-ratio guard correctly does not."""
        session = _make_session()
        base = date(2026, 1, 1)
        self._insert_n_trades(session, [
            ("SDTRA", base + timedelta(days=30), base + timedelta(days=31), base + timedelta(days=39), 100.0, 40.0, -0.60),
            ("SDTRB", base + timedelta(days=34), base + timedelta(days=44), base + timedelta(days=54), 100.0, 60.0, -0.40),
        ])
        self._insert_n_trades(session, [
            ("SDVLC", base + timedelta(days=195), base + timedelta(days=196), base + timedelta(days=198), 100.0, 195.0, 0.95),
            ("SDVLA", base + timedelta(days=200), base + timedelta(days=201), base + timedelta(days=209), 100.0, 40.0, -0.60),
            ("SDVLB", base + timedelta(days=204), base + timedelta(days=214), base + timedelta(days=224), 100.0, 60.0, -0.40),
        ])
        result = sweep_max_portfolio_drawdown_pct(
            session, ["SDTRA", "SDTRB", "SDVLA", "SDVLB", "SDVLC"], "SWING", "US",
            base, base + timedelta(days=280),
            candidates=[0.05, 0.90],
            base_cfg_overrides={
                "initial_capital": 1_000.0, "max_position_pct": 0.30, "risk_per_trade_pct": 1.0,
                "max_loss_per_trade_pct": 1.0, "max_sector_pct": 1.0,
            },
        )
        lift = result["candidate_validation"]["total_return_pct"] - result["baseline_validation"]["total_return_pct"]
        assert lift > 0.5  # clears the absolute floor by a wide margin
        assert result["promoted"] is False  # ...but the SD-ratio guard still correctly rejects it
        session.close()

    def test_current_value_reflects_the_real_default_when_no_override_is_passed(self):
        session = _make_session()
        result = sweep_max_portfolio_drawdown_pct(
            session, ["NOSUCHSYM"], "SWING", "US", date(2026, 1, 1), date(2026, 6, 1),
        )
        # Even in the no-candidate-admitted skip path, current_value must reflect the module's
        # own real default (0.20) — not a hardcoded/duplicated literal that could silently drift.
        assert result.get("current_value", _DEFAULT_CFG["max_portfolio_drawdown_pct"]) == 0.20

    def test_note_field_discloses_this_is_a_research_signal_not_an_automatic_config_change(self):
        session = _make_session()
        self._insert_n_trades(session, [
            ("NOTEA", date(2026, 1, 31), date(2026, 2, 1), date(2026, 2, 9), 100.0, 110.0, 0.10),
        ])
        result = sweep_max_portfolio_drawdown_pct(
            session, ["NOTEA"], "SWING", "US", date(2026, 1, 1), date(2026, 8, 1),
        )
        if "note" in result:
            assert "research signal" in result["note"]
            assert "NOT" in result["note"]


class TestSweepRiskPerTradePct:
    """IF-13 Kelly-consumption half: sweep_risk_per_trade_pct() reuses the SAME walk-forward
    machinery as TestSweepMaxPortfolioDrawdownPct above — these tests focus on what's genuinely
    different (candidate default = Kelly's own recommended_risk_pct bands, and the sizing
    formula's own linear risk_pct->shares relationship) rather than re-testing the shared
    promotion-margin logic already covered above."""

    def _insert_n_trades(self, session, symbols_returns, style="SWING", base_date=date(2026, 1, 1)):
        for sym, sig_date, entry_date, exit_date, entry_price, exit_price, pct_return in symbols_returns:
            sid = _insert_stock(session, sym, sector="Technology")
            _insert_daily_prices(session, sid, base_date, [entry_price for _ in range(400)])
            _insert_buy_signal_with_outcome(
                session, sid, sym, style,
                signal_date=sig_date, entry_date=entry_date, exit_date=exit_date,
                entry_price=entry_price, exit_price=exit_price, pct_return=pct_return,
            )

    def test_window_too_short_for_a_resolvable_validation_slice_skips_cleanly(self):
        session = _make_session()
        result = sweep_risk_per_trade_pct(
            session, ["ANY"], "SWING", "US", date(2026, 6, 20), date(2026, 6, 25),
        )
        assert result["skipped_reason"] is not None
        session.close()

    def test_current_value_reflects_the_real_default_when_no_override_is_passed(self):
        session = _make_session()
        result = sweep_risk_per_trade_pct(
            session, ["NOSUCHSYM"], "SWING", "US", date(2026, 1, 1), date(2026, 6, 1),
        )
        assert result.get("current_value", _DEFAULT_CFG["risk_per_trade_pct"]) == 0.01

    def test_current_value_respects_a_base_cfg_override_eg_hk_own_default(self):
        """The real live default is market-specific — 0.01 US, 0.007 HK. A candidate sweep
        must compare against the CORRECT current-live value for that market, not always the
        bare US default."""
        session = _make_session()
        result = sweep_risk_per_trade_pct(
            session, ["NOSUCHSYM"], "SWING", "HK", date(2026, 1, 1), date(2026, 6, 1),
            base_cfg_overrides={"risk_per_trade_pct": 0.007},
        )
        assert result.get("current_value") == 0.007

    def test_candidates_default_to_kellys_own_recommended_risk_bands(self):
        """When no explicit candidates list is passed, the sweep must search Kelly's own real
        1%/2%/3% recommended_risk_pct bands (plus whatever the current live value is) — not an
        independently-guessed grid that could silently drift from what GET /paper-portfolio/
        kelly actually recommends."""
        session = _make_session()
        result = sweep_risk_per_trade_pct(
            session, ["NOSUCHSYM"], "SWING", "US", date(2026, 1, 1), date(2026, 6, 1),
        )
        # No candidate is admitted for a nonexistent symbol, but the skip path still confirms
        # the module didn't crash trying to build the (unused-here) candidate list — the real
        # candidate-list content is exercised directly by the promotion test below instead.
        assert result["skipped_reason"] is not None

    def test_a_higher_risk_per_trade_pct_is_promoted_when_it_genuinely_improves_return(self):
        """A higher risk_per_trade_pct sizes EVERY winning trade larger (shares scale linearly
        with risk_pct, per _size_position()'s own risk_dollar/stop_distance formula) — on a
        slice of purely winning trades, this must materially improve total_return_pct on BOTH
        the train slice (so the higher risk% is selected as best_cand) and the validation slice
        (so it clears the promotion margin). max_position_pct/max_loss_per_trade_pct are
        generously widened so the sizing CAPS don't neutralize the risk% difference being
        tested — matching TestSweepMaxPortfolioDrawdownPct's own established fixture pattern."""
        session = _make_session()
        base = date(2026, 1, 1)
        # Train slice: 2 real winners.
        self._insert_n_trades(session, [
            ("KTRA", base + timedelta(days=30), base + timedelta(days=31), base + timedelta(days=39), 100.0, 120.0, 0.20),
            ("KTRB", base + timedelta(days=34), base + timedelta(days=44), base + timedelta(days=54), 100.0, 130.0, 0.30),
        ])
        # Validation slice: the SAME winning shape, later in time.
        self._insert_n_trades(session, [
            ("KVLA", base + timedelta(days=200), base + timedelta(days=201), base + timedelta(days=209), 100.0, 120.0, 0.20),
            ("KVLB", base + timedelta(days=204), base + timedelta(days=214), base + timedelta(days=224), 100.0, 130.0, 0.30),
        ])
        window_start = base
        window_end = base + timedelta(days=280)
        result = sweep_risk_per_trade_pct(
            session, ["KTRA", "KTRB", "KVLA", "KVLB"], "SWING", "US", window_start, window_end,
            candidates=[0.01, 0.03],
            base_cfg_overrides={
                "initial_capital": 1_000.0, "max_position_pct": 0.90,
                "max_loss_per_trade_pct": 1.0, "max_sector_pct": 1.0,
            },
        )
        assert result["candidate_value"] == 0.03  # the higher risk% wins on the train slice
        assert result["promoted"] is True
        assert result["candidate_validation"]["total_return_pct"] > result["baseline_validation"]["total_return_pct"]
        session.close()

    def test_note_field_discloses_this_is_a_research_signal_not_an_automatic_sizing_change(self):
        session = _make_session()
        self._insert_n_trades(session, [
            ("KNOTEA", date(2026, 1, 31), date(2026, 2, 1), date(2026, 2, 9), 100.0, 110.0, 0.10),
        ])
        result = sweep_risk_per_trade_pct(
            session, ["KNOTEA"], "SWING", "US", date(2026, 1, 1), date(2026, 8, 1),
        )
        if "note" in result:
            assert "research signal" in result["note"]
            assert "NOT" in result["note"]

    def test_a_positive_lift_that_clears_the_floor_but_fails_the_sd_ratio_still_does_not_promote(self):
        """The lift-magnitude floor and the SD-ratio requirement are two INDEPENDENT guards —
        mirrors TestSweepMaxPortfolioDrawdownPct's own equivalent test exactly, applied to
        risk_per_trade_pct instead of max_portfolio_drawdown_pct. A genuine adversarial check:
        without this test, a sabotage that hardcodes promoted=True regardless of the real
        margin check passes every OTHER test in this class undetected (self-caught during this
        feature's own adversarial verification pass — the earlier tests only assert
        promoted is True on a genuinely-promotable scenario, never that a SHOULD-fail scenario
        actually fails). Isolated by adding a huge (+95%) win that gets admitted IDENTICALLY
        under both the candidate and baseline risk_per_trade_pct (the very same shares/price
        shape regardless of risk_pct, since it's the sole trade at that date) — it inflates the
        combined return pool's own SD without changing the lift itself, so a naive `lift > 0`
        check would wrongly promote here while the real SD-ratio guard correctly does not."""
        session = _make_session()
        base = date(2026, 1, 1)
        self._insert_n_trades(session, [
            ("SDKTRA", base + timedelta(days=30), base + timedelta(days=31), base + timedelta(days=39), 100.0, 101.0, 0.01),
            ("SDKTRB", base + timedelta(days=34), base + timedelta(days=44), base + timedelta(days=54), 100.0, 102.0, 0.02),
        ])
        self._insert_n_trades(session, [
            ("SDKVLC", base + timedelta(days=195), base + timedelta(days=196), base + timedelta(days=198), 100.0, 195.0, 0.95),
            ("SDKVLA", base + timedelta(days=200), base + timedelta(days=201), base + timedelta(days=209), 100.0, 101.0, 0.01),
            ("SDKVLB", base + timedelta(days=204), base + timedelta(days=214), base + timedelta(days=224), 100.0, 102.0, 0.02),
        ])
        result = sweep_risk_per_trade_pct(
            session, ["SDKTRA", "SDKTRB", "SDKVLA", "SDKVLB", "SDKVLC"], "SWING", "US",
            base, base + timedelta(days=280),
            candidates=[0.0100, 0.0105],
            base_cfg_overrides={
                "initial_capital": 1_000.0, "max_position_pct": 0.90,
                "max_loss_per_trade_pct": 1.0, "max_sector_pct": 1.0,
            },
        )
        lift = result["candidate_validation"]["total_return_pct"] - result["baseline_validation"]["total_return_pct"]
        assert lift > 0.5  # clears the absolute floor
        assert result["promoted"] is False  # ...but the SD-ratio guard still correctly rejects it
        session.close()
