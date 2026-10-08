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
    Gate, GateStatus, State, compose, equity_discount, ValuationMisaligned,
    REMEDY, WORK_REMAINING,
    MAX_VALUATION_ALIGNMENT_DAYS, GATE_CLAIM,
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


def test_no_work_remaining_state_is_ever_read_as_a_pass():
    """The recurring defect this platform has: a check that never fires on a missing number.
    Every state that means "we have not finished the work" must block, not just one."""
    for name in REQUIRED_FOR_ENTRY:
      for st in WORK_REMAINING:
        e = compose("MU", all_passing(**{name: g(name, st)}))
        assert e.state is not State.ENTRY_REVIEW_READY, f"{name}/{st.value} reached entry review"
        assert name in {b.name for b in e.blocking()}


def test_a_strong_gate_cannot_compensate_for_a_missing_one():
    """There is no score to outvote with, and this proves there is no back door either."""
    gates = all_passing(**{COMPETITIVE_DURABILITY: g(COMPETITIVE_DURABILITY, GateStatus.NOT_IMPLEMENTED)})
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
        COMPETITIVE_DURABILITY: g(COMPETITIVE_DURABILITY, GateStatus.NOT_IMPLEMENTED, ("nothing stored",))}))
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
        Gate(VALUATION, GateStatus.NOT_IMPLEMENTED, ())


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

def test_durability_is_not_collected_and_still_says_persistence_is_not_proof():
    """The state changed from NOT_IMPLEMENTED; the warning it carries must not be lost with it —
    margin persistence must never start reading as durability just because the branch moved."""
    gate = durability_gate()
    assert gate.status is GateStatus.NOT_COLLECTED
    joined = " ".join(gate.reasons)
    assert "equally consistent with the cycle" in joined
    assert "no durability assessment is stored" in joined


def test_an_attached_source_is_not_a_durability_assessment():
    """A source on file is not an assessment of it — the gate reads the ASSESSMENT."""
    gate = durability_gate({"sources": ["10-K item 1"]})
    assert gate.status in WORK_REMAINING
    assert gate.status is GateStatus.NOT_COLLECTED


def test_valuation_without_a_stored_assessment_stays_in_work_remaining():
    gate = valuation_gate({"market_cap": 1.0e11, "equity_value": 1.5e11})
    assert gate.status in WORK_REMAINING
    assert gate.status is GateStatus.NOT_COLLECTED


def test_todays_evidence_cannot_reach_entry_review_for_any_symbol():
    """The honest shape of the shadow dashboard: an empty eligible list is the right result."""
    e = compose("MU", [g(BUSINESS_QUALITY), durability_gate(), valuation_gate(),
                       g(ENTRY_CONDITION), g(VALUE_TRAP_RISK)])
    assert e.state is State.INSUFFICIENT_EVIDENCE
    assert {b.name for b in e.blocking()} == {COMPETITIVE_DURABILITY, VALUATION}


# ---- the two denominators, and the alignment the comparison needs ------------------------

from datetime import datetime as _dt  # noqa: E402

_V = _dt(2026, 10, 6)
_C = _dt(2026, 10, 6)


def test_discount_and_upside_are_different_numbers_from_the_same_gap():
    r = equity_discount(1.25e11, 1.0e11, value_as_of=_V, cap_as_of=_C)
    assert r["available"] is True
    assert round(r["discount_to_value"], 4) == 0.2
    assert round(r["upside_to_price"], 4) == 0.25
    assert r["discount_denominator"] != r["upside_denominator"]


def test_both_timestamps_travel_with_the_result():
    r = equity_discount(1.25e11, 1.0e11, value_as_of=_V, cap_as_of=_C)
    assert r["value_as_of"].startswith("2026-10-06")
    assert r["cap_as_of"].startswith("2026-10-06")
    assert r["alignment_days"] == 0


def test_a_cap_observed_too_long_after_the_valuation_refuses():
    """25-day-old caps are what production actually holds; the gap is not valuation."""
    r = equity_discount(1.25e11, 1.0e11, value_as_of=_V,
                        cap_as_of=_dt(2026, 9, 11))
    assert r["available"] is False and r["discount_to_value"] is None
    assert "alignment limit" in r["reason"]
    assert r["alignment_days"] == 25


def test_the_alignment_limit_is_inclusive_at_its_boundary():
    at = equity_discount(1.25e11, 1.0e11, value_as_of=_V,
                         cap_as_of=_dt(2026, 10, 6) - __import__("datetime").timedelta(
                             days=MAX_VALUATION_ALIGNMENT_DAYS))
    assert at["available"] is True


def test_an_untimestamped_side_refuses_rather_than_assuming_now():
    assert equity_discount(1.25e11, 1.0e11, value_as_of=_V, cap_as_of=None)["available"] is False
    assert equity_discount(1.25e11, 1.0e11)["available"] is False


def test_an_enterprise_value_is_refused_not_silently_compared():
    with pytest.raises(ValuationMisaligned, match="sourced debt, cash and other-claims bridge"):
        equity_discount(1.25e11, 1.0e11, value_as_of=_V, cap_as_of=_C, basis="enterprise")


