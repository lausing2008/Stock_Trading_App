"""Tests for SR-WATCH-PROXIMITY-ALERT's check_sr_watch_reverts() (scheduler.py) and
send_sr_watch_alert_email() (email_service.py).

send_sr_watch_alert_email() is pure string composition (no DB/network dependency), so it's
tested directly with real inputs. check_sr_watch_reverts() itself can't be imported in this
test environment — scheduler.py's import chain pulls in apscheduler and other unstubbed
modules — so the scan logic/job registration is covered by source-text regression checks
instead, matching test_squeeze_watch_revert_alert.py's established pattern.
"""
import pathlib
from unittest.mock import patch

from src.services.email_service import send_sr_watch_alert_email

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


def _capture_send():
    calls = []
    def _fake_send(to, subject, body_html, body_text):
        calls.append({"to": to, "subject": subject, "html": body_html, "text": body_text})
        return True
    return calls, _fake_send


def _check_sr_watch_reverts_body() -> str:
    start = _scheduler_source.index("def check_sr_watch_reverts(")
    end = _scheduler_source.index("\ndef ", start + 1)
    return _scheduler_source[start:end]


# ── send_sr_watch_alert_email() — pure composition, tested directly ────────────────────────

def test_support_approach_uses_green_bounce_framing():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_sr_watch_alert_email("user@example.com", "AAPL", "support", 180.0, 181.5, 2.0, 1.0)
    subject, html = calls[0]["subject"], calls[0]["html"]
    assert "approaching support" in subject
    assert "AAPL" in subject
    assert "$180.00" in html
    assert "bounce" in html.lower()


def test_resistance_approach_uses_red_rejection_framing():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_sr_watch_alert_email("user@example.com", "MSFT", "resistance", 420.0, 418.0, 3.5, 1.5)
    subject, html = calls[0]["subject"], calls[0]["html"]
    assert "approaching resistance" in subject
    assert "MSFT" in subject
    assert "$420.00" in html
    assert "rejection" in html.lower()


def test_body_reports_level_price_current_price_and_atr():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_sr_watch_alert_email("user@example.com", "TSLA", "support", 250.0, 252.0, 5.0, 1.0)
    html, text = calls[0]["html"], calls[0]["text"]
    for s in (html, text):
        assert "$250.00" in s
        assert "$252.00" in s
        assert "$5.00" in s


def test_distance_pct_is_computed_from_current_price_not_level_price():
    """A 2.0 distance on a 100.0 current price is exactly 2.00% — hand-computed check that the
    percentage denominator is the CURRENT price, not the level price (the two would diverge on
    a real, non-trivial move)."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_sr_watch_alert_email("user@example.com", "XYZ", "resistance", 102.0, 100.0, 4.0, 1.0)
    assert "2.00%" in calls[0]["html"]


def test_body_states_it_is_a_measured_fact_not_a_prediction():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_sr_watch_alert_email("user@example.com", "AAPL", "support", 180.0, 181.0, 2.0, 1.0)
    html, text = calls[0]["html"], calls[0]["text"]
    assert "not a prediction" in html.lower()
    assert "not a prediction" in text.lower()


def test_body_states_it_fires_again_after_moving_away_and_returning():
    """Distinguishes this from SqueezeWatch's permanent one-shot framing — must say it can
    fire again, not that it never will. HTML and text bodies phrase this slightly
    differently ("will alert again" vs. "fires again") — both are checked in their own words
    rather than forcing an artificial identical wording between the two."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_sr_watch_alert_email("user@example.com", "AAPL", "support", 180.0, 181.0, 2.0, 1.0)
    html, text = calls[0]["html"], calls[0]["text"]
    assert "alert again" in html.lower()
    assert "fires again" in text.lower()


# ── check_sr_watch_reverts() — source-text regression checks ───────────────────────────────

def test_uses_a_redis_lock():
    body = _check_sr_watch_reverts_body()
    assert "_SR_WATCH_LOCK_KEY" in body
    assert "nx=True" in body


def test_uses_live_prices_cache_and_redis_cached_atr_not_a_fresh_yfinance_call_per_watch():
    """MUST read stockai:live_prices for price and a per-symbol Redis-cached ATR — never a
    fresh yfinance call inside the per-watch loop itself (only the batch top-up for cache
    misses, computed once for the whole watch list)."""
    body = _check_sr_watch_reverts_body()
    assert '"stockai:live_prices"' in body
    assert "stockai:sr_watch_atr:" in body
    assert "_batch_compute_atr" in body


def test_atr_cache_misses_are_batched_not_looked_up_one_at_a_time():
    """The whole point of the Redis cache — _batch_compute_atr must be called ONCE, outside
    the per-watch for-loop, for every symbol missing from cache, not once per watch."""
    body = _check_sr_watch_reverts_body()
    loop_idx = body.index("for w in watches:")
    batch_call_idx = body.index("fresh = _batch_compute_atr(_atr_cache_misses)")
    assert batch_call_idx < loop_idx
    # exactly one call site in the whole function
    assert body.count("_batch_compute_atr(") == 1


