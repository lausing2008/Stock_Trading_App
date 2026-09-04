"""Tests for the 7 real, still-open gaps confirmed against current code from
docs/AUDIT_SHORT_SQUEEZE_2026-07-25.md (Issues 1, 2, 4, 5, 6 and Performance items 4.1, 4.3).

check_short_squeeze_alerts()/check_squeeze_watch_reverts()/run_data_quality_checks() can't be
imported directly in this test environment (scheduler.py's import chain pulls in apscheduler
and other unstubbed modules) — covered via source-text regression checks, matching this repo's
established test_scheduler_static_names.py/test_short_squeeze_alert.py pattern. The two new
email-rendering helpers (_short_interest_age_str, send_gamma_unwind_email's 0-DTE badge) and
the admin.py backtest endpoint's reason field ARE directly importable/testable — tested with
real behavioral assertions, not source-text checks, wherever possible.
"""
import pathlib

from src.services.email_service import _short_interest_age_str, send_gamma_unwind_email

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()
_admin_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "admin.py"
_admin_source = _admin_path.read_text()


def _function_body(name: str, source: str, end_marker: str) -> str:
    start = source.index(f"def {name}(")
    end = source.index(end_marker, start)
    return source[start:end]


def _capture_send():
    calls = []
    def _fake_send(to, subject, body_html, body_text):
        calls.append({"to": to, "subject": subject, "html": body_html, "text": body_text})
        return True
    return calls, _fake_send


# ── Issue 2: staleness tiers on _short_interest_age_str() ───────────────────────────────────

def test_short_interest_age_str_returns_empty_for_missing_date():
    assert _short_interest_age_str(None) == ""
    assert _short_interest_age_str("") == ""


def test_short_interest_age_str_fresh_reading_has_no_staleness_tier():
    from datetime import date, timedelta
    d = (date.today() - timedelta(days=3)).isoformat()
    result = _short_interest_age_str(d)
    assert "3d ago" in result
    assert "stale" not in result


def test_short_interest_age_str_exactly_15_days_still_fresh_no_tier():
    from datetime import date, timedelta
    d = (date.today() - timedelta(days=15)).isoformat()
    result = _short_interest_age_str(d)
    assert "15d ago" in result
    assert "stale" not in result


def test_short_interest_age_str_16_days_is_moderately_stale():
    from datetime import date, timedelta
    d = (date.today() - timedelta(days=16)).isoformat()
    result = _short_interest_age_str(d)
    assert "moderately stale" in result
    assert "very stale" not in result


def test_short_interest_age_str_exactly_21_days_still_moderately_stale():
    from datetime import date, timedelta
    d = (date.today() - timedelta(days=21)).isoformat()
    result = _short_interest_age_str(d)
    assert "moderately stale" in result
    assert "very stale" not in result


def test_short_interest_age_str_22_days_is_very_stale():
    from datetime import date, timedelta
    d = (date.today() - timedelta(days=22)).isoformat()
    result = _short_interest_age_str(d)
    assert "very stale" in result


def test_short_interest_age_str_malformed_date_fails_soft_to_bare_string():
    result = _short_interest_age_str("not-a-real-date")
    assert result == " (as of not-a-real-date)"


def test_short_interest_age_str_does_not_reject_anything_itself():
    """This is a pure rendering helper — the 30-day HARD reject stays in check_short_squeeze_
    alerts()/check_prebreakout_alerts()'s own candidate-building loop (unchanged), not here.
    Confirm a reading well past 30 days still renders (as "very stale"), it just would never
    reach this function in production since the hard reject filters it out earlier."""
    from datetime import date, timedelta
    d = (date.today() - timedelta(days=45)).isoformat()
    result = _short_interest_age_str(d)
    assert "very stale" in result
    assert "45d ago" in result


# ── Issue 2 (cont.): both email builders delegate to the same shared helper ─────────────────

_email_source = pathlib.Path(__file__).resolve().parents[1].joinpath(
    "src", "services", "email_service.py").read_text()


