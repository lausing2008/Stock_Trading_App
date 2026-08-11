"""Tests for AUD263-STYLEPROFILES-SUPERSET-BASELINE.

tune_style_profiles()'s ml_weight_cap sweep used to compare a candidate cap's validation
subset against EVERY validation outcome unfiltered — a strict SUPERSET of the candidate's own
subset. "Does removing some rows raise the mean" is close to a coin flip whenever the excluded
high-ml_weight rows happen to have below-average returns in that window, so the candidate beat
its own superset by chance on a regular basis, with no real edge required. Fixed to compare
against the CURRENT LIVE cap's own filtered subset instead — tune_strategy() already does
exactly this correctly (calibration.py:2328); this function was simply never updated to match.

calibration.py can't be imported directly in this test environment (its import chain pulls in
common.jwt_auth) — tune_style_profiles()'s real source is extracted via exec() and run against
real sqlalchemy + the real shared/db/models.py, matching test_tune_strategy.py's established
technique for the sibling function exactly (same file, same shape of DB-heavy dependency).
"""
import importlib.util
import pathlib
import sys
import types
from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_styleprofiles", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_styleprofiles"] = _models
_spec.loader.exec_module(_models)

SignalOutcome = _models.SignalOutcome
Signal = _models.Signal
SignalHorizon = _models.SignalHorizon
TuneHistory = _models.TuneHistory
Base = _models.Base

_CALIB_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "calibration.py"
_CALIB_SOURCE = _CALIB_PATH.read_text()


class _FakeRedis:
    def __init__(self):
        self.writes: dict[str, str] = {}

    def setex(self, key, ttl, value):
        self.writes[key] = value


def _extract_tune_style_profiles(fake_redis, tune_history_calls, style_profiles=None):
    """Pulls tune_style_profiles()'s real body out of calibration.py, exec()s it against real
    sqlalchemy/models with only its side-effecting collaborators stubbed — same technique as
    test_tune_strategy.py uses for the sibling function in the same file."""
    from sqlalchemy import select

    start = _CALIB_SOURCE.index("def tune_style_profiles(")
    end = _CALIB_SOURCE.index('return {"applied": applied, "skipped": skipped,', start)
    end = _CALIB_SOURCE.index("\n", end) + 1
    raw = _CALIB_SOURCE[start:end]
    sig_end = raw.index("):\n") + 3
    body = raw[sig_end:]
    func_source = (
        "def tune_style_profiles(days=120, min_samples=10, session=None):\n" + body
    )

    _default_profiles = style_profiles or {
        h: {"ml_weight_cap": 0.50} for h in ("SHORT", "SWING", "LONG", "GROWTH")
    }

    def _record_tune_history_stub(session, run_id, parameter_class, parameter_name, style, market,
                                   old_value, new_value, train_window, validation_window,
                                   train_ev_pct, validation_ev_pct, baseline_validation_ev_pct,
                                   validation_n, promoted, gate_failures, triggered_by="manual"):
        tune_history_calls.append({
            "style": style, "param": parameter_name, "old_value": old_value, "new_value": new_value,
            "promoted": promoted, "gate_failures": gate_failures,
            "validation_ev_pct": validation_ev_pct, "baseline_validation_ev_pct": baseline_validation_ev_pct,
        })

    def _mark_tuned_stub(*a, **kw):
        pass

    namespace = {
        "select": select,
        "SignalOutcome": SignalOutcome,
        "Signal": Signal,
        "date": date,
        "timedelta": timedelta,
        "_get_redis": lambda: fake_redis,
        "_record_tune_history": _record_tune_history_stub,
        "_mark_tuned": _mark_tuned_stub,
        "__import__": __import__,
    }

    fake_pkg = types.ModuleType("fake_generators_pkg_styleprofiles")
    fake_signals_mod = types.ModuleType("fake_generators_pkg_styleprofiles.signals")
    fake_signals_mod._STYLE_PROFILES = _default_profiles
    sys.modules["fake_generators_pkg_styleprofiles"] = fake_pkg
    sys.modules["fake_generators_pkg_styleprofiles.signals"] = fake_signals_mod

    func_source = func_source.replace(
        "from ..generators.signals import _STYLE_PROFILES",
        "from fake_generators_pkg_styleprofiles.signals import _STYLE_PROFILES",
    )

    exec(func_source, namespace)  # noqa: S102 — isolated eval of one function's real source
    return namespace["tune_style_profiles"]


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[SignalOutcome.__table__, Signal.__table__, TuneHistory.__table__])
    return Session(engine)


