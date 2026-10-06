"""Does the Quality & Value composition refuse what it has no evidence for?

The bar these hold the code to is the one the design states: a required gate cannot be
compensated for, an unmeasured input is never read as a pass, and an empty eligible list is a
valid result rather than a defect.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from intel_reports.quality_value import (  # noqa: E402
    Gate, GateStatus, State, compose, discount, upside,
    durability_gate, valuation_gate,
    ALL_GATES, REQUIRED_FOR_ENTRY, QUALITY_GATES,
    BUSINESS_QUALITY, COMPETITIVE_DURABILITY, VALUATION, ENTRY_CONDITION,
    VALUE_TRAP_RISK, CATALYSTS, PORTFOLIO_FIT)


def g(name, status=GateStatus.PASS, reasons=("because",)):
    return Gate(name, status, () if status is GateStatus.PASS else reasons)


def all_passing(**over):
    gates = {n: g(n) for n in REQUIRED_FOR_ENTRY}
    gates.update(over)
    return list(gates.values())


# ---- the composition rule -------------------------------------------------------------

def test_every_required_gate_passing_reaches_entry_review():
    e = compose("MU", all_passing())
    assert e.state is State.ENTRY_REVIEW_READY
    assert e.blocking() == ()


def test_an_unknown_required_gate_is_never_read_as_a_pass():
    """The recurring defect this platform has: a check that never fires on a missing number."""
    for name in REQUIRED_FOR_ENTRY:
        e = compose("MU", all_passing(**{name: g(name, GateStatus.UNKNOWN)}))
        assert e.state is not State.ENTRY_REVIEW_READY, f"{name} unknown reached entry review"
        assert name in {b.name for b in e.blocking()}


def test_a_strong_gate_cannot_compensate_for_a_missing_one():
    """There is no score to outvote with, and this proves there is no back door either."""
    gates = all_passing(**{COMPETITIVE_DURABILITY: g(COMPETITIVE_DURABILITY, GateStatus.UNKNOWN)})
    gates += [Gate(CATALYSTS, GateStatus.PASS), Gate(PORTFOLIO_FIT, GateStatus.PASS)]
    assert compose("MU", gates).state is State.INSUFFICIENT_EVIDENCE


def test_a_disqualifying_risk_outranks_every_other_gate():
    e = compose("MU", all_passing(**{
        VALUE_TRAP_RISK: g(VALUE_TRAP_RISK, GateStatus.BLOCKED,
                           ("structural demand decline with a 2026 refinancing wall",))}))
    assert e.state is State.THESIS_AT_RISK
    assert "structural demand decline" in " ".join(e.explanation)


def test_a_blocked_gate_is_reported_as_a_risk_not_a_data_gap():
    """More ingestion would not close it, and a reader must not be told to wait for data."""
    e = compose("MU", all_passing(**{
        VALUE_TRAP_RISK: g(VALUE_TRAP_RISK, GateStatus.BLOCKED, ("cash burn with no funding",))}))
    # NOT just "is not insufficient evidence" — that passes vacuously if the blocked gate is
    # ignored entirely and every other gate passes. Name the state it must reach.
    assert e.state is State.THESIS_AT_RISK
    assert e.state is not State.INSUFFICIENT_EVIDENCE
    assert "cash burn with no funding" in " ".join(e.explanation)


def test_a_data_gap_is_ranked_ahead_of_a_failure_judged_on_it():
    """A gate failed on incomplete evidence may not survive the missing input."""
    e = compose("MU", all_passing(**{
        VALUATION: g(VALUATION, GateStatus.FAIL, ("trades above value",)),
        COMPETITIVE_DURABILITY: g(COMPETITIVE_DURABILITY, GateStatus.UNKNOWN, ("nothing stored",))}))
    assert e.state is State.INSUFFICIENT_EVIDENCE


def test_a_sound_business_at_an_unattractive_price_is_a_watch_not_a_rejection():
    e = compose("MU", all_passing(**{
        VALUATION: g(VALUATION, GateStatus.FAIL, ("discount 2% against a required 25%",))}))
    assert e.state is State.QUALITY_WATCH


def test_a_discounted_business_awaiting_confirmation_is_a_value_candidate():
    e = compose("MU", all_passing(**{
        ENTRY_CONDITION: g(ENTRY_CONDITION, GateStatus.FAIL, ("one close above the average",))}))
    assert e.state is State.VALUE_CANDIDATE


def test_a_failed_quality_gate_is_not_promoted_to_a_watch():
    e = compose("MU", all_passing(**{
        BUSINESS_QUALITY: g(BUSINESS_QUALITY, GateStatus.FAIL, ("negative cash conversion",)),
        VALUATION: g(VALUATION, GateStatus.FAIL, ("no discount",))}))
    assert e.state is State.INSUFFICIENT_EVIDENCE


def test_a_gate_never_evaluated_blocks_rather_than_defaulting():
    gates = [x for x in all_passing() if x.name != VALUATION]
    e = compose("MU", gates)
    assert e.state is State.INSUFFICIENT_EVIDENCE
    assert "never evaluated" in " ".join(e.explanation)


def test_a_price_that_gapped_away_expires_rather_than_staying_ready():
    e = compose("MU", all_passing(), entry_zone_held=False)
    assert e.state is State.ENTRY_EXPIRED
    assert "expired rather than ready" in " ".join(e.explanation)


def test_the_same_gate_twice_is_an_error_not_a_last_one_wins():
    with pytest.raises(ValueError, match="evaluated twice"):
        compose("MU", all_passing() + [g(VALUATION)])


# ---- the gate contract ----------------------------------------------------------------

def test_a_non_passing_gate_must_say_why():
    with pytest.raises(ValueError, match="no reason given"):
        Gate(VALUATION, GateStatus.UNKNOWN, ())


def test_an_undeclared_gate_name_is_refused():
    with pytest.raises(ValueError, match="unknown gate"):
        Gate("vibes", GateStatus.PASS)


def test_catalysts_and_portfolio_fit_are_deliberately_not_required():
    """A catalyst is context; inventing a deadline for convergence is worse than having none."""
    assert CATALYSTS not in REQUIRED_FOR_ENTRY
    assert PORTFOLIO_FIT not in REQUIRED_FOR_ENTRY
    assert set(REQUIRED_FOR_ENTRY) <= set(ALL_GATES)
    assert set(QUALITY_GATES) <= set(REQUIRED_FOR_ENTRY)


# ---- what today's stored evidence actually produces ------------------------------------

def test_durability_is_unknown_and_says_persistence_is_not_proof():
    gate = durability_gate()
    assert gate.status is GateStatus.UNKNOWN
    joined = " ".join(gate.reasons)
    assert "equally consistent with the cycle" in joined
    assert "no sourced evidence" in joined


def test_an_attached_source_is_not_a_durability_assessment():
    gate = durability_gate({"sources": ["10-K item 1"]})
    assert gate.status is GateStatus.UNKNOWN
    assert "attaching a source is not the same as reading it" in " ".join(gate.reasons)


def test_valuation_is_unknown_without_a_frozen_discount_policy():
    gate = valuation_gate({"market_cap": 1.0e11, "equity_value": 1.5e11})
    assert gate.status is GateStatus.UNKNOWN
    assert "has not been frozen" in " ".join(gate.reasons)
    assert "fitted to its own sample" in " ".join(gate.reasons)


def test_todays_evidence_cannot_reach_entry_review_for_any_symbol():
    """The honest shape of the shadow dashboard: an empty eligible list is the right result."""
    e = compose("MU", [g(BUSINESS_QUALITY), durability_gate(), valuation_gate(),
                       g(ENTRY_CONDITION), g(VALUE_TRAP_RISK)])
    assert e.state is State.INSUFFICIENT_EVIDENCE
    assert {b.name for b in e.blocking()} == {COMPETITIVE_DURABILITY, VALUATION}


# ---- the two denominators --------------------------------------------------------------

def test_discount_and_upside_are_different_numbers_from_the_same_gap():
    d, u = discount(1.25e11, 1.0e11), upside(1.25e11, 1.0e11)
    assert round(d, 4) == 0.2 and round(u, 4) == 0.25
    assert d != u, "the same gap is 20% off the value and 25% of upside"


def test_neither_denominator_divides_by_zero_or_a_negative_value():
    assert discount(0, 1.0e11) is None
    assert discount(-5.0e10, 1.0e11) is None
    assert upside(1.0e11, 0) is None
    assert upside(1.0e11, -1.0) is None


def test_a_missing_input_yields_none_rather_than_a_zero_discount():
    """A zero discount sorts as 'fairly valued'; None sorts as unknown. They are not the same."""
    assert discount(1.0e11, None) is None
    assert upside(None, 1.0e11) is None


# ---- the gates the statement series can decide ------------------------------------------

from intel_reports.quality_value import (  # noqa: E402
    business_quality_gate, value_trap_gate, entry_condition_gate,
    MAX_RETRIEVAL_AGE_DAYS, MAX_REPORTED_YEAR_AGE_DAYS)


def _bq(**over):
    ev = {"annual_periods": 4, "retrieval_age_days": 3, "reported_year_age_days": 120,
          "revenue": 37378000000.0}
    ev.update(over)
    return ev


def test_business_quality_passes_on_a_current_multi_year_series():
    assert business_quality_gate(_bq()).status is GateStatus.PASS


def test_one_annual_period_cannot_measure_a_change():
    gate = business_quality_gate(_bq(annual_periods=1))
    assert gate.status is GateStatus.UNKNOWN
    assert "at least two are needed" in " ".join(gate.reasons)


def test_a_stale_retrieval_blocks_even_though_the_reported_year_is_current():
    """MU's two ages are different facts; this is the one a refetch closes."""
    gate = business_quality_gate(_bq(retrieval_age_days=MAX_RETRIEVAL_AGE_DAYS + 1))
    assert gate.status is GateStatus.UNKNOWN
    assert "last retrieved" in " ".join(gate.reasons)


