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
    ClaimKind as Kind, PACKET_VERSION, build_packet, compute_eligibility,
    packet_hash)
from intelligence.claims import Claim, check_claim, narrate  # noqa: E402

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
        "revenue_actual": _f({"value": 54230000000.0, "units": "USD", "basis": "GAAP",
                              "period": "fiscal Q4 2026"}),
        "eps_actual": _f({"value": 33.42, "units": "USD/share", "basis": "non-GAAP adjusted",
                          "period": "fiscal Q4 2026"}),
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

def test_a_comparison_needs_a_comparable_pair_not_merely_two_numbers():
    """Report-wide permission is gone: the estimate must match the actual on metric, units,
    basis and period, and the summary reports the PAIR that failed."""
    e = {x.claim: x for x in compute_eligibility(_mu_fields())}
    c = e[Kind.COMPARISON]
    assert c.allowed is False
    assert "identity incomplete" in c.reason and "eps_actual vs eps_expectation" in c.reason
    ok = compute_eligibility(_mu_fields(
        eps_expectation=_f({"value": 31.818, "units": "USD/share",
                            "basis": "non-GAAP adjusted", "period": "fiscal Q4 2026"})))
    assert {x.claim: x for x in ok}[Kind.COMPARISON].allowed is True


def test_guidance_level_is_eligible_while_guidance_change_is_not():
    e = {x.claim: x for x in compute_eligibility(_mu_fields())}
    assert e[Kind.GUIDANCE_LEVEL].allowed is True
    assert e[Kind.GUIDANCE_CHANGE].allowed is False
    assert "prior" in e[Kind.GUIDANCE_CHANGE].reason.lower()


def test_a_causal_claim_is_never_eligible_however_complete_the_evidence():
    full = _mu_fields(accounting_basis=_f({"estimate_basis": "non-GAAP adjusted"}),
                      prior_guidance=_f({"value": 60000000000.0}))
    e = {x.claim: x for x in compute_eligibility(full)}
    assert e[Kind.CAUSAL].allowed is False
    assert "counterfactual" in e[Kind.CAUSAL].reason


def test_a_price_reaction_needs_a_named_window():
    assert {x.claim: x for x in compute_eligibility(_mu_fields())}[
        Kind.PRICE_REACTION].allowed is True
    no_window = _mu_fields(return_1d=_f({"pct": 3.03}))
    assert {x.claim: x for x in compute_eligibility(no_window)}[
        Kind.PRICE_REACTION].allowed is False


# ------------------------------------------------------------------ 3. contradiction checks
#
# These moved to the TYPED path. There is no ungated prose entry point any more: text is always
# checked against the claim it belongs to. See test_n1_claims.py for the full set.

def _c(kind, comment=None, **kw):
    return Claim(kind=kind, comment=comment, **kw)


def test_a_comment_may_not_contain_a_figure():
    p = build_packet(_report(_mu_fields()))
    v = check_claim(_c(Kind.REPORTED_FIGURE, "Revenue was $55.10B.",
                       quantity_ids=("revenue_actual",)), p)
    assert not v.accepted
    assert any("writes its own figures" in r for r in v.reasons)


def test_comparison_language_outside_a_comparison_claim_is_refused():
    p = build_packet(_report(_mu_fields()))
    v = check_claim(_c(Kind.REPORTED_FIGURE, "Profits surpassed analyst forecasts.",
                       quantity_ids=("eps_actual",)), p)
    assert not v.accepted
    assert any("comparison language" in r for r in v.reasons)


def test_a_guidance_raise_contradicts_the_packets_own_verdict():
    p = build_packet(_report(_mu_fields()))
    v = check_claim(_c(Kind.REPORTED_FIGURE, "The company raised guidance.",
                       quantity_ids=("eps_actual",)), p)
    assert not v.accepted
    joined = " ".join(v.reasons)
    assert "change-of-guidance language" in joined
    assert "not established" in joined


def test_a_conflict_may_not_be_presented_as_settled():
    p = build_packet(_report(_mu_fields(
        revenue_actual=_f({"value": 54230000000.0, "units": "USD", "basis": "GAAP",
                           "period": "fiscal Q4 2026",
                           "conflict": "the provider and the issuer differ"}))))
    v = check_claim(_c(Kind.REPORTED_FIGURE, "This figure is confirmed.",
                       quantity_ids=("revenue_actual",)), p)
    assert not v.accepted
    assert any("overstates" in r for r in v.reasons)


