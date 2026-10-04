"""Layer 1: does the report say what the evidence supports, and what to watch next?

The bar these tests hold the code to is the product one: after reading the assessment, can a
reader state the conclusion, the strongest thing against it, and the specific next observation
that would change it? A test that only checks another field appeared would not measure that.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))

from intel_reports.interpretation import (  # noqa: E402
    outlook_assessment, post_earnings_assessment)
from intelligence.report_contract import (  # noqa: E402
    FieldState, calculated, observed, unavailable, unknown)


def _mu_fields(**over):
    f = {
        "trend_structure": calculated({"last_close": 1074.89, "sma20": 1025.0, "sma50": 956.05,
                                       "above_sma20": True, "above_sma50": True,
                                       "structure": "above both moving averages"},
                                      evidence_ids=["price:33:2026-10-02T00:00"]),
        "return_1_bars": calculated({"pct": -2.05}, units="pct"),
        "return_5_bars": calculated({"pct": -0.68}, units="pct"),
        "return_20_bars": calculated({"pct": 12.18}, units="pct"),
        "return_63_bars": calculated({"pct": 9.15}, units="pct"),
        "company_condition": unavailable("no fundamentals time series is stored"),
        "valuation": unavailable("no multiples with peer or historical context are stored"),
        "estimate_revisions": unavailable("only a single current consensus snapshot is stored"),
        "news": unavailable("not joined to this report"),
        "options_positioning": unavailable("per-leg option quotes are not assembled"),
        "outlook_short": unavailable("no horizon-specific rule exists"),
        "outlook_medium": unavailable("no horizon-specific rule exists"),
        "outlook_long": unavailable("no horizon-specific rule exists"),
    }
    f.update(over)
    return f


def _market_fields(participation=54.2, **over):
    f = _mu_fields()
    for k in ("company_condition", "valuation", "estimate_revisions", "news",
              "options_positioning"):
        f.pop(k)
    f.update({
        "trend_structure": calculated({"last_close": 769.64, "sma20": 764.83, "sma50": 763.70,
                                       "above_sma20": True, "above_sma50": True,
                                       "structure": "above both moving averages"}),
        "return_1_bars": calculated({"pct": 0.74}, units="pct"),
        "return_5_bars": calculated({"pct": -0.22}, units="pct"),
        "return_20_bars": calculated({"pct": -0.46}, units="pct"),
        "breadth": calculated({"above_sma20": 78, "covered": 144, "universe": 145,
                               "participation_pct": participation, "coverage_pct": 99.3}),
        "sector_leadership": calculated({"sessions": 20, "ranked": [
            {"sector": "Technology", "mean_return_pct": 11.52, "symbols": 59},
            {"sector": "Industrials", "mean_return_pct": 0.09, "symbols": 19},
            {"sector": "Consumer Defensive", "mean_return_pct": -3.84, "symbols": 1},
            {"sector": "Energy", "mean_return_pct": -14.40, "symbols": 6}]}),
        "volatility": unavailable("no volatility index or realised-volatility series"),
        "rates_credit_fx": unavailable("no yield, credit-spread or FX series is ingested"),
        "macro": unavailable("an economic calendar exists but is not assembled here"),
        "liquidity": unavailable("no financial-conditions measure is assembled here"),
        "positioning": unavailable("options/GEX positioning is not aggregated here"),
    })
    f.update(over)
    return f


# ---------------------------------------------------------------- the reading, not the data

def test_the_assessment_names_strength_and_weakness_rather_than_one_side():
    """MU is above both averages AND down over the last two windows. A reading that reports
    only the first is how "above the 20-bar average" becomes a bullish call."""
    a = outlook_assessment(_mu_fields(), subject="MU", report_type="stock_outlook").value
    assert "longer-window strength" in a["assessment"]
    assert "recent weakness" in a["assessment"]
    assert "unresolved" in a["assessment"]


def test_the_structure_finding_carries_its_own_counterevidence_and_an_invalidation_level():
    a = outlook_assessment(_mu_fields(), subject="MU", report_type="stock_outlook").value
    s = a["why_it_matters"][0]
    assert "above both the 20- and 50-bar averages" in s["supports"]
    assert "12.18%" in s["supports"]
    assert "-2.05% over the latest bar" in s["contradicts"]
    assert "-0.68% over five bars" in s["contradicts"]
    assert "does not establish whether the pullback reverses or deepens" in s["contradicts"]
    assert s["invalidated_by"] == "a daily close below 1025.0"


def test_a_structure_with_no_recent_weakness_does_not_invent_any():
    f = _mu_fields(return_1_bars=calculated({"pct": 1.2}, units="pct"),
                   return_5_bars=calculated({"pct": 2.4}, units="pct"))
    a = outlook_assessment(f, subject="MU", report_type="stock_outlook").value
    assert "recent weakness" not in a["assessment"]
    assert "carries no horizon" in a["why_it_matters"][0]["contradicts"]


def test_an_absent_price_structure_yields_insufficient_evidence_not_a_guess():
    f = {"trend_structure": unavailable("no bars on file")}
    a = outlook_assessment(f, subject="MU", report_type="stock_outlook").value
    assert "insufficient evidence" in a["assessment"]


# ---------------------------------------------------------------- level vs direction

def test_participation_is_reported_as_a_level_and_its_direction_as_unknown():
    """54.2% is a level. "Breadth is strengthening" is a claim about change."""
    a = outlook_assessment(_market_fields(), subject="The US benchmark",
                           report_type="market_outlook").value
    p = [f for f in a["why_it_matters"] if "Participation" in f["finding"]][0]
    assert "54.2% of the 144 covered symbols" in p["supports"]
    assert "144 of 145" in p["supports"], "the universe is named, not implied"
    assert "UNKNOWN" in p["contradicts"]
    assert "cannot support a claim that breadth is strengthening" in p["contradicts"]


def test_participation_direction_is_stated_once_an_earlier_reading_exists():
    a = outlook_assessment(_market_fields(), subject="The US benchmark",
                           report_type="market_outlook", prior_participation=49.0).value
    p = [f for f in a["why_it_matters"] if "Participation" in f["finding"]][0]
    assert "broadening from 49.0%" in p["supports"]
    assert "+5.2pp" in p["supports"]
    assert "UNKNOWN" not in p["contradicts"]


def test_a_falling_participation_reading_is_described_as_narrowing():
    a = outlook_assessment(_market_fields(), subject="The US benchmark",
                           report_type="market_outlook", prior_participation=61.0).value
    p = [f for f in a["why_it_matters"] if "Participation" in f["finding"]][0]
    assert "narrowing from 61.0%" in p["supports"] and "-6.8pp" in p["supports"]


def test_a_thin_sector_is_flagged_rather_than_read_as_a_sector():
    a = outlook_assessment(_market_fields(), subject="The US benchmark",
                           report_type="market_outlook").value
    s = [f for f in a["why_it_matters"] if "leads" in f["finding"]][0]
    assert "Consumer Defensive" in s["contradicts"]
    assert "fewer than five symbols" in s["contradicts"]
    assert "not sector indices" in s["contradicts"]


# ---------------------------------------------------------------- material gaps, not a list

def test_only_the_gaps_that_constrain_this_conclusion_are_named():
    a = outlook_assessment(_market_fields(), subject="The US benchmark",
                           report_type="market_outlook").value
    named = a["what_limits_this"]
    assert len(named) == 3, "two or three, not eight"
    assert named[0].startswith("volatility")
    assert "a calm advance cannot be told from a fragile one" in named[0]
    assert a["other_unavailable_inputs"] > 0, "the rest are counted, not listed"


def test_a_stock_report_ranks_different_gaps_than_a_market_one():
    """The same absence constrains different conclusions differently."""
    m = outlook_assessment(_market_fields(), subject="X", report_type="market_outlook").value
    s = outlook_assessment(_mu_fields(), subject="MU", report_type="stock_outlook").value
    assert m["what_limits_this"][0].startswith("volatility")
    assert s["what_limits_this"][0].startswith("company condition")


# ---------------------------------------------------------------- what to watch

def test_the_reader_is_given_a_specific_next_trigger_not_a_topic():
    a = outlook_assessment(_mu_fields(), subject="MU", report_type="stock_outlook").value
    w = a["watch_next"][0]
    assert "1025.0" in w["watch"] and "1025.0" in w["trigger"]
    assert w["would_change"]


def test_the_counterargument_names_the_limit_rather_than_hedging_generally():
    a = outlook_assessment(_market_fields(), subject="The US benchmark",
                           report_type="market_outlook").value
    c = a["counterargument"]
    assert "rests entirely on price relative to its own averages" in c
    assert "calm advance" in c, "it names the top-ranked limit"
    assert "continuation from a reversal" in c


def test_no_horizon_is_claimed_from_daily_bars():
    a = outlook_assessment(_mu_fields(), subject="MU", report_type="stock_outlook").value
    assert "no horizon is claimed" in a["horizon"]


# ---------------------------------------------------------------- post-earnings

def _post_fields(**over):
    f = {
        "official_figures": observed({"revenue": {"value": 54230000000.0}},
                                     evidence_ids=["issuer_document:1"]),
        "revenue_actual": observed({"value": 54230000000.0, "units": "USD", "basis": "GAAP"},
                                   evidence_ids=["issuer_document:1"]),
        "eps_actual": observed({"value": 33.42, "units": "USD/share",
                                "basis": "non-GAAP adjusted"}),
        "guidance_current": observed({"guidance_q1_revenue": {"value": 61500000000.0}}),
        "guidance_change": unknown("no prior guidance for the same period is stored"),
        "return_1d": observed({"pct": 3.03, "window": "2026-09-29 close to 2026-10-01 close",
                               "basis": "2 close-to-close interval(s)"}, units="pct"),
        "pre_report_link": unavailable("no frozen pre-earnings report exists for this event"),
        "accounting_basis": observed({"estimate_basis": "UNKNOWN"}),
        "management_commentary": unavailable("no management commentary is stored"),
        "revenue_surprise_pct": unknown("the stored estimate carries no units"),
        "options_reaction": unavailable("no fresh per-leg option quotes"),
    }
    f.update(over)
    return f


def test_post_earnings_separates_available_guidance_from_a_verified_raise():
    a = post_earnings_assessment(_post_fields(), subject="MU").value
    g = [f for f in a["why_it_matters"] if "Guidance" in f["finding"]][0]
    assert "issued with these results" in g["finding"]
    assert "NOT established" in g["contradicts"]
    assert "SAME target period" in g["contradicts"]
    assert any("prior guidance" in w["watch"] for w in a["watch_next"])


def test_post_earnings_states_the_price_window_and_attributes_no_cause():
    a = post_earnings_assessment(_post_fields(), subject="MU").value
    r = [f for f in a["why_it_matters"] if "returned" in f["finding"]][0]
    assert "+3.03%" in r["finding"]
    assert "2026-09-29 close to 2026-10-01 close" in r["supports"]
    assert "does not isolate the announcement" in r["contradicts"]
    assert "No cause is attributed" in r["contradicts"]


def test_post_earnings_says_the_comparison_is_unverified_when_no_baseline_exists():
    a = post_earnings_assessment(_post_fields(), subject="MU").value
    assert "comparison with pre-release expectations is unverified" in a["assessment"]
    assert a["what_limits_this"][0].startswith("pre report link")


def test_post_earnings_without_a_release_reports_insufficient_evidence():
    f = _post_fields(official_figures=unavailable("no issuer document is stored"),
                     revenue_actual=unavailable("no revenue actual on file"),
                     eps_actual=unavailable("no eps actual on file"))
    a = post_earnings_assessment(f, subject="MU").value
    assert "insufficient evidence" in a["assessment"]