def test_a_missing_reported_year_blocks_even_though_retrieval_is_fresh():
    """And this is the one only the issuer closes."""
    gate = business_quality_gate(_bq(reported_year_age_days=MAX_REPORTED_YEAR_AGE_DAYS + 1))
    assert gate.status is GateStatus.UNKNOWN
    assert "later fiscal year has almost certainly been reported" in " ".join(gate.reasons)


def test_an_absent_retrieval_time_is_not_treated_as_fresh():
    gate = business_quality_gate(_bq(retrieval_age_days=None))
    assert gate.status is GateStatus.UNKNOWN


def test_value_trap_is_unknown_even_when_what_can_be_checked_looks_fine():
    """PASS here would read as 'no value trap' when only two classes were observable."""
    gate = value_trap_gate({"net_debt_to_equity": 0.1, "free_cashflow": 1.0e9})
    assert gate.status is GateStatus.UNKNOWN
    joined = " ".join(gate.reasons)
    assert "structural demand decline" in joined and "customer concentration" in joined
    assert "not as a verdict" in joined


# AUD-QV-UNVALIDATEDTHRESHOLD. The first version blocked on net debt > 2x equity or two
# negative free-cash-flow years and flagged 48 of 200 production companies as a thesis at risk.
# The list refuted it: LMT (FCF $6.9bn and rising, ratio raised by buybacks), CM (a bank),
# VST/CWEN/NATL (utilities), ORCL (capex). These pin the retraction.