def test_no_per_share_figure_is_offered_anywhere_in_the_result():
    r = equity_discount(1.25e11, 1.0e11, value_as_of=_V, cap_as_of=_C)
    assert "NOT PROVIDED" in r["per_share"]
    assert not any("per_share" in k and isinstance(v, (int, float))
                   for k, v in r.items()), "no numeric per-share value may be returned"


def test_a_non_positive_valuation_refuses_rather_than_reading_as_a_full_discount():
    r = equity_discount(-5.0e10, 1.0e11, value_as_of=_V, cap_as_of=_C)
    assert r["available"] is False and r["discount_to_value"] is None
    assert "not a 100% discount" in r["reason"]
    assert equity_discount(0, 1.0e11, value_as_of=_V, cap_as_of=_C)["available"] is False


def test_a_missing_input_yields_unavailable_rather_than_a_zero_discount():
    """A zero discount sorts as 'fairly valued'; unavailable must not sort at all."""
    r = equity_discount(None, 1.0e11, value_as_of=_V, cap_as_of=_C)
    assert r["available"] is False and r["discount_to_value"] is None
    assert "not a zero discount" in r["reason"]


# ---- what a gate verdict is allowed to mean ----------------------------------------------

def test_every_gate_declares_what_a_pass_does_not_establish():
    for name in ALL_GATES:
        assert GATE_CLAIM[name]["does_not_establish"], name


def test_a_fundamentals_pass_is_not_a_quality_endorsement():
    d = Gate(BUSINESS_QUALITY, GateStatus.PASS).as_dict()
    assert d["label"] == "Fundamental checks"
    assert "passed the configured completeness and freshness checks" in d["establishes"]
    assert "that this is a high-quality business" in d["does_not_establish"]


def test_an_entry_condition_pass_is_not_a_suitable_entry():
    d = Gate(ENTRY_CONDITION, GateStatus.PASS).as_dict()
    assert d["label"] == "Price-stabilization rule"
    assert "that the stock is a suitable entry" in d["does_not_establish"]


def test_a_non_passing_gate_establishes_nothing():
    d = Gate(VALUATION, GateStatus.NOT_IMPLEMENTED, ("nothing stored",)).as_dict()
    assert d["establishes"] is None
    assert d["does_not_establish"]


def test_reaching_entry_review_says_what_it_is_not():
    e = compose("MU", all_passing())
    assert "NOT that this is a suitable investment" in " ".join(e.explanation)


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
    assert gate.status in WORK_REMAINING
    assert "at least two are needed" in " ".join(gate.reasons)


def test_a_stale_retrieval_blocks_even_though_the_reported_year_is_current():
    """MU's two ages are different facts; this is the one a refetch closes."""
    gate = business_quality_gate(_bq(retrieval_age_days=MAX_RETRIEVAL_AGE_DAYS + 1))
    assert gate.status in WORK_REMAINING
    assert "last retrieved" in " ".join(gate.reasons)


def test_a_missing_reported_year_blocks_even_though_retrieval_is_fresh():
    """And this is the one only the issuer closes."""
    gate = business_quality_gate(_bq(reported_year_age_days=MAX_REPORTED_YEAR_AGE_DAYS + 1))
    assert gate.status in WORK_REMAINING
    # Wording withdrawn after checking EDGAR; see the dedicated test at the end of this file.
    assert "beyond one reporting year" in " ".join(gate.reasons)


def test_an_absent_retrieval_time_is_not_treated_as_fresh():
    gate = business_quality_gate(_bq(retrieval_age_days=None))
    assert gate.status in WORK_REMAINING


def test_value_trap_is_never_a_pass_when_only_two_classes_were_observable():
    """PASS here would read as 'no value trap' when only two classes were observable."""
    gate = value_trap_gate({"net_debt_to_equity": 0.1, "free_cashflow": 1.0e9})
    assert gate.status in WORK_REMAINING and gate.status is not GateStatus.PASS
    joined = " ".join(gate.reasons)
    assert "not as a verdict" in joined
    # The computed figures survive the routing change — they are observations, not a verdict.
    assert "net debt is 0.10x equity" in joined


# AUD-QV-UNVALIDATEDTHRESHOLD. The first version blocked on net debt > 2x equity or two
# negative free-cash-flow years and flagged 48 of 200 production companies as a thesis at risk.
# The list refuted it: LMT (FCF $6.9bn and rising, ratio raised by buybacks), CM (a bank),
# VST/CWEN/NATL (utilities), ORCL (capex). These pin the retraction.

def test_a_profitable_company_is_not_blocked_by_a_leverage_ratio():
    """LMT's real figures: the ratio rises because buybacks shrink the denominator."""
    gate = value_trap_gate({"net_debt_to_equity": 2.62, "free_cashflow": 6.908e9,
                            "free_cashflow_prior": 5.287e9, "industry": "Aerospace & Defense"})
    assert gate.status in WORK_REMAINING
    assert "no threshold on this ratio has been validated" in " ".join(gate.reasons)


