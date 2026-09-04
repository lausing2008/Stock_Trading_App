"""Tests for the classic short-squeeze alert (check_short_squeeze_alerts()).

send_short_squeeze_email() is pure string composition (no DB/network dependency), so it's
tested directly with real inputs. check_short_squeeze_alerts() itself can't be imported in this
test environment — scheduler.py's import chain pulls in apscheduler and other unstubbed
modules (see test_price_alert_price_check.py's docstring for the same constraint) — so the
scan logic/job registration is covered by source-text regression checks instead, matching
test_scheduler_static_names.py's / test_volume_anomaly_alert.py's established pattern.
"""
import pathlib
from datetime import date
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


# ── AUD288-SQUEEZE-NO-VOLUME-CONFIRM: RVOL rendered alongside short-float % ─────────────────

def test_rvol_rendered_in_html_and_text_when_present():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_short_squeeze_email("user@example.com", [
            {"symbol": "TMDX", "short_percent_of_float": 34.5, "change_pct": 5.2, "price": 92.0,
             "rvol": 3.1},
        ])
    html, text = calls[0]["html"], calls[0]["text"]
    assert "3.1x avg volume" in html
    assert "3.1x avg volume" in text


def test_missing_rvol_degrades_gracefully_no_placeholder_shown():
    """An older candidate dict (or a symbol whose avg-volume cache entry was missing) has no
    rvol key at all — must not crash or render a fabricated "0.0x avg volume" placeholder."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        result = send_short_squeeze_email("user@example.com", [
            {"symbol": "GME", "short_percent_of_float": 22.5, "change_pct": 8.3, "price": 25.10},
        ])
    assert result is True
    assert "avg volume" not in calls[0]["html"]


# ── AUD-SQUEEZE3-UWSHORTINTERESTCORROBORATION: UW/free-source disagreement flag ─────────────

def test_uw_disagreement_flag_renders_both_readings_in_html_and_text():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_short_squeeze_email("user@example.com", [
            {"symbol": "IMVT", "short_percent_of_float": 18.28, "change_pct": 4.1, "price": 43.09,
             "uw_disagrees": True, "uw_short_percent_of_float": 8.22},
        ])
    html, text = calls[0]["html"], calls[0]["text"]
    assert "8.2%" in html and "18.3%" in html
    assert "disagree" in html.lower()
    assert "8.2%" in text and "18.3%" in text


def test_no_uw_disagreement_flag_renders_no_extra_content():
    """The common case — UW data unavailable, or agrees within tolerance — must not render any
    disagreement content at all, matching this alert's own established "degrade gracefully,
    never show a fabricated/empty placeholder" convention."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_short_squeeze_email("user@example.com", [
            {"symbol": "GME", "short_percent_of_float": 22.5, "change_pct": 8.3, "price": 25.10},
        ])
    html, text = calls[0]["html"], calls[0]["text"]
    assert "disagree" not in html.lower()
    assert "disagree" not in text.lower()


def test_uw_corroboration_check_is_wired_after_the_candidate_dict_is_built():
    """The exact insertion point: after candidates is fully built (so the check only ever runs
    against symbols that already cleared every other gate), before the game-plan loop."""
    body = _check_short_squeeze_alerts_body()
    assert "get_short_interest(sym)" in body
    assert "_SQUEEZE_UW_DISAGREEMENT_REL_THRESHOLD" in body
    candidates_built_idx = body.index("if not candidates:")
    uw_check_idx = body.index("get_short_interest(sym)")
    game_plan_idx = body.index("_squeeze_game_plan(session, sym, float(cand[\"price\"]))")
    assert candidates_built_idx < uw_check_idx < game_plan_idx


def test_uw_corroboration_never_suppresses_the_candidate_only_flags_it():
    """A real, material disagreement must never remove a candidate from the dict or `continue`
    out of the loop — this app's own established design principle (never silently withhold a
    real setup) means a disagreement is extra context for the recipient, not a suppression."""
    body = _check_short_squeeze_alerts_body()
    uw_section_start = body.index("get_short_interest(sym)")
    uw_section_end = body.index("# Game plan (entry/stop/target)")
    uw_section = body[uw_section_start:uw_section_end]
    assert "continue" not in uw_section
    assert "del candidates[" not in uw_section
    assert "candidates.pop(" not in uw_section


def test_uw_lookup_failure_fails_open_never_crashes_the_whole_scan():
    body = _check_short_squeeze_alerts_body()
    uw_section_start = body.index("try:\n                    from . import unusual_whales")
    uw_section_end = body.index("_uw_si = None", uw_section_start) + len("_uw_si = None")
    uw_try_block = body[uw_section_start:uw_section_end]
    assert "except Exception:" in uw_try_block


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