def test_send_short_squeeze_email_and_send_prebreakout_email_share_the_staleness_helper():
    """Before this fix, both functions independently duplicated the exact same age-string
    logic with no staleness tier at all — confirm both now delegate to ONE shared
    implementation rather than keeping two copies that could silently drift apart again."""
    squeeze_body = _function_body(
        "send_short_squeeze_email", _email_source, "\n\ndef send_gamma_unwind_email(",
    )
    prebreakout_body = _function_body(
        "send_prebreakout_email", _email_source, "\n\ndef send_squeeze_watch_revert_email(",
    )
    assert "si_str = _short_interest_age_str(si_date)" in squeeze_body
    assert "si_str = _short_interest_age_str(si_date)" in prebreakout_body


# ── Issue 4: 0-DTE visual warning on the gamma-unwind email ─────────────────────────────────

def _gamma_candidate(**overrides):
    base = {
        "symbol": "TEST", "dominant_side": "calls", "concentration_pct": 60.0,
        "days_to_expiry": 3, "total_oi_near_money": 5000, "price": 100.0, "expiry": "2026-08-21",
    }
    base.update(overrides)
    return base


def test_gamma_unwind_zero_dte_row_gets_amber_border_and_warning_marker():
    calls, fake_send = _capture_send()
    import src.services.email_service as es
    orig = es.send_email
    es.send_email = fake_send
    try:
        send_gamma_unwind_email("u@x.com", [_gamma_candidate(days_to_expiry=0)])
    finally:
        es.send_email = orig
    html = calls[0]["html"]
    assert "expires TODAY" in html
    assert "⚠️" in html
    assert "rgba(217,119,6,0.35)" in html  # the new amber row border


def test_gamma_unwind_nonzero_dte_row_gets_no_amber_border():
    calls, fake_send = _capture_send()
    import src.services.email_service as es
    orig = es.send_email
    es.send_email = fake_send
    try:
        send_gamma_unwind_email("u@x.com", [_gamma_candidate(days_to_expiry=3)])
    finally:
        es.send_email = orig
    html = calls[0]["html"]
    assert "expires in 3d" in html
    assert "rgba(217,119,6,0.35)" not in html
    assert "⚠️" not in html


def test_gamma_unwind_mixed_batch_only_flags_the_zero_dte_row():
    """A batch with both a 0-DTE row and a normal row must only mark the 0-DTE one — confirms
    the badge is per-row, not accidentally applied to the whole email."""
    calls, fake_send = _capture_send()
    import src.services.email_service as es
    orig = es.send_email
    es.send_email = fake_send
    try:
        send_gamma_unwind_email("u@x.com", [
            _gamma_candidate(symbol="ZERO", days_to_expiry=0),
            _gamma_candidate(symbol="NORMAL", days_to_expiry=5),
        ])
    finally:
        es.send_email = orig
    html = calls[0]["html"]
    assert html.count("rgba(217,119,6,0.35)") == 1
    assert html.count("⚠️") == 1


# ── Issue 6: distinguishing the two zero-candidate backtest reasons ─────────────────────────

def test_backtest_no_qualifying_snapshots_case_has_its_own_reason():
    body = _function_body("squeeze_alert_backtest", _admin_source, "\n\n@router.get(\"/watchlist-rotation-history\")")
    assert '"reason": "no_qualifying_snapshots"' in body


def test_backtest_no_candidate_days_case_has_its_own_distinct_reason():
    body = _function_body("squeeze_alert_backtest", _admin_source, "\n\n@router.get(\"/watchlist-rotation-history\")")
    assert 'reason = "no_qualifying_moves" if not candidate_days else None' in body


def test_backtest_normal_result_reason_is_none_not_a_stale_default():
    """When candidate_days IS non-empty, `reason` must resolve to None, not accidentally keep
    whatever the previous zero-case set — confirmed via the ternary's own else-branch."""
    body = _function_body("squeeze_alert_backtest", _admin_source, "\n\n@router.get(\"/watchlist-rotation-history\")")
    assert 'reason = "no_qualifying_moves" if not candidate_days else None' in body
    # The `reason` key must actually be threaded into the final returned dict, not just computed
    # and discarded.
    reason_computed_idx = body.index('reason = "no_qualifying_moves" if not candidate_days else None')
    return_dict_idx = body.index('"reason": reason,', reason_computed_idx)
    assert reason_computed_idx < return_dict_idx


