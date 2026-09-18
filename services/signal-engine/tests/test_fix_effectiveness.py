"""Tests for T325-FIXEFFECTIVENESS — "did this fix actually work" tracking, direct user
request (2026-09-02) after the AI Signal deep audit: "I would like to have a dashboard to show
the performance after we applied the fix so that we can compare later."

fix_effectiveness.py can't be imported directly in this test environment (fastapi/
common.jwt_auth aren't stubbed by conftest.py) — matching test_delisted_loss_scoring.py's own
established convention: source-text extraction for route-wiring/structural checks, plus a real
in-memory-SQLite model to directly exercise _compute_ai_signal_win_rate_metrics()'s real query
logic (extracted via exec(), the same technique test_eval_outcomes_first_fire_snapshot.py and
test_fetch_ml_data_falsy_zero_auc.py already use for isolating one function's source from a
file that can't be imported as a whole).
"""
import importlib.util
import pathlib
import sys
import textwrap
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_fix_eff", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_fix_eff"] = _models
_spec.loader.exec_module(_models)

SignalOutcome = _models.SignalOutcome
SignalHorizon = _models.SignalHorizon
Stock = _models.Stock
Market = _models.Market
Exchange = _models.Exchange
Base = _models.Base

_FIX_EFF_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "fix_effectiveness.py"
_FIX_EFF_SOURCE = _FIX_EFF_PATH.read_text()


# ── Route wiring / structural checks (source-text extraction) ──────────────────────────

def test_router_has_the_expected_prefix():
    assert 'router = APIRouter(prefix="/fix-effectiveness"' in _FIX_EFF_SOURCE


def test_register_endpoint_rejects_a_duplicate_fix_id():
    assert "already registered" in _FIX_EFF_SOURCE
    assert "409" in _FIX_EFF_SOURCE


def test_snapshot_endpoint_404s_for_an_unregistered_fix_id():
    assert "No FixRecord registered for fix_id" in _FIX_EFF_SOURCE
    assert "404" in _FIX_EFF_SOURCE


def test_snapshot_dispatch_is_an_explicit_registry_not_a_generic_fallback():
    """The dispatch must explicitly refuse an unimplemented domain rather than silently
    producing a meaningless/empty snapshot. Asserted structurally because it is a design
    constraint (there must be no "compute something" fallback); the BEHAVIOUR of both branches
    is covered by the wiring tests below."""
    assert "_SNAPSHOT_METRIC_FNS = {" in _FIX_EFF_SOURCE
    assert "_SNAPSHOT_METRIC_FNS.get(record.domain)" in _FIX_EFF_SOURCE


# ── take_fix_snapshot() wiring — the gap that allowed AUD-C01 ──────────────────────────
#
# `_compute_ai_signal_win_rate_metrics`'s `since` parameter was documented in its own docstring,
# spelled out in the stored success_criteria of the fix it exists for, AND covered by a passing
# unit test (test_since_filter_excludes_rows_before_the_given_date, below). Its single CALL SITE
# omitted it for two weeks, so every snapshot would have measured all history — including the
# exact pre-fix population the fix corrected. A tested unit behind untested wiring.

