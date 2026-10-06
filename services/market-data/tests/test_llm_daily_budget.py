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


def test_the_endpoint_reports_the_days_usage_against_the_same_budget():
    assert "_daily_token_budget" in _ADMIN
    assert "LLM_DAILY_TOKEN_BUDGET" in _ADMIN
    assert '"over_budget"' in _ADMIN


def test_the_endpoint_reports_relevance_and_repeats():
    """Volume alone cannot say how much was spent on tracked stocks, nor whether an article
    was classified twice — the 2026-10-05 review could not rule repeats in or out at all."""
    for key in ('"unique_articles"', '"repeat_classifications"', '"tracked"',
                '"market_context"', '"out_of_scope"'):
        assert key in _ADMIN, key
    assert "calls_with_relevance_data" in _ADMIN, \
        "pre-instrumentation calls are excluded, not assumed"