# ── Issues 1 & 5: rolling cache-miss counters + the new "gauge" DQ-check type ───────────────

def test_squeeze_alert_cache_miss_increments_the_new_rolling_counter():
    body = _function_body("check_short_squeeze_alerts", _scheduler_source, "\n\ndef check_squeeze_ignition_alerts(")
    assert "_incr_rolling_counter(_SQUEEZE_FUND_CACHE_MISS_COUNTER_KEY)" in body


def test_squeeze_watch_revert_cache_miss_increments_its_own_distinct_rolling_counter():
    """AUD-SQUEEZE250725-ISSUE5: this job previously had NO cache-miss counter at all, unlike
    check_short_squeeze_alerts(). Must use its OWN key, not accidentally share the other job's
    counter (which would conflate two different jobs' cache-miss rates into one number)."""
    body = _function_body("check_squeeze_watch_reverts", _scheduler_source, "\n\ndef _squeeze_outcome_lookup_price(")
    assert "_incr_rolling_counter(_SQUEEZE_WATCH_FUND_CACHE_MISS_COUNTER_KEY)" in body
    assert "_SQUEEZE_FUND_CACHE_MISS_COUNTER_KEY" not in body


def test_the_two_cache_miss_counter_keys_are_genuinely_distinct():
    idx1 = _scheduler_source.index('_SQUEEZE_FUND_CACHE_MISS_COUNTER_KEY = "stockai:metric:')
    idx2 = _scheduler_source.index('_SQUEEZE_WATCH_FUND_CACHE_MISS_COUNTER_KEY = "stockai:metric:')
    key1 = _scheduler_source[idx1:idx1 + 120].split('"')[1]
    key2 = _scheduler_source[idx2:idx2 + 120].split('"')[1]
    assert key1 != key2


def test_gauge_dq_checks_registered_for_both_new_counters():
    assert '"counter_key": _SQUEEZE_FUND_CACHE_MISS_COUNTER_KEY' in _scheduler_source
    assert '"counter_key": _SQUEEZE_WATCH_FUND_CACHE_MISS_COUNTER_KEY' in _scheduler_source
    # T260-SQUEEZE-IGNITION added a 3rd gauge (its own fundamentals-cache-miss counter,
    # matching this same pattern) — the count below was 2 before that alert existed.
    assert '"counter_key": _SQUEEZE_IGNITION_FUND_CACHE_MISS_COUNTER_KEY' in _scheduler_source
    # AUD-DQCHECKS-VISIBILITY added a 4th gauge (Unusual Whales 429 rate-limit rollup,
    # counter_key=_UW_RATE_LIMIT_COUNTER_KEY) — the count below was 3 before that check existed.
    assert '"counter_key": _UW_RATE_LIMIT_COUNTER_KEY' in _scheduler_source
    assert _scheduler_source.count('"source": "gauge"') == 4


def test_gauge_dq_check_dispatch_branch_always_reports_ok_true():
    """A gauge check must be purely informational — never appended to `failing`, since a
    nonzero cache-miss count is expected background noise, not a functional failure. Confirm
    the dispatch branch hardcodes ok=True and has no `failing.append` call inside its own body."""
    start = _scheduler_source.index('if check.get("source") == "gauge":')
    end = _scheduler_source.index("continue", start)
    body = _scheduler_source[start:end]
    assert '"ok": True' in body
    assert "failing.append" not in body


def test_gauge_dispatch_branch_comes_before_the_job_status_branch_and_after_ratio():
    """Confirms the new branch was added alongside its siblings (after "ratio", before
    "job_status"), not accidentally nested inside one of them where it would never dispatch."""
    ratio_idx = _scheduler_source.index('if check.get("source") == "ratio":')
    gauge_idx = _scheduler_source.index('if check.get("source") == "gauge":')
    job_status_idx = _scheduler_source.index('if check.get("source") == "job_status":')
    assert ratio_idx < gauge_idx < job_status_idx


