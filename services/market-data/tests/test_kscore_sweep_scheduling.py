"""Regression guard for T288-KSCORE-WEIGHT-SWEEP / T234-CONFIG-UNJUSTIFIED-THRESHOLDS Group B's
missing cron registration.

tune_kscore_weights and tune_kscore_curve (POST /rankings/tune_kscore_weights,
POST /rankings/tune_kscore_curve in ranking-engine's routes.py) were both built, live-verified,
and self-apply their promotion via Redis — but neither had ever been wired into
_weekly_full_refresh(), the same SELFIMPROVE-MISSING-SCHEDULE-REGISTRATIONS gap class already
fixed once for calibrate_ml_weight and once for tune_strategy (see
test_tune_strategy_scheduling.py). Before this fix, the ONLY way either sweep ever ran was a
manual HTTP call — including the one real promotion currently live in production
(rsi_mid/volatility_scale, promoted 2026-08-27 off a thin 13-day validation sample), which had
no scheduled path to ever be re-checked against a larger dataset.

scheduler.py imports sqlalchemy/apscheduler/db, all stubbed as MagicMock() by conftest.py — a
real import would silently "succeed" even with a real bug (MagicMock attribute access never
raises), so this is a source-text regression check (matching test_scheduler_static_names.py's
and test_tune_strategy_scheduling.py's established pattern for this exact constraint), not a
behavioral test.
"""
import pathlib

_SCHEDULER_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
)
_SOURCE = _SCHEDULER_PATH.read_text()


def _weekly_full_refresh_body() -> str:
    start = _SOURCE.index("def _weekly_full_refresh(")
    end = _SOURCE.index("\ndef ", start + 1)
    return _SOURCE[start:end]


def test_tune_kscore_weights_is_posted_inside_weekly_full_refresh():
    body = _weekly_full_refresh_body()
    assert '_post(f"{_settings.ranking_engine_url}/rankings/tune_kscore_weights"' in body


def test_tune_kscore_weights_records_job_status():
    body = _weekly_full_refresh_body()
    assert '_record_job_status("tune_kscore_weights_sent", "ok", 0.0)' in body


def test_tune_kscore_curve_is_posted_inside_weekly_full_refresh():
    body = _weekly_full_refresh_body()
    assert '_post(f"{_settings.ranking_engine_url}/rankings/tune_kscore_curve"' in body


def test_tune_kscore_curve_records_job_status():
    body = _weekly_full_refresh_body()
    assert '_record_job_status("tune_kscore_curve_sent", "ok", 0.0)' in body


def test_tune_kscore_weights_runs_before_tune_kscore_curve():
    """Load-bearing this time, unlike the sibling tune_strategy fix: tune_kscore_curve's own
    composite_fn recomputes the score using whatever weight set is CURRENTLY live
    (_kscore_curve_composite_fn(current_weights, ...)) — if the curve sweep ran BEFORE the
    weights sweep in the same cycle, a same-cycle weights promotion would silently not be
    reflected until the following week's curve sweep instead."""
    body = _weekly_full_refresh_body()
    weights_idx = body.index(
        '_post(f"{_settings.ranking_engine_url}/rankings/tune_kscore_weights"'
    )
    curve_idx = body.index('_post(f"{_settings.ranking_engine_url}/rankings/tune_kscore_curve"')
    assert weights_idx < curve_idx


def test_kscore_sweep_calls_are_inside_weekly_full_refresh_not_a_different_function():
    """A copy-paste mistake could add these calls to the wrong function entirely (e.g. a daily
    job) — confirm both are specifically inside _weekly_full_refresh by checking they're absent
    from the rest of the file outside that function's own boundaries."""
    start = _SOURCE.index("def _weekly_full_refresh(")
    end = _SOURCE.index("\ndef ", start + 1)
    before = _SOURCE[:start]
    after = _SOURCE[end:]
    for call in (
        '_post(f"{_settings.ranking_engine_url}/rankings/tune_kscore_weights"',
        '_post(f"{_settings.ranking_engine_url}/rankings/tune_kscore_curve"',
    ):
        assert call not in before
        assert call not in after


def test_tune_kscore_sweeps_use_the_heavy_sweep_timeout_not_the_default():
    """BUG-WEEKLYREFRESH-HEAVYSWEEP-TIMEOUT: these are genuinely heavy synchronous DB sweeps —
    the default _post() timeout=15/retries=3 is both too short AND actively harmful for a
    non-idempotent-cost route (a client retry after a timeout doesn't cancel the still-running
    server-side request, it queues a SECOND overlapping heavy query). Confirmed live across 3
    consecutive Sundays (2026-08-16/23/30) that this exact class of sweep call either times out
    on every retry (completing minutes later regardless) or, on 2026-08-30, hangs long enough
    to silently truncate the rest of the weekly tuning chain entirely."""
    body = _weekly_full_refresh_body()
    for call_prefix in (
        '_post(f"{_settings.ranking_engine_url}/rankings/tune_kscore_weights"',
        '_post(f"{_settings.ranking_engine_url}/rankings/tune_kscore_curve"',
    ):
        idx = body.index(call_prefix)
        call_end = body.index(")", idx)
        call_text = body[idx:call_end + 1]
        assert "timeout=180" in call_text, f"missing heavy-sweep timeout: {call_text!r}"
        assert "retries=1" in call_text, f"missing retries=1 (no retry storm): {call_text!r}"


def test_every_sibling_calibration_job_is_still_present():
    """Regression guard that adding the two K-Score sweep calls didn't accidentally clobber or
    remove any of the sibling calibration jobs already registered in the same function."""
    body = _weekly_full_refresh_body()
    siblings = [
        "/signals/calibrate_ta_weights",
        "/signals/calibrate_conviction_weights",
        "/signals/calibrate_ml_weight",
        "/signals/outcomes/calibrate/apply",
        "/signals/tune_style_profiles",
        "/signals/tune_strategy",
    ]
    for path in siblings:
        assert path in body, f"expected sibling call missing: {path!r}"