def _run_take_snapshot(record, *, metric_fns, session):
    """exec() take_fix_snapshot's real source with fakes for its FastAPI/DB surface, so the
    actual deployed control flow runs. Same isolation technique the module already uses for
    _compute_ai_signal_win_rate_metrics."""
    start = _FIX_EFF_SOURCE.index("def take_fix_snapshot(")
    body = textwrap.dedent(_FIX_EFF_SOURCE[start:])

    class _FakeSnapshot:
        def __init__(self, **kw):
            self.__dict__.update(kw)
            self.taken_at = None

    class _HTTPError(Exception):
        def __init__(self, code, detail): self.code, self.detail = code, detail

    namespace = {
        "select": lambda *a, **k: _Q(record),
        "FixRecord": _FakeFixRecordModel, "FixSnapshot": _FakeSnapshot,
        "HTTPException": _HTTPError,
        "Session": object, "Depends": lambda *a, **k: None,
        "get_session": None, "get_current_username": None,
        "_SNAPSHOT_METRIC_FNS": metric_fns,
        "log": _NullLog(), "datetime": __import__("datetime").datetime,
        "timezone": __import__("datetime").timezone,
    }
    exec(body, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["take_fix_snapshot"](record.fix_id, session=session, _="tester")


class _Col:
    """Stand-in for a SQLAlchemy column so `FixRecord.fix_id == x` builds instead of raising."""
    def __eq__(self, o): return True
    def __hash__(self): return id(self)


class _FakeFixRecordModel:
    fix_id = _Col()


class _Q:
    def __init__(self, record): self._r = record
    def where(self, *a, **k): return self


class _NullLog:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass


class _FakeRecord:
    def __init__(self, domain, fixed_at):
        self.id = 1; self.fix_id = "AUD-TEST"; self.domain = domain; self.fixed_at = fixed_at


class _FakeSession:
    def __init__(self, record): self._r = record; self.added = []; self.commits = 0
    def execute(self, *a, **k):
        outer = self
        class R:
            def scalar_one_or_none(s): return outer._r
        return R()
    def add(self, o): self.added.append(o)
    def commit(self): self.commits += 1


def test_snapshot_passes_the_records_own_fixed_at_as_the_since_cutoff():
    """AUD-C01-FIXSNAPSHOTCUTOFF, stated as behaviour: the snapshot must measure the POST-FIX
    cohort only. Without the cutoff a snapshot blends in the pre-fix rows the fix corrected and
    dilutes exactly the improvement it exists to detect."""
    from datetime import datetime as _dt, timezone as _tz
    seen = {}

    def _spy(session, since=None):
        seen["since"] = since
        return {"by_bucket": {}, "total_resolved_5d": 7}

    rec = _FakeRecord("ai_signal", _dt(2026, 9, 3, 1, 57, 54, tzinfo=_tz.utc))
    sess = _FakeSession(rec)
    out = _run_take_snapshot(rec, metric_fns={"ai_signal": _spy}, session=sess)

    assert seen["since"] == date(2026, 9, 3), "must pass the record's own fixed_at date as `since`"
    assert out["status"] == "ok"
    assert out["metrics"]["since"] == "2026-09-03"
    assert sess.commits == 1 and len(sess.added) == 1
    assert sess.added[0].sample_size == 7


def test_snapshot_records_the_cutoff_on_the_snapshot_itself():
    """A stored snapshot whose window is unknowable is not evidence. The cutoff must travel
    with the numbers, including the disclosure that signal_date is date-granular."""
    from datetime import datetime as _dt, timezone as _tz
    rec = _FakeRecord("ai_signal", _dt(2026, 9, 3, 1, 57, 54, tzinfo=_tz.utc))
    sess = _FakeSession(rec)
    out = _run_take_snapshot(
        rec, metric_fns={"ai_signal": lambda s, since=None: {"by_bucket": {}, "total_resolved_5d": 0}},
        session=sess)
    assert "2026-09-03" in sess.added[0].note
    assert "date-granular" in out["metrics"]["cutoff_note"]


def test_unsupported_domain_records_an_explicit_snapshot_instead_of_failing():
    """Previously a bare HTTP 400, which made the daily scheduled recheck log a failure for this
    record forever while still reporting itself "ok", and left no durable trace. An explicit
    unsupported snapshot advances the recheck clock AND stays visible on the dashboard."""
    from datetime import datetime as _dt, timezone as _tz
    rec = _FakeRecord("decision_making", _dt(2026, 9, 3, 3, 19, 40, tzinfo=_tz.utc))
    sess = _FakeSession(rec)
    out = _run_take_snapshot(rec, metric_fns={"ai_signal": lambda s, since=None: {}}, session=sess)

    assert out["status"] == "unsupported"
    assert out["domain"] == "decision_making"
    assert sess.commits == 1 and len(sess.added) == 1
    assert sess.added[0].sample_size is None, "nothing was measured — must not claim a sample"
    assert "UNSUPPORTED" in sess.added[0].note


def test_main_py_registers_the_router_before_the_catch_all_router():
    main_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "main.py"
    main_source = main_path.read_text()
    fix_eff_idx = main_source.index("fix_effectiveness_router")
    routers_list_idx = main_source.index("routers=[")
    router_bare_idx = main_source.index(", router]", routers_list_idx)
    assert fix_eff_idx < router_bare_idx


# ── _compute_ai_signal_win_rate_metrics() — real query logic, real in-memory SQLite ─────

def _extract_compute_metrics_func():
    start = _FIX_EFF_SOURCE.index("def _compute_ai_signal_win_rate_metrics(")
    end = _FIX_EFF_SOURCE.index("\n@router.get", start)
    return textwrap.dedent(_FIX_EFF_SOURCE[start:end])


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Stock.__table__, SignalOutcome.__table__])
    return Session(engine)


