"""Tests for the options-expiry gamma-unwind alert (check_gamma_unwind_alerts()).

send_gamma_unwind_email() is pure string composition (no DB/network dependency), so it's
tested directly with real inputs. check_gamma_unwind_alerts() itself can't be imported in this
test environment — scheduler.py's import chain pulls in apscheduler and other unstubbed
modules — so the scan logic/job registration is covered by source-text regression checks
instead, matching test_volume_anomaly_alert.py's / test_short_squeeze_alert.py's established
pattern.
"""
import pathlib
from unittest.mock import patch

from src.services.email_service import send_gamma_unwind_email

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


def _capture_send():
    calls = []
    def _fake_send(to, subject, body_html, body_text):
        calls.append({"to": to, "subject": subject, "html": body_html, "text": body_text})
        return True
    return calls, _fake_send


def _check_gamma_unwind_alerts_body() -> str:
    start = _scheduler_source.index("def check_gamma_unwind_alerts(")
    end = _scheduler_source.index("\ndef ", start + 1)
    return _scheduler_source[start:end]


# ── send_gamma_unwind_email() — pure composition, tested directly ───────────────────────────

def test_single_candidate_renders_symbol_side_and_concentration():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_gamma_unwind_email("user@example.com", [
            {"symbol": "TSLA", "expiry": "2026-08-07", "days_to_expiry": 2,
             "dominant_side": "calls", "concentration_pct": 68.5,
             "total_oi_near_money": 45000, "price": 250.0},
        ])
    html, text = calls[0]["html"], calls[0]["text"]
    assert "TSLA" in html and "68% calls" in html and "45,000" in html and "$250.00" in html
    assert "TSLA" in text


def test_expires_today_renders_distinct_urgency_text():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_gamma_unwind_email("user@example.com", [
            {"symbol": "TSLA", "expiry": "2026-08-05", "days_to_expiry": 0,
             "dominant_side": "puts", "concentration_pct": 60.0,
             "total_oi_near_money": 10000, "price": 250.0},
        ])
    html = calls[0]["html"]
    assert "expires TODAY" in html
    assert "expires in 0d" not in html  # must use the distinct today-specific phrasing


# ── AUD265-ZERO-DTE-OI-IS-STALE-BY-CONSTRUCTION ─────────────────────────────────────────────

def test_zero_dte_row_qualifies_the_oi_figure_as_stale_relative_to_todays_session():
    """Open interest is exchange-published once per day, as of the PRIOR close — on the day a
    contract actually expires, the reported OI figure is already a full trading session stale
    relative to the unwind the alert is about. The 0-DTE row specifically must carry an
    explicit "as of yesterday's close" qualifier so a reader doesn't mistake it for a live
    intraday number."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_gamma_unwind_email("user@example.com", [
            {"symbol": "TSLA", "expiry": "2026-08-05", "days_to_expiry": 0,
             "dominant_side": "puts", "concentration_pct": 60.0,
             "total_oi_near_money": 10000, "price": 250.0},
        ])
    html, text = calls[0]["html"], calls[0]["text"]
    assert "as of yesterday" in html.lower()
    assert "as of yesterday" in text.lower()


def test_non_zero_dte_row_does_not_carry_the_stale_oi_qualifier():
    """The qualifier must be scoped to the dte=0 row only — a 1-5 day-to-expiry row's OI figure
    genuinely is current as of the most recent close, so tacking the same caveat onto every row
    would misleadingly suggest every row's data is equally stale."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_gamma_unwind_email("user@example.com", [
            {"symbol": "XYZ", "expiry": "2026-08-08", "days_to_expiry": 3,
             "dominant_side": "calls", "concentration_pct": 70.0,
             "total_oi_near_money": 5000, "price": 42.0},
        ])
    html, text = calls[0]["html"], calls[0]["text"]
    assert "as of yesterday" not in html.lower()
    assert "as of yesterday" not in text.lower()
    assert "expires in 3d" in html


