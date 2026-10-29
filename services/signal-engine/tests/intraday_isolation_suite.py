"""Intraday Stage C daily isolation, signal-engine side (design §10 guard coverage matrix).

Each test is the witness for one guard; the sabotage pass removes each guard and expects its
own witness here to fail. Kinds of witness, labelled per test:
  behaviour   -- the guarded call is refused/skipped (an HTTP 422 before any SQL, a skip count)
  compiled    -- every statement the reader executes against signals/signal_outcomes carries the
                 daily-horizon predicate, read from the real compiled PostgreSQL SQL. On
                 PostgreSQL this filter cannot change a result while the enum lacks INTRADAY; it
                 is a CODE-PRESENCE check for defence in depth, not an independent data guard.
  key set     -- a daily map's keys equal DAILY_HORIZONS.
"""
import ast
import importlib.util
import pathlib
import sys
import types
from unittest.mock import MagicMock, patch

# Run by test_intraday_daily_isolation.py with --noconftest: this directory's conftest stubs `db`
# (so no statement could be compiled) and omits common.jwt_auth (so the API modules cannot load).
# Here the ORM models are REAL; only the JWT dependency and the session factory are replaced.
ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "shared"), str(ROOT / "services" / "signal-engine")]
_db = types.ModuleType("db")
_db.__path__ = [str(ROOT / "shared/db")]
sys.modules["db"] = _db
_spec = importlib.util.spec_from_file_location("db.models", ROOT / "shared/db/models.py")
_models = importlib.util.module_from_spec(_spec)
sys.modules["db.models"] = _models
_spec.loader.exec_module(_models)
for _k in vars(_models):
    setattr(_db, _k, getattr(_models, _k))
_db.get_session = lambda: None
_db.SessionLocal = MagicMock()
_jwt = types.ModuleType("common.jwt_auth")
_jwt.get_current_username = lambda: "u"
sys.modules["common.jwt_auth"] = _jwt

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from common.horizons import DAILY_HORIZONS, UnsupportedHorizon
from src.api import analytics, calibration, outcomes, routes
from src.api import signals_shared
from src.generators import signals as generators
from src import calibration_store

SRC = ROOT / "services" / "signal-engine" / "src"
DAILY_SQL = "horizon IN ('SHORT', 'SWING', 'LONG', 'GROWTH')"


# ── key sets ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,mapping", [
    ("_STYLE_PROFILES", generators._STYLE_PROFILES),
    ("_OUTCOME_HOLD_DAYS", signals_shared._OUTCOME_HOLD_DAYS),
    ("_SELL_OUTCOME_HOLD_DAYS", signals_shared._SELL_OUTCOME_HOLD_DAYS),
])
def test_daily_maps_are_pinned_to_the_daily_horizons(name, mapping):
    assert set(mapping) == set(DAILY_HORIZONS), name


# ── behaviour: unknown-style fallbacks refuse instead of widening/narrowing ─────────────────

def _no_sql_session():
    s = MagicMock()
    s.execute.side_effect = AssertionError("SQL ran for a refused style")
    s.query.side_effect = AssertionError("SQL ran for a refused style")
    return s


@pytest.mark.parametrize("call", [
    lambda s: routes.all_latest_signals(style="INTRADAY", session=s, _="u"),
    lambda s: routes.suppressed_signals(style="INTRADAY", market=None, session=s),
    lambda s: routes.signal_history(symbol="AAA", style="intraday", days=60, session=s),
    lambda s: routes.signal_for("AAA", persist=False, live=False, style="INTRADAY", session=s),
    lambda s: routes.signal_for("AAA", persist=True, live=True, style="BOGUS", session=s),
])
def test_unknown_or_intraday_style_is_422_before_any_sql(call):
    with patch.object(routes, "generate_all_signals", side_effect=AssertionError("live regeneration ran")):
        with pytest.raises(HTTPException) as exc:
            call(_no_sql_session())
    assert exc.value.status_code == 422 and exc.value.detail["error"] == "unsupported_horizon"


