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

AUD-MISFIREGRACE-GAMMAUNWIND (2026-09-06 deep audit): this file used to deliberately exclude
gamma_unwind_alert_check/prebreakout_alert_check with the stated reasoning "a 1-second misfire
grace window is not remotely as risky against a 4-hour interval." That reasoning was itself
the bug this audit found: APScheduler's default grace window is a property of the SCHEDULER,
triggered whenever a job's actual start is delayed past its scheduled time by more than the
window — a delay caused by a PRIOR run still executing when the next tick comes due (both jobs
use max_instances=1). The relevant risk factor is each job's own EXECUTION DURATION, not its
tick interval — and gamma_unwind_alert_check is, by a wide margin, the longest-running job in
this entire file (up to 40 symbols x a rate-limit-fragile yfinance options-chain fetch plus a
1s sleep per symbol, then a second per-symbol Unusual Whales loop: floor runtime >=40s, likely
60-120s+), making it MORE likely to overrun into its own next tick under load, not less. Both
jobs now carry the same misfire_grace_time=60 as every 1-minute job below — see
test_gamma_unwind_and_prebreakout_now_have_a_misfire_grace_time below. avg_volume_cache_refresh
(also hours=4) was not part of this specific fix and remains a separate, still-open item.
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


def test_gamma_unwind_and_prebreakout_now_have_a_misfire_grace_time():
    """AUD-MISFIREGRACE-GAMMAUNWIND: these 4-hour jobs must now carry an explicit
    misfire_grace_time — the same defect class as the 1-minute jobs above, since the risk
    factor is each job's own execution duration (gamma_unwind_alert_check is the longest-
    running job in the file, floor runtime >=40s of pure sleep, realistically 60-120s+), not
    its tick interval. Before this fix, this exact test asserted the OPPOSITE (the guard's
    absence) — locking the bug in rather than catching it."""
    for job_id in ("gamma_unwind_alert_check", "prebreakout_alert_check"):
        block = _registration_block(job_id)
        assert "hours=4" in block
        assert "misfire_grace_time=60" in block, (
            f"{job_id} is missing misfire_grace_time — this 4-hour job's own execution "
            f"duration, not its cadence, is what makes the 1s scheduler default risky here."
        )


def test_every_listed_job_id_is_actually_registered_with_minutes_equal_one():
    """Sanity check on the fixture list itself — every id in _MINUTE_JOB_IDS must really be a
    1-minute job in the source, not a stale/renamed id that would make the tests above
    vacuously pass against the wrong registration."""
    for job_id in _MINUTE_JOB_IDS:
        block = _registration_block(job_id)
        assert "minutes=1" in block, f"{job_id} is not registered with minutes=1"
