"""Tests for AUD-MISFIREGRACE-OPTIONSFLOW.

Every 1-minute interval job registered inside start_scheduler() must pass an explicit
misfire_grace_time — APScheduler's own scheduler-level default (confirmed directly against a
real BackgroundScheduler() instance: `_job_defaults == {'misfire_grace_time': 1, 'coalesce':
True, 'max_instances': 1}`) is only 1 second, far shorter than several of these jobs' own real
execution time (confirmed live in production: options_flow_alert_check and
dark_pool_alert_check both ran ~15s, sr_watch_check's own scheduler:job: Redis status showed it
stuck too). Confirmed live: options_flow_alert_check and dark_pool_alert_check both ran exactly
ONCE after a container restart and then silently never fired again for 20+ minutes, while every
sibling minute job with an explicit misfire_grace_time kept firing every single minute in the
same window.

scheduler.py can't be imported directly in this test environment (apscheduler/db import chain
not stubbed) — source-text regression checks, matching test_alert_jobs_record_status.py's own
established pattern for this class of function.

Deliberately excludes gamma_unwind_alert_check/prebreakout_alert_check (hours=4 cadence — a
1-second misfire grace window is not remotely as risky against a 4-hour interval) and
avg_volume_cache_refresh (also hours=4).
"""
import pathlib

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()

# Every job registered with "interval", minutes=1 inside start_scheduler() — the exact set this
# bug class applies to. Intentionally a fixed list (not derived via regex from the source under
# test) so a future job added WITHOUT misfire_grace_time is caught by name, not silently
# excluded by whatever pattern happened to match at the time these tests were written.
_MINUTE_JOB_IDS = [
    "price_alert_check",
    "volume_anomaly_check",
    "conditional_order_check",
    "short_squeeze_alert_check",
    "squeeze_ignition_alert_check",
    "squeeze_watch_revert_check",
    "options_flow_alert_check",
    "dark_pool_alert_check",
    "sr_watch_check",
    "value_area_breakdown_check",
    "portfolio_drawdown_alert_check",
    "top3_conviction_check",
    "earnings_reaction_check",
    "macro_reaction_alert_check",
    "earnings_impact_alert_check",
    "early_earnings_news_alert_check",
    "live_price_cache_refresh",
]


def _registration_block(job_id: str) -> str:
    """The add_job(...) call registering this job id, as its own source chunk."""
    id_marker = f'id="{job_id}",'
    id_idx = _scheduler_source.index(id_marker)
    # Registrations in this file are consistently ~10 lines — search back for the nearest
    # "_scheduler.add_job(" and forward for the FIRST line that is only a closing paren
    # (indentation varies slightly across call sites, so match on the bare ")" content itself
    # rather than a fixed indent level).
    start = _scheduler_source.rindex("_scheduler.add_job(", 0, id_idx)
    end = id_idx
    for line in _scheduler_source[id_idx:].splitlines(keepends=True):
        end += len(line)
        if line.strip() == ")":
            break
    return _scheduler_source[start:end]


def test_every_minute_job_has_an_explicit_misfire_grace_time():
    missing = []
    for job_id in _MINUTE_JOB_IDS:
        block = _registration_block(job_id)
        if "misfire_grace_time=" not in block:
            missing.append(job_id)
    assert not missing, f"1-minute jobs missing an explicit misfire_grace_time: {missing}"


def test_options_flow_and_dark_pool_alert_checks_use_a_60_second_grace_window():
    """The exact 2 jobs confirmed live-stuck — assert the specific value, not just presence,
    so a future edit that sets an unreasonably short grace time is still caught."""
    for job_id in ("options_flow_alert_check", "dark_pool_alert_check", "sr_watch_check"):
        block = _registration_block(job_id)
        assert "misfire_grace_time=60" in block, f"{job_id} does not use a 60s grace window"


def test_four_hour_jobs_are_unaffected_by_this_fix():
    """gamma_unwind_alert_check/prebreakout_alert_check run every 4 hours — confirm they were
    NOT touched by this fix (a 1s default grace window is not the same risk at that cadence,
    and this test would catch an over-broad find/replace accidentally including them)."""
    for job_id in ("gamma_unwind_alert_check", "prebreakout_alert_check"):
        block = _registration_block(job_id)
        assert "hours=4" in block
        assert "misfire_grace_time=" not in block


def test_every_listed_job_id_is_actually_registered_with_minutes_equal_one():
    """Sanity check on the fixture list itself — every id in _MINUTE_JOB_IDS must really be a
    1-minute job in the source, not a stale/renamed id that would make the tests above
    vacuously pass against the wrong registration."""
    for job_id in _MINUTE_JOB_IDS:
        block = _registration_block(job_id)
        assert "minutes=1" in block, f"{job_id} is not registered with minutes=1"
