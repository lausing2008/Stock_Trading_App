"""Tests for AUD263-CONVICTION-WEIGHTS-UNGATED (Deep Audit #3, Tier 263).

calibrate_conviction_weights() previously fit on the FULL sample and wrote conviction_weights
.json/Redis UNCONDITIONALLY — no chronological split, no baseline comparison, no TuneHistory
record at all (confirmed empty in production: 0 rows for parameter_class LIKE
'%conviction%'). Now uses the same chronological 70/30 split + validation-beats-baseline +
TuneHistory pattern as its sibling calibrate_ta_weights (test_calibrate_ta_weights_validation.py
is the direct precedent this file mirrors).

Separately (tested in services/market-data/tests/test_conviction_weights_wired_and_gated.py),
the output (edge_pct) now has a real consumer: _is_conviction_buy()'s soft-fail allowance.

calibration.py can't be imported directly in this environment (needs common.jwt_auth/FastAPI
Depends/db) — the function's computational core is extracted via exec() and run against real
numpy/sklearn with synthetic `rows` fixtures.
"""
import pathlib
from datetime import date, timedelta

import numpy as np
from sklearn.linear_model import LogisticRegression

_CAL_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "calibration.py"
_CAL_SOURCE = _CAL_PATH.read_text()


def _extract_calibrate_conviction_weights_core():
    """Pulls the split-through-both-returns body of calibrate_conviction_weights() out of
    calibration.py — the actual computational core, with the FastAPI/DB-query prologue (rows
    already provided as a fixture) and disk/Redis/log side effects stubbed via injected fakes."""
    start = _CAL_SOURCE.index("    MIN_VAL_SAMPLES = 15  # same floor established by calibrate_ml_weight/calibrate_ta_weights")
    end = _CAL_SOURCE.index('\n\n\n@router.get("/outcomes/calibration")')
    body = _CAL_SOURCE[start:end]
    # The real body re-imports load_conviction_weights and rebinds it to
    # _current_live_edges_fn — strip that one line so the injected parameter (a plain lambda,
    # since the real signals.py module isn't importable in this test environment) is used as-is
    # instead of being overwritten by a relative import exec() can't resolve.
    body = body.replace(
        "    from ..generators.signals import load_conviction_weights as _current_live_edges_fn\n", ""
    )
    lines = body.splitlines()
    dedented = "\n".join(line[4:] if line.startswith("    ") else line for line in lines)
    func_source = (
        "def _core(rows, min_count, lookback_days, _current_live_edges_fn, "
        "_record_tune_history, _get_redis, log, HTTPException, session=None):\n"
        + "\n".join("    " + line if line.strip() else line for line in dedented.splitlines())
    )
    namespace = {
        "np": np,
        "LogisticRegression": LogisticRegression,
        "json": __import__("json"),
        "Path": pathlib.Path,
        "date": date,
        "uuid": __import__("uuid"),
    }
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_core"]


_core = None


def _run_core(rows, current_live_edges, min_count=10, lookback_days=365):
    global _core
    if _core is None:
        _core = _extract_calibrate_conviction_weights_core()

    recorded = {}

    def _fake_record_tune_history(session, run_id, parameter_class, parameter_name, style, market,
                                   old_value, new_value, train_window, validation_window,
                                   train_ev_pct, validation_ev_pct, baseline_validation_ev_pct,
                                   validation_n, promoted, gate_failures):
        recorded.update(locals())

    written = {}

    class _FakeRedis:
        def setex(self, key, ttl, value):
            written["redis_key"] = key
            written["redis_value"] = value

    class _FakeHTTPException(Exception):
        def __init__(self, status_code, detail):
            self.status_code = status_code
            self.detail = detail
            super().__init__(detail)

    class _FakeLog:
        def info(self, *a, **kw):
            pass

        def warning(self, *a, **kw):
            pass

    # _CONVICTION_WEIGHTS_PATH import happens inside the real function via a relative import
    # (`from ..generators.signals import _CONVICTION_WEIGHTS_PATH`) that isn't reachable in this
    # extraction — the extracted core starts AFTER that import already ran, so it's injected as
    # a real Path pointing at scratch space instead.
    import tempfile
    namespace_patch = {"_CONVICTION_WEIGHTS_PATH": str(pathlib.Path(tempfile.gettempdir()) / "test_conviction_weights_under_test.json")}
    _core.__globals__.update(namespace_patch)

    try:
        result = _core(
            rows, min_count, lookback_days, lambda: current_live_edges,
            _fake_record_tune_history, lambda: _FakeRedis(), _FakeLog(), _FakeHTTPException,
        )
    except _FakeHTTPException as exc:
        return {"_raised_http_exception": True, "status_code": exc.status_code, "detail": exc.detail}, recorded, written
    return result, recorded, written


