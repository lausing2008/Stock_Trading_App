"""Tests for BUG-LOCALDEV-ALERTS-UNGATED (T270-DBSYNC-PROD-TO-LOCAL-WEEKLY's Layer 1) —
_is_alerting_enabled() and the gating of every alert/digest job registration + inline alert
call inside start_scheduler()/scheduler.py.

Before this fix, every alert-emitting job (~19 total: morning digests, premarket briefs,
post-open digests, the paper-portfolio digest, and every 1-minute alert checker) registered
and ran completely regardless of Settings.env — a locally-run stack restored from a real prod
DB dump (the exact workflow T270-DBSYNC-PROD-TO-LOCAL-WEEKLY would enable) would email real
users within a minute of booting, since local dev's own .env already has real SMTP
credentials configured with nothing in the code to stop it.

_is_alerting_enabled() is extracted via exec() from the real source (matching
test_correlation_preentry.py's established technique) and tested behaviorally against a real
mock Settings/Redis client — scheduler.py can't be imported directly in this test environment
(its import chain pulls in apscheduler, not installed locally). The job-gating checks below
are source-text regression checks over the real start_scheduler() body, confirming every
known alert-emitting job id sits inside an `if _is_alerting_enabled():` block and every
non-alert (ingestion/pricing/ranking/signal/ML/DB-purge) job does NOT — via a real AST parse
of scheduler.py, not a fragile string-proximity heuristic (a naive "check the last N chars
before this line" approach was tried first and gave false results for jobs registered later
inside an already-open if-block; AST correctly tracks nesting regardless of how many sibling
statements come before it).
"""
import ast
import pathlib
from unittest.mock import MagicMock

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


def _extract_is_alerting_enabled():
    start = _scheduler_source.index("def _is_alerting_enabled(")
    end = _scheduler_source.index("\n\n\ndef _record_job_status", start)
    func_source = _scheduler_source[start:end]
    namespace = {
        "_settings": MagicMock(env="development"),
        "_get_redis": lambda: MagicMock(),
        "_REDIS_ALERTS_FORCE_ENABLED": "stockai:admin:feature:alerts_force_enabled_non_prod",
    }
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_is_alerting_enabled"], namespace


# ── _is_alerting_enabled() behavior ──────────────────────────────────────────────

def test_true_in_production_regardless_of_redis():
    _is_alerting_enabled, ns = _extract_is_alerting_enabled()
    ns["_settings"].env = "production"
    ns["_get_redis"] = lambda: (_ for _ in ()).throw(ConnectionError("redis down"))
    assert _is_alerting_enabled() is True


def test_false_in_development_when_force_flag_unset():
    _is_alerting_enabled, ns = _extract_is_alerting_enabled()
    ns["_settings"].env = "development"
    fake_redis = MagicMock()
    fake_redis.get.return_value = None
    ns["_get_redis"] = lambda: fake_redis
    assert _is_alerting_enabled() is False


def test_false_in_staging_when_force_flag_unset():
    _is_alerting_enabled, ns = _extract_is_alerting_enabled()
    ns["_settings"].env = "staging"
    fake_redis = MagicMock()
    fake_redis.get.return_value = None
    ns["_get_redis"] = lambda: fake_redis
    assert _is_alerting_enabled() is False


def test_true_in_development_when_force_flag_explicitly_set():
    """The one deliberate escape hatch — an admin who genuinely wants to test real alert
    delivery against local data must explicitly turn this on; it's never on by default."""
    _is_alerting_enabled, ns = _extract_is_alerting_enabled()
    ns["_settings"].env = "development"
    fake_redis = MagicMock()
    fake_redis.get.return_value = "1"
    ns["_get_redis"] = lambda: fake_redis
    assert _is_alerting_enabled() is True


def test_false_in_development_when_force_flag_is_any_other_value():
    """Only the exact string "1" enables the override — not "true", not "yes", not "on" —
    matching this repo's own established Redis-flag convention elsewhere in this file."""
    _is_alerting_enabled, ns = _extract_is_alerting_enabled()
    ns["_settings"].env = "development"
    fake_redis = MagicMock()
    fake_redis.get.return_value = "true"
    ns["_get_redis"] = lambda: fake_redis
    assert _is_alerting_enabled() is False


