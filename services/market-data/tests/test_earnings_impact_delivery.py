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
    """impact_sent_at must only advance inside an `if any_sent and all_recipients_notified:`
    gate — a failed send cycle must retry next minute, not get silently marked done (same
    discipline already established for check_macro_reaction_alerts()'s own reaction_sent_at).

    AUD266-ANY-SENT-GLOBAL-FLAG-CROSS-USER-SUPPRESSION (2026-08-06): any_sent alone is no
    longer sufficient — a partial-delivery cycle (some recipients succeeded, at least one
    didn't) must NOT advance impact_sent_at either, or the un-notified recipient would be
    silently skipped forever once the DB query's WHERE impact_sent_at IS NULL stops matching."""
    body = _func_body("check_earnings_impact_alerts")
    any_sent_idx = body.index("if any_sent and all_recipients_notified:")
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


def test_earnings_impact_alerts_scoped_to_each_users_own_subscribed_symbols():
    """BUG-EARNINGS-IMPACT-UNSCOPED: this job used to email EVERY subscribed user for ANY
    reporting symbol, regardless of whether they'd actually subscribed to it — unlike
    check_earnings_reactions() (the plain, non-LLM alert), which has always correctly scoped
    per-symbol. Must use the shared _earnings_alert_recipient_symbols() merge and only send to
    a user if the reporting symbol is in THEIR OWN subscribed set."""
    body = _func_body("check_earnings_impact_alerts")
    assert "user_symbols, users_by_id = _earnings_alert_recipient_symbols(session)" in body
    assert "if sym not in syms:" in body


def test_earnings_impact_alerts_db_query_filters_to_subscribed_symbols_only():
    """The pending-impact query itself must filter to Stock.symbol.in_(all_symbols) — not
    just filter at the Python send-loop level — so an unrelated symbol's impact is never even
    fetched for a cycle where nobody has subscribed to it."""
    body = _func_body("check_earnings_impact_alerts")
    assert "Stock.symbol.in_(all_symbols)" in body


def test_earnings_impact_alerts_no_subscribers_for_any_symbol_returns_early():
    """If nobody qualifies for earnings alerts at all (no PriceAlert AND no
    EarningsAlertSubscription), the function must bail before ever querying EarningsEvent —
    matching check_earnings_reactions()'s own early-exit shape."""
    body = _func_body("check_earnings_impact_alerts")
    merge_idx = body.index("user_symbols, users_by_id = _earnings_alert_recipient_symbols(session)")
    all_symbols_idx = body.index("all_symbols = {sym for syms in user_symbols.values()")
    early_exit_idx = body.index("if not all_symbols:")
    pending_query_idx = body.index("pending = session.execute(")
    assert merge_idx < all_symbols_idx < early_exit_idx < pending_query_idx


# ── _earnings_alert_recipient_symbols() — the shared merge helper ──────────────────

def test_recipient_symbols_helper_merges_both_price_alert_and_earnings_subscription():
    """The core additive property: a symbol from EITHER source must appear in the merged
    user_symbols set — this is what makes the fix additive rather than a replacement."""
    body = _func_body("_earnings_alert_recipient_symbols")
    assert "PriceAlert.triggered.is_(False)" in body
    assert "EarningsAlertSubscription" in body
    assert 'user_symbols.setdefault(a.user_id, set()).add(a.symbol)' in body
    assert 'user_symbols.setdefault(s.user_id, set()).add(s.symbol)' in body


def test_recipient_symbols_helper_skips_users_with_no_email():
    body = _func_body("_earnings_alert_recipient_symbols")
    assert "if not a.user or not a.user.email:" in body
    assert "if not s.user or not s.user.email:" in body


def test_both_earnings_functions_use_the_shared_helper_not_duplicated_logic():
    """Both check_earnings_reactions() and check_earnings_impact_alerts() must call the SAME
    shared helper — a hand-duplicated second copy could silently drift from it."""
    reactions_body = _func_body("check_earnings_reactions")
    impact_body = _func_body("check_earnings_impact_alerts")
    assert "user_symbols, users_by_id = _earnings_alert_recipient_symbols(session)" in reactions_body
    assert "user_symbols, users_by_id = _earnings_alert_recipient_symbols(session)" in impact_body


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