def test_leverage_is_not_interpreted_at_all_for_a_bank():
    gate = value_trap_gate({"net_debt_to_equity": 2.68, "industry": "Banks - Diversified"})
    assert gate.status in WORK_REMAINING
    joined = " ".join(gate.reasons)
    assert "not a solvency reading" in joined
    assert "no threshold on this ratio has been validated" not in joined


def test_two_negative_cash_flow_years_are_an_observation_not_a_verdict():
    """ORCL's real shape: operating cash flow spent on capacity, not distress."""
    gate = value_trap_gate({"free_cashflow": -2.3686e10, "free_cashflow_prior": -3.94e8,
                            "industry": "Software - Infrastructure"})
    assert gate.status in WORK_REMAINING
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
                            "free_cashflow_prior": -1.0}).status in WORK_REMAINING


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
    assert gate.status in WORK_REMAINING
    assert "a forming bar is not a close" in " ".join(gate.reasons)


def test_no_average_yields_unknown_rather_than_a_pass_on_price_alone():
    assert entry_condition_gate({"recent_closes": [12.0, 11.5], "sma20": None}
                                ).status in WORK_REMAINING


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


# ---- "No evidence" was hiding five different problems ------------------------------------
# The review's point: MU's stale annual history and a durability assessment that has never been
# built rendered as the identical yellow badge, so a reader could not tell "refresh a job" from
# "build an analysis" — nor could either be read as "no relevant information exists".

def test_every_state_declares_what_would_close_it():
    for st in GateStatus:
        assert st in REMEDY
        if st is not GateStatus.PASS:
            assert REMEDY[st], st


def test_a_finding_about_the_company_is_not_listed_as_work_remaining():
    """BLOCKED, FAIL and NOT_APPLICABLE are conclusions; counting them as gaps would
    misreport an unfinished pipeline as a universe of poor businesses, or the reverse."""
    for st in (GateStatus.BLOCKED, GateStatus.FAIL, GateStatus.NOT_APPLICABLE, GateStatus.PASS):
        assert st not in WORK_REMAINING


def test_stale_data_and_an_unresearched_company_are_different_states():
    """MU's real shape: 401 days since the newest stored annual period. UPDATED: durability
    with no stored assessment is NOT_COLLECTED, not NOT_IMPLEMENTED — the capability exists."""
    stale = business_quality_gate(_bq(reported_year_age_days=500))
    unresearched = durability_gate()
    assert stale.status is GateStatus.STALE
    assert unresearched.status is GateStatus.NOT_COLLECTED
    assert stale.status is not unresearched.status
    assert REMEDY[stale.status] != REMEDY[unresearched.status]


def test_stale_says_refresh_and_unbuilt_names_both_halves():
    """WITHDRAWN: "Build the assessment — no ingestion closes this". That was half the story.
    These gates need sourced evidence AND an assessment that reads it; ingestion alone is
    insufficient, not irrelevant, and the remedy must not send a reader looking for only one."""
    assert "Refresh" in REMEDY[GateStatus.STALE]
    r = REMEDY[GateStatus.NOT_IMPLEMENTED]
    assert "sourced evidence" in r and "implemented assessment" in r
    assert "no ingestion closes this" not in r


def test_an_optional_gate_that_did_not_run_has_its_own_state():
    """Catalysts and portfolio fit showed "0 ≠ 200": the row read as 200 missing companies when
    the gate simply had not run. A declared absence is not a missing result."""
    assert GateStatus.NOT_ASSESSED.value == "not_assessed"
    from intel_reports.quality_value import status_catalog
    e = status_catalog()["not_assessed"]
    assert e["label"] == "Not assessed"
    assert e["is_pass"] is False


def test_a_missing_figure_is_not_collected_rather_than_stale():
    """Different remedies: one is a refresh job, the other is collecting a field at all."""
    gate = business_quality_gate(_bq(revenue=None))
    assert gate.status is GateStatus.NOT_COLLECTED


def test_too_few_periods_is_insufficient_not_a_data_gap():
    """Two annuals is a real, current dataset that cannot answer the question asked of it."""
    assert business_quality_gate(_bq(annual_periods=1)).status is GateStatus.INSUFFICIENT


def test_a_forming_bar_is_insufficient_now_not_permanently_missing():
    gate = entry_condition_gate({"recent_closes": [12.0, 11.5], "sma20": 10.0,
                                 "session_complete": False})
    assert gate.status is GateStatus.INSUFFICIENT


def test_valuation_with_no_stored_assessment_is_not_collected():
    """SUPERSEDED. This separated an unbuilt model from an uncollected market cap, which was
    the right distinction while the gate computed its own value. The gate now READS a stored
    assessment, so the question is whether one exists for this issuer — an evidence gap."""
    g = valuation_gate({})
    assert g.status is GateStatus.NOT_COLLECTED
    assert "what is missing is the research for this company" in " ".join(g.reasons)