def test_fails_closed_on_a_redis_error_outside_production():
    """A Redis outage must never be the reason alerting silently turns ON outside
    production — the opposite of every other fail-open convention in this codebase, and
    deliberately so, since the failure mode here is real users getting emailed by accident."""
    _is_alerting_enabled, ns = _extract_is_alerting_enabled()
    ns["_settings"].env = "development"
    ns["_get_redis"] = lambda: (_ for _ in ()).throw(ConnectionError("redis down"))
    assert _is_alerting_enabled() is False


def test_redis_lookup_never_happens_in_production():
    """Production must return True unconditionally on the env check alone — never even
    touching Redis, so a Redis outage in production can't accidentally suppress real alerts
    (the opposite failure mode from the non-production case above)."""
    _is_alerting_enabled, ns = _extract_is_alerting_enabled()
    ns["_settings"].env = "production"
    redis_calls = {"count": 0}

    def _tracked_get_redis():
        redis_calls["count"] += 1
        return MagicMock()

    ns["_get_redis"] = _tracked_get_redis
    assert _is_alerting_enabled() is True
    assert redis_calls["count"] == 0


# ── start_scheduler() job-registration gating, via real AST parse ───────────────────

def _job_registration_gating() -> dict[str, bool]:
    """Walks start_scheduler()'s real AST and returns {job_id: is_guarded_by_is_alerting_
    enabled} for every _scheduler.add_job(...) call found — correctly tracks nesting
    regardless of sibling-statement count, unlike a string-proximity heuristic."""
    tree = ast.parse(_scheduler_source)
    result: dict[str, bool] = {}

    def walk(node, guarded):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.If):
                is_gate = "_is_alerting_enabled" in ast.dump(child.test)
                walk(child, guarded or is_gate)
            elif isinstance(child, ast.For):
                walk(child, guarded)
            elif isinstance(child, ast.Try):
                walk(child, guarded)
            elif isinstance(child, ast.Expr) and isinstance(child.value, ast.Call):
                call = child.value
                if "_scheduler" in ast.dump(call.func) and "add_job" in ast.dump(call.func):
                    job_id = None
                    for kw in call.keywords:
                        if kw.arg == "id":
                            if isinstance(kw.value, ast.Constant):
                                job_id = kw.value.value
                            elif isinstance(kw.value, ast.JoinedStr):
                                # f-string id — resolve the literal prefix (e.g.
                                # "post_open_digest_" from f"post_open_digest_{m}_{w}")
                                prefix = "".join(
                                    v.value for v in kw.value.values if isinstance(v, ast.Constant)
                                )
                                job_id = f"(f-string prefix={prefix!r})"
                    result[job_id] = guarded

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "start_scheduler":
            walk(node, guarded=False)
            break
    return result


_ALERT_JOB_IDS = {
    "broker_auth_check",
    "morning_digest_us",
    "morning_digest_hk",
    "premarket_brief_us",
    "premarket_brief_hk",
    "(f-string prefix='post_open_digest__')",
    "paper_portfolio_digest",
    "price_alert_check",
    "volume_anomaly_check",
    "gamma_unwind_alert_check",
    "prebreakout_alert_check",
    "short_squeeze_alert_check",
    "squeeze_watch_revert_check",
    "value_area_breakdown_check",
    "top3_conviction_check",
    "earnings_reaction_check",
    "macro_reaction_alert_check",
    "earnings_impact_alert_check",
    "early_earnings_news_alert_check",
    "theme_forecast_weekly",
    "signal_alert_startup",
}

_NON_ALERT_JOB_IDS = {
    "us_open_burst", "us_intra", "us_close_burst", "us_post_close", "options_flow_eod",
    "hk_open_burst", "hk_intra", "hk_close_burst", "hk_post_close", "weekly_full_refresh",
    "us_premarket_5m_early", "us_premarket_5m_9am", "us_5m_intraday", "hk_5m_intraday",
    "(f-string prefix='broker_token_renewal_')",
    "data_quality_checks", "value_area_levels_daily", "squeeze_alert_outcome_eval_daily",
    "prebreakout_alert_outcome_eval_daily",
    "live_price_cache_refresh", "avg_volume_cache_refresh", "avg_volume_startup_check",
    "signal_watchdog_daily", "db_purge_weekly", "meta_model_monthly_retrain",
    "backfill_realized_ev_monthly", "position_scaling_gate_weekly_retrain",
    "position_scaling_shadow_daily_resolve", "position_scaling_gate_weekly_drift_check",
    "edgar_8k_ingest_daily", "hk_connect_flows_daily", "sector_rotation_weekly",
    "fundamentals_snapshot_weekly", "watchlist_auto_rotation_weekly",
}


