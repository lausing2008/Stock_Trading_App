"""Tests for T255-STRATEGY-TUNER-PER-HORIZON's POST /signals/tune_strategy.

routes.py can't be imported directly in this environment (conftest.py stubs `common`/`db`
wholesale) — so tune_strategy()'s real function body is extracted from routes.py and exec()'d
against real sqlalchemy + the real shared/db/models.py, with only its two side-effecting
collaborators (_get_redis, _record_tune_history) stubbed. This exercises the ACTUAL grid-search/
gating logic under test, not a hand-copied re-implementation that could silently drift from it —
matching the established technique in test_backfill_realized_ev.py.
"""
import importlib.util
import pathlib
import sys
from datetime import date, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test"] = _models
_spec.loader.exec_module(_models)

SignalOutcome = _models.SignalOutcome
Signal = _models.Signal
TuneHistory = _models.TuneHistory
Base = _models.Base

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "calibration.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


class _FakeRedis:
    """Records every setex call so tests can assert exactly what key/value was written,
    without needing a real Redis instance."""
    def __init__(self):
        self.writes: dict[str, str] = {}

    def setex(self, key, ttl, value):
        self.writes[key] = value


def _extract_tune_strategy(fake_redis, tune_history_calls, style_profiles=None):
    """Pulls tune_strategy()'s real body out of routes.py, exec()s it against real
    sqlalchemy/models with _get_redis/_record_tune_history/_STYLE_PROFILES stubbed."""
    start = _ROUTES_SOURCE.index("def tune_strategy(")
    end = _ROUTES_SOURCE.index('@router.post("/watchdog")', start)
    raw = _ROUTES_SOURCE[start:end]
    # Strip the FastAPI parameter defaults (Query(...)/Depends(...)) — this function is called
    # directly in tests, not through FastAPI's dependency injection.
    sig_end = raw.index("):\n") + 3
    body = raw[sig_end:]
    func_source = (
        "def tune_strategy(days=180, min_samples=_TUNE_STRATEGY_MIN_SAMPLES, session=None):\n"
        + body
    )

    _default_profiles = style_profiles or {
        h: {"buy_threshold": {"bull": 0.65}, "ml_weight_cap": 0.50}
        for h in ("SHORT", "SWING", "LONG", "GROWTH")
    }

    class _FakeGeneratorsModule:
        _STYLE_PROFILES = _default_profiles

    def _record_tune_history_stub(session, run_id, parameter_class, parameter_name, style, market,
                                   old_value, new_value, train_window, validation_window,
                                   train_ev_pct, validation_ev_pct, baseline_validation_ev_pct,
                                   validation_n, promoted, gate_failures, triggered_by="manual"):
        tune_history_calls.append({
            "style": style, "old_value": old_value, "new_value": new_value,
            "promoted": promoted, "gate_failures": gate_failures,
            "validation_ev_pct": validation_ev_pct, "baseline_validation_ev_pct": baseline_validation_ev_pct,
        })

    namespace = {
        "select": select,
        "SignalOutcome": SignalOutcome,
        "Signal": Signal,
        "date": date,
        "timedelta": timedelta,
        "_get_redis": lambda: fake_redis,
        "_record_tune_history": _record_tune_history_stub,
        "_TUNE_STRATEGY_MIN_SAMPLES": 15,
        "_TUNE_STRATEGY_BUY_GRID": [i / 100.0 for i in range(55, 86)],
        "_TUNE_STRATEGY_ML_CAP_GRID": [i / 100.0 for i in range(15, 76, 5)],
        "_TUNE_STRATEGY_BUY_BOUNDS": (0.55, 0.85),
        "_TUNE_STRATEGY_ML_CAP_BOUNDS": (0.15, 0.75),
        "__import__": __import__,
    }
    # `from ..generators.signals import _STYLE_PROFILES` inside the function body needs a real
    # importable module — register a fake one under that dotted path.
    import types
    fake_pkg = types.ModuleType("fake_generators_pkg")
    fake_signals_mod = types.ModuleType("fake_generators_pkg.signals")
    fake_signals_mod._STYLE_PROFILES = _default_profiles
    sys.modules["fake_generators_pkg"] = fake_pkg
    sys.modules["fake_generators_pkg.signals"] = fake_signals_mod

    func_source = func_source.replace(
        "from ..generators.signals import _STYLE_PROFILES",
        "from fake_generators_pkg.signals import _STYLE_PROFILES",
    )

    exec(func_source, namespace)  # noqa: S102 — isolated eval of one function's real source
    return namespace["tune_strategy"]


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[SignalOutcome.__table__, Signal.__table__, TuneHistory.__table__])
    return Session(engine)


def _add_pair(session, i, horizon, signal_date, fused_prob, ml_weight, is_correct, pct_return):
    session.add(Signal(
        id=i, stock_id=1, signal="BUY", horizon=horizon,
        confidence=50.0, reasons={"ml_weight": ml_weight}, ts=signal_date,
    ))
    session.add(SignalOutcome(
        id=i, signal_id=i, stock_id=1, symbol="TEST", horizon=horizon,
        signal_direction="BUY", signal_date=signal_date, confidence=50.0, fused_prob=fused_prob,
        is_correct=is_correct, pct_return=pct_return,
    ))