def test_the_removed_prose_gate_cannot_be_imported_back():
    """A function returning accepted=True over ineligible prose is a trap for the next reader."""
    import pytest
    with pytest.raises(ImportError, match="free prose was never checkable"):
        import importlib
        importlib.import_module("intelligence.narration_validator")


# ------------------------------------------------------------------ 4. historical wording

def test_a_superseded_packet_must_be_written_in_the_past_tense():
    p = build_packet(_report(_mu_fields()), superseded_by=15)
    assert p.historical is not None
    assert "PAST tense" in p.historical["required_framing"]
    v = check_claim(_c(Kind.REPORTED_FIGURE, "These are the latest results.",
                       quantity_ids=("revenue_actual",)), p)
    assert not v.accepted
    assert any("what was known at its cutoff" in r for r in v.reasons)


def test_a_current_packet_carries_no_historical_framing():
    assert build_packet(_report(_mu_fields())).historical is None


# ------------------------------------------------------------------ 5. degraded evidence

def test_missing_evidence_narrows_eligibility_rather_than_blocking_the_packet():
    bare = {"fiscal_period": _f(None, state="UNKNOWN")}
    p = build_packet(_report(bare))
    assert p.packet_hash, "a packet is still built"
    assert not p.allows(Kind.REPORTED_FIGURE)
    assert not p.allows(Kind.COMPARISON)
    assert not p.allows(Kind.PERIOD_IDENTITY)
    assert "calendar month" in p.reason(Kind.PERIOD_IDENTITY)


def test_a_stale_field_is_not_treated_as_resolved():
    p = build_packet(_report(_mu_fields(
        revenue_actual=_f({"value": 54230000000.0}, state="STALE"),
        eps_actual=_f(None, state="STALE"),
        official_figures=_f(None, state="STALE"))))
    assert not p.allows(Kind.REPORTED_FIGURE), "STALE is not OK"


def test_a_conflicting_field_does_not_license_a_clean_assertion():
    """Where sources disagree, both values are in the packet, so a figure check passes either.
    The defect is the CERTAINTY, not the digits."""
    p = build_packet(_report(_mu_fields(
        revenue_actual=_f({"value": 54230000000.0, "units": "USD", "basis": "GAAP",
                           "period": "fiscal Q4 2026",
                           "provider_value": 54000000000.0,
                           "conflict": "the provider and the issuer differ"}))))
    settled = check_claim(_c(Kind.REPORTED_FIGURE, "This is confirmed by all sources.",
                             quantity_ids=("revenue_actual",)), p)
    assert not settled.accepted
    assert any("overstates" in r for r in settled.reasons)

    honest = check_claim(_c(Kind.REPORTED_FIGURE, "The issuer and the provider differ here.",
                            quantity_ids=("revenue_actual",)), p)
    assert honest.accepted, honest.reasons


def test_a_revised_report_produces_a_different_packet_and_the_old_one_still_resolves():
    v1 = build_packet(_report(_mu_fields(), rid=12, version=1))
    v2 = build_packet(_report(_mu_fields(
        revenue_actual=_f({"value": 54250000000.0, "units": "USD"})), rid=15, version=2))
    assert v1.packet_hash != v2.packet_hash
    assert v1.report_id == 12 and v2.report_id == 15
    # A narrative written from v1 is still checkable against v1 after v2 exists.
    out = narrate([_c(Kind.REPORTED_FIGURE, quantity_ids=("revenue_actual",))], v1,
                  deterministic=DET)
    assert out.accepted and out.packet_hash == v1.packet_hash
    assert v1.verify(), "and v1 still matches its own recorded hash"


def test_the_fallback_is_the_deterministic_text_never_a_repaired_draft():
    p = build_packet(_report(_mu_fields()))
    out = narrate([_c(Kind.REPORTED_FIGURE, "EPS beat the estimate because demand was strong.",
                      quantity_ids=("eps_actual",))], p, deterministic=DET)
    assert out.text == DET
    assert "beat" not in out.text and "because" not in out.text
    bad = [v for v in out.verdicts if not v.accepted][0]
    assert len(bad.reasons) >= 2, "every reason is reported, not just the first"