# ── Performance 4.1: MGET pre-warming ────────────────────────────────────────────────────────

def test_short_squeeze_alerts_uses_mget_not_per_symbol_get_for_fundamentals():
    body = _function_body("check_short_squeeze_alerts", _scheduler_source, "\n\ndef check_squeeze_ignition_alerts(")
    assert "_rc.mget(_fund_mget_keys)" in body
    # The old per-symbol _rc.get(f"stockai:fundamentals:v2:{sym}") call must be gone from the
    # main candidate-building loop — only the pre-warming pass constructs a fundamentals key
    # now (using the loop variable `s`, not `sym`, since it iterates _pricefilter_qualifying).
    assert body.count('f"stockai:fundamentals:v2:{s}"') == 1
    assert 'f"stockai:fundamentals:v2:{sym}"' not in body


def test_mget_prewarm_pass_only_includes_symbols_that_already_cleared_price_filters():
    """The MGET pass must run AFTER the price-only filtering loop (presence, market-hours,
    intraday-move threshold) — fetching fundamentals for every live-price row regardless of
    those filters would defeat the whole point of narrowing the MGET to real candidates."""
    body = _function_body("check_short_squeeze_alerts", _scheduler_source, "\n\ndef check_squeeze_ignition_alerts(")
    filter_pass_idx = body.index("_pricefilter_qualifying: list[str] = []")
    mget_idx = body.index("_rc.mget(_fund_mget_keys)")
    assert filter_pass_idx < mget_idx


def test_prewarm_filter_pass_and_main_loop_apply_the_identical_intraday_move_threshold():
    """The MGET pre-warm pass and the main candidate-building loop each independently repeat
    the same 4 price-only filters (presence, market-hours, intraday-move) — both copies must
    stay in sync, since a future edit to only ONE of them would silently mean the pre-warmed
    dict either omits a real candidate (treated as a false cache-miss) or wastes MGET slots on
    symbols the main loop would reject anyway. Confirms both copies reference the SAME
    threshold constant, not two independently-hardcoded literals that could drift apart."""
    body = _function_body("check_short_squeeze_alerts", _scheduler_source, "\n\ndef check_squeeze_ignition_alerts(")
    assert body.count("change_pct < _SQUEEZE_MIN_INTRADAY_MOVE_PCT") == 2
    assert body.count("_is_hk_sym and not _hk_market_open") == 2
    assert body.count("not _is_hk_sym and not _us_market_open") == 2


def test_main_loop_reads_from_the_prewarmed_dict_not_a_fresh_redis_call():
    body = _function_body("check_short_squeeze_alerts", _scheduler_source, "\n\ndef check_squeeze_ignition_alerts(")
    assert "cached = _fund_by_symbol.get(sym)" in body


def test_mget_failure_fails_open_to_an_empty_dict_not_a_crash():
    body = _function_body("check_short_squeeze_alerts", _scheduler_source, "\n\ndef check_squeeze_ignition_alerts(")
    mget_try_idx = body.index("_fund_mget_keys = [")
    except_idx = body.index("except Exception:\n                _fund_by_symbol = {}", mget_try_idx)
    assert mget_try_idx < except_idx


# ── Performance 4.3: calibration bucket Redis caching ───────────────────────────────────────

def _exec_cached_calibration_buckets(namespace_extra: dict):
    """Extracts _cached_calibration_buckets()'s real source (plus the real _SQUEEZE_CAL_CACHE_
    TTL_S constant it references — reading the real value rather than hardcoding a duplicate
    that could silently drift from it) and execs it into a controlled namespace."""
    import json as _json
    start = _scheduler_source.index("_SQUEEZE_CAL_CACHE_TTL_S = ")
    ttl_line_end = _scheduler_source.index("\n", start)
    ttl_line = _scheduler_source[start:ttl_line_end].split("#")[0]  # strip the trailing comment
    fn_start = _scheduler_source.index("def _cached_calibration_buckets(")
    fn_end = _scheduler_source.index("\n\ndef _build_prebreakout_calibration(")
    src = ttl_line + "\n" + _scheduler_source[fn_start:fn_end]
    namespace = {"json": _json, **namespace_extra}
    exec(src, namespace)  # noqa: S102 — isolated eval of real source, not a hand-copied reimplementation
    return namespace["_cached_calibration_buckets"]


