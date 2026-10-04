"""N1 offline acceptance: frozen packets, eligibility, and what a narrator may not say.

NOTHING HERE CALLS A MODEL. Every "draft" is a hand-written sentence standing for one a narrator
could plausibly produce — including the plausible-and-wrong ones, which are the point.
"""
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))

from intelligence.evidence_packet import (  # noqa: E402
    Claim, PACKET_VERSION, build_packet, compute_eligibility, packet_hash)
from intelligence.narration_validator import validate  # noqa: E402

DET = "Deterministic fallback text."


def _f(value, state="OK", **kw):
    return {"value": value, "state": state, **kw}


def _mu_fields(**over):
    f = {
        "fiscal_period": _f({"label": "FY2026 Q4", "period_end": "2026-09-03"}),
        "official_figures": _f({"revenue": {"value": 54230000000.0, "units": "USD",
                                            "basis": "GAAP"},
                                "eps_adjusted": {"value": 33.42, "units": "USD/share",
                                                 "basis": "non-GAAP adjusted"}}),
        "revenue_actual": _f({"value": 54230000000.0, "units": "USD", "basis": "GAAP"}),
        "eps_actual": _f({"value": 33.42, "units": "USD/share", "basis": "non-GAAP adjusted"}),
        "eps_expectation": _f({"value": 31.818, "is_frozen": False}),
        "accounting_basis": _f({"stated_by_issuer_per_metric": {"revenue": "GAAP"},
                                "estimate_basis": "UNKNOWN"}),
        "guidance_current": _f({"guidance_q1_revenue": {"value": 61500000000.0}}),
        "guidance_change": _f({"current_guidance_available": True,
                               "comparison": "not established"}, state="UNKNOWN"),
        "return_1d": _f({"pct": 3.03, "window": "2026-09-29 close to 2026-10-01 close"}),
        "three_verdicts": _f({"forward_outlook": "whether it is a RAISE is NOT established"}),
    }
    f.update(over)
    return f


def _report(fields, rid=12, version=1):
    return SimpleNamespace(
        id=rid, version=version, subject_key="earnings:MU:2026-09-30",
        report_type="post_earnings", contract_version=3, policy_version="3",
        cutoff_at=datetime(2026, 10, 4, 3, 54), generated_at=datetime(2026, 10, 4, 3, 54),
        payload={"fields": fields, "evidence": {"issuer_document:1": {"source": "sec.gov"}}})


# ------------------------------------------------------------------ 1. immutable packets

def test_a_packet_is_pinned_to_its_report_cutoff_and_contract():
    p = build_packet(_report(_mu_fields()))
    assert p.packet_version == PACKET_VERSION
    assert p.report_id == 12 and p.report_version == 1
    assert p.contract_version == 3 and p.policy_version == "3"
    assert p.cutoff_at == "2026-10-04T03:54:00", "the moment the report could see"
    assert p.packet_hash.startswith("sha256-packet:")


def test_the_same_report_always_hashes_the_same_and_a_changed_field_does_not():
    a = build_packet(_report(_mu_fields()))
    b = build_packet(_report(_mu_fields()))
    assert a.packet_hash == b.packet_hash
    changed = _mu_fields(revenue_actual=_f({"value": 54250000000.0, "units": "USD"}))
    assert build_packet(_report(changed)).packet_hash != a.packet_hash


def test_a_packet_carries_the_evidence_needed_to_resolve_its_own_citations():
    p = build_packet(_report(_mu_fields()))
    assert "issuer_document:1" in p.evidence, "a citation must be resolvable offline"


def test_the_hash_covers_eligibility_so_rules_cannot_change_underneath_a_narrative():
    base = build_packet(_report(_mu_fields()))
    # Same fields, but an established estimate basis makes COMPARISON eligible.
    other = _mu_fields(accounting_basis=_f({"estimate_basis": "non-GAAP adjusted"}))
    assert build_packet(_report(other)).packet_hash != base.packet_hash


# ------------------------------------------------------------------ 2. structured eligibility

def test_a_comparison_needs_a_comparable_basis_not_merely_two_numbers():
    e = {x.claim: x for x in compute_eligibility(_mu_fields())}
    c = e[Claim.COMPARISON]
    assert c.allowed is False
    assert "NOT a validated beat or miss" in c.reason
    # Supply the estimate's basis and it becomes eligible.
    ok = compute_eligibility(_mu_fields(
        accounting_basis=_f({"estimate_basis": "non-GAAP adjusted"})))
    assert {x.claim: x for x in ok}[Claim.COMPARISON].allowed is True


def test_guidance_level_is_eligible_while_guidance_change_is_not():
    e = {x.claim: x for x in compute_eligibility(_mu_fields())}
    assert e[Claim.GUIDANCE_LEVEL].allowed is True
    assert e[Claim.GUIDANCE_CHANGE].allowed is False
    assert "prior" in e[Claim.GUIDANCE_CHANGE].reason.lower()


def test_a_causal_claim_is_never_eligible_however_complete_the_evidence():
    full = _mu_fields(accounting_basis=_f({"estimate_basis": "non-GAAP adjusted"}),
                      prior_guidance=_f({"value": 60000000000.0}))
    e = {x.claim: x for x in compute_eligibility(full)}
    assert e[Claim.CAUSAL].allowed is False
    assert "counterfactual" in e[Claim.CAUSAL].reason


def test_a_price_reaction_needs_a_named_window():
    assert {x.claim: x for x in compute_eligibility(_mu_fields())}[
        Claim.PRICE_REACTION].allowed is True
    no_window = _mu_fields(return_1d=_f({"pct": 3.03}))
    assert {x.claim: x for x in compute_eligibility(no_window)}[
        Claim.PRICE_REACTION].allowed is False