def test_intraday_rejection_points_to_the_intraday_api():
    with pytest.raises(HTTPException) as exc:
        routes._daily_style_or_422("INTRADAY")
    assert "/intraday" in exc.value.detail["hint"]
    assert routes._daily_style_or_422("swing") == "SWING"


# ── behaviour: the outcome evaluator skips and counts instead of aborting ──────────────────

def test_outcome_hold_days_refuses_non_daily_horizons():
    assert outcomes._hold_days_for("SHORT", "SELL") == signals_shared._SELL_OUTCOME_HOLD_DAYS["SHORT"]
    for bad in ("INTRADAY", "intraday ", "SWINGX"):
        with pytest.raises(UnsupportedHorizon):
            outcomes._hold_days_for(bad, "BUY")


def _calls_in(func_name, path, callee):
    tree = ast.parse(path.read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func_name)
    return [n for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == callee]


def test_both_outcome_loops_route_hold_days_through_the_guard_code_presence():
    """code presence: both passes read the hold window only through _hold_days_for, inside a
    try that catches UnsupportedHorizon (a direct _OUTCOME_HOLD_DAYS[...] would KeyError)."""
    path = SRC / "api" / "outcomes.py"
    assert len(_calls_in("evaluate_signal_outcomes", path, "_hold_days_for")) == 2
    fn = next(n for n in ast.walk(ast.parse(path.read_text()))
              if isinstance(n, ast.FunctionDef) and n.name == "evaluate_signal_outcomes")
    subscripts = [n for n in ast.walk(fn) if isinstance(n, ast.Subscript)
                  and getattr(n.value, "id", None) in ("_OUTCOME_HOLD_DAYS", "_SELL_OUTCOME_HOLD_DAYS")]
    handlers = [h for n in ast.walk(fn) if isinstance(n, ast.Try) for h in n.handlers
                if getattr(h.type, "id", None) == "UnsupportedHorizon"]
    assert subscripts == [] and len(handlers) == 2


def test_both_signal_upserts_bind_a_validated_daily_horizon_code_presence():
    """code presence: the two INSERT ... CAST(:hor AS signalhorizon) executions pass
    hor=parse_daily_horizon(...), so an INTRADAY value raises before SQL."""
    tree = ast.parse((SRC / "api" / "routes.py").read_text())
    hors = [kw for n in ast.walk(tree) if isinstance(n, ast.Call) for kw in n.keywords if kw.arg == "hor"]
    assert len(hors) == 2
    assert all(isinstance(k.value, ast.Call) and getattr(k.value.func, "id", None) == "parse_daily_horizon" for k in hors)


# ── compiled: every daily reader statement carries the predicate ──────────────────────────

class _Result:
    def all(self): return []
    def first(self): return None
    def scalar(self): return None
    def scalar_one(self): return 0
    def scalars(self): return self
    def fetchall(self): return []
    def __iter__(self): return iter([])


class _CaptureSession:
    def __init__(self, first_rows=None):
        self.stmts, self._first_rows = [], first_rows

    def execute(self, stmt, *a, **k):
        self.stmts.append(stmt)
        if self._first_rows is not None and len(self.stmts) == 1:
            r = MagicMock()
            r.all.return_value = self._first_rows
            return r
        return _Result()

    def query(self, *a, **k):
        raise AssertionError("ORM query path not expected")


def _sql(stmt):
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def _assert_guarded(session, *, tables=("signals", "signal_outcomes", "signal_outcome_horizons")):
    reads = [s for s in map(_sql, session.stmts) if any(f"FROM {t}" in s or f"JOIN {t}" in s for t in tables)]
    assert reads, "the reader executed no statement against the daily tables"
    for s in reads:
        assert DAILY_SQL in s, s[:400]


