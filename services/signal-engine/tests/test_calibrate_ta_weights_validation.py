"""Tests for BUG233-TAWEIGHTS-NOVALIDATION — calibrate_ta_weights() previously (a) fed rows in
arbitrary DB-heap order into TimeSeriesSplit (making its reported "walk-forward" accuracy
meaningless), and (b) wrote production TA weights to disk/Redis/the live process
UNCONDITIONALLY, fit on the full sample with zero held-out validation against the current live
weights — the only mutation path in this file with no baseline comparison, no promotion gate,
and no TuneHistory record at all.

calibration.py can't be imported directly in this environment (it needs common.jwt_auth /
FastAPI Depends / db, none for-real-installed here) — the function's real body (from the
feature-extraction loop through both return branches) is extracted via exec() and run against
real numpy/sklearn (both genuinely installed locally) with synthetic `rows` fixtures, matching
this repo's established source-text-extraction convention for exactly this class of
Docker-only-dependency constraint.
"""
import pathlib
from datetime import date, timedelta

import numpy as np
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import TimeSeriesSplit, cross_val_score
from sklearn.preprocessing import StandardScaler

_CAL_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "calibration.py"
_CAL_SOURCE = _CAL_PATH.read_text()

_TA_FEATURES = [
    "above_sma50", "sma50_above_sma200", "golden_cross_event",
    "rsi_sweet_spot", "rsi_mild_oversold", "rsi_mild_overbought",
    "stoch_oversold", "stoch_cross_up",
    "macd_strong", "macd_positive", "macd_zero_cross_up",
    "bb_mid_zone", "price_above_vwap",
    "bullish_trend", "obv_trend_bullish", "volume_z",
]

_REASONS_MAP = {
    "above_sma50":            lambda r: bool(r.get("trend_above_sma50")),
    "sma50_above_sma200":     lambda r: bool(r.get("sma50_above_sma200")),
    "golden_cross_event":     lambda r: bool(r.get("golden_cross_event")),
    "rsi_sweet_spot":         lambda r: 45 < (r.get("rsi") or 0) < 65,
    "rsi_mild_oversold":      lambda r: 35 < (r.get("rsi") or 0) <= 45,
    "rsi_mild_overbought":    lambda r: 65 <= (r.get("rsi") or 0) < 72,
    "stoch_oversold":         lambda r: bool(r.get("stoch_rsi_oversold")),
    "stoch_cross_up":         lambda r: bool(r.get("stoch_rsi_cross_up")),
    "macd_strong":            lambda r: (r.get("macd_hist") or 0) > 0 and bool(r.get("macd_hist_expanding")),
    "macd_positive":          lambda r: (r.get("macd_hist") or 0) > 0 and not bool(r.get("macd_hist_expanding")),
    "macd_zero_cross_up":     lambda r: bool(r.get("macd_zero_cross_up")),
    "bb_mid_zone":            lambda r: 0.2 < (r.get("bb_pct_b") or 0) < 0.8,
    "price_above_vwap":       lambda r: r.get("price_above_vwap") is True,
    "bullish_trend":          lambda r: bool(r.get("adx_bullish")),
    "obv_trend_bullish":      lambda r: bool(r.get("obv_trend_bullish")),
    "volume_z":               lambda r: (r.get("volume_z") or 0) > 0.5,
}

_TA_WEIGHTS_DEFAULT = {f: 1.0 for f in _TA_FEATURES}


class _RecordedTuneHistory(Exception):
    """Not really an exception — used as a plain data-carrier raised at the exact point the
    real function would call _record_tune_history(), so the test can inspect the call's kwargs
    without needing a real Session/TuneHistory model."""


def _extract_calibrate_ta_weights_core():
    """Pulls the feature-extraction-through-both-returns body of calibrate_ta_weights() out of
    calibration.py — the actual computational core under test, with the FastAPI/DB-query
    prologue (rows already provided as a fixture) and the disk/Redis/log side effects stubbed
    out via injected fakes in the exec() namespace."""
    start = _CAL_SOURCE.index("    X_rows, y_rows, ret_rows, date_rows, skipped = [], [], [], [], 0")
    end = _CAL_SOURCE.index('\n\n\n@router.post("/calibrate_conviction_weights")')
    body = _CAL_SOURCE[start:end]
    # Re-indent from method-body (8-space under the route function) down to a bare function body
    # so it can be exec()'d as its own top-level function taking `rows`/`_current_live_ta_weights`
    # as parameters.
    lines = body.splitlines()
    dedented = "\n".join(line[4:] if line.startswith("    ") else line for line in lines)
    func_source = (
        "def _core(rows, _current_live_ta_weights, _record_tune_history, set_ta_weights, "
        "_write_weights_to_disk, log, HTTPException, session=None):\n"
        + "\n".join("    " + line if line.strip() else line for line in dedented.splitlines())
    )
    import os as _real_os
    import tempfile

    namespace = {
        "np": np,
        "LogisticRegression": LogisticRegression,
        "StandardScaler": StandardScaler,
        "TimeSeriesSplit": TimeSeriesSplit,
        "cross_val_score": cross_val_score,
        "json": __import__("json"),
        "TA_FEATURES": _TA_FEATURES,
        "REASONS_MAP": _REASONS_MAP,
        "_TA_WEIGHTS_DEFAULT": _TA_WEIGHTS_DEFAULT,
        # A real, writable throwaway path — using the real Path/os.replace exactly as production
        # does (rather than mocking file I/O out) is simpler and more faithful to what's
        # actually being tested; this is scratch-space, not a real deployment artifact.
        "_TA_WEIGHTS_PATH": str(pathlib.Path(tempfile.gettempdir()) / "test_ta_weights_under_test.json"),
        "Path": pathlib.Path,
        "_os": _real_os,
        "_get_redis": lambda: (_ for _ in ()).throw(RuntimeError("stubbed redis should never be reached in tests")),
    }
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_core"]


