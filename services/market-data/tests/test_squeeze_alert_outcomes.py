"""Tests for T264-SQUEEZEALERT-PERFORMANCE — _record_squeeze_alert_outcome(),
_squeeze_outcome_lookup_price(), and evaluate_squeeze_alert_outcomes() in scheduler.py, plus
squeeze_alert_performance() in admin.py.

Direct user request: "design a page under Admin to measure the option sell and short squeeze
performance and win rates if I buy from the signal, the first email alert."

scheduler.py and admin.py can't be imported directly in this test environment — conftest.py
stubs `sqlalchemy` itself as a MagicMock (needed so ingestion.py-adjacent modules don't need a
real Postgres driver at import time), which breaks any real ORM query construction. This test
pops the stubbed sqlalchemy/db modules from sys.modules BEFORE importing anything else, so the
REAL sqlalchemy + the REAL shared/db/models.py load for this file specifically — matching the
established technique in test_broker_position_sync.py/test_correlation_preentry.py. The real
source of each function under test is then extracted and exec()'d against this real session, so
these tests exercise the real logic, not a hand-copied duplicate.
"""
import sys

_STUBBED_MODULES = ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql", "db")
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util
import pathlib
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
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
    tables=[
        _models.Stock.__table__, _models.Price.__table__, _models.SqueezeAlertOutcome.__table__,
        _models.FundamentalsSnapshot.__table__,
    ],
)

# SqueezeAlertOutcome.id (like Price.id/SignalOutcome.id elsewhere in this app) is a
# BigInteger primary key, which doesn't get SQLite's implicit INTEGER PRIMARY KEY
# autoincrement (a real Postgres sequence handles this in production). Every OTHER test in
# this file that constructs a SqueezeAlertOutcome directly assigns id=_new_id() explicitly —
# but _record_squeeze_alert_outcome() itself (the real code under test) constructs one with
# no id at all, exactly as it does in production where Postgres fills it in. A before_insert
# listener scoped to ONLY this test engine assigns one automatically, so the function's real,
# unmodified source can be exercised as-is rather than requiring a test-only code change.
_autoincrement_counter = [0]


@event.listens_for(_models.SqueezeAlertOutcome, "before_insert")
def _assign_test_id(mapper, connection, target):
    if target.id is None:
        _autoincrement_counter[0] += 1
        target.id = _autoincrement_counter[0]

# Restore every stub now — this file's own module-level names already hold real, working
# references; later-collected test files must see the ORIGINAL stubbed sys.modules state.
for _mod, _stub in _saved_stubs.items():
    if _stub is not None:
        sys.modules[_mod] = _stub
    else:
        sys.modules.pop(_mod, None)

Stock = _models.Stock
Price = _models.Price
SqueezeAlertOutcome = _models.SqueezeAlertOutcome
FundamentalsSnapshot = _models.FundamentalsSnapshot
Market = _models.Market
TimeFrame = _models.TimeFrame

_SCHEDULER_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_SCHEDULER_SOURCE = _SCHEDULER_PATH.read_text()
_ADMIN_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "admin.py"
_ADMIN_SOURCE = _ADMIN_PATH.read_text()

_next_id = [1000]


def _new_id() -> int:
    """SqueezeAlertOutcome.id (like Price.id/SignalOutcome.id elsewhere in this app) is a
    BigInteger primary key, which doesn't get SQLite's implicit autoincrement — test fixtures
    must assign id explicitly (a real Postgres sequence handles this in production)."""
    _next_id[0] += 1
    return _next_id[0]


def _make_session():
    session = Session(_ENGINE)
    for table in (SqueezeAlertOutcome.__table__, Price.__table__, FundamentalsSnapshot.__table__, Stock.__table__):
        session.execute(table.delete())
    session.commit()
    return session


def _make_stock(session, symbol="AAPL"):
    st = Stock(id=_new_id(), symbol=symbol, name=symbol, market=Market.US, exchange="NASDAQ", sector="Tech", active=True)
    session.add(st)
    session.commit()
    return st


def _make_snapshot(session, symbol: str, snapshot_date: date, short_pct_of_float: float):
    """short_percent_of_float is stored as a FRACTION (e.g. 0.20 for 20%), matching
    check_short_squeeze_alerts()'s own `spf * 100 < _SQUEEZE_MIN_SHORT_FLOAT` comparison."""
    session.add(FundamentalsSnapshot(id=_new_id(), symbol=symbol, snapshot_date=snapshot_date, short_percent_of_float=short_pct_of_float))
    session.commit()


def _make_price(session, stock_id, ts_date: date, close: float):
    session.add(Price(
        id=_new_id(), stock_id=stock_id, ts=datetime.combine(ts_date, datetime.min.time()),
        timeframe=TimeFrame.D1, open=close, high=close, low=close, close=close, volume=1000.0,
    ))
    session.commit()