class _Row:
    def __init__(self, is_correct, pct_return, reasons, signal_date):
        self.is_correct = is_correct
        self.pct_return = pct_return
        self.reasons = reasons
        self.signal_date = signal_date


def _make_rows(n, base_date=date(2026, 1, 1)):
    """Rows constructed so the fitted candidate edge (concentrated on obv_trend_bullish/
    adx_trending, perfectly correlated with is_correct) beats a NOISE baseline on validation.
    Includes an uncorrelated noise flag (macd_zero_cross_up) so the median-split scorer used by
    _edge_separation_ev genuinely differs between the candidate and a noise baseline — matching
    this repo's own documented test-design lesson (a 2D/edge-based test needs a real
    distinguishing signal between candidate and baseline, not two features that happen to
    produce the identical selected subset either way)."""
    rows = []
    for i in range(n):
        signal_date = base_date + timedelta(days=i)
        is_correct = (i % 2 == 0)
        pct_return = 0.05 if is_correct else -0.05
        noise = (i % 3 == 0)  # uncorrelated with is_correct
        reasons = {
            "obv_trend_bullish": is_correct,
            "adx_trending": is_correct,
            "macd_zero_cross_up": noise,
        }
        rows.append(_Row(is_correct, pct_return, reasons, signal_date))
    return rows


def test_below_30_total_rows_is_rejected_before_the_extracted_core_even_runs():
    assert 'raise HTTPException(400, f"Need ≥30 evaluated BUY outcomes, found {len(rows)}")' in _CAL_SOURCE


def test_chronological_split_uses_the_oldest_70_percent_to_train():
    assert ".order_by(SignalOutcome.signal_date)" in _CAL_SOURCE
    assert "train_rows, val_rows = rows[:split], rows[split:]" in _CAL_SOURCE


def test_first_ever_calibration_auto_promotes_with_no_baseline_to_beat():
    """An empty current_live_edges (genuinely first-ever run) must auto-promote — matching
    every sibling mechanism's own first-run convention — rather than being permanently unable
    to turn the feature on. On success the real function returns the raw payload dict (no
    "applied" key — that shape is specific to calibrate_ta_weights, a different function),
    so success is asserted via the actual write side-effects instead."""
    rows = _make_rows(60)
    result, recorded, written = _run_core(rows, current_live_edges={})
    assert "_raised_http_exception" not in result
    assert written.get("redis_key") == "stockai:conviction_weights"
    assert recorded.get("promoted") is True


def test_candidate_beating_a_noise_baseline_applies_and_promotes():
    # n=140 -> validation slice = 42 rows; MIN_VAL_SAMPLES (15) applies to the median-split
    # FIRED subset (roughly half the validation slice), not the raw validation row count, so
    # this must comfortably clear 15 on the fired half, not just on n_val itself.
    rows = _make_rows(140)
    noise_baseline = {"macd_zero_cross_up": 50.0}  # a real but WRONG edge — noise, not signal
    result, recorded, written = _run_core(rows, current_live_edges=noise_baseline)
    assert "_raised_http_exception" not in result
    assert written.get("redis_key") == "stockai:conviction_weights"
    assert recorded.get("promoted") is True
    assert recorded.get("gate_failures") == []


def test_candidate_not_beating_an_oracle_baseline_does_not_apply():
    """A baseline that already perfectly separates winners from losers can never be beaten —
    must reject and leave Redis/disk untouched."""
    rows = _make_rows(60)
    oracle_baseline = {"obv_trend_bullish": 100.0, "adx_trending": 100.0}
    result, recorded, written = _run_core(rows, current_live_edges=oracle_baseline)
    assert result.get("applied") is False
    assert not written
    assert recorded.get("promoted") is False
    assert "ev_lift_not_positive_on_validation" in recorded.get("gate_failures", [])


def test_rejected_candidate_still_records_a_tune_history_row():
    rows = _make_rows(60)
    oracle_baseline = {"obv_trend_bullish": 100.0, "adx_trending": 100.0}
    _, recorded, _ = _run_core(rows, current_live_edges=oracle_baseline)
    assert recorded.get("parameter_class") == "conviction_weights"
    assert recorded.get("parameter_name") == "edge_pct_vector"


def test_old_value_in_tune_history_reflects_the_actual_current_live_edges_not_a_hardcoded_literal():
    rows = _make_rows(60)
    custom_live = {"obv_trend_bullish": 100.0, "adx_trending": 100.0, "some_other_flag": 7.5}
    _, recorded, _ = _run_core(rows, current_live_edges=custom_live)
    assert recorded["old_value"]["edge_pct"]["some_other_flag"] == 7.5