@pytest.mark.parametrize("name,call", [
    # Both are behind F12's @research_only guard (they return before any SQL in production);
    # the witness exercises the wrapped body so the filter is there if the guard is ever lifted.
    ("calibrate_ta_weights",
     lambda s: calibration.calibrate_ta_weights.__wrapped__(lookback_days=365, session=s, _="u")),
    ("calibrate_conviction_weights",
     lambda s: calibration.calibrate_conviction_weights.__wrapped__(lookback_days=365, min_count=10, session=s, _="u")),
    ("outcomes_calibration", lambda s: calibration.outcomes_calibration(days=180, session=s)),
    ("signal_accuracy", lambda s: analytics.signal_accuracy(lookback_days=90, symbol=None, market=None, from_date=None,
                                                             to_date=None, page=1, page_size=200, session=s)),
    ("rolling_accuracy", lambda s: analytics.rolling_accuracy(window=30, lookback_days=180, session=s)),
    ("factor_exposure", lambda s: analytics.factor_exposure(lookback_days=90, session=s)),
    ("walkforward_backtest", lambda s: analytics.walkforward_backtest(train_days=180, test_days=30, lookback_days=365,
                                                                       hold_days=5, session=s)),
    ("outcomes_summary", lambda s: analytics.outcomes_summary(horizon=None, days=90, market=None, symbol=None, session=s)),
    ("signal_consensus", lambda s: routes.signal_consensus(market=None, session=s, _="u")),
])
def test_daily_reader_statements_carry_the_daily_predicate_compiled(name, call):
    s = _CaptureSession()
    with patch.object(analytics, "_get_redis", return_value=None, create=True), \
         patch.object(calibration, "_get_redis", return_value=None, create=True):
        try:
            call(s)
        except HTTPException:
            pass                    # e.g. "need >= 30 outcomes": the guarded read already ran
    _assert_guarded(s)


def test_recent_changes_statement_carries_the_daily_predicate_compiled():
    row = MagicMock(id=1, symbol="AAA")
    row.name = "a"
    s = _CaptureSession(first_rows=[row])
    routes.recent_signal_changes(symbols="AAA", hours=48, session=s)
    _assert_guarded(s)


def test_calibration_store_read_carries_the_daily_predicate_compiled():
    from datetime import datetime, timezone
    s = _CaptureSession()
    with patch.object(calibration_store, "compare", return_value={}):
        calibration_store.load_comparison(s, now=datetime(2026, 10, 29, tzinfo=timezone.utc))
    _assert_guarded(s)


# ── structural: the storage boundary (NOT counted as a witness for any filter) ─────────────

def _load_intraday_store():
    """market-data's Intraday package under an alias (both services name their package `src`)."""
    pkg = types.ModuleType("mdi")
    pkg.__path__ = [str(ROOT / "services" / "market-data" / "src" / "services" / "intraday")]
    sys.modules["mdi"] = pkg
    import importlib
    return (importlib.import_module("mdi.contract"), importlib.import_module("mdi.schema"),
            importlib.import_module("mdi.store"))


def _daily_outputs(session):
    out = {
        "summary": analytics.outcomes_summary(horizon=None, days=90, market=None, symbol=None, session=session),
        "calibration": calibration.outcomes_calibration(days=180, session=session),
        "consensus": routes.signal_consensus(market=None, session=session, _="u"),
    }
    return out


