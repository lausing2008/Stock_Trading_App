"""Source-text regression checks for the 3 squeeze-family recommendations built 2026-08-15:

1. AUD265-SHORT-INTEREST-AGE-NEVER-CHECKED (extended to check_prebreakout_alerts()): a
   short_interest_date staleness reject + threading, matching check_short_squeeze_alerts()'s
   own established convention exactly.
2. T264-SHORTSQUEEZE-PREBREAKOUT-CONFIDENCE (extended to short_squeeze/gamma_unwind_calls/
   puts): _build_squeeze_family_calibration()/_squeeze_family_calibration_for_alert_type()
   wired into check_short_squeeze_alerts() and check_gamma_unwind_alerts().
3. T264-SQUEEZEFAMILY-REGIME-FLAG: a SOFT, non-suppressing regime flag threaded into all 3
   check_*_alerts() functions and rendered by _regime_warning_lines() in email_service.py.

None of the 3 check_*_alerts() functions can be imported/exercised directly in this test
environment (full DB/Redis/HTTP integration paths, apscheduler in the import chain) — matching
this repo's established convention (see test_prebreakout_confidence_wiring.py), these are
direct source-text checks against the real files, not a hand-copied reimplementation that
could silently drift from them.
"""
import pathlib

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()
_email_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "email_service.py"
_email_source = _email_path.read_text()


def _function_body(name: str, end_marker: str) -> str:
    start = _scheduler_source.index(f"def {name}(")
    end = _scheduler_source.index(end_marker, start)
    return _scheduler_source[start:end]


# ── 1. short_interest_date staleness + threading in check_prebreakout_alerts() ──────────────

def test_prebreakout_alerts_rejects_stale_short_interest_readings():
    body = _function_body("check_prebreakout_alerts", "\n\ndef _record_prebreakout_alert_outcome(")
    assert "_si_date is None or _si_date < _pb_stale_cutoff_str" in body


def test_prebreakout_stale_cutoff_matches_the_squeeze_alerts_own_30_day_window():
    """Both alerts assert a squeeze-precondition thesis in an unsolicited email with no human
    review before send — the staleness tolerance must match, not diverge into two different
    "how stale is too stale" answers for the identical short_percent_of_float field."""
    squeeze_body = _function_body("check_short_squeeze_alerts", "\n\ndef check_prebreakout_alerts(")
    prebreakout_body = _function_body("check_prebreakout_alerts", "\n\ndef _record_prebreakout_alert_outcome(")
    assert "timedelta(days=30)" in squeeze_body
    assert "timedelta(days=30)" in prebreakout_body


def test_prebreakout_candidate_dict_carries_short_interest_date():
    body = _function_body("check_prebreakout_alerts", "\n\ndef _record_prebreakout_alert_outcome(")
    assert '"short_interest_date": _si_date' in body


def test_send_prebreakout_email_renders_short_interest_age():
    """Mirrors send_short_squeeze_email()'s own si_str age-rendering exactly — a recipient
    shouldn't see one alert type disclose staleness and the other stay silent about it."""
    start = _email_source.index("def send_prebreakout_email(")
    end = _email_source.index("\n\ndef send_squeeze_watch_revert_email(", start)
    body = _email_source[start:end]
    assert 'si_date = c.get("short_interest_date")' in body
    assert "_si_age_days = (date.today() - date.fromisoformat(si_date)).days" in body


# ── 2. Calibration wiring in check_short_squeeze_alerts() / check_gamma_unwind_alerts() ─────

def test_short_squeeze_calibration_buckets_are_built_once_before_the_candidate_loop():
    body = _function_body("check_short_squeeze_alerts", "\n\ndef check_prebreakout_alerts(")
    cal_build_idx = body.index('_sq_cal_buckets = _build_squeeze_family_calibration(session, "short_squeeze")')
    candidates_dict_idx = body.index("candidates: dict[str, dict] = {}")
    assert cal_build_idx < candidates_dict_idx


def test_short_squeeze_candidate_dict_carries_calibration_fields():
    body = _function_body("check_short_squeeze_alerts", "\n\ndef check_prebreakout_alerts(")
    assert '"calibrated_win_rate": _sq_win_rate' in body
    assert '"calibrated_win_rate_count": _sq_win_count' in body


def test_gamma_unwind_calibration_is_built_for_both_calls_and_puts_independently():
    """Both sides must be built as two SEPARATE calls (never sharing one pooled bucket dict) —
    the exact same never-pool-calls-with-puts discipline check_gamma_unwind_alerts() itself
    already applies when splitting SqueezeAlertOutcome rows by dominant_side."""
    body = _function_body("check_gamma_unwind_alerts", "\n\ndef check_squeeze_watch_reverts(")
    assert '"gamma_unwind_calls": _build_squeeze_family_calibration(session, "gamma_unwind_calls")' in body
    assert '"gamma_unwind_puts": _build_squeeze_family_calibration(session, "gamma_unwind_puts")' in body