def test_subject_and_body_never_claim_a_firm_direction():
    """This is the one property that MUST hold — unlike the short-squeeze alert's explicit
    BUY-signal framing, this alert must never assert a specific direction, since the app has
    no real gamma-exposure calc to back that claim up."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_gamma_unwind_email("user@example.com", [
            {"symbol": "TSLA", "expiry": "2026-08-07", "days_to_expiry": 2,
             "dominant_side": "calls", "concentration_pct": 68.5,
             "total_oi_near_money": 45000, "price": 250.0},
        ])
    subject, html = calls[0]["subject"], calls[0]["html"]
    assert "BUY" not in subject.upper() and "SELL" not in subject.upper()
    assert "not a directional" in html.lower() or "genuinely uncertain" in html.lower()


def test_puts_dominant_renders_red_calls_dominant_renders_green():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_gamma_unwind_email("user@example.com", [
            {"symbol": "AAA", "expiry": "2026-08-07", "days_to_expiry": 2,
             "dominant_side": "calls", "concentration_pct": 60.0,
             "total_oi_near_money": 1000, "price": 100.0},
            {"symbol": "BBB", "expiry": "2026-08-07", "days_to_expiry": 2,
             "dominant_side": "puts", "concentration_pct": 60.0,
             "total_oi_near_money": 1000, "price": 100.0},
        ])
    html = calls[0]["html"]
    assert "#22c55e" in html  # calls-dominant color
    assert "#ef4444" in html  # puts-dominant color


def test_missing_price_degrades_gracefully_not_crash():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        result = send_gamma_unwind_email("user@example.com", [
            {"symbol": "XYZ", "expiry": "2026-08-07", "days_to_expiry": 2,
             "dominant_side": "calls", "concentration_pct": 60.0,
             "total_oi_near_money": 1000, "price": None},
        ])
    assert result is True
    assert "—" in calls[0]["html"]


# ── check_gamma_unwind_alerts() — source-text regression checks ─────────────────────────────

def test_uses_bounded_symbol_set_not_universe_wide():
    """yfinance options-chain is this app's most rate-limit-fragile call — must never scan the
    whole universe, matching the pre-existing EOD options-flow snapshot job's own discipline."""
    body = _check_gamma_unwind_alerts_body()
    assert "_bounded_options_flow_symbols" in body


def test_filters_to_expiries_within_the_max_days_window():
    body = _check_gamma_unwind_alerts_body()
    assert "_GAMMA_UNWIND_MAX_DAYS_TO_EXPIRY" in body


def test_requires_minimum_notional_floor_to_avoid_thin_chain_false_signal():
    body = _check_gamma_unwind_alerts_body()
    assert "_GAMMA_UNWIND_MIN_NOTIONAL_USD" in body


def test_notional_floor_scales_with_price_not_a_flat_contract_count():
    """AUD265-GAMMA-MIN-OI-FLOOR-TOO-LOW: a flat contract-count floor treats a $5 stock and a
    $500 stock identically at the same OI count, even though the real notional (and therefore
    dealer hedge-unwind exposure) differs by 100x. The floor comparison must multiply total_oi
    by price (and the 100-shares-per-contract multiplier), not compare total_oi alone against a
    bare number."""
    body = _check_gamma_unwind_alerts_body()
    assert "notional_usd = total_oi * 100 * price" in body
    assert "notional_usd < _GAMMA_UNWIND_MIN_NOTIONAL_USD" in body
    # guard against a stale bare contract-count comparison sneaking back in alongside this
    assert "total_oi < _GAMMA_UNWIND_MIN_NOTIONAL_USD" not in body


def test_requires_concentration_threshold_on_either_side():
    body = _check_gamma_unwind_alerts_body()
    assert "_GAMMA_UNWIND_MIN_OI_CONCENTRATION" in body
    assert 'dominant_side = "calls"' in body
    assert 'dominant_side = "puts"' in body


# ── AUD265-GAMMA-OI-THRESHOLD-ASYMMETRIC ────────────────────────────────────────────────────
# Live-calibrated 2026-08-13 against this job's own exact methodology (5% near-money strike
# band, nearest expiry <=5 days) across the real bounded symbol set: n=30, call_share median
# (p50) = 0.676, p80 = 0.854 — the old shared 55% threshold cleared on 70% of scanned symbols
# from the calls side (barely selective) vs. only 20% from the puts side (genuinely selective).
# Calls now requires 0.85 (the measured ~80th percentile); puts is unchanged at 0.55.

