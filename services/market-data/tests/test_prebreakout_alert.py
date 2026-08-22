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
        _models.Stock.__table__, _models.Price.__table__, _models.PreBreakoutAlertOutcome.__table__,
        _models.SqueezeAlertOutcome.__table__,
    ],
)

# PreBreakoutAlertOutcome.id/SqueezeAlertOutcome.id are BigInteger primary keys, which don't
# get SQLite's implicit INTEGER PRIMARY KEY autoincrement (a real Postgres sequence handles
# this in production). The real code under test constructs a row with no id at all, exactly as
# it does in production where Postgres fills it in — a before_insert listener scoped to ONLY
# this test engine assigns one automatically, so the function's real, unmodified source can be
# exercised as-is.
_autoincrement_counter = [0]


@event.listens_for(_models.PreBreakoutAlertOutcome, "before_insert")
def _assign_test_id(mapper, connection, target):
    if target.id is None:
        _autoincrement_counter[0] += 1
        target.id = _autoincrement_counter[0]


@event.listens_for(_models.SqueezeAlertOutcome, "before_insert")
def _assign_test_id_squeeze(mapper, connection, target):
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
SqueezeAlertOutcome = _models.SqueezeAlertOutcome
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
    for table in (PreBreakoutAlertOutcome.__table__, SqueezeAlertOutcome.__table__, Price.__table__, Stock.__table__):
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


def _extract_prebreakout_calibration_functions():
    """T264-SHORTSQUEEZE-PREBREAKOUT-CONFIDENCE (extended 2026-08-15 to short_squeeze/
    gamma_unwind_*): extracts the whole contiguous calibration block — band constants,
    _build_prebreakout_calibration(), _prebreakout_calibration_for_band(),
    _build_squeeze_family_calibration(), _squeeze_family_calibration_for_band(),
    _squeeze_family_calibration_for_alert_type() — as one unit, since
    _prebreakout_calibration_for_band() now delegates to _squeeze_family_calibration_for_band()
    and would raise a real NameError if extracted alone. Real select()/PreBreakoutAlertOutcome/
    SqueezeAlertOutcome injected so the DB-facing halves run against the real in-memory SQLite
    session."""
    start = _SCHEDULER_SOURCE.index("_SQUEEZE_FAMILY_CAL_MIN_COUNT = ")
    end = _SCHEDULER_SOURCE.index("\n\ndef _fetch_ml_price_direction(", start)
    body = _SCHEDULER_SOURCE[start:end]
    namespace = {"select": select, "PreBreakoutAlertOutcome": PreBreakoutAlertOutcome, "SqueezeAlertOutcome": SqueezeAlertOutcome}
    exec(body, namespace)  # noqa: S102 — isolated eval of real source
    return (
        namespace["_build_prebreakout_calibration"], namespace["_prebreakout_calibration_for_band"],
        namespace["_build_squeeze_family_calibration"], namespace["_squeeze_family_calibration_for_alert_type"],
    )


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
    # T264-SHORTSQUEEZE-PREBREAKOUT-CONFIDENCE: the two new signal fields default to None
    # when not passed — this call site (matching every pre-existing caller in this file)
    # never supplies them, so a real regression here would silently break every OTHER
    # test in this file too, not just this one.
    assert rows[0].ml_price_direction_confidence is None
    assert rows[0].ml_price_direction_model_version is None
    assert rows[0].calibrated_win_rate is None
    assert rows[0].calibrated_win_rate_count is None


def test_record_persists_the_new_confidence_signals_when_given():
    session = _make_session()
    st = _make_stock(session, "AAPL")
    record, _ = _extract_record_prebreakout_alert_outcome()

    record(session, st.id, "AAPL", 150.0, 22.5, 0.1, 0.15, True, 1.3, 61.5, "2026-08-01T00:00:00", 0.42, 37)

    row = session.execute(select(PreBreakoutAlertOutcome)).scalar_one()
    assert row.ml_price_direction_confidence == 61.5
    assert row.ml_price_direction_model_version == "2026-08-01T00:00:00"
    assert row.calibrated_win_rate == 0.42
    assert row.calibrated_win_rate_count == 37


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