def test_a_profitable_company_is_not_blocked_by_a_leverage_ratio():
    """LMT's real figures: the ratio rises because buybacks shrink the denominator."""
    gate = value_trap_gate({"net_debt_to_equity": 2.62, "free_cashflow": 6.908e9,
                            "free_cashflow_prior": 5.287e9, "industry": "Aerospace & Defense"})
    assert gate.status is GateStatus.UNKNOWN
    assert "no threshold on this ratio has been validated" in " ".join(gate.reasons)


def test_leverage_is_not_interpreted_at_all_for_a_bank():
    gate = value_trap_gate({"net_debt_to_equity": 2.68, "industry": "Banks - Diversified"})
    assert gate.status is GateStatus.UNKNOWN
    joined = " ".join(gate.reasons)
    assert "not a solvency reading" in joined
    assert "no threshold on this ratio has been validated" not in joined


def test_two_negative_cash_flow_years_are_an_observation_not_a_verdict():
    """ORCL's real shape: operating cash flow spent on capacity, not distress."""
    gate = value_trap_gate({"free_cashflow": -2.3686e10, "free_cashflow_prior": -3.94e8,
                            "industry": "Software - Infrastructure"})
    assert gate.status is GateStatus.UNKNOWN
    assert "cannot separate heavy investment from distress" in " ".join(gate.reasons)


