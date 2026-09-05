"""AUD-IGNITION-NEVERFIRES: per-reason rejection counters for check_squeeze_ignition_alerts().

The alert has fired ZERO times since T260 shipped it — confirmed 2026-09-05: 0 rows in
squeeze_alert_outcomes for alert_type "squeeze_ignition", while its job liveness record
reported status=ok, duration_s=0.0 every single minute. It runs, finds nothing, and reports
success, forever.

The cause is the CONJUNCTION, not any one bar: >=15% short float AND a narrow 1.0-3.0% move
band (bounded on BOTH sides — above 3% the classic alert owns the candidate) AND a
session-scaled RVOL bar AND short-interest data <30 days old, all on the same 1-minute tick.
Each condition individually passes plenty of symbols (20 of 96 clear the short-float bar; all
20 pass staleness) — the intersection is what is empty.

From outside, "correctly found nothing" and "silently broken" were indistinguishable. These
counters make the funnel visible so any future loosening targets the binding constraint rather
than guessing — and deliberately do NOT loosen anything themselves, since every looser variant
measured in SHORT_SQUEEZE_ALERT_TUNING_REVIEW.md performed worse.

scheduler.py can't be imported here (apscheduler isn't installed locally), so this verifies the
real source text, matching the established technique in this repo.
"""
import pathlib

_SOURCE = (pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py").read_text()

_KEYS = [
    "_SQUEEZE_IGNITION_REJECT_MOVE_BAND_KEY",
    "_SQUEEZE_IGNITION_REJECT_RVOL_KEY",
    "_SQUEEZE_IGNITION_REJECT_SHORT_FLOAT_KEY",
    "_SQUEEZE_IGNITION_REJECT_STALE_SI_KEY",
]


def _ignition_fn() -> str:
    start = _SOURCE.index("def check_squeeze_ignition_alerts(")
    return _SOURCE[start:_SOURCE.index("\n\n\ndef ", start)]


def test_all_four_counter_keys_are_defined():
    for k in _KEYS:
        assert f'{k} = "stockai:metric:' in _SOURCE


def test_counter_keys_are_distinct():
    vals = [next(l.split("=")[1].strip() for l in _SOURCE.splitlines() if l.startswith(f"{k} =")) for k in _KEYS]
    assert len(set(vals)) == len(vals)


def test_every_rejection_reason_is_instrumented():
    fn = _ignition_fn()
    for k in _KEYS:
        assert f"_incr_rolling_counter({k})" in fn


def test_counters_fire_exactly_once_each_not_double_counted():
    """The function runs a TWO-PASS filter (cheap price/volume pre-filter, then a second pass
    after the fundamentals MGET) over the same rows. Instrumenting both passes would
    double-count every rejection and make the funnel numbers meaningless."""
    fn = _ignition_fn()
    for k in _KEYS:
        assert fn.count(f"_incr_rolling_counter({k})") == 1


def test_move_band_counter_only_counts_rising_symbols():
    """Counting every flat/down symbol in the whole universe would swamp the signal and say
    nothing about which bar is actually too tight."""
    fn = _ignition_fn()
    idx = fn.index("_incr_rolling_counter(_SQUEEZE_IGNITION_REJECT_MOVE_BAND_KEY)")
    preceding = fn[max(0, idx - 200):idx]
    assert "if change_pct > 0:" in preceding


def test_instrumentation_is_in_the_second_pass_not_the_first():
    """The second pass is the one that also resolves fundamentals, so it sees every rejection
    reason. The first pass cannot count short-float/staleness rejections at all."""
    fn = _ignition_fn()
    second_pass_idx = fn.index("candidates: dict[str, dict] = {}")
    for k in _KEYS:
        assert fn.index(f"_incr_rolling_counter({k})") > second_pass_idx


def test_all_four_are_registered_as_gauges():
    for name in ("squeeze_ignition_reject_move_band_48h", "squeeze_ignition_reject_rvol_48h",
                 "squeeze_ignition_reject_short_float_48h", "squeeze_ignition_reject_stale_si_48h"):
        assert f'"name": "{name}"' in _SOURCE
        idx = _SOURCE.index(f'"name": "{name}"')
        entry = _SOURCE[idx:idx + 400]
        assert '"source": "gauge"' in entry


def test_gauges_never_enter_the_failing_path():
    """A nonzero rejection count is normal, expected work — not a fault. The gauge source is
    always ok=True by construction; this pins that these use it rather than 'ratio'."""
    for name in ("squeeze_ignition_reject_move_band_48h", "squeeze_ignition_reject_rvol_48h"):
        idx = _SOURCE.index(f'"name": "{name}"')
        entry = _SOURCE[idx:idx + 400]
        assert '"source": "ratio"' not in entry
        assert '"min_ratio"' not in entry


def test_no_threshold_was_loosened_by_this_change():
    """Instrumentation only. Every looser variant measured in the sibling review performed
    WORSE, so this change must not quietly relax a bar while adding visibility."""
    assert "_SQUEEZE_IGNITION_MIN_MOVE_PCT = 1.0" in _SOURCE
    assert "_SQUEEZE_IGNITION_RVOL_BASE = 1.8" in _SOURCE
    assert "_SQUEEZE_MIN_SHORT_FLOAT = 15.0" in _SOURCE