# ---- the catalog the page renders from ----------------------------------------------------
# AUD-QV-FRONTENDSTATES. The page kept its own five-entry status map and defaulted anything
# else to "No evidence". When this enum grew to nine, all four new states rendered as the one
# label they were introduced to stop saying, and the coverage columns — a hardcoded four —
# summed to zero for every gate reporting a new state. One served mapping, exhaustive over the
# enum, is the fix; these pin it.

def test_the_catalog_covers_every_state_with_no_gaps():
    from intel_reports.quality_value import status_catalog
    cat = status_catalog()
    assert set(cat) == {s.value for s in GateStatus}
    for st in GateStatus:
        e = cat[st.value]
        assert e["label"] and isinstance(e["label"], str)
        assert e["is_pass"] is (st is GateStatus.PASS)
        assert e["is_work_remaining"] is (st in WORK_REMAINING)


def test_every_state_has_a_distinct_label():
    """Two states sharing a label is the defect restated, just at a different layer."""
    from intel_reports.quality_value import status_catalog
    labels = [e["label"] for e in status_catalog().values()]
    assert len(set(labels)) == len(labels), f"duplicate labels: {labels}"


def test_no_state_is_labelled_no_evidence():
    """The phrase that was hiding five different problems must not reappear as a label."""
    from intel_reports.quality_value import status_catalog
    for v in status_catalog().values():
        assert "no evidence" not in v["label"].lower()


def test_the_catalog_is_derived_from_the_enum_so_a_new_state_cannot_be_missed():
    """Exhaustive BY CONSTRUCTION: adding a member must not require editing the catalog."""
    import inspect
    from intel_reports import quality_value as qv
    src = inspect.getsource(qv.status_catalog)
    assert "for st in GateStatus" in src, \
        "the catalog must iterate the enum, not enumerate states by hand"


def test_the_stale_reason_no_longer_claims_what_the_issuer_has_filed():
    """WITHDRAWN: 'a later fiscal year has almost certainly been reported and is absent'.
    Checked against EDGAR — MU's newest 10-K is still FY2025, so no later ANNUAL REPORT had
    been filed. The age is a fact; what has since been filed is a different question."""
    gate = business_quality_gate(_bq(reported_year_age_days=402))
    joined = " ".join(gate.reasons)
    assert "almost certainly" not in joined
    assert "402 days ago" in joined
    assert "is not checked here" in joined


# ---- connected assessments: the research the evaluator reads -------------------------------
# The gap this closes: the MU/CRDO findings lived in a document beside the dashboard, so the
# evaluator said `not_implemented` however much work had been done. The rule that matters is
# that connecting one must NOT mean the gate passes.

from intel_reports.quality_value import from_assessment, VERDICT_STATUS  # noqa: E402
from intel_reports.assessment_seed import ASSESSMENTS, RISK_CLASSES  # noqa: E402


def _asmt(**over):
    a = {"verdict": "insufficient", "summary": "a summary",
         "findings": [{"claim": "a claim", "source": "a 10-K",
                       "counterevidence": "the case against"}],
         "not_assessed": ["litigation"], "version": 1, "cutoff": "2026-10-07T00:00:00"}
    a.update(over)
    return a


def test_a_completed_assessment_that_found_insufficient_evidence_still_blocks():
    """Finished work and an unmet gate are not in tension — this is the central rule."""
    g = from_assessment(COMPETITIVE_DURABILITY, _asmt(verdict="insufficient"), absent_reasons=())
    assert g.status is GateStatus.INSUFFICIENT
    assert g.status is not GateStatus.PASS
    e = compose("X", all_passing(**{COMPETITIVE_DURABILITY: g}))
    assert e.state is State.INSUFFICIENT_EVIDENCE


def test_only_a_supported_verdict_passes():
    for verdict, expected in VERDICT_STATUS.items():
        g = from_assessment(VALUATION, _asmt(verdict=verdict), absent_reasons=())
        assert g.status is expected, verdict
    assert sum(1 for v in VERDICT_STATUS.values() if v is GateStatus.PASS) == 1


def test_context_only_does_not_pass_because_context_is_not_a_conclusion():
    """MU's valuation: three multiples at three anchors is not a value estimate."""
    assert VERDICT_STATUS["context_only"] is GateStatus.INSUFFICIENT


def test_a_contradicted_verdict_fails_rather_than_reading_as_a_gap():
    g = from_assessment(VALUE_TRAP_RISK, _asmt(verdict="contradicted"), absent_reasons=())
    assert g.status is GateStatus.FAIL


def test_an_unrecognised_verdict_is_never_silently_a_pass():
    g = from_assessment(VALUATION, _asmt(verdict="looks_great"), absent_reasons=())
    assert g.status is GateStatus.INSUFFICIENT
    assert "unrecognised verdict" in " ".join(g.reasons)
    assert "NOT treated as a pass" in " ".join(g.reasons)


def test_no_assessment_connected_reports_not_collected():
    """SUPERSEDED: this asserted NOT_IMPLEMENTED. Once the gate can read a stored assessment,
    a company without one has an evidence gap, not a software gap — see the dedicated test
    below for why that distinction matters to a reader."""
    g = from_assessment(VALUATION, None, absent_reasons=("nothing is connected",))
    assert g.status is GateStatus.NOT_COLLECTED


