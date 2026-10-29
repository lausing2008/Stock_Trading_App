import json
import pathlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "shared"))
from src.intel_reports import valuation_report as V

NOW = datetime(2026, 10, 28, 14, tzinfo=timezone.utc)


def source(symbol="NEW"):
    return {"symbol": symbol, "name": "New Company", "quote_type": "EQUITY", "exchange": "NYQ",
            "currency": "USD", "financial_currency": "USD", "sector": "Industrials", "industry": "Tools",
            "fetched_at": NOW.isoformat(), "source_url": "https://finance.yahoo.com/quote/NEW/",
            "tracked": False, "info": {"marketCap": 10e9, "sharesOutstanding": 100e6,
                "trailingPE": 15, "forwardPE": 12, "trailingEps": 4, "forwardEps": 5,
                "pegRatio": 1.2, "revenueGrowth": .08,
                "profitMargins": .15, "targetMeanPrice": 80, "numberOfAnalystOpinions": 9},
            "bars": [{"date": (datetime(2026, 10, 27) - timedelta(days=119-i)).date().isoformat(),
                      "close": 50, "adj_close": 50, "volume": 1e6} for i in range(120)],
            "annuals": [{"period_end": f"{year}-12-31", "ebit": 1e9, "pretax_income": .9e9,
                         "tax_provision": .18e9, "depreciation": .2e9, "capital_expenditure": -.15e9,
                         "change_working_capital": -.05e9, "debt": 1e9, "cash": .5e9,
                         "revenue": revenue, "free_cashflow": .7e9, "preferred": 0,
                         "minority_interest": 0, "equity": 5e9}
                        for year, revenue in ((2025, 5e9), (2024, 4.5e9))],
            "analyst_actions": [{"date": "2026-10-23", "firm": "Example Research", "rating": "Underperform", "action": "down"}],
            "errors": []}


def inputs(raw=None):
    return {"rows": [raw or source()], "price_session": "2026-10-27", "queries": ["test"],
            "queries_failed": [], "discovered": 1, "sampled": 1, "sample_limit": 60,
            "universe": "Test sample"}


def test_fcff_enterprise_to_equity_bridge_and_denominator_guards():
    base = V.dcf(100, 10, 50, 20, .05, .10)
    debt = V.dcf(100, 10, 150, 20, .05, .10)
    senior = V.dcf(100, 10, 50, 20, .05, .10, senior_claims=20)
    assert base["fair_value"] - debt["fair_value"] == pytest.approx(10)
    assert base["fair_value"] - senior["fair_value"] == pytest.approx(2)
    for args in ((100, 0, 0, 0, .1, .1), (-1, 10, 0, 0, .1, .1), (100, 10, 0, 0, .1, .025)):
        assert V.dcf(*args) is None


def test_discount_and_upside_have_distinct_denominators(monkeypatch):
    monkeypatch.setattr(V, "valuation_model", lambda *_: {"state": "OK", "scenarios": {
        "base": {"fair_value": 100, "terminal_value_share_pct": 60}}, "eligibility_blockers": []})
    raw = source()
    for bar in raw["bars"]:
        bar["close"] = 80
    row = V.build_row(raw, "2026-10-27", NOW)
    assert row["metrics"]["discount_pct"]["value"] == pytest.approx(20)
    assert row["metrics"]["upside_pct"]["value"] == pytest.approx(25)
    assert row["classification"] == "undervalued"


def test_scenarios_are_ordered_and_freeze_the_cashflow_bridge():
    model = V.valuation_model(source(), NOW)
    assert model["state"] == "OK"
    assert model["inputs"]["fcff"] == pytest.approx(.8e9)
    scenarios = model["scenarios"]
    assert scenarios["bear"]["fair_value"] < scenarios["base"]["fair_value"] < scenarios["bull"]["fair_value"]
    assert model["eligibility_blockers"]  # Basic shares cannot silently establish dilution safety.