def _mk_outcome_and_signal(session, *, signal_date, ml_weight, pct_return, is_correct, oid, horizon="SWING"):
    sig = Signal(id=oid, stock_id=1, horizon=SignalHorizon.SWING if horizon == "SWING" else SignalHorizon(horizon),
                 signal="BUY", confidence=60.0, ts=signal_date, reasons={"ml_weight": ml_weight})
    session.add(sig)
    outcome = SignalOutcome(
        id=oid, signal_id=oid, stock_id=1, symbol="TEST", confidence=60.0,
        horizon=SignalHorizon.SWING if horizon == "SWING" else SignalHorizon(horizon),
        signal_date=signal_date, signal_direction="BUY",
        is_correct=is_correct, pct_return=pct_return,
    )
    session.add(outcome)


def test_baseline_is_filtered_by_the_current_live_cap_not_unfiltered(monkeypatch):
    """The exact fix: with a current live cap of 0.50, the baseline subset must exclude rows
    with ml_weight > 0.55 (0.50 + the 0.05 tolerance) — not include every validation row."""
    session = _make_session()
    fake_redis = _FakeRedis()
    tune_history_calls = []
    fn = _extract_tune_style_profiles(fake_redis, tune_history_calls, style_profiles={
        "SWING": {"ml_weight_cap": 0.50}, "SHORT": {"ml_weight_cap": 0.50},
        "LONG": {"ml_weight_cap": 0.50}, "GROWTH": {"ml_weight_cap": 0.50},
    })

    base_date = date.today() - timedelta(days=65)
    oid = 1
    # 60 rows total, chronological — first 42 (70%) train, last 18 (30%) validation.
    # Validation half: half at ml_weight=0.30 (below the 0.50 cap, real-return -1%), half at
    # ml_weight=0.90 (well above the cap, real-return +5%) — designed so the OLD unfiltered
    # baseline (which includes the high-ml_weight winners) would score much higher than the
    # NEW cap-filtered baseline (which correctly excludes them).
    for i in range(60):
        is_val = i >= 42
        if is_val:
            ml_w = 0.30 if (i - 42) % 2 == 0 else 0.90
            ret = -0.01 if ml_w == 0.30 else 0.05
        else:
            ml_w = 0.40
            ret = 0.01
        _mk_outcome_and_signal(
            session, signal_date=base_date + timedelta(days=i), ml_weight=ml_w,
            pct_return=ret, is_correct=(ret > 0), oid=oid,
        )
        oid += 1
    session.commit()

    fn(days=120, min_samples=3, session=session)

    swing_calls = [c for c in tune_history_calls if c["style"] == "SWING" and c["param"] == "ml_weight_cap"]
    assert len(swing_calls) == 1
    # The old baseline (unfiltered) would have averaged in the +5% high-ml_weight winners,
    # producing a baseline EV pulled up toward +2%. The fixed, cap-filtered baseline only sees
    # the ml_weight=0.30 rows (-1% each) — a materially lower, CORRECT baseline.
    assert swing_calls[0]["baseline_validation_ev_pct"] < 0.0


def test_old_value_records_the_real_current_live_cap_not_an_empty_placeholder():
    """Regression guard: old_value used to be a bare {} — now records the real
    CURRENT_ML_CAP[style] value, matching tune_strategy()'s own convention of recording a real
    old value in every TuneHistory row."""
    session = _make_session()
    fake_redis = _FakeRedis()
    tune_history_calls = []
    fn = _extract_tune_style_profiles(fake_redis, tune_history_calls, style_profiles={
        "SWING": {"ml_weight_cap": 0.42}, "SHORT": {"ml_weight_cap": 0.42},
        "LONG": {"ml_weight_cap": 0.42}, "GROWTH": {"ml_weight_cap": 0.42},
    })

    base_date = date.today() - timedelta(days=65)
    for i in range(60):
        _mk_outcome_and_signal(
            session, signal_date=base_date + timedelta(days=i), ml_weight=0.40,
            pct_return=0.01, is_correct=True, oid=i + 1,
        )
    session.commit()

    fn(days=120, min_samples=3, session=session)

    swing_calls = [c for c in tune_history_calls if c["style"] == "SWING" and c["param"] == "ml_weight_cap"]
    assert len(swing_calls) == 1
    assert swing_calls[0]["old_value"] == {"ml_weight_cap": 0.42}