def _seed_clean_promotable_horizon(session, horizon="SWING", n=200):
    """A dataset where a tighter ml_weight_cap genuinely has better EV than the current
    baseline (0.65, 0.50): every outcome clears fused_prob 0.80 (so both the candidate's lower
    buy_threshold and the baseline's higher one select the exact same rows on THAT axis alone),
    but half of them were driven by an overconfident ML weight (0.45, above the candidate cap
    of 0.15 but within the baseline cap of 0.50's +0.05 tolerance) and lose money — while the
    low-ml-weight half (0.20, within both caps' tolerance) wins consistently. A candidate cap of
    0.15 (tolerance up to 0.20) excludes the losers; the baseline's 0.50 cap (tolerance up to
    0.55) includes them, dragging baseline EV down. This is deliberately a cap-driven
    distinction (not a threshold-driven one), so the test actually exercises the JOINT nature
    of the sweep rather than accidentally only testing buy_threshold alone."""
    base_date = date(2026, 1, 1)
    for i in range(n):
        d = base_date + timedelta(days=i // 3)
        # Alternate so both train (first 70%) and validation (last 30%) slices have the pattern.
        if i % 2 == 0:
            _add_pair(session, i, horizon, d, fused_prob=0.80, ml_weight=0.20, is_correct=True, pct_return=0.06)
        else:
            _add_pair(session, i, horizon, d, fused_prob=0.80, ml_weight=0.45, is_correct=False, pct_return=-0.04)
    session.commit()


# ── source-text checks ─────────────────────────────────────────────────────────

def test_endpoint_is_registered():
    assert '@router.post("/tune_strategy")' in _ROUTES_SOURCE
    assert "def tune_strategy(" in _ROUTES_SOURCE


def test_applies_through_the_existing_redis_keys_not_new_ones():
    """The whole point of reusing outcomes_calibrate_apply/tune_style_profiles' own keys is
    that _decide_style()/_get_style_tuned_param() need zero changes to pick this up."""
    start = _ROUTES_SOURCE.index("def tune_strategy(")
    end = _ROUTES_SOURCE.index('@router.post("/watchdog")', start)
    body = _ROUTES_SOURCE[start:end]
    assert 'f"stockai:signal_thresholds:{h}"' in body
    assert 'f"stockai:style_tune:{h}:ml_weight_cap"' in body


def test_never_applies_negative_ev_lift():
    start = _ROUTES_SOURCE.index("def tune_strategy(")
    end = _ROUTES_SOURCE.index('@router.post("/watchdog")', start)
    body = _ROUTES_SOURCE[start:end]
    assert "if ev_lift < 0:" in body
    idx = body.index("if ev_lift < 0:")
    next_gate_idx = body.index("if not (_TUNE_STRATEGY_BUY_BOUNDS", idx)
    assert 'gate_failures=["ev_lift_negative"]' in body[idx:next_gate_idx]


def test_records_one_tune_history_row_per_horizon_regardless_of_outcome():
    """Matches every sibling mechanism's convention: 4 horizons in, 4 TuneHistory rows out,
    promoted or not."""
    start = _ROUTES_SOURCE.index("def tune_strategy(")
    end = _ROUTES_SOURCE.index('@router.post("/watchdog")', start)
    body = _ROUTES_SOURCE[start:end]
    # 7 skip/reject gates (insufficient_total_samples, no_candidate_met_train_criteria,
    # candidate_unmeasurable_on_validation, baseline_unmeasurable_on_validation,
    # ev_lift_negative, ev_lift_below_min_and_shift_too_small, suggested_outside_sane_bounds)
    # + 1 success/applied path = 8.
    assert body.count("_record_tune_history(") == 8


# ── behavioral checks against the real, extracted tune_strategy() ─────────────

def test_skips_horizon_with_insufficient_total_samples():
    session = _make_session()
    tune_history_calls = []
    fake_redis = _FakeRedis()
    func = _extract_tune_strategy(fake_redis, tune_history_calls)
    for i in range(10):
        _add_pair(session, i, "LONG", date(2026, 1, 1), 0.70, 0.30, True, 0.03)
    session.commit()
    result = func(days=3650, min_samples=15, session=session)
    long_skip = next(s for s in result["skipped"] if s["horizon"] == "LONG")
    assert "need 30 for a valid train/validation split" in long_skip["reason"]
    assert not any(a["horizon"] == "LONG" for a in result["applied"])


def test_promotes_a_genuinely_better_combination_on_a_clean_dataset():
    session = _make_session()
    tune_history_calls = []
    fake_redis = _FakeRedis()
    func = _extract_tune_strategy(fake_redis, tune_history_calls)
    _seed_clean_promotable_horizon(session, "SWING", n=200)
    result = func(days=3650, min_samples=15, session=session)
    swing_applied = [a for a in result["applied"] if a["horizon"] == "SWING"]
    assert len(swing_applied) == 1, f"expected SWING to be promoted, got skipped={result['skipped']}"
    assert swing_applied[0]["ev_lift_pct"] > 0
    # Applied through the existing keys, not new ones.
    assert "stockai:signal_thresholds:SWING" in fake_redis.writes
    assert "stockai:style_tune:SWING:ml_weight_cap" in fake_redis.writes


def test_never_promotes_a_tied_or_near_zero_lift_with_a_trivial_grid_shift():
    """T255-MINLIFT-PARITY regression: caught live in production during this feature's own
    initial deploy — a candidate with ev_lift_pct exactly 0.0 (a tie, not an improvement) was
    applied because only the hard `< 0` floor existed. A dataset where every outcome behaves
    identically regardless of threshold/cap (constant fused_prob/ml_weight/return) produces
    an exact tie between any candidate and the baseline — this must be rejected as noise, not
    promoted just because it isn't negative."""
    session = _make_session()
    tune_history_calls = []
    fake_redis = _FakeRedis()
    func = _extract_tune_strategy(fake_redis, tune_history_calls)
    base_date = date(2026, 1, 1)
    for i in range(200):
        d = base_date + timedelta(days=i // 3)
        _add_pair(session, i, "SHORT", d, fused_prob=0.90, ml_weight=0.05, is_correct=True, pct_return=0.04)
    session.commit()
    result = func(days=3650, min_samples=15, session=session)
    assert not any(a["horizon"] == "SHORT" for a in result["applied"]), (
        f"a tied (ev_lift_pct == 0) candidate with only a trivial grid shift must never be applied: {result}"
    )
    short_skip = next(s for s in result["skipped"] if s["horizon"] == "SHORT")
    assert "below min" in short_skip["reason"]


def test_never_promotes_when_candidate_does_not_beat_baseline_on_validation():
    """A dataset where the train slice makes a tight (buy_threshold=0.55, ml_weight_cap=0.15)
    combo look best (it excludes a cluster of high-ml-weight losers that the wider baseline
    cap of 0.50 includes) but that SAME cap-driven edge reverses on the validation slice — the
    low-ml-weight cohort turns into the loser there instead. A candidate must beat the
    baseline on validation, not just look good on train; this must be rejected, not applied
    on a train-only fluke."""
    session = _make_session()
    tune_history_calls = []
    fake_redis = _FakeRedis()
    func = _extract_tune_strategy(fake_redis, tune_history_calls)
    base_date = date(2026, 1, 1)
    # Train slice (older 70%): low-ml-weight (0.20) wins, high-ml-weight (0.45) loses — makes
    # the tight cap (0.15, tolerance 0.20) look like the clear winner over baseline (0.50).
    for i in range(140):
        d = base_date + timedelta(days=i // 3)
        if i % 2 == 0:
            _add_pair(session, i, "GROWTH", d, fused_prob=0.80, ml_weight=0.20, is_correct=True, pct_return=0.08)
        else:
            _add_pair(session, i, "GROWTH", d, fused_prob=0.80, ml_weight=0.45, is_correct=False, pct_return=-0.06)
    # Validation slice (newer 30%): the pattern REVERSES — low-ml-weight now loses, high-ml-
    # weight now wins. The tight cap's train-slice "edge" does not replicate here, so its
    # validation EV should be WORSE than (or equal to) the baseline's, which still captures
    # both cohorts and nets out closer to even.
    for i in range(140, 200):
        d = base_date + timedelta(days=i // 3)
        if i % 2 == 0:
            _add_pair(session, i, "GROWTH", d, fused_prob=0.80, ml_weight=0.20, is_correct=False, pct_return=-0.06)
        else:
            _add_pair(session, i, "GROWTH", d, fused_prob=0.80, ml_weight=0.45, is_correct=True, pct_return=0.08)
    session.commit()
    result = func(days=3650, min_samples=15, session=session)
    assert not any(a["horizon"] == "GROWTH" for a in result["applied"]), (
        f"a candidate that loses (or ties) on the validation slice must never be applied: {result}"
    )


def test_writes_a_tune_history_row_even_when_skipped():
    session = _make_session()
    tune_history_calls = []
    fake_redis = _FakeRedis()
    func = _extract_tune_strategy(fake_redis, tune_history_calls)
    for i in range(5):
        _add_pair(session, i, "SHORT", date(2026, 1, 1), 0.70, 0.30, True, 0.03)
    session.commit()
    func(days=3650, min_samples=15, session=session)
    assert any(c["style"] == "SHORT" and c["promoted"] is False for c in tune_history_calls)


def test_grid_search_only_considers_ml_weight_within_cap_plus_tolerance():
    """A cell for cap=X must only include outcomes whose recorded ml_weight <= X + 0.05 —
    matching tune_style_profiles' own +0.05 tolerance convention exactly."""
    start = _ROUTES_SOURCE.index("def tune_strategy(")
    end = _ROUTES_SOURCE.index('@router.post("/watchdog")', start)
    body = _ROUTES_SOURCE[start:end]
    assert 'r.get("ml_weight", 0) <= cap + 0.05' in body
