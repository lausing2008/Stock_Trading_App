"""Tests for AUD266-TWO-GATES-CONTRADICTORY-BARS — the conviction_fired_ratio DQ check.

Production showed a real 2,300:1 skip ratio (4,824 conviction-gated candidates in 48h, only
27 actually fired) with zero alarm anywhere, because neither the conviction gate's own
pass count nor the actual-alert-sent count was ever persisted anywhere outside a structlog
line. This adds two rolling 48h Redis counters (_incr_rolling_counter(), incremented at the
real check_signal_alerts() call sites) and a new `source: "ratio"` entry in _DQ_CHECKS that
flags when the ratio collapses — reusing the SAME declarative-check + dq_check:{name} Redis
key + failing-list + email-on-failure machinery every other _DQ_CHECKS entry already uses,
via its own dedicated branch in run_data_quality_checks() (no age/staleness concept applies
to a ratio, so it can't reuse the age-based branch as-is).

scheduler.py can't be imported directly in this test environment (its import chain pulls in
apscheduler, not installed locally) — the ratio-branch logic and _incr_rolling_counter() are
both extracted via exec() and run against a real, synthetic Redis-shaped mock, matching
test_dq_check_job_status_source.py's/test_correlation_preentry.py's established technique.
"""
import json
import pathlib
from datetime import datetime, timezone
from unittest.mock import MagicMock

_SCHEDULER_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_SOURCE = _SCHEDULER_PATH.read_text()

_EMAIL_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "email_service.py"
_EMAIL_SOURCE = _EMAIL_PATH.read_text()


# ── _incr_rolling_counter() ──────────────────────────────────────────────────────────

def _extract_incr_rolling_counter(get_redis_fn):
    start = _SOURCE.index("def _incr_rolling_counter(")
    end = _SOURCE.index("\n\n\ndef _record_position_scaling_promotion_status", start)
    func_source = _SOURCE[start:end]
    namespace = {"_get_redis": get_redis_fn, "_ROLLING_COUNTER_TTL_S": 48 * 3600}
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_incr_rolling_counter"]


def test_incr_sets_ttl_only_once():
    fake_redis = MagicMock()
    fake_redis.ttl.return_value = -1  # no expiry set yet
    _incr_rolling_counter = _extract_incr_rolling_counter(lambda: fake_redis)
    _incr_rolling_counter("some:key")
    fake_redis.incr.assert_called_once_with("some:key")
    fake_redis.expire.assert_called_once()


def test_incr_does_not_reset_ttl_if_already_set():
    fake_redis = MagicMock()
    fake_redis.ttl.return_value = 12345  # a real, already-set expiry
    _incr_rolling_counter = _extract_incr_rolling_counter(lambda: fake_redis)
    _incr_rolling_counter("some:key")
    fake_redis.expire.assert_not_called()


def test_incr_fails_silently_on_a_redis_error():
    """A Redis hiccup while incrementing this diagnostic counter must never raise into the
    real alert-sending code path it's instrumenting."""
    _incr_rolling_counter = _extract_incr_rolling_counter(
        lambda: (_ for _ in ()).throw(ConnectionError("down"))
    )
    _incr_rolling_counter("some:key")  # must not raise


# ── run_data_quality_checks()'s ratio branch ─────────────────────────────────────────

def _extract_ratio_branch():
    """Pulls the real `if check.get("source") == "ratio": ... continue` branch out of
    run_data_quality_checks(), isolated from the surrounding SessionLocal()/SQL-query
    machinery this test doesn't need — matches test_dq_check_job_status_source.py's
    established technique exactly. Wrapped in a real `for` loop (not just a function body) so
    the real source's own `continue` statement stays syntactically valid — a bare function
    body can't contain a loop-only statement like `continue`."""
    start = _SOURCE.index('if check.get("source") == "ratio":')
    end = _SOURCE.index("continue\n\n                    # AUD266-ALERT-JOBS-LACK-STATUS-CONSEQUENCE-DQ", start) + len("continue")
    body = _SOURCE[start:end]
    dedented = [ln[20:] if ln.startswith(" " * 20) else ln for ln in body.splitlines()]
    func_source = (
        "def _resolve(check, redis_client, failing):\n"
        "    for _once in [None]:\n"
        + "\n".join("        " + ln for ln in dedented)
        + "\n"
    )
    namespace = {"json": json, "datetime": datetime, "timezone": timezone}
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_resolve"]


_CHECK = {
    "name": "conviction_fired_ratio", "description": "test check",
    "source": "ratio",
    "numerator_key": "num:key", "denominator_key": "den:key",
    "min_denominator": 20, "min_ratio": 0.001,
}


def _fake_redis(num, den):
    r = MagicMock()
    r.get.side_effect = lambda k: {"num:key": num, "den:key": den}.get(k)
    return r