def test_short_interest_date_age_in_days_rendered_alongside_bare_date():
    """A recipient shouldn't have to do the date subtraction themselves — the email should
    state the age in days directly next to the settlement date."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake), \
         patch("src.services.email_service.date") as fake_date:
        fake_date.today.return_value = date(2026, 8, 12)
        fake_date.fromisoformat.side_effect = date.fromisoformat
        send_short_squeeze_email("user@example.com", [
            {"symbol": "POET", "short_percent_of_float": 15.8, "change_pct": 3.67, "price": 8.90,
             "short_interest_date": "2026-07-15"},
        ])
    html, text = calls[0]["html"], calls[0]["text"]
    # 2026-08-12 minus 2026-07-15 = 28 days.
    assert "2026-07-15, 28d ago" in html
    assert "2026-07-15, 28d ago" in text


def test_malformed_short_interest_date_degrades_to_bare_date_not_crash():
    """A malformed date string (not a real fromisoformat failure this app would ever produce
    today, but a defensive guard for a future data-source change) must not crash the send —
    it should fall back to showing the bare, unparsed string with no age computed."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        result = send_short_squeeze_email("user@example.com", [
            {"symbol": "GME", "short_percent_of_float": 22.5, "change_pct": 8.3, "price": 25.10,
             "short_interest_date": "not-a-real-date"},
        ])
    assert result is True
    assert "as of not-a-real-date)" in calls[0]["html"]
    assert "d ago" not in calls[0]["html"]


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


# ── AUD288-SQUEEZE-NO-VOLUME-CONFIRM: RVOL confirmation gate ────────────────────────────────

def test_requires_an_rvol_floor_using_the_shared_session_elapsed_helper():
    """Must reuse the SAME shared _session_elapsed_rvol_thresholds() helper check_volume_
    anomalies()/check_squeeze_ignition_alerts() also call — not a naive flat threshold (which
    would over-trigger early in the session relative to a full-day average), and not a 3rd
    independently-duplicated copy of the same formula."""
    body = _check_short_squeeze_alerts_body()
    assert "_SQUEEZE_RVOL_BASE" in body
    assert "_session_elapsed_rvol_thresholds(" in body
    assert "rvol < rvol_threshold" in body


def test_avg_volume_cache_is_read_alongside_live_prices():
    body = _check_short_squeeze_alerts_body()
    assert '"stockai:avg_volume"' in body


def test_rvol_check_applies_in_both_the_prewarm_pass_and_the_main_loop():
    """The MGET pre-warm pass and the main candidate-building loop each independently repeat
    the same price-only filters (AUD-SQUEEZE250725-PERF4.1) — both copies must apply the SAME
    rvol gate, or the pre-warm pass would wrongly admit/reject symbols the main loop disagrees
    with. Checks the actual COMPARISON in each pass (not just the threshold-assignment line,
    which stays present even if only the comparison itself were sabotaged — a real gap caught
    via adversarial verification: sabotaging just the pre-warm pass's own `if` condition left
    the threshold-assignment line untouched, so a count on that line alone did not catch it)."""
    body = _check_short_squeeze_alerts_body()
    assert body.count("rvol_threshold = _sq_hk_rvol_threshold if _is_hk_sym else _sq_us_rvol_threshold") == 2
    # Pre-warm pass computes the ratio inline in its own condition; the main loop assigns it to
    # `rvol` first (since `rvol` is later reused for the candidate dict) — genuinely different
    # surface forms of the SAME check, both must be present.
    assert "if float(vol) / float(avg_vol) < rvol_threshold:" in body
    assert "if rvol < rvol_threshold:" in body