# ── _build_prebreakout_calibration() / _prebreakout_calibration_for_band() ──────────────────

def _make_resolved_outcome(session, stock_id, symbol, spf, is_correct, i):
    """A minimal already-resolved PreBreakoutAlertOutcome row — only the two columns
    _build_prebreakout_calibration() actually reads (short_percent_of_float, is_correct_10d)
    matter for these tests; fired_date is varied per row only so the unique constraint on
    (stock_id, fired_date) never collides across the many rows a single test builds."""
    session.add(PreBreakoutAlertOutcome(
        id=_new_id(), stock_id=stock_id, symbol=symbol,
        fired_date=date(2026, 1, 1) + timedelta(days=i), alert_price=100.0,
        rule_gate_passed=True, short_percent_of_float=spf, is_correct_10d=is_correct,
    ))


def test_calibration_reports_a_real_bucket_once_it_clears_the_sample_floor():
    session = _make_session()
    st = _make_stock(session, "AAPL")
    # 30 rows in the 15-20 band, exactly at the floor: 18 wins / 12 losses -> 0.6 win rate.
    for i in range(30):
        _make_resolved_outcome(session, st.id, "AAPL", 17.0, i < 18, i)
    session.commit()

    build, lookup, _, _ = _extract_prebreakout_calibration_functions()
    buckets = build(session)

    assert buckets["15-20"] == {"win_rate": 0.6, "count": 30}
    win_rate, count = lookup(buckets, 17.0)
    assert win_rate == 0.6
    assert count == 30


def test_calibration_omits_a_bucket_below_the_sample_floor():
    session = _make_session()
    st = _make_stock(session, "AAPL")
    # 29 rows — one short of _PREBREAKOUT_CAL_MIN_COUNT (30).
    for i in range(29):
        _make_resolved_outcome(session, st.id, "AAPL", 17.0, True, i)
    session.commit()

    build, lookup, _, _ = _extract_prebreakout_calibration_functions()
    buckets = build(session)

    assert "15-20" not in buckets
    win_rate, count = lookup(buckets, 17.0)
    assert win_rate is None
    assert count is None


def test_calibration_buckets_are_independent_per_band():
    """A well-populated 15-20 band must never leak its win rate into a thin 30+ band lookup —
    each band's own sample count is what gates it, not the total row count across all bands."""
    session = _make_session()
    st = _make_stock(session, "AAPL")
    for i in range(30):
        _make_resolved_outcome(session, st.id, "AAPL", 17.0, True, i)
    for i in range(5):
        _make_resolved_outcome(session, st.id, "AAPL", 35.0, False, 100 + i)
    session.commit()

    build, lookup, _, _ = _extract_prebreakout_calibration_functions()
    buckets = build(session)

    assert buckets["15-20"]["count"] == 30
    assert "30+" not in buckets
    win_rate, count = lookup(buckets, 35.0)
    assert win_rate is None
    assert count is None


def test_calibration_ignores_unresolved_outcomes():
    """A row with is_correct_10d still NULL (not yet evaluated) must never count toward
    either the sample floor or the win rate — only genuinely resolved outcomes qualify."""
    session = _make_session()
    st = _make_stock(session, "AAPL")
    for i in range(30):
        _make_resolved_outcome(session, st.id, "AAPL", 17.0, True, i)
    # 10 more UNRESOLVED rows in the same band — must not inflate the count or dilute win_rate.
    for i in range(10):
        session.add(PreBreakoutAlertOutcome(
            id=_new_id(), stock_id=st.id, symbol="AAPL",
            fired_date=date(2026, 3, 1) + timedelta(days=i), alert_price=100.0,
            rule_gate_passed=True, short_percent_of_float=17.0, is_correct_10d=None,
        ))
    session.commit()

    build, _, _, _ = _extract_prebreakout_calibration_functions()
    buckets = build(session)

    assert buckets["15-20"] == {"win_rate": 1.0, "count": 30}


def test_calibration_lookup_returns_none_outside_every_band():
    """short_percent_of_float below the alert's own 15% floor should never happen in
    production, but the lookup function itself must degrade safely rather than raise."""
    build, lookup, _, _ = _extract_prebreakout_calibration_functions()
    win_rate, count = lookup({}, 5.0)
    assert win_rate is None
    assert count is None