def test_below_min_denominator_reports_ok_not_a_false_positive():
    """Too little data yet (e.g. early in a fresh 48h window) must report ok=True, not a
    false "collapsed ratio" alarm on a sample too small to mean anything."""
    resolve = _extract_ratio_branch()
    failing = []
    redis_client = _fake_redis(num="0", den="5")  # below min_denominator=20
    resolve(_CHECK, redis_client, failing)
    assert failing == []


def test_a_real_healthy_ratio_reports_ok():
    resolve = _extract_ratio_branch()
    failing = []
    redis_client = _fake_redis(num="27", den="4824")  # the real production incident's own ratio, ~0.56%
    resolve(_CHECK, redis_client, failing)
    assert failing == []


def test_a_collapsed_ratio_below_the_floor_is_flagged():
    resolve = _extract_ratio_branch()
    failing = []
    redis_client = _fake_redis(num="0", den="500")  # zero fires despite 500 conviction-met — genuinely collapsed
    resolve(_CHECK, redis_client, failing)
    assert len(failing) == 1
    assert failing[0]["name"] == "conviction_fired_ratio"
    assert "0/500" in failing[0]["detail"]


def test_missing_counters_are_treated_as_zero_not_a_crash():
    """A key that's expired or never been written (e.g. right after a Redis restart, or
    before either counter has ever incremented) must resolve to 0, not raise on int(None)."""
    resolve = _extract_ratio_branch()
    failing = []
    redis_client = _fake_redis(num=None, den=None)
    resolve(_CHECK, redis_client, failing)
    assert failing == []  # den=0 < min_denominator=20, so this is the "too little data" case