def test_every_known_alert_job_is_guarded():
    gating = _job_registration_gating()
    unguarded = {jid for jid in _ALERT_JOB_IDS if gating.get(jid) is not True}
    assert not unguarded, f"alert jobs NOT guarded by _is_alerting_enabled(): {unguarded}"


def test_every_known_non_alert_job_is_unguarded():
    """Non-alert (ingestion/pricing/ranking/signal/ML/DB-purge) jobs must NOT be gated — they
    do harmless data work useful for local dev testing, and gating them would silently break
    that, exactly the over-broad-fix risk this repo's own testing discipline warns against."""
    gating = _job_registration_gating()
    wrongly_guarded = {jid for jid in _NON_ALERT_JOB_IDS if gating.get(jid) is True}
    assert not wrongly_guarded, f"non-alert jobs incorrectly guarded: {wrongly_guarded}"


def test_no_job_id_is_missing_from_either_known_list():
    """A future job added to start_scheduler() with neither classification would silently
    fall through this test suite undetected — this guards against that by asserting every
    job id found in the real source is accounted for in one of the two lists above."""
    gating = _job_registration_gating()
    known = _ALERT_JOB_IDS | _NON_ALERT_JOB_IDS
    unclassified = set(gating.keys()) - known
    assert not unclassified, (
        f"job id(s) found in start_scheduler() not classified as alert or non-alert in this "
        f"test file — add them to _ALERT_JOB_IDS or _NON_ALERT_JOB_IDS: {unclassified}"
    )


# ── Inline alert calls (not separate job registrations) ─────────────────────────────

def _func_body(name: str) -> str:
    start = _scheduler_source.index(f"def {name}(")
    end = _scheduler_source.index("\n\ndef ", start)
    return _scheduler_source[start:end]


def test_sector_rotation_alert_inline_call_is_gated():
    body = _func_body("_compute_sector_rotation")
    idx = body.index("check_sector_rotation_alerts(rotation)")
    preceding = body[max(0, idx - 300):idx]
    assert "_is_alerting_enabled()" in preceding


def test_earnings_beat_screener_alert_inline_call_is_gated():
    idx = _scheduler_source.index("check_earnings_beat_screener_alerts()")
    preceding = _scheduler_source[max(0, idx - 300):idx]
    assert "_is_alerting_enabled()" in preceding


def test_refresh_market_stage3_alert_calls_are_gated():
    """Found via code review (2026-08-13): _refresh_market()'s Stage 3 calls
    check_signal_alerts()/check_technical_alerts() directly — the AST-based job-registration
    test above only inspects direct _scheduler.add_job() calls in start_scheduler(), so it
    cannot see that a non-alert-classified job (us_open_burst, us_intra, etc.) internally calls
    a function that itself performs unguarded alert-sending. _refresh_market() is invoked by 8
    of the highest-frequency cron jobs (open/intra/close-burst/post-close, US+HK), so this call
    site being missed by the original fix meant a locally-restored prod DB dump would still
    email real users on the very next scheduled refresh — the exact incident the whole fix was
    built to prevent."""
    body = _func_body("_refresh_market")
    # Anchor on the real call sites specifically, not the function's own docstring/comments,
    # which mention "check_signal_alerts()" in prose before the real call is ever reached —
    # the exact docstring-vs-real-code trap this repo's test-writing history has hit before.
    check_signal_idx = body.index("        check_signal_alerts()")
    check_tech_idx = body.index("        check_technical_alerts()")
    gate_idx = body.rindex("if _is_alerting_enabled():", 0, check_signal_idx)
    assert gate_idx < check_signal_idx < check_tech_idx
