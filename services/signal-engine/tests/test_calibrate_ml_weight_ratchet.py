"""Tests for AUD283-MLWEIGHT-RATCHET — calibrate_ml_weight() previously compared its candidate
fusion-weight cap against a hardcoded neutral 0.5 baseline instead of the actual LIVE cap
(prev_cap, already fetched at the top of the real function but only used for
TuneHistory.old_value bookkeeping). A candidate that beat 0.5 but was genuinely WORSE than the
cap already in production could still get promoted — the self-tuning mechanism could silently
walk this parameter in a bad direction indefinitely, with no requirement to ever beat where it
actually is.

calibration.py can't be imported directly in this environment (it needs common.jwt_auth /
FastAPI Depends / db, none for-real-installed here). Since 2026-10-22 the calibrator is
`_ml_weight_sweep` (pure evaluation, shared with the GET preview) plus `_ml_weight_apply` (the
only writing step); both real functions are loaded by AST and run against synthetic
`rows`/`price_rows` fixtures with the writers injected as fakes.
"""
import ast
import math
import pathlib

import pytest
from src.generators import signals
from datetime import date, datetime, timedelta, timezone

_CAL_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "calibration.py"
_CAL_SOURCE = _CAL_PATH.read_text()

_OUTCOME_HOLD_DAYS = {"SHORT": 7, "SWING": 14, "LONG": 28, "GROWTH": 14}
_HOLD = _OUTCOME_HOLD_DAYS["SWING"]


class _FakeHorizon:
    def __init__(self, value):
        self.value = value


class _FakeSignal:
    def __init__(self, stock_id, ts, horizon, ml_prob, ta_score):
        self.stock_id = stock_id
        self.ts = ts
        self.horizon = _FakeHorizon(horizon)
        self.reasons = {"ml_probability": ml_prob, "ta_score": ta_score,
                        "ml_test_auc": .70, "market_regime": "bull"}


class _FakePrice:
    def __init__(self, stock_id, ts, close):
        self.stock_id = stock_id
        self.ts = ts
        self.close = close


@pytest.fixture(autouse=True)
def _policy(monkeypatch):
    monkeypatch.setattr(signals, "_get_style_tuned_param", lambda style, param, default: default)
    monkeypatch.setattr(signals, "_get_dynamic_buy_threshold", lambda style, regime: .81)


def _calibration_helpers():
    """Load every real `_ml_*` function/constant from calibration.py plus the module-level names
    they use. The DB-reading loader and FastAPI routes are bypassed; rows are injected."""
    from common.outcome_price_basis import classify_observation_window, resolve_window

    class _FakeLog:
        def info(self, *a, **kw):
            pass

    namespace = {"__package__": "src.api", "math": math, "datetime": datetime,
                 "timedelta": timedelta, "date": date, "_OUTCOME_HOLD_DAYS": _OUTCOME_HOLD_DAYS,
                 "_resolve_outcome_basis": resolve_window,
                 "_classify_window": classify_observation_window, "log": _FakeLog()}
    tree = ast.parse(_CAL_SOURCE).body
    nodes = [node for node in tree
             if (isinstance(node, ast.FunctionDef)
                 and (node.name.startswith("_ml_")
                      or node.name in ("_observation_basis", "_first_adjusted_after")))
             or (isinstance(node, ast.Assign) and any(
                 isinstance(t, ast.Name) and t.id.startswith("_ML_") for t in node.targets))]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(_CAL_PATH), "exec"), namespace)
    return namespace


_helpers = None


def _run_core(rows, price_rows, prev_cap):
    """The real calibrator path: shared sweep, then the only writing step, with fake writers."""
    global _helpers
    if _helpers is None:
        _helpers = _calibration_helpers()
    recorded = {}

    def _fake_record_tune_history(session, run_id, parameter_class, parameter_name, style, market,
                                   old_value, new_value, train_window, validation_window,
                                   train_ev_pct, validation_ev_pct, baseline_validation_ev_pct,
                                   validation_n, promoted, gate_failures):
        recorded.update(locals())

    applied = {}

    def _fake_set_cap(w):
        applied["weight"] = w

    sweep = _helpers["_ml_weight_sweep"](rows, price_rows, {}, {}, prev_cap)
    result = _helpers["_ml_weight_apply"](None, sweep, prev_cap, 180, _fake_set_cap,
                                          _fake_record_tune_history)
    return result, recorded, applied