def test_calls_branch_uses_the_raised_085_threshold_not_the_puts_055_threshold():
    """The calls-dominant comparison must reference the NEW, higher constant — not the
    original 0.55 constant the puts branch still correctly uses."""
    body = _check_gamma_unwind_alerts_body()
    calls_line_idx = body.index('dominant_side = "calls"')
    calls_line_start = body.rindex("if call_share", 0, calls_line_idx)
    calls_condition = body[calls_line_start:calls_line_idx]
    assert "_GAMMA_UNWIND_MIN_CALLS_CONCENTRATION" in calls_condition
    assert "_GAMMA_UNWIND_MIN_OI_CONCENTRATION" not in calls_condition


def test_puts_branch_still_uses_the_original_055_threshold():
    """Regression guard — the puts side was already genuinely selective and must NOT have
    been touched by this fix."""
    body = _check_gamma_unwind_alerts_body()
    puts_line_idx = body.index('dominant_side = "puts"')
    puts_line_start = body.rindex("elif (1 - call_share)", 0, puts_line_idx)
    puts_condition = body[puts_line_start:puts_line_idx]
    assert "_GAMMA_UNWIND_MIN_OI_CONCENTRATION" in puts_condition
    assert "_GAMMA_UNWIND_MIN_CALLS_CONCENTRATION" not in puts_condition


def test_calls_threshold_constant_is_set_to_the_measured_085_not_the_old_055():
    start = _scheduler_source.index("_GAMMA_UNWIND_MIN_CALLS_CONCENTRATION = ")
    line_end = _scheduler_source.index("\n", start)
    assert "0.85" in _scheduler_source[start:line_end]


def test_puts_threshold_constant_is_unchanged_at_055():
    start = _scheduler_source.index("_GAMMA_UNWIND_MIN_OI_CONCENTRATION = ")
    line_end = _scheduler_source.index("\n", start)
    assert "0.55" in _scheduler_source[start:line_end]


def test_has_a_per_symbol_rate_limit_sleep():
    body = _check_gamma_unwind_alerts_body()
    assert "time.sleep(" in body


def test_uses_a_redis_lock():
    body = _check_gamma_unwind_alerts_body()
    assert "_GAMMA_UNWIND_LOCK_KEY" in body
    assert "nx=True" in body


def test_dedup_is_per_symbol_and_expiry_not_just_symbol():
    """A stock legitimately has a NEW gamma-unwind setup for a different expiry later — dedup
    keyed on symbol alone would silently suppress that genuine second setup."""
    body = _check_gamma_unwind_alerts_body()
    assert 'f"{sym}:{c[\'expiry\']}"' in body or "dedup_field" in body


def test_job_is_registered_at_a_multi_hour_interval_not_every_minute():
    assert 'id="gamma_unwind_alert_check"' in _scheduler_source
    idx = _scheduler_source.index('id="gamma_unwind_alert_check"')
    preceding = _scheduler_source[max(0, idx - 300):idx]
    assert "hours=" in preceding
    assert "minutes=1" not in preceding


# ── AUD265-GAMMA-ASSUMES-SORTED-EXPIRIES ────────────────────────────────────────────────────

def test_near_expiries_is_explicitly_sorted_not_left_in_yfinance_order():
    """near_expiries[0] is meant to be the NEAREST expiry — sorted() makes that guarantee
    structural rather than dependent on yfinance's own (undocumented) ordering of t.options."""
    body = _check_gamma_unwind_alerts_body()
    assert "near_expiries = sorted(" in body
    # The indexing itself must still be present (this is a sort-before-index fix, not a
    # removal of the "take the first one" behavior).
    assert "exp = near_expiries[0]" in body


# ── AUD-GEXCORROBORATE: real UW GEX levels cross-checking the free OI-concentration proxy ────

def test_gex_corroboration_reads_the_real_unusual_whales_function():
    body = _check_gamma_unwind_alerts_body()
    assert "_uw.get_gex_levels(sym)" in body


def test_gex_corroboration_checks_all_three_real_levels():
    body = _check_gamma_unwind_alerts_body()
    assert '"call_wall": _gex.call_wall' in body
    assert '"put_wall": _gex.put_wall' in body
    assert '"gamma_flip": _gex.gamma_flip' in body


