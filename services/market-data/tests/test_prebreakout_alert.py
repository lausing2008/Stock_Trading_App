"""Tests for T264-SHORTSQUEEZE-PREBREAKOUT — _record_prebreakout_alert_outcome() and
evaluate_prebreakout_alert_outcomes() in scheduler.py.

Direct user request: "predict the short sell not able to recover and send me the alert before
it starts to breakout... using daily volume and trading data along with the option call and
sell data expiry."

scheduler.py can't be imported directly in this test environment — conftest.py stubs
`sqlalchemy` itself as a MagicMock (needed so ingestion.py-adjacent modules don't need a real
Postgres driver at import time), which breaks any real ORM query construction. This test pops
the stubbed sqlalchemy/db modules from sys.modules BEFORE importing anything else, so the REAL
sqlalchemy + REAL shared/db/models.py load for this file specifically — matching the
established technique in test_squeeze_alert_outcomes.py/test_broker_position_sync.py. The real
source of each function under test is then extracted and exec()'d against this real session.
"""
import sys

_STUBBED_MODULES = ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql", "db")
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util
import pathlib
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test"] = _models
_spec.loader.exec_module(_models)

_ENGINE = create_engine("sqlite:///:memory:")
_models.Base.metadata.create_all(
    _ENGINE,
    tables=[_models.Stock.__table__, _models.Price.__table__, _models.PreBreakoutAlertOutcome.__table__],
)

# PreBreakoutAlertOutcome.id (like SqueezeAlertOutcome.id elsewhere in this app) is a
# BigInteger primary key, which doesn't get SQLite's implicit INTEGER PRIMARY KEY
# autoincrement (a real Postgres sequence handles this in production). The real code under
# test constructs a row with no id at all, exactly as it does in production where Postgres
# fills it in — a before_insert listener scoped to ONLY this test engine assigns one
# automatically, so the function's real, unmodified source can be exercised as-is.
_autoincrement_counter = [0]


@event.listens_for(_models.PreBreakoutAlertOutcome, "before_insert")
def _assign_test_id(mapper, connection, target):
    if target.id is None:
        _autoincrement_counter[0] += 1
        target.id = _autoincrement_counter[0]

for _mod, _stub in _saved_stubs.items():
    if _stub is not None:
        sys.modules[_mod] = _stub
    else:
        sys.modules.pop(_mod, None)

Stock = _models.Stock
Price = _models.Price
PreBreakoutAlertOutcome = _models.PreBreakoutAlertOutcome
Market = _models.Market
TimeFrame = _models.TimeFrame

_SCHEDULER_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_SCHEDULER_SOURCE = _SCHEDULER_PATH.read_text()

_next_id = [1000]


def _new_id() -> int:
    _next_id[0] += 1
    return _next_id[0]


def _make_session():
    session = Session(_ENGINE)
    for table in (PreBreakoutAlertOutcome.__table__, Price.__table__, Stock.__table__):
        session.execute(table.delete())
    session.commit()
    return session


def _make_stock(session, symbol="AAPL"):
    st = Stock(id=_new_id(), symbol=symbol, name=symbol, market=Market.US, exchange="NASDAQ", sector="Tech", active=True)
    session.add(st)
    session.commit()
    return st


def _make_price(session, stock_id, ts_date: date, close: float):
    session.add(Price(
        id=_new_id(), stock_id=stock_id, ts=datetime.combine(ts_date, datetime.min.time()),
        timeframe=TimeFrame.D1, open=close, high=close, low=close, close=close, volume=1000.0,
    ))
    session.commit()