def test_cached_calibration_buckets_returns_a_cached_value_without_calling_the_builder():
    """Direct behavioral test against the real helper (pure function, no scheduler.py-wide
    import needed) — exec the function body in isolation with a fake Redis client."""
    import json as _json

    class _FakeRedis:
        def __init__(self, store):
            self.store = store
        def get(self, key):
            return self.store.get(key)
        def setex(self, key, ttl, value):
            self.store[key] = value

    store = {"stockai:cal:test": _json.dumps({"15-20": {"win_rate": 0.5, "count": 40}})}
    fake_redis = _FakeRedis(store)
    cached_fn = _exec_cached_calibration_buckets({"_get_redis": lambda: fake_redis})

    call_count = {"n": 0}
    def _builder():
        call_count["n"] += 1
        return {"should never be returned": True}

    result = cached_fn("stockai:cal:test", _builder)
    assert result == {"15-20": {"win_rate": 0.5, "count": 40}}
    assert call_count["n"] == 0  # builder must NOT have been called — cache hit


def test_cached_calibration_buckets_calls_the_builder_and_writes_back_on_a_miss():
    import json as _json

    class _FakeRedis:
        def __init__(self, store):
            self.store = store
            self.setex_calls = []
        def get(self, key):
            return self.store.get(key)
        def setex(self, key, ttl, value):
            self.setex_calls.append((key, ttl, value))
            self.store[key] = value

    fake_redis = _FakeRedis({})
    cached_fn = _exec_cached_calibration_buckets({"_get_redis": lambda: fake_redis})

    call_count = {"n": 0}
    def _builder():
        call_count["n"] += 1
        return {"30+": {"win_rate": 0.6, "count": 50}}

    result = cached_fn("stockai:cal:miss_key", _builder)
    assert result == {"30+": {"win_rate": 0.6, "count": 50}}
    assert call_count["n"] == 1
    assert len(fake_redis.setex_calls) == 1
    key, ttl, value = fake_redis.setex_calls[0]
    assert key == "stockai:cal:miss_key"
    assert ttl == 300
    assert _json.loads(value) == {"30+": {"win_rate": 0.6, "count": 50}}


def test_cached_calibration_buckets_fails_open_to_the_builder_on_a_redis_get_exception():
    class _BrokenRedis:
        def get(self, key):
            raise ConnectionError("redis down")
        def setex(self, key, ttl, value):
            raise ConnectionError("redis down")

    cached_fn = _exec_cached_calibration_buckets({"_get_redis": lambda: _BrokenRedis()})

    result = cached_fn("stockai:cal:whatever", lambda: {"real": "data"})
    assert result == {"real": "data"}  # builder's result still returned despite Redis being down


def test_short_squeeze_short_squeeze_calibration_uses_its_own_distinct_redis_key():
    body = _function_body("check_short_squeeze_alerts", _scheduler_source, "\n\ndef check_squeeze_ignition_alerts(")
    assert '"stockai:cal:squeeze_family:short_squeeze"' in body


def test_prebreakout_calibration_uses_its_own_distinct_redis_key():
    body = _function_body("check_prebreakout_alerts", _scheduler_source, "\n\ndef _record_prebreakout_alert_outcome(")
    assert '"stockai:cal:prebreakout"' in body


def test_all_four_calibration_cache_keys_are_pairwise_distinct():
    """short_squeeze, gamma_unwind_calls, gamma_unwind_puts, and prebreakout each need their own
    cache key — sharing one key across any two would silently serve one alert type's
    calibration data to a different alert type."""
    keys = [
        "stockai:cal:squeeze_family:short_squeeze",
        "stockai:cal:squeeze_family:gamma_unwind_calls",
        "stockai:cal:squeeze_family:gamma_unwind_puts",
        "stockai:cal:prebreakout",
    ]
    assert len(set(keys)) == len(keys)
    for key in keys:
        assert f'"{key}"' in _scheduler_source