# ------------------------------------------------------------------ 3. contradiction checks

def test_an_invented_number_is_rejected():
    p = build_packet(_report(_mu_fields()))
    r = validate("Revenue was $55.10B for the quarter.", p, deterministic=DET)
    assert not r.accepted and r.used_fallback and r.text == DET
    assert any(v.kind == "unsupported_number" for v in r.violations)


def test_a_sentence_using_only_real_numbers_can_still_be_rejected():
    """Support and consistency are different properties. Every figure here is real."""
    p = build_packet(_report(_mu_fields()))
    r = validate("EPS of 33.42 beat the 31.818 estimate.", p, deterministic=DET)
    assert not r.accepted
    assert any(v.kind == "ineligible_comparison" for v in r.violations)


def test_a_guidance_raise_is_rejected_and_named_as_contradicting_the_verdict():
    p = build_packet(_report(_mu_fields()))
    r = validate("The company raised guidance for the coming quarter.", p, deterministic=DET)
    kinds = {v.kind for v in r.violations}
    assert "ineligible_guidance_change" in kinds
    assert "contradicts_verdict" in kinds


def test_a_causal_sentence_is_rejected_and_a_hedged_one_is_not():
    p = build_packet(_report(_mu_fields()))
    bad = validate("The shares rose 3.03% because results were strong.", p, deterministic=DET)
    assert any(v.kind == "causal_claim" for v in bad.violations)
    good = validate(
        "A 3.03% move over 2026-09-29 close to 2026-10-01 close coincided with the release.",
        p, deterministic=DET)
    assert good.accepted, [v.detail for v in good.violations]


def test_a_bare_percentage_is_rejected_without_its_window():
    p = build_packet(_report(_mu_fields()))
    r = validate("The shares gained 3.03% on the results.", p, deterministic=DET)
    assert any(v.kind == "price_move_without_window" for v in r.violations)


def test_a_field_the_packet_reports_missing_cannot_be_asserted():
    p = build_packet(_report(_mu_fields(
        revenue_actual=_f(None, state="UNAVAILABLE"))))
    r = validate("Revenue was 54.23 billion for the quarter.", p, deterministic=DET)
    assert any(v.kind == "asserts_missing_field" for v in r.violations)


# ------------------------------------------------------------------ 4. historical wording

def test_a_superseded_packet_must_be_written_in_the_past_tense():
    p = build_packet(_report(_mu_fields()), superseded_by=15)
    assert p.historical is not None
    assert "PAST tense" in p.historical["required_framing"]
    r = validate("These are the latest results for the company.", p, deterministic=DET)
    assert any(v.kind == "historical_presented_as_current" for v in r.violations)


def test_a_current_packet_carries_no_historical_framing():
    assert build_packet(_report(_mu_fields())).historical is None


# ------------------------------------------------------------------ 5. degraded evidence

def test_missing_evidence_narrows_eligibility_rather_than_blocking_the_packet():
    bare = {"fiscal_period": _f(None, state="UNKNOWN")}
    p = build_packet(_report(bare))
    assert p.packet_hash, "a packet is still built"
    assert not p.allows(Claim.REPORTED_FIGURE)
    assert not p.allows(Claim.COMPARISON)
    assert not p.allows(Claim.PERIOD_IDENTITY)
    assert "calendar month" in p.reason(Claim.PERIOD_IDENTITY)


def test_a_stale_field_is_not_treated_as_resolved():
    p = build_packet(_report(_mu_fields(
        revenue_actual=_f({"value": 54230000000.0}, state="STALE"),
        eps_actual=_f(None, state="STALE"),
        official_figures=_f(None, state="STALE"))))
    assert not p.allows(Claim.REPORTED_FIGURE), "STALE is not OK"


def test_a_conflicting_field_does_not_license_a_clean_assertion():
    """Both values are in the packet, so a number check passes either one. The defect is the
    CERTAINTY, not the digits — a false claim built entirely from true numbers."""
    p = build_packet(_report(_mu_fields(
        revenue_actual=_f({"value": 54230000000.0, "provider_value": 54000000000.0,
                           "conflict": "the provider and the issuer differ"}))))

    settled = validate("Revenue was 54.23 billion, confirmed by all sources.", p,
                       deterministic=DET)
    assert not settled.accepted
    assert any(v.kind == "conflict_presented_as_settled" for v in settled.violations)

    provider_side = validate("Revenue was 54.0 billion.", p, deterministic=DET)
    assert not provider_side.accepted
    assert any(v.kind == "disputed_value_stated_as_fact" for v in provider_side.violations)

    honest = validate("The issuer reported 54.23 billion; the provider's figure differs.", p,
                      deterministic=DET)
    assert honest.accepted, [v.detail for v in honest.violations]


def test_a_revised_report_produces_a_different_packet_and_the_old_one_still_resolves():
    v1 = build_packet(_report(_mu_fields(), rid=12, version=1))
    v2 = build_packet(_report(_mu_fields(
        revenue_actual=_f({"value": 54250000000.0, "units": "USD"})), rid=15, version=2))
    assert v1.packet_hash != v2.packet_hash
    assert v1.report_id == 12 and v2.report_id == 15
    # A narrative written from v1 is still checkable against v1 after v2 exists.
    r = validate("Revenue was 54.23 billion.", v1, deterministic=DET)
    assert r.accepted and r.packet_hash == v1.packet_hash


def test_the_fallback_is_the_deterministic_text_never_a_repaired_draft():
    p = build_packet(_report(_mu_fields()))
    r = validate("EPS of 33.42 beat the estimate because demand was strong.", p,
                 deterministic=DET)
    assert r.text == DET
    assert "beat" not in r.text and "because" not in r.text
    assert len(r.violations) >= 2, "every violation is reported, not just the first"