def _run_compute(session, since=None):
    namespace = {
        "select": __import__("sqlalchemy").select,
        "func": __import__("sqlalchemy").func,
        "SignalOutcome": SignalOutcome,
        "Session": Session,
        "date": date,
    }
    exec(_extract_compute_metrics_func(), namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_compute_ai_signal_win_rate_metrics"](session, since=since)


def _add_stock(session, stock_id, market=None):
    session.add(Stock(id=stock_id, symbol=f"SYM{stock_id}", name=f"Stock {stock_id}",
                        market=market or Market.US, exchange=Exchange.NASDAQ, active=True, delisted=False))


_next_id = [0]


def _add_outcome(session, *, stock_id, horizon, direction, signal_date, is_correct_5d, return_5d, is_correct=None, pct_return=None):
    # SQLite doesn't auto-increment a BigInteger PK the same way it does a plain Integer PK —
    # assign both id and signal_id explicitly rather than relying on autoincrement.
    _next_id[0] += 1
    session.add(SignalOutcome(
        id=_next_id[0], signal_id=1000 + _next_id[0],
        stock_id=stock_id, symbol=f"SYM{stock_id}", horizon=horizon, signal_direction=direction,
        signal_date=signal_date, confidence=50.0,
        is_correct_5d=is_correct_5d, return_5d=return_5d,
        is_correct=is_correct, pct_return=pct_return,
    ))


def test_computes_win_rate_and_avg_return_per_horizon_and_direction():
    session = _make_session()
    _add_stock(session, 1)
    _add_outcome(session, stock_id=1, horizon=SignalHorizon.SWING, direction="BUY",
                  signal_date=date(2026, 8, 1), is_correct_5d=True, return_5d=0.02)
    _add_outcome(session, stock_id=1, horizon=SignalHorizon.SWING, direction="BUY",
                  signal_date=date(2026, 8, 2), is_correct_5d=False, return_5d=-0.03)
    session.commit()

    result = _run_compute(session)
    bucket = result["by_bucket"]["SWING|BUY"]
    assert bucket["resolved_5d"] == 2
    assert bucket["win_rate_5d"] == 0.5
    assert bucket["avg_return_5d_pct"] == -0.5  # avg(0.02, -0.03) * 100 = -0.5


def test_separates_buckets_by_both_horizon_and_direction():
    session = _make_session()
    _add_stock(session, 1)
    _add_outcome(session, stock_id=1, horizon=SignalHorizon.SWING, direction="BUY",
                  signal_date=date(2026, 8, 1), is_correct_5d=True, return_5d=0.05)
    _add_outcome(session, stock_id=1, horizon=SignalHorizon.SHORT, direction="SELL",
                  signal_date=date(2026, 8, 1), is_correct_5d=True, return_5d=-0.01)
    session.commit()

    result = _run_compute(session)
    assert "SWING|BUY" in result["by_bucket"]
    assert "SHORT|SELL" in result["by_bucket"]
    assert result["by_bucket"]["SWING|BUY"]["resolved_5d"] == 1
    assert result["by_bucket"]["SHORT|SELL"]["resolved_5d"] == 1


def test_unresolved_rows_are_excluded_from_win_rate_but_counted_in_total():
    session = _make_session()
    _add_stock(session, 1)
    _add_outcome(session, stock_id=1, horizon=SignalHorizon.SWING, direction="BUY",
                  signal_date=date(2026, 8, 1), is_correct_5d=None, return_5d=None)
    session.commit()

    result = _run_compute(session)
    bucket = result["by_bucket"]["SWING|BUY"]
    assert bucket["total"] == 1
    assert bucket["resolved_5d"] == 0
    assert bucket["win_rate_5d"] is None


def test_since_filter_excludes_rows_before_the_given_date():
    """The critical property for a real before/after comparison: a snapshot computed with
    `since=<fix date>` must never silently blend pre-fix and post-fix rows into one figure,
    which would understate any real improvement by diluting it with the exact biased
    population the fix corrected."""
    session = _make_session()
    _add_stock(session, 1)
    _add_outcome(session, stock_id=1, horizon=SignalHorizon.SWING, direction="BUY",
                  signal_date=date(2026, 8, 1), is_correct_5d=False, return_5d=-0.05)  # pre-fix, bad
    _add_outcome(session, stock_id=1, horizon=SignalHorizon.SWING, direction="BUY",
                  signal_date=date(2026, 9, 5), is_correct_5d=True, return_5d=0.03)  # post-fix, good
    session.commit()

    result_all = _run_compute(session)
    result_since = _run_compute(session, since=date(2026, 9, 1))
    assert result_all["by_bucket"]["SWING|BUY"]["resolved_5d"] == 2
    assert result_since["by_bucket"]["SWING|BUY"]["resolved_5d"] == 1
    assert result_since["by_bucket"]["SWING|BUY"]["win_rate_5d"] == 1.0


def test_base_is_correct_window_computed_alongside_the_5d_window():
    session = _make_session()
    _add_stock(session, 1)
    _add_outcome(session, stock_id=1, horizon=SignalHorizon.SWING, direction="BUY",
                  signal_date=date(2026, 8, 1), is_correct_5d=True, return_5d=0.02,
                  is_correct=False, pct_return=-0.04)
    session.commit()

    result = _run_compute(session)
    bucket = result["by_bucket"]["SWING|BUY"]
    assert bucket["resolved_base"] == 1
    assert bucket["win_rate_base"] == 0.0
    assert bucket["avg_pct_return_base"] == -4.0
