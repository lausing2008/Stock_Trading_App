"""Tests for IF-02: signal_age_decay() (outcomes.py) — the genuinely INVERSE axis from the
pre-existing GET /alpha_decay (which holds entry_date/entry_price FIXED and varies the exit
day). This groups already-resolved BUY outcomes by ENTRY LAG — (entry_date - signal_date).days —
to answer "how much edge is lost by acting N days late?"

Unlike alpha_decay(), this function needs no Price rows at all — it reads the already-stored
pct_return directly from each SignalOutcome row, so fixtures are simpler than
test_alpha_decay_no_profitable_hold.py's own (which needs a synthetic price series per outcome).

outcomes.py can't be imported directly in this test environment (its import chain pulls in
common.jwt_auth) — signal_age_decay()'s real source is extracted via exec() and run against a
real in-memory SQLite session + the real shared/db/models.py, matching
test_alpha_decay_no_profitable_hold.py's established technique for this exact file.
"""
import importlib.util
import pathlib
import sys
from datetime import date, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_signal_age_decay", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_signal_age_decay"] = _models
_spec.loader.exec_module(_models)

Stock = _models.Stock
Market = _models.Market
Exchange = _models.Exchange
SignalOutcome = _models.SignalOutcome
SignalHorizon = _models.SignalHorizon
Base = _models.Base

_OUTCOMES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "analytics.py"
_OUTCOMES_SOURCE = _OUTCOMES_PATH.read_text()

_SIGNAL_AGE_LAG_DAYS = [0, 1, 2, 3, 4]


def _extract_signal_age_decay():
    """Pulls signal_age_decay()'s real body out of outcomes.py, exec()s it against real
    sqlalchemy/models with the FastAPI decorator machinery replaced by plain defaults — same
    technique test_alpha_decay_no_profitable_hold.py already established for the sibling
    function in the same file."""
    from fastapi import HTTPException

    start = _OUTCOMES_SOURCE.index("def signal_age_decay(")
    end = _OUTCOMES_SOURCE.index('\n@router.get("/information_coefficient")', start)
    raw = _OUTCOMES_SOURCE[start:end]
    sig_end = raw.index("):\n") + 3
    body = raw[sig_end:]
    func_source = (
        "def signal_age_decay(horizon='SWING', lookback_days=365, session=None):\n"
        + body
    )
    namespace = {
        "select": select,
        "SignalOutcome": SignalOutcome,
        "SignalHorizon": SignalHorizon,
        "date": date,
        "timedelta": timedelta,
        "HTTPException": HTTPException,
        "_SIGNAL_AGE_LAG_DAYS": _SIGNAL_AGE_LAG_DAYS,
        "_SIGNAL_AGE_MIN_N": 5,
    }
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["signal_age_decay"]


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Stock.__table__, SignalOutcome.__table__])
    return Session(engine)


def _seed_stocks(session, n):
    for i in range(1, n + 1):
        session.add(Stock(id=i, symbol=f"TEST{i}", market=Market.US, exchange=Exchange.NASDAQ, name="Test Co"))
    session.commit()


def _add_outcome(session, outcome_id, stock_id, signal_date, lag_days, pct_return_pct):
    """pct_return_pct is a plain percentage (e.g. 3.0 for +3%) — stored as the fraction the
    real model uses (0.03), matching this repo's own established pct_return-is-a-fraction
    convention (see signals_shared.py's _retro_ev_for() comment for the same point)."""
    session.add(SignalOutcome(
        id=outcome_id, signal_id=outcome_id, stock_id=stock_id, symbol=f"TEST{stock_id}",
        horizon=SignalHorizon.SWING, signal_direction="BUY", signal_date=signal_date,
        confidence=60.0, entry_date=signal_date + timedelta(days=lag_days),
        entry_price=100.0, pct_return=pct_return_pct / 100.0,
    ))


def test_zero_outcomes_returns_the_empty_shape():
    session = _make_session()
    _seed_stocks(session, 1)
    fn = _extract_signal_age_decay()
    result = fn(horizon="SWING", lookback_days=365, session=session)
    assert result["signal_count"] == 0
    assert result["curve"] == []
    assert result["fastest_lag_avg_return_pct"] is None
    assert result["slowest_lag_avg_return_pct"] is None


def test_groups_by_entry_lag_not_by_a_fixed_exit_day():
    """The core distinguishing property vs. alpha_decay(): grouping is by (entry_date -
    signal_date).days, computed directly from each row's own stored dates — not by a fixed
    offset from a constant entry point."""
    session = _make_session()
    n = 6
    _seed_stocks(session, n * 2)  # lag=1 and lag=3 buckets, n each
    base = date(2026, 1, 1)
    oid = 1
    for i in range(n):
        _add_outcome(session, oid, oid, base, lag_days=1, pct_return_pct=2.0)
        oid += 1
    for i in range(n):
        _add_outcome(session, oid, oid, base, lag_days=3, pct_return_pct=-1.0)
        oid += 1
    session.commit()

    fn = _extract_signal_age_decay()
    result = fn(horizon="SWING", lookback_days=365, session=session)

    lag1 = next(c for c in result["curve"] if c["lag_days"] == 1)
    lag3 = next(c for c in result["curve"] if c["lag_days"] == 3)
    assert lag1["avg_return_pct"] == 2.0
    assert lag1["n"] == n
    assert lag3["avg_return_pct"] == -1.0
    assert lag3["n"] == n