def test_a_connected_finding_carries_its_source_and_counterevidence():
    g = from_assessment(COMPETITIVE_DURABILITY, _asmt(), absent_reasons=())
    joined = " ".join(g.reasons)
    assert "[source: a 10-K]" in joined and "against: the case against" in joined


def test_what_was_not_assessed_travels_with_the_verdict():
    g = from_assessment(VALUE_TRAP_RISK, _asmt(), absent_reasons=())
    assert "NOT YET EXAMINED: litigation" in " ".join(g.reasons)


def test_the_assessment_version_and_cutoff_reach_the_gate_evidence():
    g = from_assessment(VALUATION, _asmt(version=3), absent_reasons=())
    assert g.evidence["assessment_version"] == 3
    assert g.evidence["assessment_cutoff"] == "2026-10-07T00:00:00"


# ---- the seeded MU and CRDO research -------------------------------------------------------

def test_every_seeded_assessment_has_a_recognised_verdict():
    for a in ASSESSMENTS:
        assert a["verdict"] in VERDICT_STATUS, (a["symbol"], a["dimension"], a["verdict"])


def test_no_seeded_assessment_passes_its_gate():
    """All six are completed work whose answer is still 'not enough'. If one ever passes, that
    must be a deliberate change, not a side effect of editing prose."""
    for a in ASSESSMENTS:
        assert VERDICT_STATUS[a["verdict"]] is not GateStatus.PASS, a["dimension"]


def test_both_companies_cover_all_three_disconnected_dimensions():
    by = {(a["symbol"], a["dimension"]) for a in ASSESSMENTS}
    for sym in ("MU", "CRDO"):
        for dim in (COMPETITIVE_DURABILITY, VALUATION, VALUE_TRAP_RISK):
            assert (sym, dim) in by, (sym, dim)


def test_every_finding_cites_a_source_and_states_its_counterevidence():
    for a in ASSESSMENTS:
        for f in a["findings"]:
            assert f.get("source"), (a["symbol"], a["dimension"], f.get("claim"))
            assert f.get("counterevidence"), (a["symbol"], a["dimension"], f.get("claim"))


def test_every_assessment_declares_what_it_did_not_assess():
    for a in ASSESSMENTS:
        assert a.get("not_assessed"), (a["symbol"], a["dimension"])


def test_every_computed_assumption_states_its_basis_and_sensitivity():
    for a in ASSESSMENTS:
        for k in (a.get("assumptions") or []):
            assert k.get("basis"), (a["symbol"], k.get("name"))
            assert k.get("sensitivity"), (a["symbol"], k.get("name"))


def test_crdo_dilution_endpoints_are_labelled_treatments_not_an_interval():
    """They do not resolve whether dilution double-counts GAAP stock compensation."""
    val = next(a for a in ASSESSMENTS
               if a["symbol"] == "CRDO" and a["dimension"] == VALUATION)
    joined = " ".join(f.get("counterevidence", "") for f in val["findings"])
    assert "NOT A CONFIDENCE INTERVAL" in joined
    assert "SEPARATE MODELLING TREATMENTS" in joined


def test_mu_valuation_is_context_only_and_says_the_run_rate_is_not_a_forecast():
    val = next(a for a in ASSESSMENTS if a["symbol"] == "MU" and a["dimension"] == VALUATION)
    assert val["verdict"] == "context_only"
    joined = " ".join(f.get("counterevidence", "") for f in val["findings"])
    assert "MECHANICAL RUN-RATE ILLUSTRATION, not a forecast" in joined


def test_the_risk_reviews_account_for_every_declared_class():
    for sym, expect in (("MU", 12), ("CRDO", 12)):
        a = next(x for x in ASSESSMENTS
                 if x["symbol"] == sym and x["dimension"] == VALUE_TRAP_RISK)
        e = a["evidence"]
        total = (e.get("complete", 0) + e.get("partial", 0)
                 + e.get("not_applicable", 0) + e.get("not_assessed", 0))
        assert e["classes_total"] == expect == len(RISK_CLASSES)
        assert total == expect, f"{sym}: {total} of {expect}"


def test_a_connected_assessment_supplies_its_own_remedy():
    """A completed durability review must not be told "more periods are needed" — the generic
    remedy for its status. It should name what IT left unassessed."""
    g = from_assessment(COMPETITIVE_DURABILITY,
                        _asmt(not_assessed=["switching costs", "patent strength"]),
                        absent_reasons=())
    assert "Examine: switching costs" in g.remedy
    assert g.as_dict()["remedy"] == g.remedy
    assert REMEDY[g.status] not in g.remedy


def test_a_long_outstanding_list_is_summarised_not_truncated_silently():
    g = from_assessment(VALUE_TRAP_RISK,
                        _asmt(not_assessed=["a", "b", "c", "d", "e"]), absent_reasons=())
    assert "(+2 more)" in g.remedy


