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
    tables=[_models.Stock.__table__, _models.Price.__table__, _models.SqueezeAlertOutcome.__table__],
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
    for table in (SqueezeAlertOutcome.__table__, Price.__table__, Stock.__table__):
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
        assert row.return_5d == 0.06
        assert row.is_correct_5d is True
        assert row.return_10d == 0.08
        assert row.is_correct_10d is True
        assert row.return_20d == 0.12
        assert row.is_correct_20d is True
        assert row.evaluated_at is not None


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


def test_squeeze_alert_performance_reports_all_three_alert_types():
    start = _ADMIN_SOURCE.index("def squeeze_alert_performance(")
    end = _ADMIN_SOURCE.index("\n\n\n@router.get(\"/watchlist-rotation-history\")", start)
    body = _ADMIN_SOURCE[start:end]
    for t in ("short_squeeze", "gamma_unwind_calls", "gamma_unwind_puts"):
        assert f'"{t}"' in body


def test_squeeze_alert_performance_gamma_unwind_puts_is_never_merged_with_calls():
    """The two are opposite theses — pooling them would silently cancel real signal in either
    direction, the same BUG233-RETROEV-SIGNMIX class of bug already fixed once in this repo for
    a different SELL-mixing case. Scoped specifically to _summary_for_window's own nested
    function body (not just anywhere in squeeze_alert_performance() as a whole) — a sibling
    query elsewhere in the same endpoint (fired_counts) also legitimately groups by
    alert_type, so a looser check could pass even if THIS specific query's own grouping were
    removed."""
    start = _ADMIN_SOURCE.index("def _summary_for_window(window: int)")
    end = _ADMIN_SOURCE.index("\n\n    by_window = ", start)
    body = _ADMIN_SOURCE[start:end]
    assert "group_by(SqueezeAlertOutcome.alert_type)" in body