def test_uw_cashflow_proxy_is_explicit_and_never_fabricates_working_capital():
    raw = source(); latest = raw["annuals"][0]
    latest.update(fcff_basis="operating_cashflow_plus_after_tax_interest_less_capex_proxy",
                  operating_cashflow=.7e9, interest_expense=.1e9, change_working_capital=None)
    raw["provider"] = "Unusual Whales"
    raw["bars"] = [{**b, "adj_close": None} for b in raw["bars"]]
    row = V.build_row(raw, "2026-10-27", NOW)
    assert row["valuation"]["inputs"]["fcff"] == pytest.approx(.63e9)
    assert "proxy" in row["valuation"]["method"]
    assert any("cash-interest" in s for s in row["valuation"]["limitations"])
    assert row["metrics"]["rsi"]["value"] is None
    assert "adjustment" in row["metrics"]["rsi"]["reason"]
    assert not row["qualified"]


@pytest.mark.parametrize("field,value,expected", [
    ("sector", "Financial Services", "NOT_APPLICABLE"),
    ("financial_currency", "EUR", "UNAVAILABLE"),
])
def test_unsupported_company_models_remain_explicit(field, value, expected):
    raw = source(); raw[field] = value
    assert V.valuation_model(raw, NOW)["state"] == expected


def test_missing_cashflow_inputs_never_receive_a_default_valuation():
    raw = source(); raw["annuals"][0]["depreciation"] = None
    model = V.valuation_model(raw, NOW)
    assert model["state"] == "UNAVAILABLE"
    assert "depreciation" in model["reason"]


def test_stale_inputs_and_unknown_quality_cannot_qualify():
    raw = source(); raw["fetched_at"] = "2026-10-01T10:00:00+00:00"
    row = V.build_row(raw, "2026-10-27", NOW)
    assert row["classification"] == "insufficient_evidence"
    assert row["valuation"]["state"] == "STALE"
    assert row["metrics"]["price"]["state"] == "STALE"
    assert not row["qualified"]
    assert any("Quality" in b for b in row["blockers"])


def test_analyst_flags_remain_independent_of_valuation_and_aggregate_is_undated():
    row = V.build_row(source(), "2026-10-27", NOW)
    assert any(r["kind"] == "analyst" for r in row["risk_flags"])
    assert row["analyst"]["state"] == "UNKNOWN"
    assert row["analyst"]["mean"] == 80
    assert not row["qualified"]


def test_rsi_flat_one_sided_and_insufficient_histories():
    assert V.rsi([10.] * 100) == 50
    assert V.rsi(list(range(1, 121))) == 100
    assert V.rsi(list(range(121, 1, -1))) == 0
    assert V.rsi([10.] * 99) is None
    assert V.rsi([10.] * 99 + [float("nan")]) is None


def test_first_observation_is_new_coverage_and_previous_comparison_does_not_change_fingerprint():
    first = V.build_report(inputs(), now=NOW)
    data = first[0]["valuation"].value
    second = V.build_report(inputs(), now=NOW, previous=data)
    assert data["changes"][0]["kind"] == "new_coverage"
    assert second[0]["valuation"].value["changes"] == []
    assert first[2]["fingerprint"] == second[2]["fingerprint"]
    assert first[1].dangling(first[0]) == []
    # Source values, not only external references, are frozen with the report.
    assert first[1].records["valuation:NEW"]["value"]["annuals"][0]["ebit"] == 1e9
    assert data["rows"][0]["tracked"] is False
    json.dumps(data, allow_nan=False)


def test_nonpositive_multiples_and_insufficient_peer_coverage_are_missing():
    raw = source(); raw["info"]["trailingEps"] = -10; raw["info"]["pegRatio"] = 0
    data = V.build_report(inputs(raw), now=NOW)[0]["valuation"].value
    assert data["rows"][0]["metrics"]["trailing_pe"]["value"] is None
    assert data["rows"][0]["metrics"]["peg"]["value"] is None
    assert data["rows"][0]["peer_pe"]["median"] is None


def test_multiples_use_frozen_close_instead_of_the_intraday_provider_multiple():
    row = V.build_row(source(), "2026-10-27", NOW)
    assert row["metrics"]["trailing_pe"]["value"] == 12.5
    assert row["metrics"]["forward_pe"]["value"] == 10


def test_real_database_and_route_report_workflow():
    probe = pathlib.Path(__file__).with_name("_valuation_report_probe.py")
    result = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr[-4000:] + result.stdout[-2000:]
    assert json.loads(result.stdout)["passes"] is True
