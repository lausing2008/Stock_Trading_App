"""AUD-DP-COVERAGE-DRIFT (2026-11-10): steady 900-960 s polling is full coverage; a real outage
is not. Uses the real NYSE session bounds."""
from datetime import date, timedelta

from common.market_calendar import session_bounds
from src.services.dark_pool_sampling import summarize

OPEN, CLOSE = session_bounds("US", date(2026, 11, 9))


def _polls(gap_s, start_s=5, skip=None):
    rows, t = [], OPEN + timedelta(seconds=start_s)
    while t < CLOSE:
        if not (skip and skip[0] <= t < skip[1]):
            rows.append({"at": t.isoformat(), "status": "ok", "count": 5, "saturated": False, "identified": 5})
        t += timedelta(seconds=gap_s)
    return rows


def test_steady_polling_with_job_tick_drift_has_no_missing_slots():
    for gap in (900, 930, 960):
        for start in (5, 300, 880):
            out = summarize(_polls(gap, start), now=CLOSE + timedelta(hours=1))
            assert out["missing_session_slots"] == 0, (gap, start)
            assert out["status"] == "observed_sample"


def test_a_45_minute_outage_still_counts_as_missing():
    outage = (OPEN + timedelta(hours=2), OPEN + timedelta(hours=2, minutes=45))
    out = summarize(_polls(930, skip=outage), now=CLOSE + timedelta(hours=1))
    assert out["missing_session_slots"] >= 2
    assert out["status"] == "degraded"


def test_coverage_reads_only_the_requested_window():
    """AUD-DP-COVERAGE-WINDOW: 21 days of poll history used to sit beside a 14-day baseline."""
    from datetime import datetime, timezone
    from src.services.dark_pool_sampling import coverage, RETENTION

    class R:
        def __init__(self):
            self.lo = None

        def zrangebyscore(self, k, lo, hi):
            self.lo = lo
            return []

    now = datetime(2026, 11, 10, 18, tzinfo=timezone.utc)
    r = R()
    coverage(r, "TSM", now=now, window_seconds=14 * 86400)
    assert r.lo == now.timestamp() - 14 * 86400
    coverage(r, "TSM", now=now, window_seconds=99 * 86400)
    assert r.lo == now.timestamp() - RETENTION        # never beyond what is retained