def test_an_assessment_naming_nothing_outstanding_says_so_rather_than_inventing_one():
    g = from_assessment(VALUATION, _asmt(not_assessed=[]), absent_reasons=())
    assert "names nothing outstanding" in g.remedy


def test_a_passing_gate_carries_no_remedy():
    g = from_assessment(VALUATION, _asmt(verdict="supported"), absent_reasons=())
    assert g.status is GateStatus.PASS and g.remedy == ""
    assert g.as_dict()["remedy"] is None


def test_every_seeded_assessment_produces_an_actionable_remedy():
    for a in ASSESSMENTS:
        g = from_assessment(a["dimension"], a, absent_reasons=())
        assert g.remedy.startswith("Examine:"), (a["symbol"], a["dimension"], g.remedy)


# ---- a missing RECORD is not a missing CAPABILITY -----------------------------------------
# Once the gate reads a stored assessment, a company without one has an evidence gap. Reporting
# it as NOT_IMPLEMENTED described a coverage backlog as an engineering backlog, and would send a
# reader to build something that already exists.

def test_a_company_without_an_assessment_is_not_collected_not_unimplemented():
    g = from_assessment(VALUATION, None, absent_reasons=())
    assert g.status is GateStatus.NOT_COLLECTED
    assert g.status is not GateStatus.NOT_IMPLEMENTED
    joined = " ".join(g.reasons)
    assert "The assessment capability exists" in joined
    assert "what is missing is the research for this company" in joined


def test_not_implemented_stays_reserved_for_an_absent_capability():
    """Still the right state where no assessment path exists at all — it must not become a
    synonym for 'this company has not been researched'."""
    assert REMEDY[GateStatus.NOT_IMPLEMENTED] != REMEDY[GateStatus.NOT_COLLECTED]
    assert "implemented assessment" in REMEDY[GateStatus.NOT_IMPLEMENTED]
    assert "for this issuer" in REMEDY[GateStatus.NOT_COLLECTED]


# ---- the unresolved question, which is what actually blocks the gate -----------------------

def test_an_assessment_states_the_one_question_blocking_its_gate():
    g = from_assessment(COMPETITIVE_DURABILITY,
                        _asmt(unresolved="are the two >10% customers locked in?"),
                        absent_reasons=())
    assert "UNRESOLVED: are the two >10% customers locked in?" in " ".join(g.reasons)


def test_every_seeded_assessment_names_its_unresolved_question():
    for a in ASSESSMENTS:
        q = a.get("unresolved") or ""
        assert q.endswith("?") or "?" in q, (a["symbol"], a["dimension"], q)
        assert len(q) > 40, (a["symbol"], a["dimension"])


def test_the_unresolved_question_differs_per_company_and_dimension():
    qs = [a["unresolved"] for a in ASSESSMENTS]
    assert len(set(qs)) == len(qs), "a shared question would be a template, not a finding"


def test_a_gate_with_no_assessment_never_reaches_the_legacy_branch():
    """AUD-QV-UNREACHABLE. The NOT_COLLECTED branch was added inside `if assessment is not
    None`, so it could only run when one EXISTED — and the 198 companies without a record kept
    falling through to the legacy path reporting NOT_IMPLEMENTED. The defect survived a full
    test run and was found by reading the deployed payload. These pin the routing."""
    for gate in (durability_gate(), valuation_gate(), value_trap_gate()):
        assert gate.status is GateStatus.NOT_COLLECTED, gate.name
        assert "what is missing is the research for this company" in " ".join(gate.reasons)


def test_the_legacy_not_implemented_wording_is_unreachable_from_these_gates():
    """If any of them can still report NOT_IMPLEMENTED, the routing regressed."""
    for ev in ({}, {"sources": ["x"]}, {"market_cap": 1.0, "equity_value": 2.0},
               {"net_debt_to_equity": 0.1, "free_cashflow": 1.0}):
        for fn in (durability_gate, valuation_gate, value_trap_gate):
            assert fn(ev).status is not GateStatus.NOT_IMPLEMENTED, (fn.__name__, ev)


# ---- a gap that cannot be closed by research is not a research task -----------------------

from intel_reports.quality_value import (  # noqa: E402
    normalise_gaps, company_summary, NOT_EXAMINED, NOT_PUBLICLY_DISCLOSED)


def test_a_plain_string_gap_defaults_to_not_yet_examined():
    """The safer default: it says work remains rather than asserting something is unknowable."""
    g = normalise_gaps(["litigation"])[0]
    assert g["status"] == NOT_EXAMINED and g["label"] == "not yet examined"


def test_an_undisclosed_item_is_labelled_differently_from_an_unexamined_one():
    gaps = normalise_gaps([{"item": "contract terms", "status": NOT_PUBLICLY_DISCLOSED},
                           {"item": "auditor opinion"}])
    assert gaps[0]["label"] == "not publicly disclosed"
    assert gaps[1]["label"] == "not yet examined"
    assert gaps[0]["label"] != gaps[1]["label"]


