"""Regression guard for T255-STRATEGY-TUNER-PER-HORIZON's missing cron registration.

tune_strategy (POST /signals/tune_strategy in signal-engine's calibration.py) was built and
live-verified in an earlier session but never wired into _weekly_full_refresh() — its sibling
calibration jobs (calibrate_ta_weights, calibrate_conviction_weights, outcomes/calibrate/apply,
tune_style_profiles, calibrate_ml_weight) were all already scheduled, but this one sat idle,
manual-HTTP-only, since the day it shipped. Same gap class as calibrate_ml_weight's own
SELFIMPROVE-MISSING-SCHEDULE-REGISTRATIONS fix (a built, gated mechanism with zero cron entry,
not a missing safety check).

scheduler.py imports sqlalchemy/apscheduler/db, all stubbed as MagicMock() by conftest.py — a
real import would silently "succeed" even with a real bug (MagicMock attribute access never
raises), so this is a source-text regression check (matching test_scheduler_static_names.py's
established pattern for this exact constraint), not a behavioral test.
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


def test_tune_strategy_is_posted_inside_weekly_full_refresh():
    body = _weekly_full_refresh_body()
    assert '_post(f"{_settings.signal_engine_url}/signals/tune_strategy")' in body


def test_tune_strategy_records_job_status():
    body = _weekly_full_refresh_body()
    assert '_record_job_status("tune_strategy_sent", "ok", 0.0)' in body


def test_tune_strategy_runs_after_tune_style_profiles():
    """Matches the comment's own stated intent (run after its closest sibling) — not load-
    bearing for correctness (both are fire-and-forget, no real ordering dependency), but a
    real regression guard that the call wasn't accidentally inserted somewhere unrelated."""
    body = _weekly_full_refresh_body()
    tune_style_idx = body.index('_post(f"{_settings.signal_engine_url}/signals/tune_style_profiles")')
    tune_strategy_idx = body.index('_post(f"{_settings.signal_engine_url}/signals/tune_strategy")')
    assert tune_style_idx < tune_strategy_idx


def test_tune_strategy_call_is_inside_weekly_full_refresh_not_a_different_function():
    """A copy-paste mistake could add this call to the wrong function entirely (e.g. a daily
    job) — confirm it's specifically inside _weekly_full_refresh by checking it's absent from
    the rest of the file outside that function's own boundaries."""
    start = _SOURCE.index("def _weekly_full_refresh(")
    end = _SOURCE.index("\ndef ", start + 1)
    before = _SOURCE[:start]
    after = _SOURCE[end:]
    assert '_post(f"{_settings.signal_engine_url}/signals/tune_strategy")' not in before
    assert '_post(f"{_settings.signal_engine_url}/signals/tune_strategy")' not in after


def test_every_sibling_calibration_job_is_still_present():
    """Regression guard that adding tune_strategy didn't accidentally clobber or remove any of
    its siblings in the same function."""
    body = _weekly_full_refresh_body()
    siblings = [
        "/signals/calibrate_ta_weights",
        "/signals/calibrate_conviction_weights",
        "/signals/calibrate_ml_weight",
        "/signals/outcomes/calibrate/apply",
        "/signals/tune_style_profiles",
    ]
    for path in siblings:
        assert path in body, f"expected sibling call missing: {path!r}"
