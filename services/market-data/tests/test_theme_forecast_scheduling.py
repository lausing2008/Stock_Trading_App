"""Tests for T270-SECTOR-THEME-FORECAST-EMAIL's scheduler wiring — send_weekly_theme_forecast()
and its cron registration in scheduler.py, plus the send_theme_forecast_email() composition in
email_service.py.

scheduler.py can't be imported directly in this test environment (its import chain pulls in
apscheduler), so this is covered via source-text regression checks, matching
test_earnings_impact_delivery.py's/test_theme_signals.py's established pattern for this exact
constraint. Real behavioral coverage for the underlying compute logic lives in
test_theme_signals.py (compute_theme_signal()/generate_theme_summary()) — this file only
checks that scheduler.py wires those functions together correctly and registers the cron job.
"""
import pathlib

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()

_email_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "email_service.py"
_email_source = _email_path.read_text()


def _func_body(source: str, name: str) -> str:
    start = source.index(f"def {name}(")
    end = source.index("\n\ndef ", start)
    return source[start:end]


# ── send_weekly_theme_forecast() ─────────────────────────────────────────────────────

def test_function_exists():
    assert "def send_weekly_theme_forecast(" in _scheduler_source


def test_gated_behind_feature_flag_default_off():
    body = _func_body(_scheduler_source, "send_weekly_theme_forecast")
    assert "_REDIS_THEME_FORECAST_ENABLED" in body
    assert 'get(_REDIS_THEME_FORECAST_ENABLED) != "1"' in body


def test_flag_check_happens_before_any_db_query():
    """The feature-flag check must be the FIRST real work this function does — a disabled
    flag must cost nothing (no DB session opened, no query issued), matching every other
    opt-in Claude-calling feature's own established discipline."""
    body = _func_body(_scheduler_source, "send_weekly_theme_forecast")
    flag_idx = body.index('get(_REDIS_THEME_FORECAST_ENABLED)')
    db_idx = body.index("with SessionLocal()")
    assert flag_idx < db_idx


def test_recipients_are_all_users_with_an_email_not_pricealert_subscribers():
    """This is a market-wide theme digest, not tied to a symbol subscription — must use
    send_morning_digest()'s own all-User audience convention, not the T249 family's
    PriceAlert-subscriber scoping. Scoped to the executable body PAST the function's own
    docstring (which legitimately mentions PriceAlert in prose, comparing this design choice
    against the T249 family) — a bare substring check over the whole function including its
    docstring would false-fail against correct code, exactly the class of trap this repo's own
    test-writing history has hit before (test_int4_research_trigger_gated.py's docstring-vs-
    real-code distinction)."""
    body = _func_body(_scheduler_source, "send_weekly_theme_forecast")
    code_only = body[body.index('"""', body.index('"""') + 3) + 3:]
    assert "select(User).where(User.email.isnot(None)" in code_only
    assert "PriceAlert" not in code_only


def test_persists_a_themesignalsnapshot_row_per_theme_via_upsert():
    body = _func_body(_scheduler_source, "send_weekly_theme_forecast")
    assert "_pg_insert(ThemeSignalSnapshot)" in body
    assert "on_conflict_do_update(" in body
    assert 'index_elements=["theme", "as_of"]' in body


def test_none_avg_return_sorts_last_not_treated_as_worst_case_zero():
    """A theme with an unmeasurable avg_return_5d_pct (e.g. every symbol lacked enough price
    history) must sort to the BOTTOM of the email, never silently read as the worst real
    return (which float('-inf') as the sort key, not 0 or a bare None comparison, guarantees)."""
    body = _func_body(_scheduler_source, "send_weekly_theme_forecast")
    assert 'float("-inf")' in body


def test_compute_failure_for_one_theme_does_not_abort_the_whole_job():
    """Each theme's compute_theme_signal() call is wrapped in its own try/except — one
    theme's DB error must not prevent every other theme's data from being computed/sent."""
    body = _func_body(_scheduler_source, "send_weekly_theme_forecast")
    assert "theme_forecast.compute_failed" in body


def test_llm_summary_failure_for_one_theme_does_not_abort_the_whole_job():
    body = _func_body(_scheduler_source, "send_weekly_theme_forecast")
    assert "theme_forecast.summary_failed" in body


def test_per_recipient_send_is_isolated_matching_aud256_precedent():
    """Matches send_premarket_brief()'s/send_morning_digest()'s own AUD256-established
    per-recipient try/except discipline — one recipient's send failure must not abort the
    whole loop or crash the job."""
    body = _func_body(_scheduler_source, "send_weekly_theme_forecast")
    assert "theme_forecast.recipient_send_error" in body


def test_records_job_status_on_both_success_and_failure_paths():
    body = _func_body(_scheduler_source, "send_weekly_theme_forecast")
    assert body.count('_record_job_status("theme_forecast_weekly"') >= 2
    assert '_record_job_status("theme_forecast_weekly", "error"' in body


# ── Cron registration ─────────────────────────────────────────────────────────────────

def test_registered_as_a_weekly_sunday_cron_job():
    assert 'id="theme_forecast_weekly"' in _scheduler_source
    # Sunday 17:30 ET — after sector_rotation (16:00), fundamentals_snapshot (16:30), and
    # watchlist_auto_rotation_weekly (17:00), matching the file's own dependency-free-but-
    # sequenced convention for weekly jobs that read that week's freshest Ranking/Signal data.
    idx = _scheduler_source.index('id="theme_forecast_weekly"')
    preceding = _scheduler_source[max(0, idx - 400):idx]
    assert 'day_of_week="sun"' in preceding
    assert "hour=17, minute=30" in preceding


def test_registration_passes_send_weekly_theme_forecast_not_a_typo():
    idx = _scheduler_source.index('id="theme_forecast_weekly"')
    preceding = _scheduler_source[max(0, idx - 200):idx]
    assert "send_weekly_theme_forecast," in preceding


# ── send_theme_forecast_email() composition ────────────────────────────────────────────

def test_email_builder_exists():
    assert "def send_theme_forecast_email(" in _email_source


def test_email_states_this_is_not_a_prediction():
    body = _func_body(_email_source, "send_theme_forecast_email")
    assert "NOT a prediction" in body


def test_email_handles_missing_summary_without_crashing():
    """A theme with no LLM summary (fail-open path) must render a real fallback line, not
    raise on a None format/concatenation."""
    body = _func_body(_email_source, "send_theme_forecast_email")
    assert "No AI summary available" in body


def test_email_handles_missing_avg_return_without_crashing():
    body = _func_body(_email_source, "send_theme_forecast_email")
    assert 'if ret is not None else "—"' in body
