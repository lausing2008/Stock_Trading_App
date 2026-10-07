"""Do the issuer-fact extraction and the scenario valuation refuse what they cannot support?

Fixtures are real shapes taken from the MU and CRDO filings fetched on 2026-10-07, so a change
that breaks on real EDGAR payloads breaks here. No network.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from intel_reports.issuer_facts import (  # noqa: E402
    extract, CONCEPTS, ANNUAL_FORMS, Fact)
from intel_reports.valuation import (  # noqa: E402
    Assumption, Scenario, Valuation, normalized_earnings, sensitivity)


def _facts(**concepts):
    return {"entityName": "Micron Technology, Inc.",
            "facts": {"us-gaap": {k: {"units": {"USD": v}} for k, v in concepts.items()}}}


def _e(val, start, end, form="10-K", filed="2025-10-03", fy=2025):
    return {"val": val, "start": start, "end": end, "form": form, "filed": filed,
            "fy": fy, "fp": "FY", "accn": "0000723125-25-000028"}


# ---- issuer facts ------------------------------------------------------------------------

def test_the_latest_annual_anchors_on_revenue_not_a_balance_sheet_instant():
    """A balance-sheet instant from a later comparative would pull 'latest year' past any year
    actually reported."""
    f = extract(_facts(
        Revenues=[_e(37_378e6, "2024-08-30", "2025-08-28")],
        Assets=[{"val": 90e9, "start": None, "end": "2026-05-28", "form": "10-K",
                 "filed": "2025-10-03", "fy": 2026, "fp": "Q3"}]), cik="0000723125")
    assert f.latest_period_end == "2025-08-28"
    assert f.latest_fiscal_year == 2025


def test_a_newer_duration_fact_in_another_series_does_not_move_the_anchor():
    """THE ANCHOR IS REVENUE, deliberately. A sabotage that anchored on whichever series came
    first passed an earlier version of these tests, because the only other series in the
    fixture was a balance-sheet instant and the period_start filter caught it by accident.
    A DURATION fact in another series is the case that distinguishes them: if operating cash
    flow is reported for a later period than revenue, the 'latest fiscal year' must still be
    the latest year REVENUE reaches, or the block mixes two different years."""
    f = extract({"entityName": "X", "facts": {"us-gaap": {
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
            _e(89_680e6, "2025-08-29", "2026-09-03", filed="2026-11-01", fy=2026)]}},
        "Revenues": {"units": {"USD": [_e(37_378e6, "2024-08-30", "2025-08-28")]}},
    }}}, cik="0000723125")
    assert f.latest_period_end == "2025-08-28", \
        "the anchor must be the newest year revenue reaches, not the newest fact of any kind"
    assert f.latest_fiscal_year == 2025
    # And the mismatched cash-flow year must NOT be pulled into the latest-annual block.
    assert "operating_cashflow" not in f.latest_annual


def test_every_figure_in_the_latest_annual_block_shares_one_period_end():
    """THE INVARIANT THAT MATTERS, stated directly. The anchor being revenue is currently also
    guaranteed by `revenue` being first in CONCEPTS, so a sabotage of the anchor line alone was
    a no-op and proved nothing. What must hold however the anchor is chosen is that the block
    is ONE fiscal year — a margin computed across two years is not a margin."""
    f = extract({"entityName": "X", "facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [_e(37_378e6, "2024-08-30", "2025-08-28"),
                                       _e(25_111e6, "2023-09-01", "2024-08-29")]}},
        "GrossProfit": {"units": {"USD": [_e(14_873e6, "2024-08-30", "2025-08-28"),
                                          _e(5_613e6, "2023-09-01", "2024-08-29")]}},
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
            _e(89_680e6, "2025-08-29", "2026-09-03", filed="2026-11-01", fy=2026)]}},
    }}}, cik="x")
    ends = {q: fact.period_end for q, fact in f.latest_annual.items()}
    assert len(set(ends.values())) == 1, f"the block mixes fiscal years: {ends}"
    assert set(ends) == {"revenue", "gross_profit"}, \
        "a quantity with no fact for the anchor year must be absent, not substituted"


def test_only_ten_k_forms_count_as_a_fiscal_year():
    """A 10-Q's year-to-date figure is not a fiscal year, and a 20-F may be IFRS — which would
    break the 'these are US GAAP by construction' guarantee."""
    f = extract(_facts(Revenues=[
        _e(37_378e6, "2024-08-30", "2025-08-28"),
        _e(99_000e6, "2025-08-29", "2026-05-28", form="10-Q", filed="2026-06-25")]),
        cik="0000723125")
    assert [x.period_end for x in f.annual_series["revenue"]] == ["2025-08-28"]
    assert "10-Q" not in ANNUAL_FORMS and "20-F" not in ANNUAL_FORMS


def test_a_restated_figure_prefers_the_later_filing_and_records_the_earlier():
    f = extract(_facts(Revenues=[
        _e(37_000e6, "2024-08-30", "2025-08-28", filed="2025-10-03"),
        _e(37_378e6, "2024-08-30", "2025-08-28", filed="2026-10-02")]), cik="0000723125")
    assert f.annual_series["revenue"][0].value == 37_378e6


def test_concepts_are_tried_in_order_and_the_one_used_is_recorded():
    """Taking the first found must not hide WHICH was found."""
    assert CONCEPTS["revenue"][0] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    f = extract(_facts(Revenues=[_e(37_378e6, "2024-08-30", "2025-08-28")]), cik="x")
    assert f.latest_annual["revenue"].concept == "Revenues"


def test_a_quantity_absent_from_the_filing_is_reported_not_zeroed():
    """CRDO really has no long-term debt concept; that must read as not found, never as 0."""
    f = extract(_facts(Revenues=[_e(1_335e6, "2025-05-04", "2026-05-02")]), cik="0001807794")
    assert "long_term_debt" in f.not_found
    assert "long_term_debt" not in f.latest_annual


def test_the_basis_is_a_property_of_the_source_not_an_assumption():
    f = extract(_facts(Revenues=[_e(1.0, "2024-08-30", "2025-08-28")]), cik="x")
    d = f.as_dict()
    assert d["accounting_basis"] == "us-gaap"
    assert "rather than an assumption" in d["basis_evidence"]


def test_a_payload_with_no_annual_revenue_yields_no_latest_year_rather_than_guessing():
    f = extract(_facts(Assets=[{"val": 1.0, "start": None, "end": "2025-08-28",
                                "form": "10-K", "filed": "2025-10-03"}]), cik="x")
    assert f.latest_fiscal_year is None and f.latest_annual == {}


# ---- valuation ---------------------------------------------------------------------------

MU_NI = [("FY2021", 5.861e9), ("FY2022", 8.687e9), ("FY2023", -5.833e9),
         ("FY2024", 0.778e9), ("FY2025", 8.539e9), ("FY2026", 84.97e9)]


def test_a_cyclical_peak_is_visible_against_the_through_cycle_mean():
    """MU's real series: the peak year is 4.95x the six-year mean, which is what a low P/E on
    peak earnings conceals."""
    n = normalized_earnings(MU_NI)
    assert n["available"] is True and n["periods"] == 6
    assert round(n["mean"] / 1e9, 2) == 17.17
    assert round(n["latest_vs_mean"], 2) == 4.95
    assert n["trough"] < 0, "the cycle contains a real loss year"


def test_fewer_than_four_periods_refuses_rather_than_averaging_one_upswing():
    n = normalized_earnings(MU_NI[-3:])
    assert n["available"] is False
    assert "the upswing restated" in n["reason"]


def test_the_mean_does_not_claim_to_span_a_cycle_on_its_own():
    assert "a judgement the caller makes" in normalized_earnings(MU_NI)["note"]


def test_both_denominators_are_reported_and_differ():
    """The error this module exists to prevent — and which a draft of the MU/CRDO write-up
    made in prose: quoting the discount to value as if it were the return from the price."""
    v = Valuation(symbol="CRDO", as_of="2026-10-07", market_cap=39.94e9,
                  market_cap_as_of="2026-10-06",
                  scenarios=[Scenario("base", 0.9e9, 30.0, "")])
    row = v.implied()[0]
    assert round(row["upside_to_price"], 3) == -0.324
    assert round(row["discount_to_value"], 3) == -0.479
    assert row["upside_to_price"] != row["discount_to_value"]


def test_the_market_cap_carries_its_own_date_distinct_from_the_valuation():
    v = Valuation(symbol="MU", as_of="2026-10-07", market_cap=1.2016e12,
                  market_cap_as_of="2026-10-06")
    d = v.as_dict()
    assert d["market_cap_as_of"] == "2026-10-06" and d["as_of"] != d["market_cap_as_of"]


def test_no_per_share_figure_is_produced():
    d = Valuation("MU", "2026-10-07", 1.0e12, "2026-10-06").as_dict()
    assert "NOT CONVERTED" in d["per_share"]
    assert not isinstance(d["per_share"], (int, float))


def test_scenarios_are_not_claimed_to_be_a_distribution():
    d = Valuation("MU", "2026-10-07", 1.0e12, "2026-10-06").as_dict()
    assert "not a confidence interval" in d["what_this_is_not"]
    assert "carry no probabilities" in d["what_this_is_not"]


def test_every_assumption_must_state_what_would_change_it():
    a = Assumption("sustainable earnings", 60e9, "USD", "FY2026 8-K",
                   "a memory downcycle historically takes earnings below a third of peak")
    assert a.as_dict()["sensitivity"]


def test_the_sensitivity_grid_says_the_two_axes_are_correlated_for_a_cyclical():
    g = sensitivity(Scenario("base", 60e9, 14.0, ""))
    assert len(g["grid"]) == 4 and len(g["grid"][0]) == 4
    lows = [c["equity_value"] for row in g["grid"] for c in row]
    assert round(min(lows) / 1e12, 2) == 0.41 and round(max(lows) / 1e12, 2) == 1.42
    assert "correlated" in g["note"]