def test_a_lag_below_the_min_n_floor_is_not_eligible_and_reports_none_average():
    """A lag bucket with fewer than _SIGNAL_AGE_MIN_N resolved outcomes must report
    avg_return_pct=None and eligible=False — never a thin, unreliable number presented as real."""
    session = _make_session()
    n_thin = 2  # < _SIGNAL_AGE_MIN_N (5)
    _seed_stocks(session, n_thin)
    base = date(2026, 1, 1)
    for i in range(n_thin):
        _add_outcome(session, i + 1, i + 1, base, lag_days=2, pct_return_pct=50.0)
    session.commit()

    fn = _extract_signal_age_decay()
    result = fn(horizon="SWING", lookback_days=365, session=session)

    lag2 = next(c for c in result["curve"] if c["lag_days"] == 2)
    assert lag2["n"] == n_thin
    assert lag2["eligible"] is False
    assert lag2["avg_return_pct"] is None


def test_a_negative_lag_data_anomaly_is_excluded_entirely_not_just_kept_out_of_lag_zero():
    """entry_date before signal_date is a real data anomaly (should never happen in practice)
    — it must be TRULY excluded (never crash, never appear anywhere in the response), not
    merely kept out of the lag=0 bucket. A dict-membership check alone (lag not in
    _SIGNAL_AGE_LAG_DAYS) would already keep a negative lag out of lag_returns[0] by accident
    — routing it into overflow_n instead — so this test asserts overflow_n stays 0 too,
    proving the anomaly is genuinely dropped, not silently miscounted as a real lag>=5 row."""
    session = _make_session()
    _seed_stocks(session, 6)
    base = date(2026, 1, 5)
    for i in range(5):
        _add_outcome(session, i + 1, i + 1, base, lag_days=0, pct_return_pct=1.0)
    # a genuine anomaly: entry BEFORE signal (negative lag) — must not crash or pollute anything
    session.add(SignalOutcome(
        id=6, signal_id=6, stock_id=6, symbol="TEST6",
        horizon=SignalHorizon.SWING, signal_direction="BUY", signal_date=base,
        confidence=60.0, entry_date=base - timedelta(days=1),
        entry_price=100.0, pct_return=0.99,
    ))
    session.commit()

    fn = _extract_signal_age_decay()
    result = fn(horizon="SWING", lookback_days=365, session=session)

    lag0 = next(c for c in result["curve"] if c["lag_days"] == 0)
    assert lag0["n"] == 5  # the anomalous row must NOT be counted here
    assert lag0["avg_return_pct"] == 1.0  # unpolluted by the anomaly's 99% return
    assert result["overflow_n"] == 0  # the real, load-bearing check — not silently in overflow either


def test_overflow_lag_5_plus_is_counted_but_not_bucketed_individually():
    """A real lag>=5 row is genuinely rare in production (per this session's own re-verification
    against real data) — it must still be counted in overflow_n, not silently dropped, but
    never given its own misleadingly-precise bucket at this sample size."""
    session = _make_session()
    _seed_stocks(session, 7)
    base = date(2026, 1, 1)
    for i in range(6):
        _add_outcome(session, i + 1, i + 1, base, lag_days=1, pct_return_pct=1.0)
    _add_outcome(session, 7, 7, base, lag_days=7, pct_return_pct=1.0)
    session.commit()

    fn = _extract_signal_age_decay()
    result = fn(horizon="SWING", lookback_days=365, session=session)

    assert result["overflow_n"] == 1
    assert all(c["lag_days"] != 7 for c in result["curve"])  # no ad-hoc bucket for lag=7


def test_fastest_and_slowest_lag_use_only_eligible_buckets():
    """fastest_lag_avg_return_pct/slowest_lag_avg_return_pct must reflect the eligible
    (>= min-n) ends of the curve, not a thin/ineligible bucket even if it sits at lag=0 or the
    highest configured lag."""
    session = _make_session()
    _seed_stocks(session, 13)
    base = date(2026, 1, 1)
    oid = 1
    # lag=0: thin, ineligible (2 rows, < _SIGNAL_AGE_MIN_N=5)
    for _ in range(2):
        _add_outcome(session, oid, oid, base, lag_days=0, pct_return_pct=99.0)
        oid += 1
    # lag=1: well-sampled, eligible (6 rows)
    for _ in range(6):
        _add_outcome(session, oid, oid, base, lag_days=1, pct_return_pct=2.0)
        oid += 1
    # lag=4: well-sampled, eligible (exactly 5 rows -> at the floor)
    for _ in range(5):
        _add_outcome(session, oid, oid, base, lag_days=4, pct_return_pct=-3.0)
        oid += 1
    session.commit()

    fn = _extract_signal_age_decay()
    result = fn(horizon="SWING", lookback_days=365, session=session)

    # lag=0 (thin) must be skipped; fastest eligible is lag=1, slowest eligible is lag=4
    assert result["fastest_lag_avg_return_pct"] == 2.0
    assert result["slowest_lag_avg_return_pct"] == -3.0


def test_only_buy_direction_included_sell_outcomes_excluded():
    """This endpoint is deliberately BUY-only (matching alpha_decay()'s own scope) — a SELL
    row must never leak into the curve."""
    session = _make_session()
    _seed_stocks(session, 6)
    base = date(2026, 1, 1)
    for i in range(5):
        _add_outcome(session, i + 1, i + 1, base, lag_days=1, pct_return_pct=2.0)
    session.add(SignalOutcome(
        id=6, signal_id=6, stock_id=6, symbol="TEST6",
        horizon=SignalHorizon.SWING, signal_direction="SELL", signal_date=base,
        confidence=60.0, entry_date=base + timedelta(days=1),
        entry_price=100.0, pct_return=-0.50,
    ))
    session.commit()

    fn = _extract_signal_age_decay()
    result = fn(horizon="SWING", lookback_days=365, session=session)

    lag1 = next(c for c in result["curve"] if c["lag_days"] == 1)
    assert lag1["n"] == 5  # the SELL row excluded
    assert result["signal_count"] == 5