def test_gex_corroboration_is_wired_after_the_candidates_dict_is_built():
    body = _check_gamma_unwind_alerts_body()
    candidates_idx = body.index("if not candidates:")
    gex_idx = body.index("_uw.get_gex_levels(sym)")
    assert candidates_idx < gex_idx


def test_gex_corroboration_never_suppresses_a_candidate_only_annotates_it():
    """The real OI-concentration proxy alone must still drive every candidate's inclusion —
    a missing/failed GEX lookup must never remove a candidate, only skip annotating it."""
    body = _check_gamma_unwind_alerts_body()
    gex_idx = body.index("_uw.get_gex_levels(sym)")
    segment = body[gex_idx:gex_idx + 800]
    assert "continue" in segment  # skips annotation on a None GEX result
    assert "del candidates" not in segment
    assert "candidates.pop" not in segment


def test_gex_lookup_failure_fails_open_never_crashes_the_whole_scan():
    body = _check_gamma_unwind_alerts_body()
    gex_block_idx = body.index("from . import unusual_whales as _uw\n                    _gex = _uw.get_gex_levels(sym)")
    segment = body[max(0, gex_block_idx - 60):gex_block_idx + 120]
    assert "except Exception" in segment


def test_gex_corroboration_uses_its_own_proximity_band_constant():
    body = _check_gamma_unwind_alerts_body()
    assert "_GEX_CORROBORATE_BAND_PCT" in body


# ── send_gamma_unwind_email() — GEX corroboration rendering ──────────────────────────────────

def test_gex_corroboration_renders_when_present():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_gamma_unwind_email("user@example.com", [
            {"symbol": "TSLA", "expiry": "2026-08-07", "days_to_expiry": 2,
             "dominant_side": "calls", "concentration_pct": 68.5,
             "total_oi_near_money": 45000, "price": 250.0,
             "gex_corroborates": True,
             "gex_nearby_levels": [{"name": "call_wall", "level": 252.0}]},
        ])
    html, text = calls[0]["html"], calls[0]["text"]
    assert "Real GEX corroborates" in html
    assert "call wall $252.00" in html
    assert "Unusual Whales" in html
    assert "Real GEX corroborates" in text


def test_gex_corroboration_renders_multiple_nearby_levels():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_gamma_unwind_email("user@example.com", [
            {"symbol": "TSLA", "expiry": "2026-08-07", "days_to_expiry": 2,
             "dominant_side": "calls", "concentration_pct": 68.5,
             "total_oi_near_money": 45000, "price": 250.0,
             "gex_corroborates": True,
             "gex_nearby_levels": [
                 {"name": "call_wall", "level": 252.0},
                 {"name": "gamma_flip", "level": 249.0},
             ]},
        ])
    html = calls[0]["html"]
    assert "call wall $252.00" in html
    assert "gamma flip $249.00" in html


def test_no_gex_corroboration_renders_no_extra_content():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_gamma_unwind_email("user@example.com", [
            {"symbol": "TSLA", "expiry": "2026-08-07", "days_to_expiry": 2,
             "dominant_side": "calls", "concentration_pct": 68.5,
             "total_oi_near_money": 45000, "price": 250.0},
        ])
    html, text = calls[0]["html"], calls[0]["text"]
    assert "Real GEX corroborates" not in html
    assert "Real GEX corroborates" not in text


def test_gex_corroborates_false_renders_no_extra_content_even_with_stale_levels_present():
    """gex_corroborates must gate the rendering, not just the presence of gex_nearby_levels —
    a defensive guard in case the two ever get out of sync."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_gamma_unwind_email("user@example.com", [
            {"symbol": "TSLA", "expiry": "2026-08-07", "days_to_expiry": 2,
             "dominant_side": "calls", "concentration_pct": 68.5,
             "total_oi_near_money": 45000, "price": 250.0,
             "gex_corroborates": False,
             "gex_nearby_levels": [{"name": "call_wall", "level": 252.0}]},
        ])
    html = calls[0]["html"]
    assert "Real GEX corroborates" not in html