def test_the_remedy_never_sends_a_reader_after_an_undisclosed_term():
    """Advice that cannot be taken is worse than none. MU's Strategic Customer Agreement terms
    are referenced by management and filed nowhere; no amount of effort closes that."""
    g = from_assessment(COMPETITIVE_DURABILITY, _asmt(not_assessed=[
        {"item": "Strategic Customer Agreement terms", "status": NOT_PUBLICLY_DISCLOSED}]),
        absent_reasons=())
    assert "Nothing here is closable by research" in g.remedy
    assert "not publicly disclosed" in g.remedy
    assert not g.remedy.startswith("Examine:")


def test_a_mixed_gap_list_separates_the_two():
    g = from_assessment(VALUE_TRAP_RISK, _asmt(not_assessed=[
        {"item": "litigation"},
        {"item": "contract terms", "status": NOT_PUBLICLY_DISCLOSED}]), absent_reasons=())
    assert "Examine: litigation" in g.remedy
    assert "Not closable by research: contract terms" in g.remedy


def test_mu_durability_records_the_agreement_terms_as_undisclosed():
    a = next(x for x in ASSESSMENTS
             if x["symbol"] == "MU" and x["dimension"] == COMPETITIVE_DURABILITY)
    gaps = {g["item"]: g["status"] for g in normalise_gaps(a["not_assessed"])}
    assert gaps["Strategic Customer Agreement terms"] == NOT_PUBLICLY_DISCLOSED
    assert gaps["HBM qualification switching costs"] == NOT_EXAMINED


# ---- observed is not modelled --------------------------------------------------------------

def test_every_assumption_declares_whether_it_is_observed_or_modelled():
    for a in ASSESSMENTS:
        for k in (a.get("assumptions") or []):
            assert k.get("kind") in ("observed", "modelled"), (a["symbol"], k.get("name"))


def test_crdos_dilution_is_split_into_what_happened_and_what_is_projected():
    """The past share-count growth is observed; extending it forward is an assumption, and an
    earlier version labelled the whole thing 'OBSERVED, not assumed'."""
    val = next(a for a in ASSESSMENTS
               if a["symbol"] == "CRDO" and a["dimension"] == VALUATION)
    by = {k["name"]: k for k in val["assumptions"]}
    assert by["dilution, historical"]["kind"] == "observed"
    assert by["dilution, projected"]["kind"] == "modelled"
    assert "that it CONTINUES is an assumption" in by["dilution, projected"]["basis"]


# ---- the conclusion, above the detail ------------------------------------------------------

def _connected_gates():
    by = {(a["symbol"], a["dimension"]): a for a in ASSESSMENTS}
    return [Gate(BUSINESS_QUALITY, GateStatus.PASS),
            from_assessment(COMPETITIVE_DURABILITY, by[("MU", COMPETITIVE_DURABILITY)],
                            absent_reasons=()),
            from_assessment(VALUATION, by[("MU", VALUATION)], absent_reasons=()),
            Gate(ENTRY_CONDITION, GateStatus.PASS),
            from_assessment(VALUE_TRAP_RISK, by[("MU", VALUE_TRAP_RISK)], absent_reasons=())]


def test_the_summary_answers_the_four_questions():
    gates = _connected_gates()
    sm = company_summary(compose("MU", gates), gates)
    assert sm["supports"] and all(x["claim"] and x["source"] for x in sm["supports"])
    assert sm["unresolved"] and all(x["question"].endswith("?") or "?" in x["question"]
                                   for x in sm["unresolved"])
    assert sm["why_not_entry_ready"].startswith("Not entry ready:")
    assert sm["next_research"]


def test_a_supporting_claim_always_carries_its_counterevidence():
    gates = _connected_gates()
    sm = company_summary(compose("MU", gates), gates)
    assert all(x.get("against") for x in sm["supports"]), \
        "a summary that drops the case against is the overclaim this layer exists to prevent"


def test_the_summary_separates_research_tasks_from_permanent_limits():
    gates = _connected_gates()
    sm = company_summary(compose("MU", gates), gates)
    items = {x["item"] for x in sm["not_closable_by_research"]}
    assert "Strategic Customer Agreement terms" in items
    assert "Strategic Customer Agreement terms" not in {x["item"] for x in sm["next_research"]}


def test_the_summary_names_which_gates_block_and_in_what_state():
    gates = _connected_gates()
    sm = company_summary(compose("MU", gates), gates)
    for word in ("competitive durability", "valuation discount", "value-trap risk"):
        assert word in sm["why_not_entry_ready"].lower(), word
    assert "insufficient" in sm["why_not_entry_ready"]


def test_the_summary_is_bounded_so_it_does_not_become_the_detail_again():
    gates = _connected_gates()
    sm = company_summary(compose("MU", gates), gates)
    assert len(sm["supports"]) <= 3 and len(sm["next_research"]) <= 4


def test_a_company_with_no_assessment_gets_a_summary_that_says_so():
    gates = all_passing(**{VALUATION: g(VALUATION, GateStatus.NOT_COLLECTED, ("none stored",))})
    sm = company_summary(compose("X", gates), gates)
    assert sm["supports"] == [] and sm["unresolved"] == []
    assert "not collected" in sm["why_not_entry_ready"]


