"""Tests for T260-BEARISH-PUTS-WATCHLIST's _bearish_puts_watch_candidates() (scheduler.py) —
the cross-check that filters the gamma-unwind scan's puts-dominant, 3-5-day subset and grades
it "high_conviction" only when at least 2 of 3 real, independent signals (SWING AI Signal,
RSI, trend-vs-SMA50) agree with the puts-heavy options read.

scheduler.py can't be imported directly in this test environment (apscheduler import-chain —
matches test_squeeze_game_plan.py's own documented constraint). Its real source is extracted
via exec() and run against a real in-memory SQLite session built from the real
shared/db/models.py, matching test_squeeze_game_plan.py's established technique exactly.
"""
import sys

_STUBBED_MODULES = ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql", "db")
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util
import pathlib

from sqlalchemy import create_engine, select as _real_select
from sqlalchemy.orm import Session

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_bpw", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_bpw"] = _models
_spec.loader.exec_module(_models)

_ENGINE = create_engine("sqlite:///:memory:")
_models.Base.metadata.create_all(
    _ENGINE, tables=[_models.Stock.__table__, _models.Signal.__table__],
)

for _mod, _stub in _saved_stubs.items():
    if _stub is not None:
        sys.modules[_mod] = _stub
    else:
        sys.modules.pop(_mod, None)

Stock = _models.Stock
Signal = _models.Signal
SignalHorizon = _models.SignalHorizon
SignalType = _models.SignalType
Market = _models.Market
Exchange = _models.Exchange

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


def _extract_bearish_puts_watch_candidates():
    """Pulls the real module-level constants AND _bearish_puts_watch_candidates()'s real source
    out of scheduler.py and exec()s them together against real sqlalchemy `select` and the real
    shared models — so this test always tracks whatever thresholds are actually defined, rather
    than a hardcoded duplicate that could silently drift from the real values.

    The function's own body does `from db import Signal, SignalHorizon, SignalType, Stock` — a
    bare (non-relative) import that WOULD resolve against the real `db` module if it were on
    sys.path, but this test deliberately keeps `db` popped/restored around only the model-
    loading step above, not the whole test session — so this import is stripped and the real
    classes injected directly, matching test_squeeze_game_plan.py's identical fix for
    _squeeze_game_plan()'s own relative import."""
    const_start = _scheduler_source.index("_BEARISH_WATCH_MIN_DAYS_TO_EXPIRY")
    const_end = _scheduler_source.index("\n\n\ndef _bearish_puts_watch_candidates(")
    const_source = _scheduler_source[const_start:const_end]

    start = _scheduler_source.index("def _bearish_puts_watch_candidates(")
    end = _scheduler_source.index("\n\n\n_SQUEEZE_WATCH_LOCK_KEY", start)
    func_source = _scheduler_source[start:end]
    import_line = "    from db import Signal, SignalHorizon, SignalType, Stock\n"
    assert import_line in func_source, "expected import line not found — has the source changed?"
    func_source = func_source.replace(import_line, "")

    namespace = {
        "select": _real_select,
        "Stock": Stock,
        "Signal": Signal,
        "SignalHorizon": SignalHorizon,
        "SignalType": SignalType,
    }
    exec(compile(const_source, "<_bearish_watch_constants>", "exec"), namespace)
    exec(compile(func_source, "<_bearish_puts_watch_candidates>", "exec"), namespace)
    return namespace["_bearish_puts_watch_candidates"]


def _make_session():
    return Session(_ENGINE)


def _clear_tables(session):
    session.query(Signal).delete()
    session.query(Stock).delete()
    session.commit()


_next_signal_id = [0]


def _new_signal_id() -> int:
    _next_signal_id[0] += 1
    return _next_signal_id[0]


def _gamma_candidate(symbol, dominant_side="puts", days_to_expiry=4, concentration_pct=62.0):
    return {
        "symbol": symbol, "expiry": "2026-08-09", "days_to_expiry": days_to_expiry,
        "dominant_side": dominant_side, "concentration_pct": concentration_pct,
        "total_oi_near_money": 1200, "price": 42.0,
    }


# ── Filtering: dominant_side + days-to-expiry window ─────────────────────────────────────────

def test_calls_dominant_candidates_are_excluded():
    fn = _extract_bearish_puts_watch_candidates()
    with _make_session() as session:
        _clear_tables(session)
        result = fn(session, {"AAA": _gamma_candidate("AAA", dominant_side="calls")})
    assert result == []


def test_days_to_expiry_below_3_is_excluded():
    fn = _extract_bearish_puts_watch_candidates()
    with _make_session() as session:
        _clear_tables(session)
        result = fn(session, {"AAA": _gamma_candidate("AAA", days_to_expiry=2)})
    assert result == []


def test_days_to_expiry_above_5_is_excluded():
    fn = _extract_bearish_puts_watch_candidates()
    with _make_session() as session:
        _clear_tables(session)
        result = fn(session, {"AAA": _gamma_candidate("AAA", days_to_expiry=6)})
    assert result == []


def test_days_to_expiry_within_3_to_5_is_included():
    fn = _extract_bearish_puts_watch_candidates()
    with _make_session() as session:
        _clear_tables(session)
        for dte in (3, 4, 5):
            result = fn(session, {"AAA": _gamma_candidate("AAA", days_to_expiry=dte)})
            assert len(result) == 1, f"days_to_expiry={dte} should be included"