def _make_row(sid, sig_date, ml_prob, ta_score, entry_close, exit_close):
    """One (Signal, symbol) tuple plus its matching entry/exit Price rows — mirrors the real
    query shape (select(Signal, Stock.symbol)) and the real entry lookup convention
    (_first_close_after(sig.stock_id, signal_date), i.e. strictly AFTER signal_date — T+1)."""
    sig = _FakeSignal(sid, datetime.combine(sig_date, datetime.min.time()), "SWING", ml_prob, ta_score)
    prices = [
        _FakePrice(sid, sig_date + timedelta(days=1), entry_close),
        _FakePrice(sid, sig_date + timedelta(days=1 + _HOLD), exit_close),
    ]
    return (sig, "TEST"), prices


def _build_fixture(calib_specs, val_specs):
    """calib_specs/val_specs: list of (ml_prob, ta_score, entry_close, exit_close) tuples.
    Calibration rows get earlier dates (older 70%), validation rows get later dates (newer
    30%) — the real function's own chronological 70/30 split (observations.sort by date, then
    split = int(len * 0.7)) depends on this ordering."""
    rows, price_rows = [], []
    base_date = date(2026, 1, 1)
    sid = 1000
    for i, (ml_prob, ta_score, entry_close, exit_close) in enumerate(calib_specs):
        row, prices = _make_row(sid, base_date + timedelta(days=i), ml_prob, ta_score, entry_close, exit_close)
        rows.append(row)
        price_rows.extend(prices)
        sid += 1
    val_base = base_date + timedelta(days=len(calib_specs) + 30)
    for i, (ml_prob, ta_score, entry_close, exit_close) in enumerate(val_specs):
        row, prices = _make_row(sid, val_base + timedelta(days=i), ml_prob, ta_score, entry_close, exit_close)
        rows.append(row)
        price_rows.extend(prices)
        sid += 1
    return rows, price_rows


# AUC=.70 yields at most .75 weight. These scores clear the .81 BUY
# boundary at cap=.45, and fail it at cap=.30; the disagreement cut is inactive.
_CALIB_ML_HEAVY = [(0.95, 0.70, 100.0, 105.0)] * 50


def test_positive_gross_candidate_stays_research_only_without_complete_replay():
    rows, prices = _build_fixture(_CALIB_ML_HEAVY, [(0.95, 0.70, 100., 108.)] * 20)
    result, recorded, applied = _run_core(rows, prices, prev_cap=.3)
    assert result["applied"] is False and not recorded and not applied
    assert "complete executable strategy" in result["reason"]


def test_candidate_that_ties_live_cap_does_not_write():
    rows, prices = _build_fixture(_CALIB_ML_HEAVY, [(0.95, 0.70, 100., 102.)] * 20)
    result, recorded, applied = _run_core(rows, prices, prev_cap=.9)
    diagnostic = result["diagnostic"]
    assert not result["applied"] and not recorded and not applied
    assert diagnostic["candidate_validation_ev_pct"] == diagnostic["baseline_validation_ev_pct"]


def test_baseline_reflects_real_prev_cap_and_no_fired_returns_are_unknown():
    rows, prices = _build_fixture(_CALIB_ML_HEAVY, [(0.95, 0.70, 100., 106.)] * 20)
    high, _, _ = _run_core(rows, prices, prev_cap=.9)
    low, _, _ = _run_core(rows, prices, prev_cap=.2)
    assert high["diagnostic"]["baseline_weight"] == .9
    assert low["diagnostic"]["baseline_weight"] == .2
    assert high["diagnostic"]["baseline_validation_ev_pct"] > 0
    assert low["diagnostic"]["baseline_validation_ev_pct"] is None


def test_no_incumbent_does_not_bypass_promotion_policy():
    val_specs = [(0.95, 0.70, 100., 108.)] * 30 + [(0.90, 0.73, 100., 112.)] * 30
    rows, prices = _build_fixture(_CALIB_ML_HEAVY[:1] * 140, val_specs)
    result, recorded, applied = _run_core(rows, prices, prev_cap=None)
    assert result["applied"] is False and not recorded and not applied
    assert "verified_forward_outcomes" in result["missing_requirements"]


def test_insufficient_history_never_reaches_writers():
    rows, prices = _build_fixture(_CALIB_ML_HEAVY[:10], [])
    result, recorded, applied = _run_core(rows, prices, prev_cap=.42)
    assert not result["applied"] and not recorded and not applied
    assert result["diagnostic"]["would_apply"] is False
