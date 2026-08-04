"""Source-text regression checks for sync_todays_earnings()'s scheduler wiring —
scheduler.py can't be imported directly in this test environment (its import chain pulls
in apscheduler, not installed locally), matching every other scheduler-wiring test in this
codebase (e.g. test_scheduler_static_names.py in market-data).
"""
from pathlib import Path

_SCHED_SRC = (Path(__file__).parent.parent / "src" / "scheduler.py").read_text()


def test_job_sync_todays_earnings_wrapper_exists_and_calls_the_real_function():
    assert "async def job_sync_todays_earnings():" in _SCHED_SRC
    assert 'await _run("sync_todays_earnings", earnings.sync_todays_earnings())' in _SCHED_SRC


def test_job_is_registered_on_a_real_intraday_cron_not_once_daily():
    assert 'id="sync_todays_earnings"' in _SCHED_SRC
    # Must be a CronTrigger with a real intraday minute cadence, not a once-daily cron= call
    # (that would just reintroduce the exact bug this job exists to fix).
    idx = _SCHED_SRC.index('id="sync_todays_earnings"')
    window = _SCHED_SRC[max(0, idx - 400):idx]
    assert 'minute="*/15"' in window


def test_job_covers_market_hours_through_after_hours_on_weekdays():
    idx = _SCHED_SRC.index('id="sync_todays_earnings"')
    window = _SCHED_SRC[max(0, idx - 400):idx]
    assert 'day_of_week="mon-fri"' in window
    assert 'timezone="America/New_York"' in window
    assert 'hour="7-20"' in window


def test_job_is_registered_alongside_the_existing_daily_sync_earnings_job():
    # Both jobs must exist — this is additive, not a replacement of the once-daily sync
    # (which still does the initial full-universe backfill).
    assert 'id="sync_earnings"' in _SCHED_SRC
    assert 'id="sync_todays_earnings"' in _SCHED_SRC