def test_ratio_check_always_continues_never_falls_through_to_sql_branch():
    """A "ratio" check has no "query" key at all — if the ratio branch didn't `continue`,
    the loop would fall through into `session.execute(text(check["query"]))` and raise a
    real KeyError. Regression guard on that exact ordering, matching
    test_dq_check_job_status_source.py's own equivalent guard for the job_status branch."""
    ratio_idx = _SOURCE.index('if check.get("source") == "ratio":')
    job_status_idx = _SOURCE.index('if check.get("source") == "job_status":')
    sql_query_idx = _SOURCE.index('result = session.execute(text(check["query"]))')
    assert ratio_idx < job_status_idx < sql_query_idx
    # The ratio branch's own body (between its own `if` and the job_status check's `if`)
    # must contain a `continue` as its last real statement — checked by finding the LAST
    # line of actual code (not blank/comment) in that span, since the span legitimately
    # also includes the job_status branch's own leading comment block.
    between = _SOURCE[ratio_idx:job_status_idx]
    code_lines = [ln for ln in between.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    assert code_lines[-1].strip() == "continue"


# ── _DQ_CHECKS entry + counter-key wiring ────────────────────────────────────────────

def test_conviction_fired_ratio_entry_exists_with_no_query_or_job_name_key():
    checks_start = _SOURCE.index("_DQ_CHECKS: list[dict] = [")
    checks_end = _SOURCE.index("\n]\n\n\ndef run_data_quality_checks", checks_start)
    checks_block = _SOURCE[checks_start:checks_end]
    entry_start = checks_block.index('"name": "conviction_fired_ratio"')
    dict_start = checks_block.rindex("{", 0, entry_start)
    dict_end = checks_block.index("}", entry_start) + 1
    entry_dict_text = checks_block[dict_start:dict_end]
    assert '"query"' not in entry_dict_text
    assert '"job_name"' not in entry_dict_text
    assert '"source": "ratio"' in entry_dict_text


def test_counter_increment_sites_are_wired_into_check_signal_alerts():
    body_start = _SOURCE.index("def check_signal_alerts(")
    body_end = _SOURCE.index("\n\n\ndef ", body_start)
    body = _SOURCE[body_start:body_end]
    assert body.count("_incr_rolling_counter(_CONVICTION_MET_COUNTER_KEY)") == 1
    assert body.count("_incr_rolling_counter(_ALERT_FIRED_COUNTER_KEY)") == 1


def test_fired_counter_only_increments_for_buy_not_exit_transitions():
    """Exit/bearish transitions bypass the conviction gate entirely — counting them against
    the fired counter would understate the real conviction-met -> fired collapse this check
    exists to catch."""
    idx = _SOURCE.index("_incr_rolling_counter(_ALERT_FIRED_COUNTER_KEY)")
    preceding = _SOURCE[max(0, idx - 200):idx]
    assert 'if current == "BUY":' in preceding


# ── send_data_quality_alert_email()'s None-safe rendering ───────────────────────────

def test_email_builder_handles_a_ratio_checks_none_max_age_hours_without_crashing():
    """A ratio-sourced failing check has max_age_hours=None and a `detail` string instead —
    the email builder must render that gracefully, not crash on `None:.0f` formatting (the
    exact class of bug this fix's own email_service.py change closes)."""
    body_start = _EMAIL_SOURCE.index("def send_data_quality_alert_email(")
    body_end = _EMAIL_SOURCE.index("\n\n\ndef ", body_start)
    func_source = _EMAIL_SOURCE[body_start:body_end]
    sent = {}

    def _fake_send_email(to, subject, body_html, body_text):
        sent["to"] = to
        sent["subject"] = subject
        sent["body_html"] = body_html
        sent["body_text"] = body_text
        return True

    namespace = {"send_email": _fake_send_email}
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source
    result = namespace["send_data_quality_alert_email"](
        "test@example.com",
        [{
            "name": "conviction_fired_ratio", "description": "test",
            "age_hours": None, "max_age_hours": None,
            "detail": "0/500 (0.0000%) fired vs. conviction-met, below the 0.10% floor",
        }],
    )
    assert result is True
    assert "0/500" in sent["body_html"]
    assert "0/500" in sent["body_text"]


# ── AUD-CONVRATIO-WEEKEND: market-closed skip for ratio checks ───────────────────────
# The ratio branch `continue`s before reaching the market-closed skip further down in
# run_data_quality_checks(), so a market-tagged ratio check never got that guard — the exact
# false-positive-every-weekend failure mode T242-DQ1 already fixed for the age-based checks.
#
# conviction_fired_ratio collapses to 0 every weekend BY CONSTRUCTION:
# _ALERT_FIRED_COUNTER_KEY only increments on a real BUY email send (market hours only) and
# carries a 48h TTL, so by Saturday afternoon the last weekday send has aged out and the key
# expires to 0 — while _CONVICTION_MET_COUNTER_KEY keeps incrementing all weekend because the
# conviction gate has no market-hours check.
#
# Verified live on Saturday 2026-09-05: conviction_met=3347, alert_fired absent, every DE
# verdict "Market closed: weekend". The alert stream itself was healthy (signal_alerts showed
# 15 BUY sends the prior trading day), confirming this as a pure false positive.

def _dq_source() -> str:
    start = _SOURCE.index("def run_data_quality_checks(")
    end = _SOURCE.index("\n\n\ndef ", start)
    return _SOURCE[start:end]


def _ratio_branch() -> str:
    src = _dq_source()
    start = src.index('if check.get("source") == "ratio":')
    end = src.index('if check.get("source") == "gauge":')
    return src[start:end]


def test_conviction_fired_ratio_is_tagged_with_a_market():
    """Without a market tag the new skip is inert for this check."""
    start = _SOURCE.index('"name": "conviction_fired_ratio"')
    entry = _SOURCE[start:_SOURCE.index("},", start)]
    assert '"market": "US"' in entry


def test_ratio_branch_has_a_market_closed_skip():
    branch = _ratio_branch()
    assert "_ratio_closed" in branch
    assert '"skipped_reason": "market_closed"' in branch


def test_ratio_market_closed_skip_runs_before_reading_the_counters():
    """If the counters were read (and judged) first, the skip would be pointless — the
    false failure is produced by comparing them at all while the market is shut."""
    branch = _ratio_branch()
    assert branch.index("if _ratio_closed:") < branch.index('redis_client.get(check["numerator_key"])')


def test_ratio_skip_reports_ok_true_so_it_never_emails():
    """Matches the age-based branches' own convention: a closed market reports ok, it is not
    a real failure and must not enter the failing-list/alert-email path."""
    branch = _ratio_branch()
    skip_block = branch[branch.index("if _ratio_closed:"):branch.index("num = redis_client.get")]
    assert '"ok": True' in skip_block


def test_ratio_skip_payload_avoids_max_age_hours_keyerror():
    """The age-based skip payloads read check["max_age_hours"], which a ratio check does NOT
    define — copying that shape verbatim would raise KeyError and get swallowed by the
    per-check except, silently turning the skip into a 'query_failed' instead."""
    branch = _ratio_branch()
    skip_block = branch[branch.index("if _ratio_closed:"):branch.index("num = redis_client.get")]
    assert "max_age_hours" not in skip_block
    assert '"min_ratio": check["min_ratio"]' in skip_block


def test_ratio_skip_honors_both_us_and_hk_tags():
    branch = _ratio_branch()
    assert "_is_us_trading_day()" in branch
    assert "_is_hk_holiday()" in branch


def test_untagged_ratio_check_is_never_skipped():
    """A ratio check with no market tag must still be evaluated every run — the skip is
    opt-in via the tag, not a blanket weekend bypass for all ratio checks."""
    branch = _ratio_branch()
    closed_expr = branch[branch.index("_ratio_closed = ("):branch.index("if _ratio_closed:")]
    # Both arms require an explicit market value; None matches neither.
    assert '_ratio_market == "US"' in closed_expr
    assert '_ratio_market == "HK"' in closed_expr
