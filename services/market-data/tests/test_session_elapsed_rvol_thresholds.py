"""Tests for AUD288-SQUEEZE-NO-VOLUME-CONFIRM's shared _session_elapsed_rvol_thresholds()
helper — extracted from check_volume_anomalies()'s own inline calculation (which check_squeeze_
ignition_alerts() then duplicated a second time) so every RVOL-gated alert in scheduler.py
shares ONE implementation of the T241-AUDIT-RVOL-INTRADAY-BIAS session-elapsed scaling, rather
than 3+ independently-copy-pasted versions that could silently drift apart.

scheduler.py can't be imported directly in this test environment (its import chain pulls in
apscheduler) — the function is extracted via exec() from the real source and exercised with a
real, injectable `datetime`/`ZoneInfo` so these tests control the "current moment" precisely,
matching test_gate_harness_confidence_delta.py's own established pattern for time-dependent
pure functions elsewhere in this codebase.
"""
import pathlib
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


class _FrozenDateTime(datetime):
    """A datetime subclass whose .now() always returns a fixed instant — lets these tests pin
    "the current moment" precisely without touching the real wall clock."""
    _frozen_utc = datetime(2026, 1, 1, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls._frozen_utc.astimezone(tz) if tz else cls._frozen_utc


def _extract_session_elapsed_rvol_thresholds(frozen_utc: datetime):
    start = _scheduler_source.index("def _session_elapsed_rvol_thresholds(")
    end = _scheduler_source.index("\n\n\n_VOL_ANOMALY_LOCK_KEY", start)
    body = _scheduler_source[start:end]

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_utc.astimezone(tz) if tz else frozen_utc

    namespace = {"datetime": _Frozen, "timezone": timezone, "ZoneInfo": ZoneInfo}
    exec(body, namespace)  # noqa: S102 — isolated eval of real source, matching repo convention
    return namespace["_session_elapsed_rvol_thresholds"]


def _at_et(hour: int, minute: int) -> datetime:
    """Builds a frozen UTC instant that corresponds to the given US Eastern wall-clock time on
    a fixed reference date (2026-01-15, a Thursday, no DST ambiguity — EST is a fixed -5 UTC
    offset in January)."""
    et = datetime(2026, 1, 15, hour, minute, tzinfo=ZoneInfo("America/New_York"))
    return et.astimezone(timezone.utc)


def test_at_the_open_threshold_is_floored_not_zero():
    """At exactly 9:30am ET (0 minutes elapsed), the scaled threshold would be base*0 = 0 —
    the floor must prevent that, or the RVOL gate would admit ANY volume at all right at the
    open."""
    fn = _extract_session_elapsed_rvol_thresholds(_at_et(9, 30))
    us_threshold, _ = fn(base=2.5, floor=1.5)
    assert us_threshold == 1.5


def test_at_full_session_elapsed_threshold_reaches_the_base():
    """390 minutes after 9:30am ET is 4:00pm ET (US regular session close) — the US fraction
    should be exactly 1.0, so the threshold should equal the base with no floor engaged."""
    fn = _extract_session_elapsed_rvol_thresholds(_at_et(16, 0))
    us_threshold, _ = fn(base=2.5, floor=1.5)
    assert us_threshold == 2.5


def test_threshold_scales_linearly_with_elapsed_session_fraction():
    """At the halfway point of the US session (195 min after open = ~12:45pm ET), the scaled
    threshold should sit roughly halfway between the floor and the base."""
    fn = _extract_session_elapsed_rvol_thresholds(_at_et(12, 45))
    us_threshold, _ = fn(base=3.0, floor=1.0)
    # 195/390 = 0.5 elapsed fraction -> 3.0 * 0.5 = 1.5, above the 1.0 floor.
    assert abs(us_threshold - 1.5) < 0.01


def test_hk_session_uses_its_own_330_minute_length_not_the_us_390():
    """HK's regular session (9:30-16:00 HKT minus the lunch break) is shorter than the US
    session — using the WRONG session length would scale the threshold incorrectly. At 330
    minutes elapsed (a full HK session), the HK fraction should reach 1.0 even though that's
    NOT enough elapsed minutes for the US fraction to reach 1.0."""
    # Build a frozen instant using HK wall-clock time directly, independent of the ET helper.
    hkt = datetime(2026, 1, 15, 15, 0, tzinfo=ZoneInfo("Asia/Hong_Kong"))  # 9:30 + 330 min = 15:00
    frozen = hkt.astimezone(timezone.utc)
    fn = _extract_session_elapsed_rvol_thresholds(frozen)
    _, hk_threshold = fn(base=2.5, floor=1.5)
    assert hk_threshold == 2.5


def test_elapsed_fraction_never_exceeds_1_after_the_session_closes():
    """Well after the session has closed (e.g. 8pm ET), the fraction must be CLAMPED at 1.0,
    not keep growing — an unclamped fraction would push the threshold arbitrarily high the
    longer after-hours trading continues, wrongly making the gate harder to clear rather than
    holding steady at the base."""
    fn = _extract_session_elapsed_rvol_thresholds(_at_et(20, 0))
    us_threshold, _ = fn(base=2.5, floor=1.5)
    assert us_threshold == 2.5  # not higher than the base


def test_before_the_open_elapsed_minutes_are_floored_at_zero_not_negative():
    """A pre-market instant (e.g. 6am ET) is BEFORE the session's own 9:30 open — elapsed
    minutes must be floored at 0, not go negative (which would silently invert the threshold
    math via a negative fraction)."""
    fn = _extract_session_elapsed_rvol_thresholds(_at_et(6, 0))
    us_threshold, _ = fn(base=2.5, floor=1.5)
    assert us_threshold == 1.5  # correctly floored, never below the floor


def test_returns_a_tuple_of_us_and_hk_thresholds_independently():
    """Confirms the function genuinely returns two DIFFERENT values when the two markets are
    at different points in their own respective sessions, not the same value duplicated."""
    # 12:45pm ET is roughly midday in the US session; simultaneously it's 1:45am the NEXT
    # calendar day in Hong Kong (well outside HK's own session entirely) — the HK fraction
    # should be floored at 0 while the US fraction is a real, nonzero value.
    fn = _extract_session_elapsed_rvol_thresholds(_at_et(12, 45))
    us_threshold, hk_threshold = fn(base=2.5, floor=1.0)
    assert us_threshold != hk_threshold
    assert hk_threshold == 1.0  # HK is well outside its own session at this instant