# ── _build_squeeze_family_calibration() / _squeeze_family_calibration_for_alert_type() ──────
# T264-SHORTSQUEEZE-PREBREAKOUT-CONFIDENCE (extended 2026-08-15 to short_squeeze/gamma_unwind_*)

def _make_resolved_squeeze_outcome(session, stock_id, alert_type, symbol, metric, is_correct, i):
    """A minimal already-resolved SqueezeAlertOutcome row — only qualifying_metric/
    is_correct_10d/alert_type matter for these tests, matching _make_resolved_outcome()'s own
    convention for the pre-breakout table."""
    session.add(SqueezeAlertOutcome(
        id=_new_id(), alert_type=alert_type, stock_id=stock_id, symbol=symbol,
        fired_date=date(2026, 1, 1) + timedelta(days=i), alert_price=100.0,
        qualifying_metric=metric, is_correct_10d=is_correct,
    ))


def test_squeeze_family_calibration_reports_a_real_bucket_for_short_squeeze():
    session = _make_session()
    st = _make_stock(session, "AAPL")
    for i in range(30):
        _make_resolved_squeeze_outcome(session, st.id, "short_squeeze", "AAPL", 17.0, i < 21, i)
    session.commit()

    _, _, build, lookup = _extract_prebreakout_calibration_functions()
    buckets = build(session, "short_squeeze")

    assert buckets["15-20"] == {"win_rate": 0.7, "count": 30}
    win_rate, count = lookup(buckets, "short_squeeze", 17.0)
    assert win_rate == 0.7
    assert count == 30


def test_squeeze_family_calibration_reports_a_real_bucket_for_gamma_unwind_puts():
    """gamma_unwind_puts uses a DIFFERENT band scheme (55-65/65-80/80+, matching the alert's
    own 55% OI-concentration floor) than short_squeeze's 15-20/20-30/30+ — this test would
    silently pass with the wrong bands if the two schemes were ever accidentally swapped,
    since 17.0 (a valid short_squeeze metric) would fall outside every gamma band entirely and
    correctly return (None, None) either way; using 62.0 (only valid under the gamma scheme)
    is what actually distinguishes a correct band lookup from a coincidentally-correct one."""
    session = _make_session()
    st = _make_stock(session, "AAPL")
    for i in range(30):
        _make_resolved_squeeze_outcome(session, st.id, "gamma_unwind_puts", "AAPL", 62.0, i < 15, i)
    session.commit()

    _, _, build, lookup = _extract_prebreakout_calibration_functions()
    buckets = build(session, "gamma_unwind_puts")

    assert buckets["55-65"] == {"win_rate": 0.5, "count": 30}
    win_rate, count = lookup(buckets, "gamma_unwind_puts", 62.0)
    assert win_rate == 0.5
    assert count == 30


def test_squeeze_family_calibration_gamma_unwind_calls_and_puts_never_pool():
    """A resolved gamma_unwind_calls row must never contribute to a gamma_unwind_puts bucket
    or vice versa — the two are scored on opposite theses and pooling them would silently
    cancel real signal (the same class of bug BUG233-RETROEV-SIGNMIX already fixed once
    elsewhere in this app for a different SELL-mixing case)."""
    session = _make_session()
    st = _make_stock(session, "AAPL")
    for i in range(30):
        _make_resolved_squeeze_outcome(session, st.id, "gamma_unwind_calls", "AAPL", 62.0, True, i)
    for i in range(10):
        _make_resolved_squeeze_outcome(session, st.id, "gamma_unwind_puts", "AAPL", 62.0, False, 100 + i)
    session.commit()

    _, _, build, lookup = _extract_prebreakout_calibration_functions()
    calls_buckets = build(session, "gamma_unwind_calls")
    puts_buckets = build(session, "gamma_unwind_puts")

    assert calls_buckets["55-65"] == {"win_rate": 1.0, "count": 30}
    # puts only has 10 resolved rows — below the 30-sample floor, and NOT inflated by the 30
    # calls rows at the identical metric value.
    assert "55-65" not in puts_buckets
    win_rate, count = lookup(puts_buckets, "gamma_unwind_puts", 62.0)
    assert win_rate is None
    assert count is None


