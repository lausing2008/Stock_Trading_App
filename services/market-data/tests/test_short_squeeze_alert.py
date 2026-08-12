"""Tests for the classic short-squeeze alert (check_short_squeeze_alerts()).

send_short_squeeze_email() is pure string composition (no DB/network dependency), so it's
tested directly with real inputs. check_short_squeeze_alerts() itself can't be imported in this
test environment — scheduler.py's import chain pulls in apscheduler and other unstubbed
modules (see test_price_alert_price_check.py's docstring for the same constraint) — so the
scan logic/job registration is covered by source-text regression checks instead, matching
test_scheduler_static_names.py's / test_volume_anomaly_alert.py's established pattern.
"""
import pathlib
from unittest.mock import patch

from src.services.email_service import send_short_squeeze_email

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


def _capture_send():
    calls = []
    def _fake_send(to, subject, body_html, body_text):
        calls.append({"to": to, "subject": subject, "html": body_html, "text": body_text})
        return True
    return calls, _fake_send


def _check_short_squeeze_alerts_body() -> str:
    start = _scheduler_source.index("def check_short_squeeze_alerts(")
    end = _scheduler_source.index("\ndef ", start + 1)
    return _scheduler_source[start:end]


# ── send_short_squeeze_email() — pure composition, tested directly ──────────────────────────

def test_single_candidate_renders_symbol_short_pct_and_change():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_short_squeeze_email("user@example.com", [
            {"symbol": "GME", "short_percent_of_float": 22.5, "change_pct": 8.3, "price": 25.10},
        ])
    html, text = calls[0]["html"], calls[0]["text"]
    assert "GME" in html and "22.5%" in html and "+8.30%" in html and "$25.10" in html
    assert "GME" in text and "22.5%" in text


def test_subject_explicitly_labels_this_a_buy_signal():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_short_squeeze_email("user@example.com", [
            {"symbol": "GME", "short_percent_of_float": 22.5, "change_pct": 8.3, "price": 25.10},
        ])
    assert "BUY signal" in calls[0]["subject"]


def test_body_states_not_a_prediction_the_move_continues():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_short_squeeze_email("user@example.com", [
            {"symbol": "GME", "short_percent_of_float": 22.5, "change_pct": 8.3, "price": 25.10},
        ])
    html = calls[0]["html"]
    assert "not a prediction" in html.lower() or "not a prediction the move continues" in html.lower()


def test_multiple_candidates_all_rendered():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_short_squeeze_email("user@example.com", [
            {"symbol": "GME", "short_percent_of_float": 22.5, "change_pct": 8.3, "price": 25.10},
            {"symbol": "AMC", "short_percent_of_float": 18.0, "change_pct": 5.1, "price": 4.50},
        ])
    html = calls[0]["html"]
    assert "GME" in html and "AMC" in html


def test_missing_change_pct_or_price_degrades_gracefully_not_crash():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        result = send_short_squeeze_email("user@example.com", [
            {"symbol": "XYZ", "short_percent_of_float": 20.0, "change_pct": None, "price": None},
        ])
    assert result is True
    assert "—" in calls[0]["html"]


# ── AUD265-SHORT-INTEREST-AGE-NEVER-CHECKED: settlement date surfaced in the email ──────────

def test_short_interest_date_rendered_when_present():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_short_squeeze_email("user@example.com", [
            {"symbol": "GME", "short_percent_of_float": 22.5, "change_pct": 8.3, "price": 25.10,
             "short_interest_date": "2026-07-15"},
        ])
    html, text = calls[0]["html"], calls[0]["text"]
    assert "2026-07-15" in html
    assert "2026-07-15" in text