# ---- AUD-QV-REUSEUNREPORTED -----------------------------------------------------------------
# I claimed a previous commit made a reused evaluation name its id and original cutoff, and
# quoted an example of the rendering. The edit had silently failed to apply — I used a string
# replacement without asserting it matched, then described the result rather than checking it.
# The reviewer caught it from a screenshot. These pin the shape so the claim is testable.

def test_the_route_reports_a_reused_evaluation_with_its_id_and_original_cutoff():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "src" / "api"
           / "quality_value_routes.py").read_text()
    seg = src[src.index("persisted = {"):src.index("except Exception as exc:")]
    for key in ('"reused"', '"original_cutoff"'):
        assert key in seg, f"{key} missing from the persisted payload"
    assert "reused.append(" in src, "a reused row must be collected for the response"
    assert '"reused_evaluations": reused' in src, "and returned to the page"


def test_the_page_renders_the_reused_evaluation_details():
    import pathlib
    page = (pathlib.Path(__file__).resolve().parents[3] / "frontend" / "src" / "pages"
            / "quality-value.tsx").read_text()
    assert "data.reused_evaluations" in page
    assert "nothing was rewritten" in page
    assert "r.id" in page and "r.cutoff" in page


# =============================================================================================
# AUD-QV-APPLICABILITY (2026-10-08). GLD was shown as "Fund (inferred)" on the same screen that
# reported it as missing annual statements and lacking a durable moat, with "collect the
# evidence" as its next research task. A gold trust holds no operating business: "no moat" there
# is not a weak finding, it is a finding about the wrong subject, and the backlog is not work.
# =============================================================================================
from intel_reports.quality_value import (assessment_applicability, company_summary,  # noqa: E402
                                         APPLICABILITY_APPLIES, APPLICABILITY_UNVERIFIED,
                                         APPLICABILITY_NOT_APPLICABLE, FUND_ANALYSIS_REQUIRED)


def test_an_inferred_fund_is_unverified_not_asserted_not_applicable():
    """Calling it not-applicable would assert the very type the classifier refuses to assert."""
    got = assessment_applicability({"type": "fund", "confidence": "inferred",
                                    "basis": "the NAME identifies a pooled vehicle"})
    assert got["status"] == APPLICABILITY_UNVERIFIED
    assert "Company assessment applicability unverified" in got["note"]


def test_an_inferred_fund_moves_the_company_research_backlog_to_conditional():
    got = assessment_applicability({"type": "fund", "confidence": "inferred"})
    assert got["company_research_applicable"] is False
    assert got["fund_analysis_required"] == FUND_ANALYSIS_REQUIRED
    assert "holdings" in " ".join(got["fund_analysis_required"])


def test_an_inferred_operating_company_keeps_its_backlog_but_says_it_is_unverified():
    """The research tasks are still the right tasks IF it is a company; the caveat carries the
    uncertainty without emptying the list."""
    got = assessment_applicability({"type": "operating_company", "confidence": "inferred"})
    assert got["status"] == APPLICABILITY_UNVERIFIED
    assert got["company_research_applicable"] is True


def test_suppression_requires_positive_evidence_not_an_unknown_type():
    """ABSENCE NEVER IMPLIES FUND. Suppressing on an unknown type would repeat the exact error
    the classifier exists to avoid, and would silently empty the backlog for every company."""
    for instrument in (None, {}, {"type": "unverified", "confidence": "inferred"}):
        got = assessment_applicability(instrument)
        assert got["company_research_applicable"] is True, instrument
        assert got["status"] == APPLICABILITY_UNVERIFIED


def test_a_declared_fund_is_not_applicable_and_names_what_it_would_need():
    got = assessment_applicability({"type": "fund", "confidence": "declared"})
    assert got["status"] == APPLICABILITY_NOT_APPLICABLE
    assert "not an operating business" in got["note"]
    assert "fund analysis" in got["note"]


def test_only_a_declared_company_reaches_applies():
    assert assessment_applicability(
        {"type": "operating_company", "confidence": "declared"})["status"] \
        == APPLICABILITY_APPLIES


def test_the_summary_carries_applicability_and_splits_the_backlog(monkeypatch):
    """The whole point is that nothing is HIDDEN: the items still travel, under a key that says
    what they are conditional on."""
    class _G:
        name = "competitive_durability"
        evidence = {"verdict": "insufficient",
                    "not_assessed": ["customer design-cycle lock-in", "patent portfolio"]}
    class _Ev:
        def blocking(self): return []
        def as_dict(self): return {}
    fund = company_summary(_Ev(), [_G()], instrument={"type": "fund", "confidence": "inferred"})
    assert fund["next_research"] == []
    assert len(fund["next_research_conditional"]) == 2, "carried, not discarded"
    assert fund["applicability"]["status"] == APPLICABILITY_UNVERIFIED

    co = company_summary(_Ev(), [_G()],
                         instrument={"type": "operating_company", "confidence": "inferred"})
    assert len(co["next_research"]) == 2 and co["next_research_conditional"] == []