# ── high_conviction cross-check — the real property this function exists for ────────────────

def test_zero_agreeing_signals_is_not_high_conviction():
    """A bullish SWING signal (BUY), RSI above 50, and trading above SMA50 — none of the 3
    real signals agree with the puts-heavy read — must NOT be high_conviction, even though
    the options data alone looked bearish."""
    fn = _extract_bearish_puts_watch_candidates()
    with _make_session() as session:
        _clear_tables(session)
        stock = Stock(symbol="BULL", market=Market.US, exchange=Exchange.NASDAQ, name="Bull Co")
        session.add(stock)
        session.commit()
        session.add(Signal(
            id=_new_signal_id(), stock_id=stock.id, signal=SignalType.BUY,
            horizon=SignalHorizon.SWING, confidence=70.0,
            reasons={"rsi": 65.0, "trend_above_sma50": True},
        ))
        session.commit()

        result = fn(session, {"BULL": _gamma_candidate("BULL")})
        assert len(result) == 1
        assert result[0]["high_conviction"] is False
        assert result[0]["agreeing_signals"] == 0


def test_all_three_signals_agreeing_is_high_conviction():
    fn = _extract_bearish_puts_watch_candidates()
    with _make_session() as session:
        _clear_tables(session)
        stock = Stock(symbol="BEAR", market=Market.US, exchange=Exchange.NASDAQ, name="Bear Co")
        session.add(stock)
        session.commit()
        session.add(Signal(
            id=_new_signal_id(), stock_id=stock.id, signal=SignalType.SELL,
            horizon=SignalHorizon.SWING, confidence=70.0,
            reasons={"rsi": 35.0, "trend_above_sma50": False},
        ))
        session.commit()

        result = fn(session, {"BEAR": _gamma_candidate("BEAR")})
        assert len(result) == 1
        assert result[0]["high_conviction"] is True
        assert result[0]["agreeing_signals"] == 3
        assert result[0]["ai_signal"] == "SELL"
        assert result[0]["rsi"] == 35.0
        assert result[0]["below_sma50"] is True


def test_exactly_two_of_three_agreeing_is_high_conviction():
    """The documented floor is >= 2 of 3, not all 3 — HOLD (not SELL) + RSI<50 + still above
    SMA50 should be exactly 2 agreeing signals, and must clear the bar."""
    fn = _extract_bearish_puts_watch_candidates()
    with _make_session() as session:
        _clear_tables(session)
        stock = Stock(symbol="MIXED", market=Market.US, exchange=Exchange.NASDAQ, name="Mixed Co")
        session.add(stock)
        session.commit()
        session.add(Signal(
            id=_new_signal_id(), stock_id=stock.id, signal=SignalType.HOLD,
            horizon=SignalHorizon.SWING, confidence=50.0,
            reasons={"rsi": 40.0, "trend_above_sma50": True},
        ))
        session.commit()

        result = fn(session, {"MIXED": _gamma_candidate("MIXED")})
        assert result[0]["agreeing_signals"] == 2
        assert result[0]["high_conviction"] is True


def test_only_one_of_three_agreeing_is_not_high_conviction():
    fn = _extract_bearish_puts_watch_candidates()
    with _make_session() as session:
        _clear_tables(session)
        stock = Stock(symbol="ONE", market=Market.US, exchange=Exchange.NASDAQ, name="One Co")
        session.add(stock)
        session.commit()
        session.add(Signal(
            id=_new_signal_id(), stock_id=stock.id, signal=SignalType.SELL,
            horizon=SignalHorizon.SWING, confidence=50.0,
            reasons={"rsi": 65.0, "trend_above_sma50": True},
        ))
        session.commit()

        result = fn(session, {"ONE": _gamma_candidate("ONE")})
        assert result[0]["agreeing_signals"] == 1
        assert result[0]["high_conviction"] is False


def test_no_signal_on_file_degrades_to_zero_agreeing_not_a_crash():
    fn = _extract_bearish_puts_watch_candidates()
    with _make_session() as session:
        _clear_tables(session)
        stock = Stock(symbol="NOSIG", market=Market.US, exchange=Exchange.NASDAQ, name="No Signal Co")
        session.add(stock)
        session.commit()

        result = fn(session, {"NOSIG": _gamma_candidate("NOSIG")})
        assert result[0]["high_conviction"] is False
        assert result[0]["agreeing_signals"] == 0
        assert result[0]["ai_signal"] is None


def test_unknown_symbol_degrades_gracefully_not_a_crash():
    fn = _extract_bearish_puts_watch_candidates()
    with _make_session() as session:
        _clear_tables(session)
        result = fn(session, {"DOESNOTEXIST": _gamma_candidate("DOESNOTEXIST")})
    assert len(result) == 1
    assert result[0]["high_conviction"] is False


def test_original_candidate_fields_are_preserved_in_the_result():
    fn = _extract_bearish_puts_watch_candidates()
    with _make_session() as session:
        _clear_tables(session)
        cand = _gamma_candidate("XYZ", concentration_pct=71.5)
        result = fn(session, {"XYZ": cand})
    assert result[0]["concentration_pct"] == 71.5
    assert result[0]["total_oi_near_money"] == 1200
    assert result[0]["price"] == 42.0