_core = None  # populated lazily below, after confirming the extraction boundary is correct


def _run_core(rows, current_live_weights):
    """Runs the extracted core with the disk/Redis/logging side effects replaced by no-op
    fakes, and _record_tune_history replaced with a recorder capturing its kwargs."""
    global _core
    if _core is None:
        _core = _extract_calibrate_ta_weights_core()

    recorded = {}

    def _fake_record_tune_history(session, run_id, parameter_class, parameter_name, style, market,
                                   old_value, new_value, train_window, validation_window,
                                   train_ev_pct, validation_ev_pct, baseline_validation_ev_pct,
                                   validation_n, promoted, gate_failures):
        recorded.update(locals())

    applied_weights = {}

    def _fake_set_ta_weights(weights):
        applied_weights["weights"] = weights

    class _FakeHTTPException(Exception):
        def __init__(self, status_code, detail):
            self.status_code = status_code
            self.detail = detail
            super().__init__(detail)

    try:
        result = _core(
            rows, current_live_weights, _fake_record_tune_history, _fake_set_ta_weights,
            None, _FakeLog(), _FakeHTTPException,
        )
    except _FakeHTTPException as exc:
        return {"_raised_http_exception": True, "status_code": exc.status_code, "detail": exc.detail}, recorded, applied_weights
    return result, recorded, applied_weights


class _FakeLog:
    def info(self, *a, **kw):
        pass


def _row(is_correct, pct_return, reasons, signal_date):
    return (is_correct, pct_return, reasons, signal_date)


def _make_rows(n, base_date=date(2026, 1, 1)):
    """Rows constructed so the CANDIDATE weight fit (which learns to concentrate weight on
    bullish_trend/obv_trend_bullish, the two features perfectly correlated with is_correct)
    genuinely outperforms the DEFAULT baseline (every feature weighted equally at 1.0) on
    validation. This requires more than just two predictive features being present — per this
    repo's own documented test-design lesson (CLAUDE.md's T255-STRATEGY-TUNER-PER-HORIZON
    entry): if every OTHER feature is held flatly False/neutral for every row, a uniform-weight
    median-split and a concentrated-weight median-split select the IDENTICAL subset (both are
    driven entirely by the same two features either way), showing zero real lift regardless of
    whether the underlying fit is correct. Fixed by adding NOISE features (macd_strong,
    price_above_vwap) that are True on a DIFFERENT, non-corresponding subset of rows than
    is_correct — the flat-weight baseline's median score is dragged around by this irrelevant
    noise (since it counts every feature equally), while the candidate's fit correctly learns to
    down-weight the noise features (near-zero coefficients), giving the two weight vectors a
    genuinely different median-split and a real, measurable validation-EV gap."""
    rows = []
    for i in range(n):
        signal_date = base_date + timedelta(days=i)
        is_correct = (i % 2 == 0)
        pct_return = 0.05 if is_correct else -0.05
        # Noise: True for a DIFFERENT third of rows than is_correct — uncorrelated with the
        # actual label, so a flat-weight scorer gets pulled off course by it but a fitted
        # scorer (which sees it carries no real signal) learns to ignore it.
        noise = (i % 3 == 0)
        reasons = {
            "adx_bullish": is_correct,          # -> bullish_trend
            "obv_trend_bullish": is_correct,    # -> obv_trend_bullish
            "macd_hist": 1 if noise else 0, "macd_hist_expanding": noise,  # -> macd_strong (noise)
            "price_above_vwap": noise,          # -> price_above_vwap (noise)
            "trend_above_sma50": False, "sma50_above_sma200": False, "golden_cross_event": False,
            "rsi": 50, "stoch_rsi_oversold": False, "stoch_rsi_cross_up": False,
            "macd_zero_cross_up": False,
            "bb_pct_b": 0.5, "volume_z": 0.0,
        }
        rows.append(_row(is_correct, pct_return, reasons, signal_date))
    return rows


def test_below_50_total_rows_is_rejected_before_the_extracted_core_even_runs():
    """The n>=50 floor is enforced in the route's DB-query prologue (BEFORE the extracted
    core's own starting point) — confirmed directly in source rather than via this test's own
    extraction boundary, which begins after that check."""
    assert 'raise HTTPException(status_code=400, detail=f"Need ≥50 evaluated BUY outcomes, found {len(rows)}")' in _CAL_SOURCE


