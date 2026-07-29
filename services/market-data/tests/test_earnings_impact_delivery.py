"""Tests for T249-EARNINGS-LLM-IMPACT's delivery half — check_earnings_impact_alerts() in
scheduler.py — plus the feature-flag gate added to check_macro_reaction_alerts() at the same
time (put both LLM-generating alert features behind a real admin toggle, per explicit user
request: "put all those to feature flag as well so that I can have control").

event-intelligence's earnings.py generates the LLM impact read (see its own test file,
test_earnings_impact.py); this job polls earnings_events for generated-but-unsent rows and
emails them — the earnings-side mirror of check_macro_reaction_alerts(), same detect/deliver
split already established for check_release_day_fast_poll()/check_macro_reaction_alerts().

scheduler.py can't be imported directly in this test environment (its import chain pulls in
apscheduler and other unstubbed modules), so this is covered via source-text regression
checks, matching test_volume_anomaly_alert.py's/test_min_ta_score_config_wiring.py's
established pattern for this exact constraint.
"""
import pathlib

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


def _func_body(name: str) -> str:
    start = _scheduler_source.index(f"def {name}(")
    end = _scheduler_source.index("\n\ndef ", start)
    return _scheduler_source[start:end]


# ── check_earnings_impact_alerts() — new function ───────────────────────────────────

def test_earnings_impact_alerts_function_exists():
    assert "def check_earnings_impact_alerts(" in _scheduler_source


def test_earnings_impact_alerts_gated_behind_feature_flag_default_off():
    """Default OFF — a brand-new Claude-calling feature, matching auto_research_enabled's
    own default-off convention (CLAUDE-API-COST-AUDIT) rather than an always-on rollout."""
    body = _func_body("check_earnings_impact_alerts")
    assert '_REDIS_EARNINGS_LLM_ENABLED' in body
    assert 'get(_REDIS_EARNINGS_LLM_ENABLED) != "1"' in body


def test_earnings_impact_alerts_flag_check_happens_before_the_lock_or_db_query():
    """The flag guard must be the FIRST thing the function does — before the Redis lock
    acquisition or the DB query — so a disabled flag costs nothing."""
    body = _func_body("check_earnings_impact_alerts")
    flag_idx = body.index("_REDIS_EARNINGS_LLM_ENABLED")
    lock_idx = body.index("_EARNINGS_IMPACT_LOCK_KEY")
    session_idx = body.index("with SessionLocal() as session:")
    assert flag_idx < lock_idx < session_idx


def test_earnings_impact_alerts_polls_for_generated_but_unsent_rows():
    """Must query impact_text IS NOT NULL AND impact_sent_at IS NULL — the exact
    generated-but-unsent condition, mirroring check_macro_reaction_alerts()'s own
    reaction_text/reaction_sent_at query shape."""
    body = _func_body("check_earnings_impact_alerts")
    assert "EarningsEvent.impact_text.isnot(None)" in body
    assert "EarningsEvent.impact_sent_at.is_(None)" in body


def test_earnings_impact_alerts_only_marks_sent_after_a_real_successful_send():
    """impact_sent_at must only advance inside an `if any_sent:` gate — a failed send cycle
    must retry next minute, not get silently marked done (same discipline already established
    for check_macro_reaction_alerts()'s own reaction_sent_at)."""
    body = _func_body("check_earnings_impact_alerts")
    any_sent_idx = body.index("if any_sent:")
    tail = body[any_sent_idx:any_sent_idx + 150]
    assert "ev.impact_sent_at = datetime.now(timezone.utc)" in tail


def test_earnings_impact_alerts_uses_the_same_lock_pattern_as_siblings():
    body = _func_body("check_earnings_impact_alerts")
    assert 'nx=True' in body
    assert '_EARNINGS_IMPACT_LOCK_TTL' in body


def test_earnings_impact_alert_job_is_registered_every_minute():
    assert 'id="earnings_impact_alert_check"' in _scheduler_source
    start = _scheduler_source.index('id="earnings_impact_alert_check"')
    block = _scheduler_source[max(0, start - 200):start]
    assert "check_earnings_impact_alerts" in block
    assert "minutes=1" in block


# ── check_macro_reaction_alerts() — feature-flag gate added retroactively ───────────

def test_macro_reaction_alerts_gated_behind_feature_flag_default_on():
    """Unlike the two brand-new features above, macro reaction has been live since T249-P2 —
    default ON (unset/None must NOT disable it), only an explicit "0" turns it off. This
    matches this function's own already-live production behavior before the flag existed."""
    body = _func_body("check_macro_reaction_alerts")
    assert '_REDIS_MACRO_LLM_ENABLED' in body
    assert 'get(_REDIS_MACRO_LLM_ENABLED) == "0"' in body


def test_macro_reaction_alerts_flag_check_happens_before_the_lock():
    body = _func_body("check_macro_reaction_alerts")
    flag_idx = body.index("_REDIS_MACRO_LLM_ENABLED")
    lock_idx = body.index("_MACRO_REACTION_LOCK_KEY")
    assert flag_idx < lock_idx