def test_the_computed_figures_are_still_reported():
    """Retracting the verdict must not also hide the evidence."""
    gate = value_trap_gate({"net_debt_to_equity": 2.62, "free_cashflow": -1.0,
                            "free_cashflow_prior": -1.0, "industry": "Aerospace & Defense"})
    assert len(gate.evidence["observations"]) == 2


def test_only_a_supplied_disqualifying_finding_can_block():
    gate = value_trap_gate({"net_debt_to_equity": 99.0,
                            "disqualifying": ("auditor resigned citing accounting concerns",)})
    assert gate.status is GateStatus.BLOCKED
    assert "auditor resigned" in " ".join(gate.reasons)


def test_no_ratio_however_extreme_blocks_on_its_own():
    assert value_trap_gate({"net_debt_to_equity": 1000.0, "free_cashflow": -1.0,
                            "free_cashflow_prior": -1.0}).status is GateStatus.UNKNOWN


def test_entry_requires_both_closes_above_the_average():
    assert entry_condition_gate({"recent_closes": [12.0, 11.5], "sma20": 10.0,
                                 "session_complete": True}).status is GateStatus.PASS
    gate = entry_condition_gate({"recent_closes": [12.0, 9.0], "sma20": 10.0,
                                 "session_complete": True})
    assert gate.status is GateStatus.FAIL
    assert "1 of the two most recent" in " ".join(gate.reasons)


def test_a_forming_bar_is_not_a_close():
    gate = entry_condition_gate({"recent_closes": [12.0, 11.5], "sma20": 10.0,
                                 "session_complete": False})
    assert gate.status is GateStatus.UNKNOWN
    assert "a forming bar is not a close" in " ".join(gate.reasons)


def test_no_average_yields_unknown_rather_than_a_pass_on_price_alone():
    assert entry_condition_gate({"recent_closes": [12.0, 11.5], "sma20": None}
                                ).status is GateStatus.UNKNOWN


# ---- the timezone shape of the stored timestamps -----------------------------------------
# AUD-QV-NAIVEUTC. Found by running the evaluator against production, not by a test: statement
# timestamps are stored naive, and subtracting one from an aware now() raises TypeError — which
# would have made every call to the endpoint a 500. These pin the normalisation.

from datetime import datetime, timezone, timedelta  # noqa: E402


def test_an_aware_now_against_a_naive_stored_timestamp_does_not_raise():
    from intel_reports.quality_value import naive_utc as _naive_utc
    aware = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    naive = datetime(2026, 9, 7, 12, 0)
    assert (_naive_utc(aware) - _naive_utc(naive)).days == 29


def test_normalising_a_naive_datetime_leaves_it_unchanged():
    from intel_reports.quality_value import naive_utc as _naive_utc
    naive = datetime(2026, 9, 7, 12, 0)
    assert _naive_utc(naive) is naive or _naive_utc(naive) == naive
    assert _naive_utc(naive).tzinfo is None


def test_the_age_is_the_same_whichever_side_carried_the_offset():
    from intel_reports.quality_value import naive_utc as _naive_utc
    a = datetime(2026, 10, 6, tzinfo=timezone.utc)
    b = datetime(2026, 10, 6)
    assert _naive_utc(a) == _naive_utc(b)
