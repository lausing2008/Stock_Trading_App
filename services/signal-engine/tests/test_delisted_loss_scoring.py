"""Tests for T232-OC6 (revisited 2026-07-28) — score confirmed delistings as real losses
instead of silently censoring them out of win-rate math.

Background: evaluate_signal_outcomes() (services/signal-engine/src/api/outcomes.py) already
censors a signal outcome (skip_reason="no_exit_price", is_correct=NULL) whenever the hold
window closes and, after a 10-day ingestion-lag grace period, still no exit price can be
found. This EXCLUDES the row from every win-rate/calibration query (all of which filter
is_correct IS NOT NULL), which silently omits the worst-case BUY outcomes (a real delisting)
from calibration instead of penalizing them — a documented, deliberate deferral in
docs/KNOWN_LIMITATIONS.md's T232-OC6 entry, blocked specifically on Stock.delisted becoming a
real, reliably-populated signal (which aud14-survivorship, 2026-07-27, finally made true via a
2-consecutive-run yfinance-exception-based detector).

This fix: when the censoring branch fires AND Stock.delisted is True AND the signal was a BUY,
write is_correct=False (a real loss) with a DISTINCT skip_reason ("delisted_loss") instead of
the ordinary NULL/"no_exit_price" — auditable and reversible, and automatically counted by
every existing is_correct.is_not(None) filter with zero downstream query changes. SELL signals
are deliberately NOT scored on a delisting (the direction is ambiguous — an acquisition at a
premium would also delist the stock but wouldn't validate a SELL thesis), so they keep the
prior, unchanged censored/NULL behavior.

evaluate_signal_outcomes() can't be driven end-to-end in this test environment (250+ lines of
FastAPI/Depends/real-Postgres-shaped query construction) — following
test_evaluate_outcomes_nested_savepoint.py's established convention exactly: source-text
extraction for structural/shape assertions against the real production code, plus a real
in-memory-SQLite model to directly exercise the classification logic in isolation.
"""
import importlib.util
import pathlib
import sys
from datetime import date, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_delisted", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_delisted"] = _models
_spec.loader.exec_module(_models)

Signal = _models.Signal
SignalOutcome = _models.SignalOutcome
SignalHorizon = _models.SignalHorizon
SignalType = _models.SignalType
Stock = _models.Stock
Market = _models.Market
Exchange = _models.Exchange
Base = _models.Base

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "outcomes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()

# T233-ARCH-INSERVICE-SPLITS-2: outcomes_summary() (read-only) moved to analytics.py while
# evaluate_signal_outcomes() (a write route) stayed in outcomes.py — this file's tests need
# both sources.
_ANALYTICS_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "analytics.py"
_ANALYTICS_SOURCE = _ANALYTICS_PATH.read_text()


def _function_body():
    start = _ROUTES_SOURCE.index("def evaluate_signal_outcomes(")
    # T233-ARCH-INSERVICE-SPLITS-2: evaluate_signal_outcomes() and gate_backtest() no longer
    # sit adjacent in outcomes.py (gate_backtest moved to analytics.py) — the T232-SIG10-SELLGATE
    # comment header is the real, stable marker for where evaluate_signal_outcomes() ends today.
    end = _ROUTES_SOURCE.index("# ── T232-SIG10-SELLGATE", start)
    return _ROUTES_SOURCE[start:end]


_body = _function_body()


# ── Source-text structural checks ─────────────────────────────────────────────────────────

def test_pending_signals_query_selects_stock_delisted_via_the_existing_join():
    """The fix must reuse the ALREADY-EXISTING Stock join (no new query) — Stock.delisted
    selected alongside Stock.symbol in the same select() call."""
    assert "select(Signal, Stock.symbol, Stock.delisted)" in _body


def test_loop_unpacks_is_delisted_from_the_extended_select():
    assert "for sig, symbol, is_delisted in pending_signals:" in _body


def test_confirmed_delisting_requires_both_delisted_flag_and_buy_direction():
    """The classification must require BOTH is_delisted AND signal == BUY — a SELL signal on
    a delisted stock must NOT be auto-scored as correct/incorrect (direction is ambiguous)."""
    start = _body.index("_is_confirmed_delisting = bool(is_delisted)")
    line_end = _body.index("\n", start)
    line = _body[start:line_end]
    assert "is_delisted" in line
    assert "SignalType.BUY" in line


def test_is_correct_set_false_only_for_confirmed_delisting_else_none():
    start = _body.index("is_correct=(False if _is_confirmed_delisting else None)")
    assert start != -1


def test_skip_reason_distinguishes_delisted_loss_from_ordinary_no_exit_price():
    start = _body.index('skip_reason=("delisted_loss" if _is_confirmed_delisting else "no_exit_price")')
    assert start != -1