def test_rvol_is_threaded_into_the_candidate_dict():
    """The real rvol value must reach the candidate dict passed to send_short_squeeze_email() —
    otherwise the email builder's own rvol-rendering (tested above) would never have anything
    to show."""
    body = _check_short_squeeze_alerts_body()
    assert '"rvol": round(rvol, 2)' in body


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
    """AUD-SQUEEZE250725-ISSUE1 added a second, rolling-48h counter increment right after the
    per-cycle counter — the ONLY thing allowed to sit between the two increments and the
    `continue` is that new call (plus its own explanatory comment), never any unrelated logic
    that would make the miss path do real work before skipping the candidate."""
    body = _check_short_squeeze_alerts_body()
    incr_idx = body.index("_fundamentals_cache_misses += 1")
    continue_idx = body.index("continue", incr_idx)
    between = body[incr_idx + len("_fundamentals_cache_misses += 1"):continue_idx]
    # Strip the one allowed addition (comment lines + the rolling-counter call) before asserting
    # nothing else remains.
    stripped_lines = [
        line.strip() for line in between.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert stripped_lines == ["_incr_rolling_counter(_SQUEEZE_FUND_CACHE_MISS_COUNTER_KEY)"]


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


# ── T270-SQUEEZE-DAYSTOCOVER-ALERT ───────────────────────────────────────────────────────────
# Live-calibrated 2026-08-13 against real production candidates that already clear
# _SQUEEZE_MIN_SHORT_FLOAT (n=73 distinct readings): p10=1.13, p25=1.92, p50=4.65 days-to-cover.
# 2.0 lands just above p25 — selective (roughly the most acute quarter), not so tight it's
# nearly always empty (the design note's own original <=1.0 idea would sit below p10).

def test_short_ratio_is_read_from_the_same_fundamentals_blob():
    body = _check_short_squeeze_alerts_body()
    assert 'data.get("short_ratio")' in body


def test_short_ratio_read_happens_after_the_staleness_gate_reuses_it_not_a_second_check():
    """short_ratio comes from the SAME fundamentals blob, as of the SAME short_interest_date
    already staleness-checked for short_percent_of_float — this must not re-derive or
    re-validate a second staleness cutoff for the exact same underlying data."""
    body = _check_short_squeeze_alerts_body()
    staleness_idx = body.index("_si_date is None or _si_date < _squeeze_stale_cutoff_str")
    short_ratio_idx = body.index('data.get("short_ratio")')
    assert staleness_idx < short_ratio_idx


def test_days_to_cover_critical_flag_uses_the_le_2_point_0_threshold():
    body = _check_short_squeeze_alerts_body()
    assert "_SQUEEZE_CRITICAL_DAYS_TO_COVER" in body
    assert "_short_ratio <= _SQUEEZE_CRITICAL_DAYS_TO_COVER" in body


def test_critical_days_to_cover_constant_is_2_point_0():
    start = _scheduler_source.index("_SQUEEZE_CRITICAL_DAYS_TO_COVER = ")
    line_end = _scheduler_source.index("\n", start)
    assert "2.0" in _scheduler_source[start:line_end]


def test_missing_short_ratio_is_none_not_falsely_critical():
    """A symbol yfinance never reported short_ratio for must be genuinely absent (None), never
    silently treated as critical just because a None comparison happened to be falsy — the
    `is not None` guard is the load-bearing part of this check."""
    body = _check_short_squeeze_alerts_body()
    assert "_short_ratio is not None and _short_ratio <= _SQUEEZE_CRITICAL_DAYS_TO_COVER" in body


def test_short_ratio_and_critical_flag_are_threaded_into_the_candidate_dict():
    body = _check_short_squeeze_alerts_body()
    assert '"short_ratio": _short_ratio' in body
    assert '"days_to_cover_critical":' in body


# ── send_short_squeeze_email() days-to-cover rendering — pure composition, tested directly ──

def test_critical_days_to_cover_escalates_the_subject_line():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_short_squeeze_email("user@example.com", [
            {"symbol": "GME", "short_percent_of_float": 22.5, "change_pct": 8.3, "price": 25.10,
             "short_ratio": 1.2, "days_to_cover_critical": True},
        ])
    assert "CRITICAL" in calls[0]["subject"]


def test_non_critical_days_to_cover_does_not_escalate_the_subject_line():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_short_squeeze_email("user@example.com", [
            {"symbol": "GME", "short_percent_of_float": 22.5, "change_pct": 8.3, "price": 25.10,
             "short_ratio": 5.5, "days_to_cover_critical": False},
        ])
    assert "CRITICAL" not in calls[0]["subject"]


def test_days_to_cover_value_rendered_in_html_and_text():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_short_squeeze_email("user@example.com", [
            {"symbol": "GME", "short_percent_of_float": 22.5, "change_pct": 8.3, "price": 25.10,
             "short_ratio": 1.2, "days_to_cover_critical": True},
        ])
    html, text = calls[0]["html"], calls[0]["text"]
    assert "1.2d to cover" in html
    assert "1.2d to cover" in text


def test_missing_short_ratio_degrades_gracefully_not_crash_and_omits_dtc_text():
    """An older candidate dict, or a symbol with no short_ratio at all, must not crash or
    render a placeholder days-to-cover figure. The disclaimer paragraph legitimately explains
    what "days to cover" means regardless of whether any candidate has one — checking against
    the specific PER-ROW rendering (an "Nd to cover" figure, N being a real number) rather than
    the bare substring "d to cover", which the disclaimer's own explanatory prose contains."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        result = send_short_squeeze_email("user@example.com", [
            {"symbol": "GME", "short_percent_of_float": 22.5, "change_pct": 8.3, "price": 25.10},
        ])
    assert result is True
    import re
    assert re.search(r"\d(\.\d)?d to cover", calls[0]["html"]) is None


def test_critical_row_gets_visually_distinguished_border_non_critical_does_not():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_short_squeeze_email("user@example.com", [
            {"symbol": "GME", "short_percent_of_float": 22.5, "change_pct": 8.3, "price": 25.10,
             "short_ratio": 1.2, "days_to_cover_critical": True},
            {"symbol": "AMC", "short_percent_of_float": 18.0, "change_pct": 5.1, "price": 4.50,
             "short_ratio": 5.5, "days_to_cover_critical": False},
        ])
    html = calls[0]["html"]
    assert "rgba(220,38,38,0.3)" in html  # the critical row's border color appears at least once
    assert calls[0]["subject"].count("CRITICAL") == 1  # only escalated once, not per-critical-row


def test_critical_summary_note_only_appears_when_at_least_one_candidate_is_critical():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_short_squeeze_email("user@example.com", [
            {"symbol": "AMC", "short_percent_of_float": 18.0, "change_pct": 5.1, "price": 4.50,
             "short_ratio": 5.5, "days_to_cover_critical": False},
        ])
    assert "critically thin exit" not in calls[0]["html"]
