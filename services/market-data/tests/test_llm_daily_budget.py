"""A spike alert fires on CHANGE; a steady high baseline never changes.

MEASURED 2026-10-05: roughly 215,000-256,000 classification tokens every weekday against
7,000-9,000 at weekends. `check_llm_usage_spike` compares the latest hour against a rolling
baseline, so a cost that has been high for weeks never departs from that baseline and never
fires. The two alerts answer different questions and neither replaces the other: one asks "did
something change", the other "is the level acceptable".
"""
import ast
import pathlib

_SCHED = (pathlib.Path(__file__).resolve().parents[1] / "src" / "services"
          / "scheduler.py").read_text()
_ADMIN = (pathlib.Path(__file__).resolve().parents[1] / "src" / "api"
          / "admin.py").read_text()
_TREE = ast.parse(_SCHED)


def _fn(name):
    return next(n for n in ast.walk(_TREE)
                if isinstance(n, ast.FunctionDef) and n.name == name)


def test_a_daily_ceiling_check_exists_separately_from_the_spike_check():
    assert _fn("check_llm_daily_budget")
    assert _fn("check_llm_usage_spike"), "the change alert is not replaced"


def test_the_ceiling_is_fixed_not_derived_from_a_baseline():
    """Deriving it from recent usage would reproduce the blind spot it exists to cover."""
    body = ast.unparse(_fn("check_llm_daily_budget"))
    assert "LLM_DAILY_TOKEN_BUDGET" in body, "overridable without a deploy"
    assert "400000" in body
    assert "median" not in body.lower() and "baseline" not in body.lower().split("\n")[0]


def test_it_measures_the_whole_day_not_an_hour():
    body = ast.unparse(_fn("check_llm_daily_budget"))
    assert "date_trunc('day', now())" in body
    assert "llm_call_log" in body


def test_it_alerts_at_most_once_per_calendar_day():
    """A budget stays crossed for the rest of the day; re-alerting every 30 minutes would
    teach the reader to ignore it."""
    body = ast.unparse(_fn("check_llm_daily_budget"))
    assert "llm:daily_budget_alerted:" in body
    assert "nx=True" in body, "claimed atomically so two ticks cannot both send"


def test_a_redis_outage_alerts_rather_than_going_silent_about_a_cost_breach():
    body = ast.unparse(_fn("check_llm_daily_budget"))
    i_try = body.index("_get_redis()")
    seg = body[i_try:i_try + 400]
    assert "except Exception:" in seg
    assert "return" not in seg.split("except Exception:")[1][:80], \
        "an unavailable Redis must not suppress the alert"


def test_it_names_the_largest_consumers():
    body = ast.unparse(_fn("check_llm_daily_budget"))
    assert "GROUP BY service, call_site" in body
    assert "LIMIT 5" in body


def test_it_is_scheduled_and_registered_for_liveness():
    assert "id='llm_daily_budget_check'" in ast.unparse(_TREE) or \
           'id="llm_daily_budget_check"' in _SCHED
    assert '"name": "check_llm_daily_budget"' in _SCHED, \
        "an alert nobody monitors can die silently"


def test_the_endpoint_distinguishes_the_alert_threshold_from_enforced_ceilings():
    """Calling the alert a "budget" was the thing that needed correcting: it emails, it does
    not refuse. The endpoint now reports both and labels which is which."""
    assert "_daily_token_budget" in _ADMIN
    assert "LLM_DAILY_TOKEN_BUDGET" in _ADMIN
    assert '"daily_alert_threshold"' in _ADMIN
    assert '"over_threshold"' in _ADMIN
    assert '"enforced_budgets"' in _ADMIN
    assert "It does NOT refuse calls" in _ADMIN
    # Both previously-unstated properties are now explicit.
    assert "ALL application LLM calls" in _ADMIN
    assert "database server's calendar day" in _ADMIN


def test_the_endpoint_reports_relevance_and_repeats():
    """Volume alone cannot say how much was spent on tracked stocks, nor whether an article
    was classified twice — the 2026-10-05 review could not rule repeats in or out at all."""
    for key in ('"unique_articles"', '"repeat_classifications"', '"tracked"',
                '"market_context"', '"out_of_scope"'):
        assert key in _ADMIN, key
    assert "calls_with_relevance_data" in _ADMIN, \
        "pre-instrumentation calls are excluded, not assumed"


# ---------------------------------------------------------------------------------------
# AUD-LLMUSAGE-JSONCAST — the usage panel spun on "Loading…" forever.
#
# `llm_call_log.context` is a `json` column. The key-exists operator `?` and
# `jsonb_array_length` exist only for `jsonb`, so the relevance query raised
# UndefinedFunction, /admin/llm-usage returned 500, and the panel — which renders "Loading…"
# whenever its data is absent — was indistinguishable from a slow request.
#
# Two defects, fixed separately: the query needed the cast, and an ADDITION to the panel
# should never be able to blank the panel.
# ---------------------------------------------------------------------------------------

def test_every_jsonb_operator_is_applied_to_a_cast_column():
    """`context` is json, not jsonb."""
    import re
    seg = _ADMIN[_ADMIN.index("def llm_usage("):]
    seg = seg[:seg.index("@router.get(\"/uw-usage\")")]
    for m in re.finditer(r"(\w+)\s*\?\s*'", seg):
        assert m.group(1) == "jsonb", f"key-exists applied to {m.group(1)!r}, not a jsonb cast"
    for m in re.finditer(r"jsonb_array_elements_text\(([^)]*)\)", seg):
        assert "::jsonb" in m.group(1), f"jsonb function on an uncast column: {m.group(1)}"
    assert "context::jsonb" in seg


def test_the_relevance_block_cannot_blank_the_whole_panel():
    """Calls, tokens and errors are the panel's reason to exist; relevance is an addition."""
    seg = _ADMIN[_ADMIN.index("def llm_usage("):]
    i_try = seg.index("try:")
    i_rel = seg.index("relevance_rows = session.execute")
    i_exc = seg.index("llm_usage.relevance_failed")
    assert i_try < i_rel < i_exc, "the relevance query must be inside a guarded block"
    assert '"error": str(_rel_exc)' in seg, "the failure is reported, not swallowed silently"


_HEALTH = (pathlib.Path(__file__).resolve().parents[3] / "frontend" / "src" / "pages"
           / "admin-health.tsx").read_text()


def test_the_dashboard_shows_a_failed_request_as_an_error_not_as_loading():
    """A 500 rendered as "Loading…" forever, so a broken panel looked like a busy one."""
    assert "error: llmUsageErr" in _HEALTH, "SWR's error state was being discarded"
    assert "Could not load Claude API usage" in _HEALTH
    i_err = _HEALTH.index("{llmUsageError ?")
    i_loading = _HEALTH.index("Loading…", i_err)
    assert i_err < i_loading, "the error branch must be checked before the loading branch"
