"""AUD-LLMUSAGE: check_llm_usage_spike() — alerts when hourly Claude token usage is a large
multiple of the recent baseline.

Direct motivation: BUG-NEWSCLASSIFY-REPEATCOST's fix (dedup for news-intelligence's EDGAR
poller) was committed 2026-07-27 but never reached the running container — a `docker cp`
deploy-drift found live 2026-09-05. Its EDGAR poller reclassified the same filings via Claude
Haiku every 2 minutes with NO dedup for six weeks, confirmed at 5.44M tokens burned in a single
day on the external Claude Console usage page, discovered only by chance days later. No call
site anywhere in this codebase logged real token usage before this, so nothing could have
caught it sooner. This alert (plus shared/common/llm_usage.py's logging wired into all 9 real
call sites) exists so the next such incident is caught within the hour instead.

scheduler.py can't be imported here (apscheduler isn't installed locally) — the constants and
the median/multiple arithmetic are verified against the real source text plus a behavioral
model of the exact expressions, matching this repo's established technique.
"""
import pathlib
import statistics

_SOURCE = (pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py").read_text()


def _fn() -> str:
    start = _SOURCE.index("def check_llm_usage_spike(")
    return _SOURCE[start:_SOURCE.index("\n\ndef _run_watchlist_auto_rotation", start)]


# ── constants and wiring ──────────────────────────────────────────────────────

def test_spike_multiple_is_well_above_normal_hourly_noise():
    """5x, not 2x — hourly totals are naturally noisy; a low threshold fires on ordinary
    variance, not just real incidents."""
    assert "_LLM_USAGE_SPIKE_MULTIPLE = 5.0" in _SOURCE


def test_minimum_tokens_floor_exists_to_avoid_noise_on_near_zero_baselines():
    assert "_LLM_USAGE_MIN_TOKENS_TO_EVALUATE = 50_000" in _SOURCE


def test_job_is_registered_on_a_15_minute_interval_not_every_minute():
    """Hourly-bucketed comparison — checking every minute adds DB load with no new information
    between hour boundaries."""
    idx = _SOURCE.index('id="llm_usage_spike_check"')
    block = _SOURCE[max(0, idx - 300):idx]
    assert "minutes=15" in block


def test_dq_check_liveness_entry_exists():
    assert '"name": "check_llm_usage_spike"' in _SOURCE
    idx = _SOURCE.index('"name": "check_llm_usage_spike"')
    entry = _SOURCE[idx:idx + 300]
    assert '"job_name": "llm_usage_spike_check"' in entry
    assert '"source": "job_status"' in entry


def test_alert_goes_to_admin_users_only():
    fn = _fn()
    assert "User.role == UserRole.ADMIN" in fn


def test_cooldown_prevents_repaging_every_cycle_while_still_elevated():
    fn = _fn()
    assert "cooldown_key" in fn
    assert "_rc.setex(cooldown_key, _LLM_USAGE_ALERT_COOLDOWN_HOURS * 3600" in fn


def test_below_min_tokens_skips_before_computing_a_multiple():
    """A near-zero current hour must never reach the division — pinned so a future refactor
    can't reorder this into a divide-by-near-zero on tiny volumes."""
    fn = _fn()
    assert fn.index("current_total < _LLM_USAGE_MIN_TOKENS_TO_EVALUATE") < fn.index("multiple = current_total")


def test_uses_median_not_mean_for_the_baseline():
    """A single prior spike hour (a real past incident, or a legitimate batch) must not
    silently inflate a mean-based baseline and mask a genuine new spike."""
    fn = _fn()
    assert "hourly_totals.sort()" in fn
    assert "baseline_median" in fn
    assert "statistics.mean" not in fn
    assert "sum(hourly_totals) / len" not in fn


def test_baseline_is_floored_to_avoid_division_by_near_zero():
    fn = _fn()
    assert "baseline_floor = max(baseline_median, 1000.0)" in fn
    assert fn.index("baseline_floor") < fn.index("multiple = current_total / baseline_floor")


def test_insufficient_history_skips_rather_than_compares_against_near_nothing():
    fn = _fn()
    assert "len(hourly_totals) < 6" in fn


def test_breakdown_is_sorted_biggest_contributor_first():
    """The email's whole value is answering "which service/function" at a glance — the
    biggest offender must be first, not buried in an unsorted list."""
    fn = _fn()
    assert 'key=lambda d: d["tokens"], reverse=True' in fn


# ── behavior of the median/multiple arithmetic itself ────────────────────────

def _median(values):
    values = sorted(values)
    n = len(values)
    mid = n // 2
    return values[mid] if n % 2 else (values[mid - 1] + values[mid]) / 2


def _multiple(current, hourly_baseline_values):
    baseline = max(_median(hourly_baseline_values), 1000.0)
    return current / baseline


def test_real_incident_magnitude_clears_the_threshold_with_margin():
    """The actual 2026-09-05 incident: ~5.44M tokens in a day vs an ordinary few-hundred-
    thousand-token day. Modelled as one bad hour vs 23 ordinary ones."""
    ordinary_hour = 15_000  # ~360k/day baseline
    spike_hour = 750_000    # a chunk of the day's 5.44M concentrated in poll-heavy hours
    baseline_hours = [ordinary_hour] * 23
    assert _multiple(spike_hour, baseline_hours) >= 5.0


def test_ordinary_hour_to_hour_noise_does_not_trigger():
    """2-3x normal variance between a quiet and a busy-but-ordinary hour must not fire."""
    baseline_hours = [10_000, 15_000, 8_000, 20_000, 12_000, 18_000] * 4
    normal_busy_hour = 40_000  # ~2.7x the flat baseline, well under 5x
    assert _multiple(normal_busy_hour, baseline_hours) < 5.0


def test_one_prior_spike_hour_does_not_permanently_mask_a_new_one():
    """Median discipline: ONE huge outlier hour in the baseline window must not drag the
    threshold up so far that a second, comparably-sized spike goes undetected."""
    baseline_hours = [10_000] * 22 + [2_000_000]  # one huge prior spike hour in the window
    another_spike = 300_000
    assert _multiple(another_spike, baseline_hours) >= 5.0


def test_all_idle_baseline_hours_still_catches_a_real_new_spike():
    """A previously-silent feature suddenly making real volume of calls is itself often the
    incident worth catching."""
    baseline_hours = [0.0] * 24
    assert _multiple(60_000, baseline_hours) >= 5.0