def _extract_record_squeeze_alert_outcome():
    start = _SCHEDULER_SOURCE.index("def _record_squeeze_alert_outcome(")
    end = _SCHEDULER_SOURCE.index("\n\ndef check_short_squeeze_alerts(", start)
    body = _SCHEDULER_SOURCE[start:end]
    fake_log = MagicMock()
    namespace = {"select": select, "Stock": Stock, "SqueezeAlertOutcome": SqueezeAlertOutcome, "date": date, "log": fake_log}
    exec(body, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_record_squeeze_alert_outcome"], fake_log


def _extract_evaluate_squeeze_alert_outcomes():
    """Extracts the constants + _squeeze_outcome_lookup_price + evaluate_squeeze_alert_outcomes
    together, since the function depends on both the constants and the helper. _get_redis and
    _record_job_status are stubbed (Redis lock / job-status bookkeeping, not logic under test)."""
    start = _SCHEDULER_SOURCE.index('_SQUEEZE_OUTCOME_EVAL_LOCK_KEY = "stockai:lock:evaluate_squeeze_alert_outcomes"')
    end = _SCHEDULER_SOURCE.index("\n\n\n_VALUE_AREA_COMPUTE_LOCK_KEY", start)
    body = _SCHEDULER_SOURCE[start:end]
    _fake_redis = MagicMock()
    _fake_redis.set.return_value = True
    namespace = {
        "select": select, "bisect": __import__("bisect"),
        "Price": Price, "TimeFrame": TimeFrame, "SqueezeAlertOutcome": SqueezeAlertOutcome,
        "SessionLocal": lambda: _CtxSession(),
        "date": date, "datetime": datetime, "timedelta": timedelta, "timezone": timezone,
        "time": __import__("time"),
        "log": MagicMock(),
        "_get_redis": lambda: _fake_redis,
        "_record_job_status": MagicMock(),
    }
    exec(body, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["evaluate_squeeze_alert_outcomes"], namespace["_squeeze_outcome_lookup_price"]


class _CtxSession:
    """evaluate_squeeze_alert_outcomes() opens its own `with SessionLocal() as session:` block
    — this wraps the ONE shared _make_session() so the function's real commit()/rollback()
    calls land on the same in-memory DB the test itself set up rows in."""
    def __enter__(self):
        self._s = Session(_ENGINE)
        return self._s

    def __exit__(self, *exc):
        self._s.close()
        return False


def _extract_squeeze_alert_backtest():
    """squeeze_alert_backtest() (admin.py) does `from ..services.scheduler import
    _squeeze_outcome_lookup_price, _SQUEEZE_OUTCOME_WIN_HURDLE_PCT, _SQUEEZE_OUTCOME_WINDOWS,
    _SQUEEZE_MIN_SHORT_FLOAT, _SQUEEZE_MIN_INTRADAY_MOVE_PCT` at call time — rather than
    stubbing those 5 names with test-chosen values (which would defeat the whole point of the
    endpoint's own claim that it can never silently drift from the live alert's real scoring/
    thresholds), this extracts the REAL scheduler.py source for all 5 first and feeds them into
    the namespace, so a future threshold change in scheduler.py is automatically reflected here
    too — exactly like production's own lazy import would pick it up.
    """
    _, _lookup = _extract_evaluate_squeeze_alert_outcomes()
    const_start = _SCHEDULER_SOURCE.index("_SQUEEZE_MIN_SHORT_FLOAT = ")
    const_end = _SCHEDULER_SOURCE.index("\n", _SCHEDULER_SOURCE.index("_SQUEEZE_MIN_INTRADAY_MOVE_PCT = ", const_start))
    const_body = _SCHEDULER_SOURCE[const_start:const_end]
    const_namespace: dict = {}
    exec(const_body, const_namespace)  # noqa: S102 — isolated eval of real source
    win_hurdle_start = _SCHEDULER_SOURCE.index("_SQUEEZE_OUTCOME_WIN_HURDLE_PCT = ")
    win_hurdle_end = _SCHEDULER_SOURCE.index("\n", win_hurdle_start)
    windows_start = _SCHEDULER_SOURCE.index("_SQUEEZE_OUTCOME_WINDOWS = ")
    windows_end = _SCHEDULER_SOURCE.index("\n", windows_start)
    exec(_SCHEDULER_SOURCE[win_hurdle_start:win_hurdle_end], const_namespace)  # noqa: S102
    exec(_SCHEDULER_SOURCE[windows_start:windows_end], const_namespace)  # noqa: S102

    func_start = _ADMIN_SOURCE.index("def squeeze_alert_backtest(")
    end = _ADMIN_SOURCE.index("\n\n\n@router.get(\"/options-flow-alert-backtest\")", func_start)
    # Skip the real function's own `from datetime import ...` / `from ..services.scheduler
    # import ...` local imports — this test injects the SAME real values via the namespace
    # instead (see the docstring above), so re-running those actual import statements here
    # would raise a real ImportError (no package context in an exec()'d string).
    signature_end = _ADMIN_SOURCE.index("):\n", func_start) + len("):\n")
    body_start = _ADMIN_SOURCE.index("\n    cutoff = date.today()", func_start)
    body = _ADMIN_SOURCE[func_start:signature_end] + _ADMIN_SOURCE[body_start:end]
    namespace = {
        "__name__": __name__,
        "select": select, "date": date, "timedelta": timedelta,
        "FundamentalsSnapshot": FundamentalsSnapshot, "Stock": Stock, "Price": Price, "TimeFrame": TimeFrame,
        "Query": lambda default, **kw: default, "Depends": lambda *a, **kw: None,
        "get_admin_user": None, "get_session": None, "User": object, "Session": Session,
        "_squeeze_outcome_lookup_price": _lookup,
        "_SQUEEZE_OUTCOME_WIN_HURDLE_PCT": const_namespace["_SQUEEZE_OUTCOME_WIN_HURDLE_PCT"],
        "_SQUEEZE_OUTCOME_WINDOWS": const_namespace["_SQUEEZE_OUTCOME_WINDOWS"],
        "_SQUEEZE_MIN_SHORT_FLOAT": const_namespace["_SQUEEZE_MIN_SHORT_FLOAT"],
        "_SQUEEZE_MIN_INTRADAY_MOVE_PCT": const_namespace["_SQUEEZE_MIN_INTRADAY_MOVE_PCT"],
    }
    exec(body, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["squeeze_alert_backtest"]


# ── _record_squeeze_alert_outcome() ──────────────────────────────────────────────────────────

def test_record_creates_a_new_row_on_first_fire():
    session = _make_session()
    _make_stock(session, "AAPL")
    record, _ = _extract_record_squeeze_alert_outcome()

    record(session, "short_squeeze", "AAPL", 150.0, 22.5)

    rows = session.execute(select(SqueezeAlertOutcome)).scalars().all()
    assert len(rows) == 1
    assert rows[0].alert_type == "short_squeeze"
    assert rows[0].symbol == "AAPL"
    assert rows[0].alert_price == 150.0
    assert rows[0].qualifying_metric == 22.5
    assert rows[0].fired_date == date.today()


def test_record_is_a_noop_on_a_second_call_same_day_same_type():
    """A cleanly-skipped re-fire must be a genuine no-op via the existence CHECK — not merely
    "the row count still happens to be 1 because the DB's own unique constraint caught a
    duplicate insert attempt and the function's fail-open except swallowed the resulting
    IntegrityError." Both would produce the same row count, but only the real existence-check
    path avoids a wasted DB round-trip and a spurious record_failed warning on every single
    cycle a symbol stays a candidate (this alert loop runs every minute)."""
    session = _make_session()
    _make_stock(session, "AAPL")
    record, fake_log = _extract_record_squeeze_alert_outcome()

    record(session, "short_squeeze", "AAPL", 150.0, 22.5)
    record(session, "short_squeeze", "AAPL", 155.0, 23.0)  # a later cycle same day, different price

    rows = session.execute(select(SqueezeAlertOutcome)).scalars().all()
    assert len(rows) == 1
    assert rows[0].alert_price == 150.0  # the FIRST alert's price, not overwritten by the later one
    fake_log.warning.assert_not_called()  # a clean skip logs nothing; a caught IntegrityError would


def test_record_creates_a_separate_row_for_a_different_alert_type_same_symbol_same_day():
    session = _make_session()
    _make_stock(session, "AAPL")
    record, _ = _extract_record_squeeze_alert_outcome()

    record(session, "short_squeeze", "AAPL", 150.0, 22.5)
    record(session, "gamma_unwind_puts", "AAPL", 150.0, 60.0)

    rows = session.execute(select(SqueezeAlertOutcome)).scalars().all()
    assert len(rows) == 2
    assert {r.alert_type for r in rows} == {"short_squeeze", "gamma_unwind_puts"}


def test_record_is_fail_open_when_the_symbol_has_no_stock_row():
    session = _make_session()
    record, _ = _extract_record_squeeze_alert_outcome()

    record(session, "short_squeeze", "NOSUCHSYMBOL", 150.0, 22.5)  # must not raise

    rows = session.execute(select(SqueezeAlertOutcome)).scalars().all()
    assert len(rows) == 0


# ── _squeeze_outcome_lookup_price() ──────────────────────────────────────────────────────────

def test_lookup_price_finds_the_first_bar_on_or_after():
    _, lookup = _extract_evaluate_squeeze_alert_outcomes()
    bucket = [(date(2026, 1, 1), 100.0), (date(2026, 1, 3), 105.0), (date(2026, 1, 6), 110.0)]
    result = lookup(bucket, date(2026, 1, 2))
    assert result == (date(2026, 1, 3), 105.0)


def test_lookup_price_exact_match():
    _, lookup = _extract_evaluate_squeeze_alert_outcomes()
    bucket = [(date(2026, 1, 1), 100.0), (date(2026, 1, 3), 105.0)]
    assert lookup(bucket, date(2026, 1, 3)) == (date(2026, 1, 3), 105.0)


def test_lookup_price_returns_none_when_nothing_on_or_after():
    _, lookup = _extract_evaluate_squeeze_alert_outcomes()
    bucket = [(date(2026, 1, 1), 100.0)]
    assert lookup(bucket, date(2026, 1, 5)) is None


def test_lookup_price_returns_none_when_the_found_bar_is_too_far_past_the_grace_window():
    """A long ingestion gap that later resumes must never be silently treated as a normal,
    timely price — the exact AUD261-CENSORING-NEVER-FIRED reasoning this helper mirrors."""
    _, lookup = _extract_evaluate_squeeze_alert_outcomes()
    bucket = [(date(2026, 1, 1), 100.0), (date(2026, 3, 1), 130.0)]  # a ~2-month gap
    assert lookup(bucket, date(2026, 1, 5)) is None


def test_lookup_price_empty_bucket_is_none():
    _, lookup = _extract_evaluate_squeeze_alert_outcomes()
    assert lookup([], date(2026, 1, 1)) is None


# ── evaluate_squeeze_alert_outcomes() ────────────────────────────────────────────────────────

def test_evaluate_fills_entry_price_and_a_bullish_win():
    session = _make_session()
    st = _make_stock(session, "AAPL")
    fired = date(2026, 1, 1)
    _make_price(session, st.id, fired + timedelta(days=1), 100.0)   # T+1 entry
    _make_price(session, st.id, fired + timedelta(days=2), 101.0)   # 1d close, up 1%
    _make_price(session, st.id, fired + timedelta(days=3), 102.0)   # 2d close, up 2%
    _make_price(session, st.id, fired + timedelta(days=4), 103.0)   # 3d close, up 3%
    _make_price(session, st.id, fired + timedelta(days=6), 106.0)   # 5d close, up 6%
    _make_price(session, st.id, fired + timedelta(days=11), 108.0)  # 10d close, up 8%
    _make_price(session, st.id, fired + timedelta(days=21), 112.0)  # 20d close, up 12%
    session.add(SqueezeAlertOutcome(id=_new_id(), alert_type="short_squeeze", stock_id=st.id, symbol="AAPL", fired_date=fired, alert_price=99.0))
    session.commit()

    evaluate, _ = _extract_evaluate_squeeze_alert_outcomes()
    evaluate()

    with Session(_ENGINE) as check:
        row = check.execute(select(SqueezeAlertOutcome)).scalar_one()
        assert row.entry_price == 100.0
        assert row.return_1d == pytest.approx(0.01)
        assert row.is_correct_1d is True
        assert row.return_2d == pytest.approx(0.02)
        assert row.is_correct_2d is True
        assert row.return_3d == pytest.approx(0.03)
        assert row.is_correct_3d is True
        assert row.return_5d == 0.06
        assert row.is_correct_5d is True
        assert row.return_10d == 0.08
        assert row.is_correct_10d is True
        assert row.return_20d == 0.12
        assert row.is_correct_20d is True
        assert row.evaluated_at is not None


def test_evaluate_leaves_the_1d_2d_3d_windows_open_when_they_havent_closed_yet():
    """DESIGN_SQUEEZE_ALERT_PERFORMANCE_MEASUREMENT: a fire from today has NO closed window at
    all yet — 1d/2d/3d must degrade to None exactly like 5d/10d/20d already do, never a
    fabricated 0.0 just because the target date happens to be "close."""
    session = _make_session()
    st = _make_stock(session, "AAPL")
    fired = date.today()  # zero days old — even the 1d window's target (fired+1) is tomorrow
    session.add(SqueezeAlertOutcome(id=_new_id(), alert_type="short_squeeze", stock_id=st.id, symbol="AAPL", fired_date=fired, alert_price=99.0))
    session.commit()

    evaluate, _ = _extract_evaluate_squeeze_alert_outcomes()
    evaluate()

    with Session(_ENGINE) as check:
        row = check.execute(select(SqueezeAlertOutcome)).scalar_one()
        assert row.entry_price is None  # T+1 entry itself hasn't happened yet either
        assert row.return_1d is None
        assert row.return_2d is None
        assert row.return_3d is None


def test_evaluate_1d_window_can_resolve_while_5d_10d_20d_are_still_open():
    """The whole point of adding 1d/2d/3d — a fire 2 real days old should already have a
    resolved 1d return even though 5d/10d/20d all remain None. Confirms the per-window loop's
    independent "target > today: skip" check, not a single all-or-nothing gate."""
    session = _make_session()
    st = _make_stock(session, "AAPL")
    fired = date.today() - timedelta(days=2)
    _make_price(session, st.id, fired + timedelta(days=1), 50.0)  # T+1 entry
    _make_price(session, st.id, fired + timedelta(days=2), 51.0)  # 1d close, up 2%
    session.add(SqueezeAlertOutcome(id=_new_id(), alert_type="short_squeeze", stock_id=st.id, symbol="AAPL", fired_date=fired, alert_price=49.0))
    session.commit()

    evaluate, _ = _extract_evaluate_squeeze_alert_outcomes()
    evaluate()

    with Session(_ENGINE) as check:
        row = check.execute(select(SqueezeAlertOutcome)).scalar_one()
        assert row.return_1d == pytest.approx(0.02)
        assert row.is_correct_1d is True
        assert row.return_5d is None
        assert row.return_10d is None
        assert row.return_20d is None


def test_evaluate_scores_gamma_unwind_puts_as_a_bearish_thesis():
    """A puts-dominant "option sell" read wins when price FELL — the mirror of the bullish
    scoring above, matching SignalOutcome's own established BUY/SELL convention."""
    session = _make_session()
    st = _make_stock(session, "TSLA")
    fired = date(2026, 1, 1)
    _make_price(session, st.id, fired + timedelta(days=1), 200.0)
    _make_price(session, st.id, fired + timedelta(days=11), 180.0)  # 10d close, down 10%
    session.add(SqueezeAlertOutcome(id=_new_id(), alert_type="gamma_unwind_puts", stock_id=st.id, symbol="TSLA", fired_date=fired, alert_price=201.0))
    session.commit()

    evaluate, _ = _extract_evaluate_squeeze_alert_outcomes()
    evaluate()

    with Session(_ENGINE) as check:
        row = check.execute(select(SqueezeAlertOutcome)).scalar_one()
        assert row.return_10d == -0.1
        assert row.is_correct_10d is True  # thesis was bearish, price fell -> correct


def test_evaluate_gamma_unwind_puts_loses_when_price_rises():
    session = _make_session()
    st = _make_stock(session, "TSLA")
    fired = date(2026, 1, 1)
    _make_price(session, st.id, fired + timedelta(days=1), 200.0)
    _make_price(session, st.id, fired + timedelta(days=11), 220.0)  # up 10%
    session.add(SqueezeAlertOutcome(id=_new_id(), alert_type="gamma_unwind_puts", stock_id=st.id, symbol="TSLA", fired_date=fired, alert_price=201.0))
    session.commit()

    evaluate, _ = _extract_evaluate_squeeze_alert_outcomes()
    evaluate()

    with Session(_ENGINE) as check:
        row = check.execute(select(SqueezeAlertOutcome)).scalar_one()
        assert row.is_correct_10d is False


def test_evaluate_leaves_a_window_open_when_it_hasnt_closed_yet():
    session = _make_session()
    st = _make_stock(session, "AAPL")
    fired = date.today() - timedelta(days=3)  # only 3 days old — 5d/10d/20d windows all still open
    _make_price(session, st.id, fired + timedelta(days=1), 100.0)
    session.add(SqueezeAlertOutcome(id=_new_id(), alert_type="short_squeeze", stock_id=st.id, symbol="AAPL", fired_date=fired, alert_price=99.0))
    session.commit()

    evaluate, _ = _extract_evaluate_squeeze_alert_outcomes()
    evaluate()

    with Session(_ENGINE) as check:
        row = check.execute(select(SqueezeAlertOutcome)).scalar_one()
        assert row.entry_price == 100.0  # entry fills immediately once T+1 is available
        assert row.return_1d is None     # no bar exists for the 1d/2d targets either
        assert row.return_2d is None
        assert row.return_5d is None     # but no forward window has closed yet
        assert row.return_10d is None
        assert row.return_20d is None


def test_evaluate_never_re_evaluates_an_already_filled_window():
    """Once a window is filled, a later run must not silently overwrite it with a different
    lookup result even if new, later price bars have since been ingested."""
    session = _make_session()
    st = _make_stock(session, "AAPL")
    fired = date(2026, 1, 1)
    row = SqueezeAlertOutcome(
        id=_new_id(), alert_type="short_squeeze", stock_id=st.id, symbol="AAPL", fired_date=fired,
        alert_price=99.0, entry_date=fired + timedelta(days=1), entry_price=100.0,
        price_5d=106.0, return_5d=0.06, is_correct_5d=True,
    )
    session.add(row)
    session.commit()
    # A later, DIFFERENT 5d-window price now exists in Price — must be ignored for the already-filled column.
    _make_price(session, st.id, fired + timedelta(days=6), 999.0)
    _make_price(session, st.id, fired + timedelta(days=11), 108.0)
    session.commit()

    evaluate, _ = _extract_evaluate_squeeze_alert_outcomes()
    evaluate()

    with Session(_ENGINE) as check:
        checked = check.execute(select(SqueezeAlertOutcome)).scalar_one()
        assert checked.return_5d == 0.06  # untouched
        assert checked.return_10d is not None  # the still-open window DOES get filled


def test_evaluate_skips_a_row_with_no_entry_price_available_yet():
    session = _make_session()
    st = _make_stock(session, "AAPL")
    fired = date(2026, 1, 1)
    # No Price rows at all — entry lookup must fail cleanly, not raise.
    session.add(SqueezeAlertOutcome(id=_new_id(), alert_type="short_squeeze", stock_id=st.id, symbol="AAPL", fired_date=fired, alert_price=99.0))
    session.commit()

    evaluate, _ = _extract_evaluate_squeeze_alert_outcomes()
    evaluate()

    with Session(_ENGINE) as check:
        row = check.execute(select(SqueezeAlertOutcome)).scalar_one()
        assert row.entry_price is None
        assert row.evaluated_at is None  # row was skipped entirely, not marked evaluated


# ── Scheduler wiring — source-text checks ────────────────────────────────────────────────────

def test_short_squeeze_alerts_records_once_per_candidate_before_the_recipient_loop():
    idx = _SCHEDULER_SOURCE.index("_record_squeeze_alert_outcome(\n                    session, \"short_squeeze\"")
    assert idx > 0
    # Must appear BEFORE the per-recipient send loop begins.
    loop_idx = _SCHEDULER_SOURCE.index("for uid, user in recipients.items():", idx)
    assert idx < loop_idx


def test_gamma_unwind_alerts_splits_by_dominant_side():
    idx = _SCHEDULER_SOURCE.index('_alert_type = "gamma_unwind_calls" if cand["dominant_side"] == "calls" else "gamma_unwind_puts"')
    assert idx > 0
    nearby = _SCHEDULER_SOURCE[idx:idx + 400]
    assert "_record_squeeze_alert_outcome(" in nearby


def test_evaluator_is_registered_as_a_daily_cron_job():
    assert 'CronTrigger(hour=18, minute=15, timezone="America/New_York")' in _SCHEDULER_SOURCE
    idx = _SCHEDULER_SOURCE.index('id="squeeze_alert_outcome_eval_daily"')
    assert idx > 0
    nearby = _SCHEDULER_SOURCE[max(0, idx - 300):idx]
    assert "evaluate_squeeze_alert_outcomes" in nearby


def test_evaluator_job_is_not_gated_behind_alerting_enabled():
    """Pure data computation, no email — must not be silently skipped in local/dev environments
    the way real alert-sending jobs correctly are. Checks only the real CODE (the
    _scheduler.add_job(...) call itself), not the surrounding comment — a comment explaining
    why this job is deliberately NOT gated legitimately mentions the guard's name in prose."""
    call_start = _SCHEDULER_SOURCE.index("_scheduler.add_job(\n        evaluate_squeeze_alert_outcomes,")
    call_end = _SCHEDULER_SOURCE.index(")\n", call_start)
    call_code = _SCHEDULER_SOURCE[call_start:call_end]
    preceding_code_only = _SCHEDULER_SOURCE[max(0, call_start - 500):call_start]
    # Only the code, not any comment lines, should be checked for the guard.
    code_lines = [ln for ln in preceding_code_only.splitlines() if not ln.strip().startswith("#")]
    assert "_is_alerting_enabled()" not in "\n".join(code_lines)
    assert "_is_alerting_enabled()" not in call_code


# ── admin.py squeeze_alert_performance() — source-text checks ────────────────────────────────

def test_squeeze_alert_performance_endpoint_is_admin_gated():
    idx = _ADMIN_SOURCE.index("def squeeze_alert_performance(")
    signature_end = _ADMIN_SOURCE.index("):\n", idx)
    signature = _ADMIN_SOURCE[idx:signature_end]
    assert "get_admin_user" in signature


def test_squeeze_alert_performance_reports_all_four_alert_types():
    """AUD-SQUEEZE-IGNITION-DASHBOARD-OMITTED (2026-08-31): squeeze_ignition is a real,
    actively-firing 4th alert type (T260) that writes into the SAME SqueezeAlertOutcome table
    via the identical _record_squeeze_alert_outcome() helper every other type uses, but was
    silently omitted from both _SQUEEZE_ALERT_TYPE_LABELS and the by_alert_type loop since the
    endpoint's own creation — its win rate/avg return/fired-count were never surfaced anywhere
    in the admin UI. Must now be present alongside the original 3."""
    start = _ADMIN_SOURCE.index("def squeeze_alert_performance(")
    end = _ADMIN_SOURCE.index("\n\n\n@router.get(\"/watchlist-rotation-history\")", start)
    body = _ADMIN_SOURCE[start:end]
    for t in ("short_squeeze", "squeeze_ignition", "gamma_unwind_calls", "gamma_unwind_puts"):
        assert f'"{t}"' in body


def test_squeeze_alert_performance_by_alert_type_loop_specifically_includes_ignition():
    """Narrower than the whole-function check above — confirms squeeze_ignition is genuinely
    in the by_alert_type FOR-LOOP itself (the actual bug site), not merely present somewhere
    else in the function (e.g. only in the _SQUEEZE_ALERT_TYPE_LABELS dict, which alone would
    still leave the loop's own hardcoded tuple silently omitting it)."""
    start = _ADMIN_SOURCE.index("for alert_type in (", _ADMIN_SOURCE.index("by_alert_type = []"))
    end = _ADMIN_SOURCE.index("\n", start)
    line = _ADMIN_SOURCE[start:end]
    assert '"squeeze_ignition"' in line, f"squeeze_ignition missing from the loop tuple: {line}"


def test_squeeze_alert_type_labels_dict_includes_ignition():
    start = _ADMIN_SOURCE.index("_SQUEEZE_ALERT_TYPE_LABELS = {")
    end = _ADMIN_SOURCE.index("\n}", start)
    body = _ADMIN_SOURCE[start:end]
    assert '"squeeze_ignition"' in body


def test_squeeze_alert_performance_gamma_unwind_puts_is_never_merged_with_calls():
    """The two are opposite theses — pooling them would silently cancel real signal in either
    direction, the same BUG233-RETROEV-SIGNMIX class of bug already fixed once in this repo for
    a different SELL-mixing case. Scoped specifically to _summary_for_window's own nested
    function body (not just anywhere in squeeze_alert_performance() as a whole) — a sibling
    query elsewhere in the same endpoint (fired_counts) also legitimately groups by
    alert_type, so a looser check could pass even if THIS specific query's own grouping were
    removed."""
    start = _ADMIN_SOURCE.index("def _summary_for_window(window: int)")
    end = _ADMIN_SOURCE.index("by_window = {w: _summary_for_window(w)", start)
    body = _ADMIN_SOURCE[start:end]
    assert "group_by(SqueezeAlertOutcome.alert_type)" in body


def test_squeeze_alert_performance_now_computes_the_1d_2d_3d_windows_too():
    """DESIGN_SQUEEZE_ALERT_PERFORMANCE_MEASUREMENT: the by_window dict comprehension must
    actually include 1/2/3 alongside the pre-existing 5/10/20, not just extend the schema/
    evaluator without wiring the endpoint's own summary to report them."""
    start = _ADMIN_SOURCE.index("by_window = {w: _summary_for_window(w) for w in")
    end = _ADMIN_SOURCE.index("\n", start)
    line = _ADMIN_SOURCE[start:end]
    for w in (1, 2, 3, 5, 10, 20):
        assert str(w) in line, f"window {w} missing from by_window comprehension: {line}"


def test_squeeze_alert_performance_by_alert_type_exposes_the_1d_2d_3d_fields():
    start = _ADMIN_SOURCE.index('by_alert_type = []')
    end = _ADMIN_SOURCE.index("\n\n    recent_rows = ", start)
    body = _ADMIN_SOURCE[start:end]
    for key in ("window_1d", "window_2d", "window_3d"):
        assert f'"{key}"' in body


def test_squeeze_alert_performance_recent_alerts_row_includes_the_1d_2d_3d_returns():
    start = _ADMIN_SOURCE.index("recent_alerts = [")
    end = _ADMIN_SOURCE.index("\n    ]\n", start)
    body = _ADMIN_SOURCE[start:end]
    for key in ("return_1d", "return_2d", "return_3d"):
        assert f'"{key}"' in body


# ── admin.py squeeze_alert_backtest() — real behavioral tests ────────────────────────────────

def _make_daily_bars(session, stock_id, prices: dict):
    """prices: {date: close}. Realistic, tightly-spaced (weekday-only) bars are required here —
    a sparse fixture (bars several calendar days apart) would make the candidate-detection
    loop's day-over-day comparison span more than one real trading day, producing a spurious
    extra "move" purely from the gap. This was a real trap hit while writing this test file:
    an early attempt used bars 5 calendar days apart and found 2 candidate days instead of the
    intended 1 — traced and confirmed to be a fixture-spacing artifact, not a real bug in the
    endpoint, by re-running with realistic daily spacing and getting the correct single match."""
    for d, close in sorted(prices.items()):
        _make_price(session, stock_id, d, close)


def test_backtest_endpoint_is_admin_gated():
    idx = _ADMIN_SOURCE.index("def squeeze_alert_backtest(")
    signature_end = _ADMIN_SOURCE.index("):\n", idx)
    signature = _ADMIN_SOURCE[idx:signature_end]
    assert "get_admin_user" in signature


def test_backtest_finds_the_one_real_qualifying_day_not_every_day_in_the_window():
    session = _make_session()
    st = _make_stock(session, "AAPL")
    _make_snapshot(session, "AAPL", date(2026, 1, 4), 0.20)  # 20% short of float, clears 15%
    _make_daily_bars(session, st.id, {
        date(2026, 1, 4): 100.0, date(2026, 1, 5): 105.0,  # +5% -> the one real candidate day
        date(2026, 1, 6): 103.0, date(2026, 1, 7): 102.0, date(2026, 1, 8): 101.0,
    })
    backtest = _extract_squeeze_alert_backtest()

    result = backtest(weeks_back=520, min_samples=1, _=None, session=session)

    assert result["n_snapshots_qualifying"] == 1
    assert result["n_candidate_days"] == 1


def test_backtest_excludes_snapshots_below_the_short_interest_floor():
    session = _make_session()
    st = _make_stock(session, "AAPL")
    _make_snapshot(session, "AAPL", date(2026, 1, 4), 0.05)  # only 5% — below the 15% floor
    _make_daily_bars(session, st.id, {date(2026, 1, 4): 100.0, date(2026, 1, 5): 110.0})
    backtest = _extract_squeeze_alert_backtest()

    result = backtest(weeks_back=520, min_samples=1, _=None, session=session)

    assert result["n_snapshots_qualifying"] == 0
    assert result["n_candidate_days"] == 0


def test_backtest_excludes_a_day_whose_move_is_below_the_intraday_threshold():
    session = _make_session()
    st = _make_stock(session, "AAPL")
    _make_snapshot(session, "AAPL", date(2026, 1, 4), 0.20)
    _make_daily_bars(session, st.id, {
        date(2026, 1, 4): 100.0, date(2026, 1, 5): 101.0,  # +1% — below the 3.0% threshold
    })
    backtest = _extract_squeeze_alert_backtest()

    result = backtest(weeks_back=520, min_samples=1, _=None, session=session)

    assert result["n_candidate_days"] == 0


def test_backtest_reports_a_real_win_rate_and_avg_return_once_forward_windows_resolve():
    session = _make_session()
    st = _make_stock(session, "AAPL")
    _make_snapshot(session, "AAPL", date(2026, 1, 4), 0.20)
    _make_daily_bars(session, st.id, {
        date(2026, 1, 4): 100.0, date(2026, 1, 5): 105.0,
    })
    _make_price(session, st.id, date(2026, 1, 10), 110.0)  # 5d forward, +4.76% from entry (105)
    backtest = _extract_squeeze_alert_backtest()

    result = backtest(weeks_back=520, min_samples=1, _=None, session=session)

    assert result["window_5d"]["n"] == 1
    assert result["window_5d"]["win_rate"] == 1.0
    assert result["window_5d"]["avg_return_pct"] > 0


def test_backtest_reports_below_sample_floor_note_instead_of_a_fabricated_win_rate():
    session = _make_session()
    st = _make_stock(session, "AAPL")
    _make_snapshot(session, "AAPL", date(2026, 1, 4), 0.20)
    _make_daily_bars(session, st.id, {date(2026, 1, 4): 100.0, date(2026, 1, 5): 105.0})
    _make_price(session, st.id, date(2026, 1, 10), 110.0)
    backtest = _extract_squeeze_alert_backtest()

    result = backtest(weeks_back=520, min_samples=50, _=None, session=session)  # unreachable floor

    assert result["window_5d"]["win_rate"] is None
    assert "Below the 50-sample floor" in result["window_5d"]["note"]


def test_backtest_excludes_a_below_floor_week_even_when_a_later_week_qualifies():
    """A real move the week BEFORE a symbol's short interest ever cleared the floor must not
    be silently counted as a qualifying candidate, even though a LATER snapshot for the same
    symbol does qualify."""
    session = _make_session()
    st = _make_stock(session, "AAPL")
    _make_snapshot(session, "AAPL", date(2026, 1, 4), 0.05)   # below floor
    _make_snapshot(session, "AAPL", date(2026, 1, 11), 0.20)  # clears floor, a week later
    _make_daily_bars(session, st.id, {
        date(2026, 1, 4): 100.0, date(2026, 1, 5): 106.0,   # +6% but BEFORE the qualifying snapshot
        date(2026, 1, 11): 100.0, date(2026, 1, 12): 106.0,  # +6% AFTER the qualifying snapshot
    })
    backtest = _extract_squeeze_alert_backtest()

    result = backtest(weeks_back=520, min_samples=1, _=None, session=session)

    assert result["n_candidate_days"] == 1  # only the Jan 12 move, not Jan 5


def test_backtest_a_later_qualifying_snapshots_window_does_not_extend_backward_past_an_earlier_one():
    """The genuine point-in-time-boundary property: TWO CONSECUTIVE qualifying snapshots for
    the same symbol, each with its own real move in its OWN week. Each week's move must be
    counted exactly once, attributed to the snapshot whose window it actually falls in — not
    double-counted, and not silently dropped by one window's boundary swallowing the other's."""
    session = _make_session()
    st = _make_stock(session, "AAPL")
    _make_snapshot(session, "AAPL", date(2026, 1, 4), 0.20)   # qualifies
    _make_snapshot(session, "AAPL", date(2026, 1, 11), 0.25)  # also qualifies, a week later
    _make_daily_bars(session, st.id, {
        date(2026, 1, 4): 100.0, date(2026, 1, 5): 106.0,    # +6% in week 1
        date(2026, 1, 6): 105.0, date(2026, 1, 7): 104.0, date(2026, 1, 8): 103.0,
        date(2026, 1, 11): 100.0, date(2026, 1, 12): 108.0,  # +8% in week 2
    })
    backtest = _extract_squeeze_alert_backtest()

    result = backtest(weeks_back=520, min_samples=1, _=None, session=session)

    assert result["n_candidate_days"] == 2  # both real moves counted, exactly once each


def test_backtest_reports_gamma_unwind_is_not_backtestable():
    idx = _ADMIN_SOURCE.index("def squeeze_alert_backtest(")
    end = _ADMIN_SOURCE.index("\n\n\n@router.get(\"/options-flow-alert-backtest\")", idx)
    body = _ADMIN_SOURCE[idx:end]
    assert "gamma_unwind is not" in body or "gamma_unwind_calls" not in body
    assert "no historical open-interest API" in body or "no historical options open-interest API" in body


def test_backtest_imports_the_real_shared_scheduler_constants_not_a_hand_copied_duplicate():
    """A future threshold change in scheduler.py's _SQUEEZE_MIN_SHORT_FLOAT/
    _SQUEEZE_MIN_INTRADAY_MOVE_PCT/_squeeze_outcome_lookup_price/_SQUEEZE_OUTCOME_WIN_HURDLE_PCT/
    _SQUEEZE_OUTCOME_WINDOWS must automatically change what this backtest considers a
    qualifying candidate and how it scores forward returns — checked here at the SOURCE level
    (the endpoint's own real import statement), not just behaviorally, since a behavioral test
    alone couldn't distinguish "imports the real shared constant" from "coincidentally hardcoded
    the same value this session" for a constant that isn't itself exercised by any single
    concrete input value in the tests above."""
    start = _ADMIN_SOURCE.index("def squeeze_alert_backtest(")
    end = _ADMIN_SOURCE.index("\n\n    cutoff = date.today()", start)
    body = _ADMIN_SOURCE[start:end]
    assert "from ..services.scheduler import" in body
    for name in (
        "_squeeze_outcome_lookup_price", "_SQUEEZE_OUTCOME_WIN_HURDLE_PCT", "_SQUEEZE_OUTCOME_WINDOWS",
        "_SQUEEZE_MIN_SHORT_FLOAT", "_SQUEEZE_MIN_INTRADAY_MOVE_PCT",
    ):
        assert name in body


def _real_squeeze_min_short_float() -> float:
    """Extracts the REAL, current _SQUEEZE_MIN_SHORT_FLOAT value from scheduler.py's own
    source text (scheduler.py can't be imported directly in this test environment) — so a
    future threshold change is picked up automatically rather than the test silently comparing
    against a stale, hand-copied literal."""
    start = _SCHEDULER_SOURCE.index("_SQUEEZE_MIN_SHORT_FLOAT = ")
    end = _SCHEDULER_SOURCE.index("\n", start)
    namespace: dict = {}
    exec(_SCHEDULER_SOURCE[start:end], namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_SQUEEZE_MIN_SHORT_FLOAT"]


def test_backtest_matches_the_live_alert_threshold_exactly_not_a_stricter_or_looser_copy():
    """Behavioral companion to the source-level import check above — confirms a stock at
    EXACTLY the real live 15% floor qualifies, and one just below it does not, using whatever
    the REAL _SQUEEZE_MIN_SHORT_FLOAT value in scheduler.py currently is (not a hardcoded 15.0
    literal in this test, so a future threshold change is still caught by this test rather than
    silently passing against a stale expectation)."""
    _SQUEEZE_MIN_SHORT_FLOAT = _real_squeeze_min_short_float()
    session = _make_session()
    st = _make_stock(session, "AAPL")
    _make_snapshot(session, "AAPL", date(2026, 1, 4), _SQUEEZE_MIN_SHORT_FLOAT / 100)  # exactly at the floor
    _make_daily_bars(session, st.id, {date(2026, 1, 4): 100.0, date(2026, 1, 5): 110.0})
    backtest = _extract_squeeze_alert_backtest()

    result = backtest(weeks_back=520, min_samples=1, _=None, session=session)
    assert result["n_snapshots_qualifying"] == 1

    session2 = _make_session()
    st2 = _make_stock(session2, "TSLA")
    _make_snapshot(session2, "TSLA", date(2026, 1, 4), (_SQUEEZE_MIN_SHORT_FLOAT - 0.5) / 100)  # just below
    _make_daily_bars(session2, st2.id, {date(2026, 1, 4): 100.0, date(2026, 1, 5): 110.0})
    result2 = backtest(weeks_back=520, min_samples=1, _=None, session=session2)
    assert result2["n_snapshots_qualifying"] == 0
