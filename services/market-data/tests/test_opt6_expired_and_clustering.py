"""AUD-OPT6 — the three option-alert fixes from Domain 6 (final) of the 2026-09-07 audit.

Measured production evidence
(docs/audits/2026-09-07-six-part-audit-6-option-alerts.md):

  F1 EXPIRED CONTRACTS — 1,102 of 1,552 alerts (71.0%) had expiry < fired_date, averaging 20.9
     days stale, worst 62 days, and ONGOING (14 of 28 on 2026-09-05). Real emailed example:
     SPCX260828C00110000, expiry 2026-08-28, alerted 2026-09-02 with $8,784,730 of premium
     presented as live positioning. UW's max_dte is created_at-relative, so it cannot express
     "not yet expired"; no filter anywhere compared expiry against today.

  F2 CALIBRATION CLUSTERING — the entire resolved dataset (n=1,444) spanned TWO entry dates
     across ~49 symbols; on 2026-09-02, 82.6% of everything rose. Both directions showed
     positive average returns (bearish +2.21%, bullish +2.08%). The builder would have
     published win_rate ~= 0.20 for bearish into user emails as a measured historical rate.

  F3 NO MARKET-HOURS GATE — all 28 alerts on 2026-09-05 fired at 00:01 UTC (~8pm ET, closed).
"""
import datetime as dt
import pathlib

import src.services.scheduler as sch
import src.services.unusual_whales as uw

SCHED_SRC = pathlib.Path(sch.__file__).read_text()
UW_SRC = pathlib.Path(uw.__file__).read_text()


class _FA:
    """Minimal FlowAlert stand-in — the filter only reads .expiry."""
    def __init__(self, expiry, chain="X"):
        self.expiry = expiry
        self.option_chain = chain


# ── F1: expired-contract filter ──────────────────────────────────────────────────────────

def test_drops_a_contract_that_already_expired():
    """THE CORE F1 FIX — the SPCX case: expiry 5 days before the alert fired."""
    past = (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=5)).isoformat()
    out = sch and uw._drop_expired_flow_alerts([_FA(past, "SPCX260828C00110000")], "SPCX")
    assert out == [], "an already-expired contract must never reach a caller"


def test_keeps_a_contract_expiring_in_the_future():
    future = (dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=7)).isoformat()
    assert len(uw._drop_expired_flow_alerts([_FA(future)], "T")) == 1


def test_keeps_a_contract_expiring_TODAY():
    """0-DTE is a real, tradeable contract — the comparison must be `< today`, not `<= today`.
    An off-by-one here would silently discard every same-day expiry."""
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    assert len(uw._drop_expired_flow_alerts([_FA(today)], "T")) == 1


def test_fails_open_on_an_unparseable_or_missing_expiry():
    """Fail OPEN, matching this module's convention everywhere else. The point is to remove
    rows we can PROVE are dead, not to reject anything we cannot verify."""
    for bad in (None, "", "not-a-date", "2026-13-99"):
        assert len(uw._drop_expired_flow_alerts([_FA(bad)], "T")) == 1, f"expiry={bad!r}"


def test_handles_a_datetime_style_expiry_string():
    """UW sends an ISO date, but a datetime-suffixed value must not crash the filter."""
    past = (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=3)).isoformat()
    assert uw._drop_expired_flow_alerts([_FA(past + "T00:00:00Z")], "T") == []


def test_mixed_batch_keeps_only_the_live_contracts():
    today = dt.datetime.now(dt.timezone.utc).date()
    batch = [
        _FA((today - dt.timedelta(days=62)).isoformat()),   # worst observed staleness
        _FA((today - dt.timedelta(days=1)).isoformat()),
        _FA(today.isoformat()),                             # 0-DTE, keep
        _FA((today + dt.timedelta(days=30)).isoformat()),   # keep
        _FA(None),                                          # unknown, keep (fail open)
    ]
    assert len(uw._drop_expired_flow_alerts(batch, "T")) == 3


def test_empty_input_is_a_clean_noop():
    assert uw._drop_expired_flow_alerts([], "T") == []


def test_filter_runs_BEFORE_the_cache_write():
    """Ordering is load-bearing: an expired row cached first would keep being served for the
    cache's whole TTL even with this filter in place."""
    body = UW_SRC[UW_SRC.index("result = _parse_flow_alert_rows(data, sym)"):][:800]
    drop_at = body.index("_drop_expired_flow_alerts")
    cache_at = body.index("setex(cache_key")
    assert drop_at < cache_at, "must filter before caching"


def test_dropping_is_logged_not_silent():
    """71% of rows were being dropped — that magnitude must be observable."""
    assert "unusual_whales.flow_alerts_expired_dropped" in UW_SRC