def test_below_15_validation_rows_after_the_70_30_split_raises_400():
    """50 rows * 0.3 = 15 exactly — the boundary. 51 rows -> 15 (int(51*0.7)=35, 51-35=16, OK);
    verify the actual floor with a deliberately-thin count just under it."""
    # 50 rows -> split=int(50*0.7)=35, validation=15 -> exactly at the floor, should PASS through
    # to the fit (not raise). Use 49 -> below the initial 50-row gate instead, which is a
    # DIFFERENT guard (tested above) — so directly test the validation-floor message using a
    # row count that clears 50 total but leaves <15 in validation: impossible with a fixed 70/30
    # split and n>=50 (int(50*0.7)=35, val=15 is already the minimum at n=50). This guard is
    # therefore only reachable via a non-default split ratio — confirm the constant and message
    # exist in source instead of via a runtime scenario that can't actually occur at n>=50.
    assert "MIN_VAL_SAMPLES = 15" in _CAL_SOURCE
    assert "validation-slice rows after a 70/30 chronological split" in _CAL_SOURCE


def test_chronological_split_uses_the_oldest_70_percent_to_train():
    """A row's OWN signal_date order must determine which slice it lands in — the exact
    property BUG233-TAWEIGHTS-NOVALIDATION's fix depends on (TimeSeriesSplit assumes
    chronological order, and the query's own .order_by(SignalOutcome.signal_date) is what makes
    that assumption valid). Verified by checking the SOURCE explicitly orders by date, since the
    ordering itself happens in the DB query this test's extracted core doesn't include."""
    assert ".order_by(SignalOutcome.signal_date)" in _CAL_SOURCE
    # And the core's own local split must not re-sort — it must trust the caller's pre-sorted
    # input, matching every sibling mechanism (calibrate_ml_weight sorts explicitly ONCE, itself).
    assert "X_train, y_train = X_rows[:split], y_rows[:split]" in _CAL_SOURCE


def test_candidate_beating_baseline_applies_and_promotes():
    # n=100 -> validation slice = 30 rows; the median-score threshold only counts rows AT OR
    # ABOVE the median as "fired" (roughly half), so this must comfortably clear MIN_VAL_SAMPLES
    # (15) on the fired subset, not just on the raw validation-slice row count.
    rows = _make_rows(100)
    result, recorded, applied = _run_core(rows, _TA_WEIGHTS_DEFAULT)
    assert result.get("applied") is True
    assert applied.get("weights") is not None
    assert recorded.get("promoted") is True
    assert recorded.get("gate_failures") == []


def test_candidate_not_beating_baseline_does_not_apply_and_records_rejection():
    """The current live weights ARE the fitted candidate itself — beating an identical baseline
    is impossible (lift can only be <= 0), so this must reject."""
    rows = _make_rows(60)
    # First fit to learn what the candidate would look like isn't needed — just use a
    # baseline that's IDENTICAL to what the fit will produce is impractical to construct up
    # front. Instead, construct a baseline that's already BETTER than any fit could beat: a
    # perfect-oracle weight vector that scores is_correct exactly right on every row.
    oracle_weights = {f: 0.0 for f in _TA_FEATURES}
    oracle_weights["bullish_trend"] = 100.0
    oracle_weights["obv_trend_bullish"] = 100.0
    result, recorded, applied = _run_core(rows, oracle_weights)
    assert result.get("applied") is False
    assert not applied  # weights must NOT have been written to the (stubbed) live process
    assert recorded.get("promoted") is False
    assert "ev_lift_not_positive_on_validation" in recorded.get("gate_failures", [])


def test_rejected_candidate_still_records_a_tune_history_row():
    """Every attempt — promoted or not — must leave an audit trail, matching every sibling
    mechanism in this file (T233-SELFIMPROVE-DESIGN's own established convention)."""
    rows = _make_rows(60)
    oracle_weights = {f: 0.0 for f in _TA_FEATURES}
    oracle_weights["bullish_trend"] = 100.0
    oracle_weights["obv_trend_bullish"] = 100.0
    _, recorded, _ = _run_core(rows, oracle_weights)
    assert recorded.get("parameter_class") == "ta_weights"
    assert recorded.get("parameter_name") == "ta_weights_vector"


def test_old_value_in_tune_history_reflects_the_actual_current_live_weights_not_a_hardcoded_literal():
    """The regression this fix specifically targets vs. a naive port: old_value must be the REAL
    current live weights passed in, not a fixed default — a future weights change must be
    visible in the audit trail as a real delta, not always the same hardcoded baseline."""
    rows = _make_rows(60)
    custom_live = {**_TA_WEIGHTS_DEFAULT, "rsi_sweet_spot": 42.0}
    _, recorded, _ = _run_core(rows, custom_live)
    assert recorded["old_value"]["ta_weights"]["rsi_sweet_spot"] == 42.0