def test_technical_analysis_levels_call_is_per_symbol_inside_the_loop():
    """Unlike ATR (batched once), the S/R levels HTTP call is legitimately per-watch — each
    symbol's own nearest support/resistance is only fetched for symbols actually reached in
    the loop (market-hours-gated, price/ATR-available), never a universe-wide sweep."""
    body = _check_sr_watch_reverts_body()
    assert "/ta/{quote(w.symbol)}/levels" in body
    assert "_settings.technical_analysis_url" in body


def test_reads_sr_nearest_support_and_resistance_not_the_cleared_variants():
    """sr_cleared_* levels are the ALREADY-BROKEN levels (breakout-quality assessment) — a
    proximity watch needs the NOT-yet-reached nearest levels instead."""
    body = _check_sr_watch_reverts_body()
    assert 'sr.get("sr_nearest_support")' in body
    assert 'sr.get("sr_nearest_resistance")' in body
    assert "sr_cleared" not in body


def test_band_is_atr_times_the_watchs_own_multiplier_not_a_fixed_percent():
    body = _check_sr_watch_reverts_body()
    assert "band = float(atr) * float(w.atr_multiplier)" in body


def test_is_near_is_true_if_within_band_of_either_level_an_or_not_an_and():
    body = _check_sr_watch_reverts_body()
    assert "dist_support is not None and dist_support <= band" in body
    assert "dist_resistance is not None and dist_resistance <= band" in body


def test_currently_near_resets_to_false_once_price_moves_out_of_the_band():
    """The core dedup-reset mechanism this feature was explicitly asked for: leaving the band
    must reset currently_near so the NEXT approach (of any level) can fire again."""
    body = _check_sr_watch_reverts_body()
    not_near_idx = body.index("if not is_near:")
    tail = body[not_near_idx:not_near_idx + 350]
    assert "w.currently_near = False" in tail


def test_already_near_watches_are_skipped_not_re_alerted_every_cycle():
    """The transition-only dedup: a watch that's ALREADY currently_near must not send a second
    email while it remains inside the band."""
    body = _check_sr_watch_reverts_body()
    assert "if w.currently_near:" in body
    already_near_idx = body.index("if w.currently_near:")
    # the FIRST occurrence (inside the is_near branch) must skip via continue, not send again
    tail = body[already_near_idx:already_near_idx + 250]
    assert "continue" in tail


def test_marks_currently_near_true_only_after_a_successful_send_not_before():
    """A failed email send must not silently mark the watch as alerted — the user would then
    never learn about a real approach that happened to hit a delivery failure, and the watch
    would incorrectly stay silent on the next cycle too."""
    body = _check_sr_watch_reverts_body()
    sent_idx = body.index("sent_ok = send_sr_watch_alert_email(")
    marked_idx = body.index("w.currently_near = True")
    assert sent_idx < marked_idx
    assert "if sent_ok:" in body


def test_picks_the_closer_level_when_both_are_within_the_band():
    body = _check_sr_watch_reverts_body()
    assert "dist_support is not None and (dist_resistance is None or dist_support <= dist_resistance)" in body


def test_job_is_registered_at_one_minute_interval():
    assert 'id="sr_watch_check"' in _scheduler_source
    idx = _scheduler_source.index('id="sr_watch_check"')
    preceding = _scheduler_source[max(0, idx - 300):idx]
    assert "minutes=1" in preceding


def test_job_registration_sits_inside_the_alerting_enabled_gate():
    """BUG-LOCALDEV-ALERTS-UNGATED: this sends real email — must be registered inside the same
    `if _is_alerting_enabled():` block as every other alert-emitting job, not outside it."""
    idx = _scheduler_source.index('id="sr_watch_check"')
    preceding = _scheduler_source[:idx]
    gate_idx = preceding.rindex("if _is_alerting_enabled():")
    # nothing that would close the block (a dedent back to the same or lesser indentation as
    # the if-statement itself) appears between the gate and this job's own registration
    between = preceding[gate_idx:]
    assert "\n    if _is_alerting_enabled():" not in between[len("if _is_alerting_enabled():"):]


# ── Market-hours gating, mirroring check_squeeze_watch_reverts()'s own established pattern ──

def test_uses_the_same_is_market_hours_helper_as_squeeze_watch():
    body = _check_sr_watch_reverts_body()
    assert "from .paper_trading_engine import _is_market_hours, _batch_compute_atr" in body
    assert '_is_market_hours("US")' in body
    assert '_is_market_hours("HK")' in body


def test_whole_function_short_circuits_when_both_markets_are_closed():
    body = _check_sr_watch_reverts_body()
    assert "if not _us_market_open and not _hk_market_open:" in body


def test_per_watch_gate_skips_hk_symbols_when_hk_is_closed_even_if_us_is_open():
    body = _check_sr_watch_reverts_body()
    assert '_is_hk_watch = w.symbol.upper().endswith(".HK")' in body
    assert "if _is_hk_watch and not _hk_market_open:" in body
    assert "if not _is_hk_watch and not _us_market_open:" in body
