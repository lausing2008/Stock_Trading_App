"""AUD-SCAN-SILENTZERO / AUD-SCAN-FIXEDTIMES (2026-11-11).

On Nov 11 the gamma scan recorded "ok" three times while measuring 0 of 39 symbols, because its
4-hour interval had moved (with a restart) into the gap after each 5-minute ingest.
"""
import importlib
from unittest.mock import patch

sched = importlib.import_module("src.services.scheduler")


def test_a_scan_that_measured_nothing_is_degraded_and_says_why():
    s = sched._scan_status(39, 0, {"no_live_price": 39})
    assert s == "degraded: measured 0 of 39 (no_live_price 39)"


def test_a_scan_that_measured_something_is_ok_even_with_no_candidates():
    assert sched._scan_status(39, 27, {"no_expiry_within_window": 8}) == "ok"
    assert sched._scan_status(0, 0, {}) == "ok"          # nothing to scan is not a failure


def test_reasons_are_listed_largest_first():
    s = sched._scan_status(10, 0, {"error": 1, "no_live_price": 9})
    assert s.index("no_live_price 9") < s.index("error 1")


def test_empty_live_cache_is_degraded_only_during_a_session_and_is_counted():
    import src.services.paper_trading_engine as pte
    counted = []
    with patch.object(sched, "_incr_rolling_counter", lambda k: counted.append(k)):
        with patch.object(pte, "_is_market_hours", lambda m: m == "HK"):
            assert sched._empty_live_cache_status().startswith("degraded")
        with patch.object(pte, "_is_market_hours", lambda m: False):
            assert sched._empty_live_cache_status() == "skipped: markets closed"
    assert counted == [sched._LIVE_CACHE_EMPTY_COUNTER_KEY]


def test_fixed_scan_times_are_in_session_off_ingest_boundaries_and_apart():
    for times in (sched._GAMMA_SCAN_TIMES_ET, sched._PREBREAKOUT_SCAN_TIMES_ET):
        assert len(times) == 3
        for h, m in times:
            assert (9, 30) < (h, m) < (16, 0)
            assert m % 5 != 0                    # never on a 5-minute ingest start
    assert set(sched._GAMMA_SCAN_TIMES_ET).isdisjoint(sched._PREBREAKOUT_SCAN_TIMES_ET)


def test_gamma_skips_outside_the_session_and_records_its_measured_status():
    src = open(sched.__file__).read()
    body = src[src.index("def check_gamma_unwind_alerts("):]
    body = body[:body.index("\ndef ")]
    # Before the lock is taken: an early return after it would hold the lock (released in finally).
    assert body.index("if not _us_session_now():") < body.index("acquired = _get_redis().set(_GAMMA_UNWIND_LOCK_KEY")
    assert '"skipped: US market closed"' in body
    assert body.count('_record_job_status("check_gamma_unwind_alerts", _scan_state,') == 2
    for reason in ("no_live_price", "no_expiries_from_yahoo", "no_expiry_within_window",
                   "oi_unmeasured", "below_notional_floor", "error"):
        assert f'_skip("{reason}")' in body


def test_the_data_quality_check_fails_a_degraded_job_and_watches_both_scans():
    names = {c["name"] for c in sched._DQ_CHECKS}
    assert {"check_gamma_unwind_alerts", "check_prebreakout_alerts", "live_cache_empty_in_session_48h"} <= names
    src = open(sched.__file__).read()
    run = src[src.index("def run_data_quality_checks("):]
    assert 'if str(_status_payload.get("status") or "").startswith("degraded"):' in run
    assert "if _job_degraded:\n                        ok = False" in run


def test_the_alert_email_states_the_degraded_reason_not_just_the_age():
    from unittest.mock import patch
    from src.services.email_service import send_data_quality_alert_email
    calls = []
    def fake(to, subject, html, text):
        calls.append(text)
        return True
    with patch("src.services.email_service.send_email", fake):
        send_data_quality_alert_email("ops@example.com", [{
            "name": "check_gamma_unwind_alerts", "description": "Gamma-unwind scan",
            "age_hours": 1.5, "max_age_hours": 30,
            "degraded": "degraded: measured 0 of 39 (no_live_price 39)",
            "detail": "ran, but degraded: measured 0 of 39 (no_live_price 39)"}])
    assert calls and "measured 0 of 39 (no_live_price 39)" in calls[0]
