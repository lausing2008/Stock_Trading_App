from copy import deepcopy
import pytest

from src.intel_reports import valuation_report as V
from src.intel_reports import sector_valuation as S
from test_valuation_report import source, NOW


def bank():
    raw = source()
    raw.update(sector="Financial Services", industry="Banks - Regional")
    raw["annuals"] = [{"period_end": f"{year}-12-31", "common_equity": 10e9,
                       "net_income_common": 1.2e9} for year in (2025, 2024, 2023)]
    return raw


def test_bank_uses_common_roe_and_equity_cost_without_subtracting_deposit_debt():
    raw = bank()
    value = V.valuation_model(raw, NOW)
    assert value["state"] == "OK"
    assert value["scenarios"]["base"]["fair_value"] == pytest.approx(100 * (.12 - .025) / (.105 - .025), abs=.01)
    assert value["scenarios"]["base"]["enterprise_value"] is None
    assert value["eligibility_blockers"]
    raw["annuals"][0]["debt"] = 500e9
    assert V.valuation_model(raw, NOW)["scenarios"] == value["scenarios"]


def test_bank_does_not_substitute_total_earnings_or_nonconsecutive_book_values():
    raw = bank()
    raw["annuals"][0].pop("net_income_common")
    raw["annuals"][0]["net_income"] = 1.2e9
    assert S.model(raw, NOW)["state"] == "UNAVAILABLE"
    raw = bank()
    raw["annuals"][-1]["period_end"] = "2020-12-31"
    assert S.model(raw, NOW)["state"] == "UNAVAILABLE"


def test_reit_requires_reported_ffo_and_does_not_fabricate_it_from_depreciation():
    raw = source(); raw.update(sector="Real Estate", industry="REIT - Retail")
    assert S.model(raw, NOW)["state"] == "UNAVAILABLE"
    raw["sector_inputs"] = {"reit": {"ffo_common_per_diluted_share": 4,
        "period_end": "2025-12-31", "source_ids": ["issuer-ffo"]}}
    model = S.model(raw, NOW)
    assert model["scenarios"]["base"]["fair_value"] == 60
    assert model["scenarios"]["base"]["discount_rate_pct"] is None
    assert model["scenarios"]["base"]["multiple"] == 15
    raw["sector_inputs"]["reit"]["period_end"] = "2024-12-31"
    assert S.model(raw, NOW)["state"] == "UNAVAILABLE"


def unprofitable():
    raw = source()
    raw["annuals"][0].update(ebit=-1e6, free_cashflow=-120e6, cash=500e6, debt=50e6)
    raw["sector_inputs"] = {"unprofitable": {label: {
        "fcff": [v * 1e6 * scale for v in (-10, -5, 20, 40, 60)],
        "discount": cost, "terminal_growth": .02, "diluted_shares": 110e6,
        "source_ids": ["reviewed-forecast"]} for label, scale, cost in (("bear", .8, .16), ("base", 1, .13), ("bull", 1.2, .11))}}
    return raw


def test_loss_model_prices_five_explicit_cashflows_and_dilution_instead_of_negative_pe():
    raw = unprofitable()
    model = S.model(raw, NOW)
    assert model["state"] == "OK"
    base = model["scenarios"]["base"]
    spec = raw["sector_inputs"]["unprofitable"]["base"]
    terminal = 60e6 * 1.02 / (.13 - .02) / 1.13 ** 5
    ev = sum(v / 1.13 ** (i + 1) for i, v in enumerate(spec["fcff"])) + terminal
    assert base["fair_value"] == pytest.approx((ev + 450e6) / 110e6, abs=.01)
    assert base["growth_pct"] is None


@pytest.mark.parametrize("issue", ["funding", "dilution", "terminal", "missing_forecast"])
def test_loss_model_refuses_unfunded_or_incomplete_forecasts(issue):
    raw = unprofitable()
    if issue == "funding": raw["annuals"][0]["cash"] = 1e6
    if issue == "dilution": raw["sector_inputs"]["unprofitable"]["base"]["diluted_shares"] = 1e6
    if issue == "terminal": raw["sector_inputs"]["unprofitable"]["base"]["fcff"][-1] = -1
    if issue == "missing_forecast": raw["sector_inputs"].clear()
    assert S.model(raw, NOW)["state"] == "UNAVAILABLE"
    assert S.model(raw, NOW)["diagnostics"]["historical_cash_runway_months"] is not None