def test_missing_short_interest_date_degrades_gracefully_not_crash():
    """An older candidate dict (or a symbol whose fundamentals cache predates this fix) has no
    short_interest_date key at all — must not crash or render a placeholder that looks like a
    real date."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        result = send_short_squeeze_email("user@example.com", [
            {"symbol": "GME", "short_percent_of_float": 22.5, "change_pct": 8.3, "price": 25.10},
        ])
    assert result is True
    assert "as of" not in calls[0]["html"]


# ── check_short_squeeze_alerts() — source-text regression checks ────────────────────────────

def test_uses_stockai_live_prices_not_yfinance_in_the_scan_loop():
    """MUST read the same cache check_volume_anomalies() reads (never a per-symbol yfinance
    call inside the universe loop) — this repo has hit real yfinance rate-limiting from
    exactly this class of tight loop before."""
    body = _check_short_squeeze_alerts_body()
    assert '"stockai:live_prices"' in body
    assert "import yfinance" not in body


def test_uses_is_market_hours_helper():
    body = _check_short_squeeze_alerts_body()
    assert "_is_market_hours" in body


def test_uses_a_redis_lock():
    body = _check_short_squeeze_alerts_body()
    assert "_SQUEEZE_LOCK_KEY" in body
    assert 'nx=True' in body


def test_requires_both_short_float_and_intraday_move_thresholds():
    body = _check_short_squeeze_alerts_body()
    assert "_SQUEEZE_MIN_SHORT_FLOAT" in body
    assert "_SQUEEZE_MIN_INTRADAY_MOVE_PCT" in body


def test_fires_only_on_state_transition_via_redis_set_diff():
    """The dedup mechanism must diff against a PRIOR set, not just re-alert every cycle a
    stock stays qualified — this is the "only email on the transition" property."""
    body = _check_short_squeeze_alerts_body()
    assert "prev_active" in body
    assert "newly_qualifying" in body
    assert "current_active - prev_active" in body


def test_job_is_registered_at_one_minute_interval():
    assert 'id="short_squeeze_alert_check"' in _scheduler_source
    idx = _scheduler_source.index('id="short_squeeze_alert_check"')
    preceding = _scheduler_source[max(0, idx - 300):idx]
    assert "minutes=1" in preceding


# ── AUD265-SHORT-INTEREST-AGE-NEVER-CHECKED: stale short-interest rejected outright ──────────

def test_stale_short_interest_is_rejected_not_just_flagged():
    """This alert is the highest-consequence consumer (an unsolicited email explicitly
    claiming a squeeze thesis) — unlike the browsable screener endpoints, which surface
    is_stale for a human to judge, this must REJECT a stale candidate outright before it can
    ever reach the email."""
    body = _check_short_squeeze_alerts_body()
    assert "_squeeze_stale_cutoff_str" in body
    assert '_si_date is None or _si_date < _squeeze_stale_cutoff_str' in body


def test_stale_cutoff_is_computed_fresh_each_cycle_not_a_frozen_constant():
    """The cutoff must be derived from date.today() INSIDE the function body, not a module-
    level constant computed once at import time (which would never advance and eventually
    reject everything, or nothing, depending on when the process started)."""
    body = _check_short_squeeze_alerts_body()
    assert "_squeeze_stale_cutoff_str = (" in body
    assert "_sq_date.today()" in body


def test_staleness_check_happens_before_the_candidate_is_added():
    """The reject must fire INSIDE the same try block that reads spf, before
    candidates[sym] = {...} — a staleness check added after the candidate is already queued
    would be a no-op."""
    body = _check_short_squeeze_alerts_body()
    staleness_idx = body.index("_si_date is None or _si_date < _squeeze_stale_cutoff_str")
    candidate_add_idx = body.index('candidates[sym] = {')
    assert staleness_idx < candidate_add_idx


def test_short_interest_date_is_threaded_into_the_candidate_dict():
    """The real settlement date must reach the candidate dict passed to
    send_short_squeeze_email() — otherwise the email builder's own date-rendering (tested
    above) would never actually have anything to show."""
    body = _check_short_squeeze_alerts_body()
    assert '"short_interest_date": _si_date' in body


# ── AUD265-SQUEEZE-CACHE-MISS-SILENT-SKIP ───────────────────────────────────────────────────

def test_fundamentals_cache_misses_are_counted_not_silently_dropped():
    """`if not cached: continue` previously treated a fundamentals-cache miss identically to
    "this symbol doesn't qualify" with no signal anywhere. Confirm the miss is now counted."""
    body = _check_short_squeeze_alerts_body()
    assert "_fundamentals_cache_misses += 1" in body


def test_cache_miss_counter_is_incremented_before_its_own_continue():
    body = _check_short_squeeze_alerts_body()
    incr_idx = body.index("_fundamentals_cache_misses += 1")
    continue_idx = body.index("continue", incr_idx)
    between = body[incr_idx + len("_fundamentals_cache_misses += 1"):continue_idx].strip()
    assert between == ""


def test_cache_miss_count_reaches_the_done_log_line():
    """The count must actually surface somewhere observable, not just be computed and
    discarded — confirm it's included in this job's own established short_squeeze_alert.done
    summary log line."""
    body = _check_short_squeeze_alerts_body()
    done_log_idx = body.index('log.info("short_squeeze_alert.done"')
    # There may be two such log lines (the early zero-candidates return, and the main one) —
    # both must include the miss count, not just one.
    assert body.count("fundamentals_cache_misses=_fundamentals_cache_misses") >= 2


def test_zero_candidates_path_also_reports_the_miss_count_when_nonzero():
    """The `if not candidates: return` early-exit must not silently swallow a real miss count
    that was already accumulated before it — a cycle with a fundamentals-cache outage but zero
    otherwise-qualifying candidates must still be observable."""
    body = _check_short_squeeze_alerts_body()
    early_return_idx = body.index("if not candidates:")
    next_return_idx = body.index("return", early_return_idx)
    early_return_block = body[early_return_idx:next_return_idx]
    assert "fundamentals_cache_misses" in early_return_block