# ── F2: calibration date-diversity floor ─────────────────────────────────────────────────

def test_calibration_requires_distinct_dates_not_just_count():
    """THE CORE F2 FIX. A COUNT floor alone cannot tell a real sample from one market day —
    alerts fire in bursts across dozens of contracts within minutes."""
    assert "_OPTIONS_FLOW_ALERT_CAL_MIN_DATES" in SCHED_SRC
    body = SCHED_SRC[SCHED_SRC.index("def _build_options_flow_alert_calibration"):][:2600]
    assert "distinct_dates < _OPTIONS_FLOW_ALERT_CAL_MIN_DATES" in body
    assert "return None" in body


def test_date_floor_is_above_the_two_days_that_produced_the_artifact():
    """The measured artifact spanned exactly 2 entry dates. A floor of 2 would not have caught
    it — the threshold must be strictly greater."""
    assert sch._OPTIONS_FLOW_ALERT_CAL_MIN_DATES > 2
    assert sch._OPTIONS_FLOW_ALERT_CAL_MIN_DATES == 5


def test_count_floor_is_retained():
    """The fix ADDS a dimension; it must not replace the existing sample-size floor."""
    body = SCHED_SRC[SCHED_SRC.index("def _build_options_flow_alert_calibration"):][:2600]
    assert "len(outcomes) < _OPTIONS_FLOW_ALERT_CAL_MIN_COUNT" in body
    assert sch._OPTIONS_FLOW_ALERT_CAL_MIN_COUNT == 30


def test_suppression_is_logged_with_the_diagnostic_numbers():
    """When a calibration is withheld, the reason must be inspectable — otherwise 'no
    calibration' is indistinguishable from 'no data'."""
    body = SCHED_SRC[SCHED_SRC.index("def _build_options_flow_alert_calibration"):][:2600]
    assert "calibration_suppressed_clustered" in body
    assert "distinct_dates=distinct_dates" in body


def test_result_exposes_distinct_dates_for_downstream_honesty():
    body = SCHED_SRC[SCHED_SRC.index("def _build_options_flow_alert_calibration"):][:2600]
    assert '"distinct_dates": distinct_dates' in body


def test_query_selects_fired_date_so_the_floor_can_be_computed():
    body = SCHED_SRC[SCHED_SRC.index("def _build_options_flow_alert_calibration"):][:2600]
    assert "OptionsFlowAlertOutcome.fired_date" in body


def test_the_measured_artifact_would_now_be_suppressed():
    """Behavioural: replay the real shape — 761 bearish outcomes across 2 distinct dates."""
    outcomes_n, distinct = 761, 2
    assert outcomes_n >= sch._OPTIONS_FLOW_ALERT_CAL_MIN_COUNT     # clears the count floor
    assert distinct < sch._OPTIONS_FLOW_ALERT_CAL_MIN_DATES        # ...but not the date floor


# ── F3: market-hours gate ────────────────────────────────────────────────────────────────

def _flow_job_src() -> str:
    """Body of check_options_flow_alerts(), up to the next top-level def.

    Sized by the NEXT DEFINITION rather than a fixed character count: a 4000-char window ended
    at exactly the offset the gate begins (3999), truncating it by one character and producing
    four confident-but-wrong failures.
    """
    start = SCHED_SRC.index("def check_options_flow_alerts()")
    nxt = SCHED_SRC.index("\ndef ", start + 10)
    return SCHED_SRC[start:nxt]


def test_options_flow_job_has_a_market_hours_gate():
    """Its sibling check_short_squeeze_alerts() has one; this job ran every minute, 24/7."""
    body = _flow_job_src()
    assert "_is_market_hours" in body
    assert 'not _is_market_hours("US") and not _is_market_hours("HK")' in body


def test_market_hours_gate_precedes_any_uw_work():
    """It must short-circuit before spending UW quota, not after."""
    body = _flow_job_src()
    gate = body.index("_is_market_hours")
    assert "SessionLocal()" in body
    assert gate < body.index("SessionLocal()"), "gate must run before the DB/UW work"


def test_market_hours_check_fails_open():
    """A market-calendar lookup failure must not silently disable a real alert."""
    body = _flow_job_src()
    blk = body[body.index("_is_market_hours") - 400:body.index("_is_market_hours") + 700]
    assert "except Exception" in blk
    assert "market_hours_check_failed" in blk, "the fail-open must be logged, not silent"


def test_gate_records_job_status_before_returning():
    """An early return that skips _record_job_status would make the job look dead to the
    liveness checks."""
    body = _flow_job_src()
    blk = body[body.index('not _is_market_hours("US")'):][:400]
    assert '_record_job_status("check_options_flow_alerts", "ok"' in blk