def test_squeeze_family_calibration_unknown_alert_type_returns_empty_buckets():
    """_SQUEEZE_FAMILY_CAL_BANDS only defines short_squeeze/gamma_unwind_calls/gamma_unwind_
    puts — an unrecognized alert_type must fail open to an empty bucket dict rather than
    raising, matching every other optional-signal lookup in this alert family."""
    session = _make_session()
    _, _, build, lookup = _extract_prebreakout_calibration_functions()

    assert build(session, "not_a_real_alert_type") == {}
    win_rate, count = lookup({}, "not_a_real_alert_type", 62.0)
    assert win_rate is None
    assert count is None


def test_squeeze_family_calibration_for_alert_type_fails_open_on_none_metric():
    """A candidate with no resolvable qualifying_metric (e.g. a genuinely missing
    short_percent_of_float) must degrade to (None, None), never raise on a None comparison."""
    _, _, _, lookup = _extract_prebreakout_calibration_functions()
    win_rate, count = lookup({"15-20": {"win_rate": 0.6, "count": 30}}, "short_squeeze", None)
    assert win_rate is None
    assert count is None


# ── evaluate_prebreakout_alert_outcomes() ────────────────────────────────────────────────────

def test_evaluate_fills_entry_price_and_scores_a_bullish_win():
    session = _make_session()
    st = _make_stock(session, "AAPL")
    fired = date(2026, 1, 1)
    _make_price(session, st.id, fired + timedelta(days=1), 100.0)
    _make_price(session, st.id, fired + timedelta(days=2), 101.0)
    _make_price(session, st.id, fired + timedelta(days=3), 102.0)
    _make_price(session, st.id, fired + timedelta(days=4), 103.0)
    _make_price(session, st.id, fired + timedelta(days=11), 108.0)
    session.add(PreBreakoutAlertOutcome(id=_new_id(), stock_id=st.id, symbol="AAPL", fired_date=fired, alert_price=99.0, rule_gate_passed=True))
    session.commit()

    evaluate = _extract_evaluate_prebreakout_alert_outcomes()
    evaluate()

    with Session(_ENGINE) as check:
        row = check.execute(select(PreBreakoutAlertOutcome)).scalar_one()
        assert row.entry_price == 100.0
        assert row.return_1d == pytest.approx(0.01)
        assert row.is_correct_1d is True
        assert row.return_2d == pytest.approx(0.02)
        assert row.is_correct_2d is True
        assert row.return_3d == pytest.approx(0.03)
        assert row.is_correct_3d is True
        assert row.return_10d == 0.08
        assert row.is_correct_10d is True
        assert row.evaluated_at is not None


def test_evaluate_1d_2d_3d_are_always_bullish_thesis_scored_never_a_bearish_variant():
    """PreBreakoutAlertOutcome has no gamma_unwind_puts-style bearish sibling — every window,
    including the new 1d/2d/3d, must score a price DROP as a loss, matching the module's own
    docstring ("shorts forced to cover" only ever predicts a move UP)."""
    session = _make_session()
    st = _make_stock(session, "AAPL")
    fired = date(2026, 1, 1)
    _make_price(session, st.id, fired + timedelta(days=1), 100.0)
    _make_price(session, st.id, fired + timedelta(days=2), 97.0)  # 1d close, down 3%
    session.add(PreBreakoutAlertOutcome(id=_new_id(), stock_id=st.id, symbol="AAPL", fired_date=fired, alert_price=99.0, rule_gate_passed=True))
    session.commit()

    evaluate = _extract_evaluate_prebreakout_alert_outcomes()
    evaluate()

    with Session(_ENGINE) as check:
        row = check.execute(select(PreBreakoutAlertOutcome)).scalar_one()
        assert row.is_correct_1d is False


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
        # 1d/2d targets are already in the PAST here (fired is 3 days old) — they legitimately
        # resolve against the same future-dated bar via the nearest-on-or-after lookup, unlike
        # 5d/10d whose OWN targets are still in the future and must stay None.
        assert row.return_1d is not None
        assert row.return_2d is not None
        assert row.return_5d is None
        assert row.return_10d is None
        assert row.return_20d is None