def test_outcomes_summary_censored_count_excludes_delisted_loss_rows():
    """The censored-count query in outcomes_summary() must filter skip_reason == "no_exit_price"
    specifically, NOT skip_reason.is_not(None) — otherwise a delisted_loss row (now correctly
    scored, not excluded from win-rate math) would be double-reported as also "censored"."""
    start = _ANALYTICS_SOURCE.index("censored_q = select(func.count())")
    end = _ANALYTICS_SOURCE.index("\n    )", start)
    censored_block = _ANALYTICS_SOURCE[start:end]
    assert 'SignalOutcome.skip_reason == "no_exit_price"' in censored_block
    assert "skip_reason.is_not(None)" not in censored_block


# ── Behavioral checks against a real in-memory SQLite model ──────────────────────────────

def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine, tables=[Stock.__table__, Signal.__table__, SignalOutcome.__table__]
    )
    return Session(engine)


def _classify(is_delisted: bool, signal_type) -> tuple[bool | None, str]:
    """Mirrors the exact classification expression added to evaluate_signal_outcomes()'s
    censored branch — kept in sync via the source-text checks above, which assert this exact
    expression shape exists in the real function."""
    is_confirmed_delisting = bool(is_delisted) and signal_type == SignalType.BUY
    is_correct = False if is_confirmed_delisting else None
    skip_reason = "delisted_loss" if is_confirmed_delisting else "no_exit_price"
    return is_correct, skip_reason


def test_confirmed_delisted_buy_scores_as_a_real_loss():
    is_correct, skip_reason = _classify(is_delisted=True, signal_type=SignalType.BUY)
    assert is_correct is False
    assert skip_reason == "delisted_loss"


def test_delisted_sell_stays_censored_not_auto_scored():
    """A SELL signal on a delisted stock is genuinely ambiguous (could be an unrelated
    acquisition at a premium, which wouldn't validate the SELL thesis) — must NOT be scored."""
    is_correct, skip_reason = _classify(is_delisted=True, signal_type=SignalType.SELL)
    assert is_correct is None
    assert skip_reason == "no_exit_price"


def test_non_delisted_buy_with_no_exit_price_stays_censored():
    """The ordinary case this fix must NOT change: a genuine unknown price gap (halt, an
    ingestion hole with no confirmed delisting) stays censored/NULL, exactly as before."""
    is_correct, skip_reason = _classify(is_delisted=False, signal_type=SignalType.BUY)
    assert is_correct is None
    assert skip_reason == "no_exit_price"


def test_delisted_loss_row_persists_and_is_queryable_as_a_loss():
    """End-to-end proof against the REAL SignalOutcome model: a row written with
    is_correct=False, skip_reason='delisted_loss' round-trips correctly and is picked up by
    the exact is_correct.is_not(None) filter every win-rate query in this file already uses —
    zero downstream query changes needed."""
    session = _make_session()
    session.add(Stock(id=1, symbol="DELISTED", market=Market.US, exchange=Exchange.NASDAQ,
                       name="Delisted Co", delisted=True))
    session.commit()

    session.add(SignalOutcome(
        id=1, signal_id=1, stock_id=1, symbol="DELISTED",
        horizon=SignalHorizon.SWING, signal_direction="BUY",
        signal_date=date(2026, 1, 1), confidence=50.0,
        entry_date=date(2026, 1, 2), entry_price=10.0,
        is_correct=False, skip_reason="delisted_loss",
    ))
    session.commit()

    from sqlalchemy import select as _select
    rows = session.execute(
        _select(SignalOutcome).where(SignalOutcome.is_correct.is_not(None))
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].is_correct is False
    assert rows[0].skip_reason == "delisted_loss"


def test_ordinary_censored_row_still_excluded_from_is_correct_filter():
    """Contrast case: an ordinary censored row (is_correct=NULL, skip_reason='no_exit_price')
    must still be excluded from the is_correct.is_not(None) filter, exactly as before this
    fix — confirming the fix only changes behavior for confirmed delistings."""
    session = _make_session()
    session.add(Stock(id=1, symbol="HALTED", market=Market.US, exchange=Exchange.NASDAQ,
                       name="Halted Co", delisted=False))
    session.commit()

    session.add(SignalOutcome(
        id=1, signal_id=1, stock_id=1, symbol="HALTED",
        horizon=SignalHorizon.SWING, signal_direction="BUY",
        signal_date=date(2026, 1, 1), confidence=50.0,
        entry_date=date(2026, 1, 2), entry_price=10.0,
        is_correct=None, skip_reason="no_exit_price",
    ))
    session.commit()

    from sqlalchemy import select as _select
    rows = session.execute(
        _select(SignalOutcome).where(SignalOutcome.is_correct.is_not(None))
    ).scalars().all()
    assert len(rows) == 0