def test_intraday_records_leave_daily_outputs_identical_structural(tmp_path):
    """Extreme Intraday records (every setup entered and a 100% winner) beside real daily rows:
    daily readers never query the Intraday tables, so their outputs are identical. This shows the
    storage boundary only; it cannot detect a removed horizon filter (see the compiled tests)."""
    from datetime import date, datetime, timedelta, timezone
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    m = _models
    engine = create_engine(f"sqlite:///{tmp_path / 'iso.db'}")
    m.Base.metadata.create_all(engine, tables=[m.Stock.__table__, m.Signal.__table__, m.SignalOutcome.__table__])
    today = date.today()
    with Session(engine) as s:
        for i, sym in enumerate(("AAA", "BBB"), start=1):
            s.add(m.Stock(id=i, symbol=sym, market=m.Market.US, exchange=m.Exchange.NASDAQ, name=sym, active=True,
                          delisted=False))
        for j in range(8):
            sid = 100 + j
            s.add(m.Signal(id=sid, stock_id=1 + j % 2, signal=m.SignalType.BUY, horizon=m.SignalHorizon.SWING,
                           confidence=50 + j, bullish_probability=0.6, ts=datetime.now(timezone.utc) - timedelta(days=j + 1)))
            s.add(m.SignalOutcome(id=200 + j, signal_id=sid, stock_id=1 + j % 2, symbol=("AAA", "BBB")[j % 2],
                                  horizon=m.SignalHorizon.SWING, signal_direction="BUY",
                                  signal_date=today - timedelta(days=20 + j), confidence=50 + j * 5,
                                  is_correct=bool(j % 3), pct_return=0.01 * (j - 3)))
        s.commit()
    with Session(engine) as s:
        before = _daily_outputs(s)

    c, schema, store = _load_intraday_store()
    with engine.begin() as conn:
        schema.create(conn)
        ident = store.ensure_identity(conn, strategy_version="1", strategy_source_hash="s" * 64, params=c.Params())
        b = store.start_batch(conn, run_key="observed", identity=ident, code_version="x",
                              started_at=datetime(2026, 10, 28, 14, 7), data_cutoff=datetime(2026, 10, 28, 14, 7))
        times = c.session_times("US", date(2026, 10, 28))
        for k, sym in enumerate(("AAA", "BBB")):
            trig = datetime(2026, 10, 28, 14, 0, tzinfo=timezone.utc) + timedelta(minutes=5 * k)
            det = c.decide_at_detection(trigger_bar_open=trig, detected_at=trig + timedelta(minutes=7),
                                        direction="bullish", times=times, stop_breached=False, inputs_ok=True)
            res = store.record_evaluation(
                conn, batch_id=b, run_key="observed", identity=ident, stock_id=k + 1, symbol=sym,
                trigger_bar_open=trig, result="setup", detected_at=trig + timedelta(minutes=7),
                data_cutoff=trig + timedelta(minutes=7), detection=det, times=times, input_records=[],
                setup=dict(direction="bullish", entry_reference=100.0, stop=99.0, target=200.0, risk_per_share=1.0,
                           reasons={"rsi": 99, "ta_score": 1.0, "atr_14_pct": 50.0}))
            ev = c.EventTimes(trig + timedelta(minutes=9), trig + timedelta(minutes=10), trig + timedelta(minutes=10), "observed")
            store.append_transition(conn, setup_id=res["setup_id"], to_state="entered", event=ev, idempotency_key=f"e{k}")
            store.record_outcome(conn, setup_id=res["setup_id"], kind="trade", window="trade", evaluator_version="v1",
                                 evidence_hash="e", status="realized", values={"r": 99.0, "win": True},
                                 now=datetime(2026, 10, 28, 20, 0))
    with Session(engine) as s:
        after = _daily_outputs(s)
    assert before == after
    assert before["summary"]["overall"] and before["consensus"]          # the comparison is not vacuous


def test_outcome_evaluator_pending_read_is_daily_only_compiled():
    """compiled: the evaluator's pending-signal select (frozen first BUY/SELL) carries the
    daily predicate. Code presence on PostgreSQL."""
    s = _CaptureSession()
    try:
        outcomes.evaluate_signal_outcomes(session=s, _="u")
    except Exception:  # noqa: BLE001 - only the statements already issued matter
        pass
    pending = [q for q in map(_sql, s.stmts) if "first_buy_sell_signal IN" in q and "FROM signals" in q]
    assert len(pending) == 1 and DAILY_SQL in pending[0], pending
