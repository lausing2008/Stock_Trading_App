"""Regression guard for T232-SIG10-SELLGATE's missing cron registration.

backfill_bearish_pillars and tune_sell_pillars (both in signal-engine's outcomes.py/
calibration.py) were built, tested, and live-verified in an earlier session but never wired
into _weekly_full_refresh() — the same SELFIMPROVE-MISSING-SCHEDULE-REGISTRATIONS gap class
already fixed once for calibrate_ml_weight and once for tune_strategy (a built, already-gated
mechanism with zero cron entry, not a missing safety check).

Ordering matters here in a way it didn't for the prior two fixes: tune_sell_pillars only reads
SignalOutcome rows where bearish_pillars_active IS NOT NULL — if backfill_bearish_pillars
hasn't run first (or runs after), the sweep sees zero usable rows and always skips every
horizon, silently never promoting anything despite genuinely having enough underlying data.

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

_BACKFILL_CALL = '_post(f"{_settings.signal_engine_url}/signals/backfill_bearish_pillars")'
_TUNE_CALL = '_post(f"{_settings.signal_engine_url}/signals/tune_sell_pillars")'


def _weekly_full_refresh_body() -> str:
    start = _SOURCE.index("def _weekly_full_refresh(")
    end = _SOURCE.index("\ndef ", start + 1)
    return _SOURCE[start:end]


def test_backfill_bearish_pillars_is_posted_inside_weekly_full_refresh():
    body = _weekly_full_refresh_body()
    assert _BACKFILL_CALL in body


def test_backfill_bearish_pillars_records_job_status():
    body = _weekly_full_refresh_body()
    assert '_record_job_status("backfill_bearish_pillars_sent", "ok", 0.0)' in body


def test_tune_sell_pillars_is_posted_inside_weekly_full_refresh():
    body = _weekly_full_refresh_body()
    assert _TUNE_CALL in body


def test_tune_sell_pillars_records_job_status():
    body = _weekly_full_refresh_body()
    assert '_record_job_status("tune_sell_pillars_sent", "ok", 0.0)' in body


def test_backfill_runs_before_the_sweep():
    """Load-bearing ordering, not cosmetic: tune_sell_pillars only reads rows the backfill
    just populated. If this order were ever reversed, the sweep would silently see zero
    backfilled rows and always skip every horizon — never crashing, never promoting."""
    body = _weekly_full_refresh_body()
    backfill_idx = body.index(_BACKFILL_CALL)
    tune_idx = body.index(_TUNE_CALL)
    assert backfill_idx < tune_idx


def test_both_calls_run_after_tune_strategy():
    """Matches the comment's own stated intent (placed alongside tune_strategy, its closest
    sibling in the per-style gate-parameter-sweep family) — a real regression guard that the
    calls weren't accidentally inserted somewhere unrelated."""
    body = _weekly_full_refresh_body()
    tune_strategy_idx = body.index('_post(f"{_settings.signal_engine_url}/signals/tune_strategy")')
    backfill_idx = body.index(_BACKFILL_CALL)
    tune_sell_idx = body.index(_TUNE_CALL)
    assert tune_strategy_idx < backfill_idx < tune_sell_idx


def test_calls_are_inside_weekly_full_refresh_not_a_different_function():
    """A copy-paste mistake could add either call to the wrong function entirely (e.g. a
    daily job) — confirm both are specifically inside _weekly_full_refresh by checking they're
    absent from the rest of the file outside that function's own boundaries."""
    start = _SOURCE.index("def _weekly_full_refresh(")
    end = _SOURCE.index("\ndef ", start + 1)
    before = _SOURCE[:start]
    after = _SOURCE[end:]
    for call in (_BACKFILL_CALL, _TUNE_CALL):
        assert call not in before, f"found outside _weekly_full_refresh (before): {call!r}"
        assert call not in after, f"found outside _weekly_full_refresh (after): {call!r}"


def test_every_sibling_calibration_job_is_still_present():
    """Regression guard that adding these two calls didn't accidentally clobber or remove any
    of their siblings in the same function."""
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