def _extract_record_prebreakout_alert_outcome():
    start = _SCHEDULER_SOURCE.index("def _record_prebreakout_alert_outcome(")
    end = _SCHEDULER_SOURCE.index("\n\n\n_GAMMA_UNWIND_LOCK_KEY", start)
    body = _SCHEDULER_SOURCE[start:end]
    fake_log = MagicMock()
    namespace = {"select": select, "PreBreakoutAlertOutcome": PreBreakoutAlertOutcome, "date": date, "log": fake_log}
    exec(body, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_record_prebreakout_alert_outcome"], fake_log


class _CtxSession:
    def __enter__(self):
        self._s = Session(_ENGINE)
        return self._s

    def __exit__(self, *exc):
        self._s.close()
        return False


def _extract_squeeze_outcome_lookup_price():
    """evaluate_prebreakout_alert_outcomes() reuses the REAL _squeeze_outcome_lookup_price()
    (a genuine shared implementation, per that function's own docstring — see the scheduler.py
    module's design note) rather than a duplicate — extracted here the same way
    test_squeeze_alert_outcomes.py already does, so both test files exercise the identical
    real helper. Extraction starts at _SQUEEZE_OUTCOME_CENSOR_GRACE_DAYS's own definition (not
    bare `def _squeeze_outcome_lookup_price(`), since the function's body references that
    constant directly — a narrower extraction boundary silently produced a real NameError
    inside the function's own per-row try/except (caught during test debugging, not by pyflakes
    — exec()'d source has no static analysis), which this wider boundary fixes."""
    start = _SCHEDULER_SOURCE.index("_SQUEEZE_OUTCOME_CENSOR_GRACE_DAYS = ")
    end = _SCHEDULER_SOURCE.index("\n\ndef evaluate_squeeze_alert_outcomes(", start)
    body = _SCHEDULER_SOURCE[start:end]
    namespace = {"bisect": __import__("bisect"), "date": date}
    exec(body, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_squeeze_outcome_lookup_price"]


def _extract_evaluate_prebreakout_alert_outcomes():
    lookup = _extract_squeeze_outcome_lookup_price()
    win_hurdle_start = _SCHEDULER_SOURCE.index("_SQUEEZE_OUTCOME_WIN_HURDLE_PCT = ")
    win_hurdle_end = _SCHEDULER_SOURCE.index("\n", win_hurdle_start)
    windows_start = _SCHEDULER_SOURCE.index("_SQUEEZE_OUTCOME_WINDOWS = ")
    windows_end = _SCHEDULER_SOURCE.index("\n", windows_start)
    const_namespace: dict = {}
    exec(_SCHEDULER_SOURCE[win_hurdle_start:win_hurdle_end], const_namespace)  # noqa: S102
    exec(_SCHEDULER_SOURCE[windows_start:windows_end], const_namespace)  # noqa: S102

    start = _SCHEDULER_SOURCE.index('_PREBREAKOUT_OUTCOME_EVAL_LOCK_KEY = "stockai:lock:evaluate_prebreakout_alert_outcomes"')
    end = _SCHEDULER_SOURCE.index("\n\n\n_VALUE_AREA_COMPUTE_LOCK_KEY", start)
    body = _SCHEDULER_SOURCE[start:end]
    _fake_redis = MagicMock()
    _fake_redis.set.return_value = True
    namespace = {
        "select": select, "Price": Price, "TimeFrame": TimeFrame, "PreBreakoutAlertOutcome": PreBreakoutAlertOutcome,
        "SessionLocal": lambda: _CtxSession(),
        "date": date, "datetime": datetime, "timedelta": timedelta, "timezone": timezone,
        "time": __import__("time"),
        "log": MagicMock(),
        "_get_redis": lambda: _fake_redis,
        "_record_job_status": MagicMock(),
        "_squeeze_outcome_lookup_price": lookup,
        "_SQUEEZE_OUTCOME_WIN_HURDLE_PCT": const_namespace["_SQUEEZE_OUTCOME_WIN_HURDLE_PCT"],
        "_SQUEEZE_OUTCOME_WINDOWS": const_namespace["_SQUEEZE_OUTCOME_WINDOWS"],
    }
    exec(body, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["evaluate_prebreakout_alert_outcomes"]


# ── _record_prebreakout_alert_outcome() ──────────────────────────────────────────────────────

def test_record_creates_a_new_row_on_first_fire():
    session = _make_session()
    st = _make_stock(session, "AAPL")
    record, _ = _extract_record_prebreakout_alert_outcome()

    record(session, st.id, "AAPL", 150.0, 22.5, 0.1, 0.15, True, 1.3)

    rows = session.execute(select(PreBreakoutAlertOutcome)).scalars().all()
    assert len(rows) == 1
    assert rows[0].symbol == "AAPL"
    assert rows[0].alert_price == 150.0
    assert rows[0].rule_gate_passed is True
    assert rows[0].short_percent_of_float == 22.5
    assert rows[0].bb_width_pctile == 0.1
    assert rows[0].atr_pctile == 0.15
    assert rows[0].volume_dried_up is True
    assert rows[0].options_modifier_applied is True
    assert rows[0].options_cp_ratio == 1.3
    assert rows[0].fired_date == date.today()


def test_record_options_modifier_applied_is_false_when_no_cp_ratio_given():
    session = _make_session()
    st = _make_stock(session, "AAPL")
    record, _ = _extract_record_prebreakout_alert_outcome()

    record(session, st.id, "AAPL", 150.0, 22.5, 0.1, 0.15, True, None)

    row = session.execute(select(PreBreakoutAlertOutcome)).scalar_one()
    assert row.options_modifier_applied is False
    assert row.options_cp_ratio is None


def test_record_is_a_noop_on_a_second_call_same_day():
    """A clean skip logs nothing; a caught IntegrityError would — matching the exact
    discipline test_squeeze_alert_outcomes.py's own equivalent test already established (see
    that file's docstring for the "still passes after sabotage" red flag this guards against)."""
    session = _make_session()
    st = _make_stock(session, "AAPL")
    record, fake_log = _extract_record_prebreakout_alert_outcome()

    record(session, st.id, "AAPL", 150.0, 22.5, 0.1, 0.15, True, None)
    record(session, st.id, "AAPL", 155.0, 23.0, 0.12, 0.18, False, 1.1)

    rows = session.execute(select(PreBreakoutAlertOutcome)).scalars().all()
    assert len(rows) == 1
    assert rows[0].alert_price == 150.0
    fake_log.warning.assert_not_called()


# ── evaluate_prebreakout_alert_outcomes() ────────────────────────────────────────────────────

def test_evaluate_fills_entry_price_and_scores_a_bullish_win():
    session = _make_session()
    st = _make_stock(session, "AAPL")
    fired = date(2026, 1, 1)
    _make_price(session, st.id, fired + timedelta(days=1), 100.0)
    _make_price(session, st.id, fired + timedelta(days=11), 108.0)
    session.add(PreBreakoutAlertOutcome(id=_new_id(), stock_id=st.id, symbol="AAPL", fired_date=fired, alert_price=99.0, rule_gate_passed=True))
    session.commit()

    evaluate = _extract_evaluate_prebreakout_alert_outcomes()
    evaluate()

    with Session(_ENGINE) as check:
        row = check.execute(select(PreBreakoutAlertOutcome)).scalar_one()
        assert row.entry_price == 100.0
        assert row.return_10d == 0.08
        assert row.is_correct_10d is True
        assert row.evaluated_at is not None


def test_evaluate_scores_a_loss_when_price_falls():
    session = _make_session()
    st = _make_stock(session, "AAPL")
    fired = date(2026, 1, 1)
    _make_price(session, st.id, fired + timedelta(days=1), 100.0)
    _make_price(session, st.id, fired + timedelta(days=11), 92.0)
    session.add(PreBreakoutAlertOutcome(id=_new_id(), stock_id=st.id, symbol="AAPL", fired_date=fired, alert_price=99.0, rule_gate_passed=True))
    session.commit()

    evaluate = _extract_evaluate_prebreakout_alert_outcomes()
    evaluate()

    with Session(_ENGINE) as check:
        row = check.execute(select(PreBreakoutAlertOutcome)).scalar_one()
        assert row.is_correct_10d is False


def test_evaluate_leaves_a_window_open_when_it_hasnt_closed_yet():
    """A real price row exists exactly AT the (still-future) 5d target date — simulating what
    a data backfill or clock skew could produce. The `target > today` guard, not the
    `_squeeze_outcome_lookup_price(...) is None` fallback, is what must stop this from being
    scored early: without it, the lookup would happily find and use that future row. This is
    a deliberately stronger fixture than one relying only on "no row exists yet" — that version
    let sabotaging the `target > today` guard alone pass every test, since the lookup's own
    None-on-no-match path masked the missing guard identically."""
    session = _make_session()
    st = _make_stock(session, "AAPL")
    fired = date.today() - timedelta(days=3)
    entry_date = fired + timedelta(days=1)
    _make_price(session, st.id, entry_date, 100.0)
    _make_price(session, st.id, entry_date + timedelta(days=5), 120.0)
    session.add(PreBreakoutAlertOutcome(id=_new_id(), stock_id=st.id, symbol="AAPL", fired_date=fired, alert_price=99.0, rule_gate_passed=True))
    session.commit()

    evaluate = _extract_evaluate_prebreakout_alert_outcomes()
    evaluate()

    with Session(_ENGINE) as check:
        row = check.execute(select(PreBreakoutAlertOutcome)).scalar_one()
        assert row.entry_price == 100.0
        assert row.return_5d is None
        assert row.return_10d is None
        assert row.return_20d is None