def test_gamma_unwind_candidate_dict_carries_calibration_fields():
    body = _function_body("check_gamma_unwind_alerts", "\n\ndef check_squeeze_watch_reverts(")
    assert '"calibrated_win_rate": _gamma_win_rate' in body
    assert '"calibrated_win_rate_count": _gamma_win_count' in body


def test_gamma_unwind_calibration_lookup_uses_the_resolved_dominant_side_alert_type():
    """The lookup must key off _gamma_alert_type (derived from dominant_side), not a hardcoded
    literal — a hardcoded "gamma_unwind_calls" here would silently score every puts-dominant
    candidate against the wrong band scheme/bucket set."""
    body = _function_body("check_gamma_unwind_alerts", "\n\ndef check_squeeze_watch_reverts(")
    assert "_gamma_cal_buckets[_gamma_alert_type], _gamma_alert_type, _concentration_pct" in body


def test_send_short_squeeze_email_renders_calibration():
    start = _email_source.index("def send_short_squeeze_email(")
    end = _email_source.index("\n\ndef send_gamma_unwind_email(", start)
    body = _email_source[start:end]
    assert 'cal_win_rate = c.get("calibrated_win_rate")' in body


def test_send_gamma_unwind_email_renders_calibration():
    start = _email_source.index("def send_gamma_unwind_email(")
    end = _email_source.index("\n\ndef send_prebreakout_email(", start)
    body = _email_source[start:end]
    assert 'cal_win_rate = c.get("calibrated_win_rate")' in body


# ── 3. Soft regime flag in all 3 check_*_alerts() + _regime_warning_lines() ─────────────────

def test_short_squeeze_regime_is_fetched_once_per_cycle_not_per_candidate():
    body = _function_body("check_short_squeeze_alerts", "\n\ndef check_prebreakout_alerts(")
    fetch_idx = body.index('_sq_us_regime = (get_last_regime() or {}).get("state", "neutral")')
    with_session_idx = body.index("with SessionLocal() as session:")
    assert fetch_idx < with_session_idx, "regime must be fetched before the DB session/candidate loop, not inside it"


def test_short_squeeze_candidate_dict_picks_hk_or_us_regime_by_symbol_market():
    """A short_squeeze candidate can be either a US or HK symbol in the SAME cycle (unlike
    gamma_unwind/prebreakout, which are US-only) — the regime assigned must depend on
    _is_hk_sym, not always default to one market's regime."""
    body = _function_body("check_short_squeeze_alerts", "\n\ndef check_prebreakout_alerts(")
    assert '"market_regime": _sq_hk_regime if _is_hk_sym else _sq_us_regime' in body


def test_gamma_unwind_candidate_dict_carries_regime():
    body = _function_body("check_gamma_unwind_alerts", "\n\ndef check_squeeze_watch_reverts(")
    assert '"market_regime": _gamma_us_regime' in body


def test_prebreakout_candidate_dict_carries_regime():
    body = _function_body("check_prebreakout_alerts", "\n\ndef _record_prebreakout_alert_outcome(")
    assert '"market_regime": _pb_us_regime' in body


def test_regime_flag_never_gates_or_rejects_any_candidate_in_any_of_the_3_functions():
    """The whole point of this being a SOFT flag: none of the 3 functions may ever `continue`
    or `return` (skip/reject a candidate or abort the scan) based on a regime value — every
    reference to the regime variables must be pure read/display, never a control-flow
    condition."""
    for fn_name, end_marker in (
        ("check_short_squeeze_alerts", "\n\ndef check_prebreakout_alerts("),
        ("check_gamma_unwind_alerts", "\n\ndef check_squeeze_watch_reverts("),
        ("check_prebreakout_alerts", "\n\ndef _record_prebreakout_alert_outcome("),
    ):
        body = _function_body(fn_name, end_marker)
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith(("if ", "elif ")) and "regime" in stripped:
                assert False, f"{fn_name} appears to branch on regime state: {stripped!r}"


def test_regime_warning_lines_returns_empty_strings_for_bull_regime():
    start = _email_source.index("def _regime_warning_lines(")
    end = _email_source.index("\n\n\ndef send_short_squeeze_email(", start)
    body = _email_source[start:end]
    assert 'if not regime or regime == "bull":' in body
    assert 'return "", ""' in body


def test_all_3_emails_splice_in_the_regime_warning():
    for fn_name, end_marker in (
        ("send_short_squeeze_email", "\n\ndef send_gamma_unwind_email("),
        ("send_gamma_unwind_email", "\n\ndef send_prebreakout_email("),
        ("send_prebreakout_email", "\n\ndef send_squeeze_watch_revert_email("),
    ):
        start = _email_source.index(f"def {fn_name}(")
        end = _email_source.index(end_marker, start)
        body = _email_source[start:end]
        assert "_regime_warning_lines(c.get(\"market_regime\"))" in body, f"{fn_name} is missing the regime warning call"
