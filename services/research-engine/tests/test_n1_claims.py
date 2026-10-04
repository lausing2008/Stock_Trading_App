"""The four N1 review findings, as regressions, plus the typed-claim interface.

Each N1-R0x test asserts the EXACT behaviour the review reproduced, so a regression reproduces
the review rather than merely failing somewhere nearby.
"""
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))

from intelligence.claims import Claim, check_claim, narrate  # noqa: E402
from intelligence.evidence_packet import (  # noqa: E402
    ClaimKind, ELIGIBILITY_POLICY_VERSION, build_packet)
from intelligence.quantities import comparable, extract, render  # noqa: E402

DET = "Deterministic fallback text."


def _f(value, state="OK", **kw):
    return {"value": value, "state": state, **kw}


def _fields(**over):
    f = {
        "fiscal_period": _f({"label": "FY2026 Q4", "period_end": "2026-09-03"}),
        "official_figures": _f({
            "revenue": {"value": 54230000000.0, "units": "USD", "basis": "GAAP",
                        "period": "fiscal Q4 2026"},
            "eps_adjusted": {"value": 33.42, "units": "USD/share",
                             "basis": "non-GAAP adjusted", "period": "fiscal Q4 2026"},
            "guidance_q1_revenue": {"value": 61500000000.0, "units": "USD",
                                    "basis": "company guidance", "period": "fiscal Q1 2027"},
        }, evidence_ids=["issuer_document:1"]),
        "revenue_actual": _f({"value": 54230000000.0, "units": "USD", "basis": "GAAP",
                              "period": "fiscal Q4 2026"}, evidence_ids=["issuer_document:1"]),
        "eps_actual": _f({"value": 33.42, "units": "USD/share", "basis": "non-GAAP adjusted",
                          "period": "fiscal Q4 2026"}, evidence_ids=["issuer_document:1"]),
        "eps_expectation": _f({"value": 31.818, "is_frozen": False}),
        "guidance_current": _f({
            "guidance_q1_revenue": {"value": 61500000000.0, "units": "USD",
                                    "basis": "company guidance", "period": "fiscal Q1 2027"}},
            evidence_ids=["issuer_document:1"]),
        "guidance_change": _f({"current_guidance_available": True}, state="UNKNOWN"),
        "return_1d": _f({"pct": 3.03, "window": "2026-09-29 close to 2026-10-01 close"}),
        "three_verdicts": _f({"forward_outlook": "whether it is a RAISE is NOT established"}),
    }
    f.update(over)
    return f


def _packet(fields=None, **kw):
    fields = _fields() if fields is None else fields
    r = SimpleNamespace(
        id=12, version=1, subject_key="earnings:MU:2026-09-30", report_type="post_earnings",
        contract_version=3, policy_version="3",
        cutoff_at=datetime(2026, 10, 4, 3, 54), generated_at=datetime(2026, 10, 4, 3, 54),
        payload={"fields": fields, "evidence": {"issuer_document:1": {"source": "sec.gov"},
                                                "call:1": {"source": "transcript"}}})
    return build_packet(r, **kw)


# ============================================================ N1-R01 metric/unit identity

def test_n1r01_the_eps_value_cannot_be_published_as_revenue():
    """The review's exact case: "Revenue was $33.42 billion" using the EPS figure."""
    p = _packet()
    # There is no route to say it: a figure can only be NAMED, and the name carries the metric.
    eps = p.quantities["eps_actual"]
    assert eps.metric == "eps"
    assert p.quantities["revenue_actual"].metric == "revenue"
    out = narrate([Claim(kind=ClaimKind.REPORTED_FIGURE,
                         quantity_ids=("eps_actual",))], p, deterministic=DET)
    assert out.accepted
    assert "eps" in out.text and "revenue" not in out.text.lower()


def test_n1r01_a_magnitude_cannot_be_got_wrong_because_the_model_never_writes_it():
    """"Revenue was $54.23 million" was accepted. Rendering is ours, so the magnitude is ours."""
    p = _packet()
    out = narrate([Claim(kind=ClaimKind.REPORTED_FIGURE,
                         quantity_ids=("revenue_actual",))], p, deterministic=DET)
    assert "$54.23B" in out.text, out.text
    assert "million" not in out.text.lower()


def test_n1r01_a_comment_that_writes_its_own_figure_is_refused():
    p = _packet()
    v = check_claim(Claim(kind=ClaimKind.REPORTED_FIGURE, quantity_ids=("revenue_actual",),
                          comment="Revenue was $54.23 million."), p)
    assert not v.accepted
    assert any("writes its own figures" in r for r in v.reasons)


def test_n1r01_quantities_keep_their_identity():
    q = extract(_fields())
    assert q["revenue_actual"].metric == "revenue"
    assert q["revenue_actual"].units == "USD"
    assert q["official_figures.eps_adjusted"].metric == "eps"
    # A guidance figure is a FORECAST and is typed as one.
    assert q["official_figures.guidance_q1_revenue"].kind == "guidance"


def test_n1r01_guidance_cannot_be_stated_as_a_reported_result():
    p = _packet()
    v = check_claim(Claim(kind=ClaimKind.REPORTED_FIGURE,
                          quantity_ids=("guidance_current.guidance_q1_revenue",)), p)
    assert not v.accepted
    assert any("GUIDANCE" in r for r in v.reasons)


# ============================================================ N1-R02 comparability

def test_n1r02_a_gaap_estimate_cannot_be_compared_with_a_non_gaap_actual():
    """The review's case, now refused on the specific pair."""
    f = _fields(eps_expectation=_f({"value": 31.818, "units": "USD/share", "basis": "GAAP",
                                    "period": "fiscal Q4 2026"}))
    p = _packet(f)
    c = p.comparison("eps_actual", "eps_expectation")
    assert c is not None and not c.allowed
    assert "accounting bases" in c.reason
    v = check_claim(Claim(kind=ClaimKind.COMPARISON,
                          quantity_ids=("eps_actual", "eps_expectation")), p)
    assert not v.accepted


def test_n1r02_a_matching_basis_period_and_units_is_comparable():
    f = _fields(eps_expectation=_f({"value": 31.818, "units": "USD/share",
                                    "basis": "non-GAAP adjusted", "period": "fiscal Q4 2026"}))
    p = _packet(f)
    assert p.comparison("eps_actual", "eps_expectation").allowed
    assert check_claim(Claim(kind=ClaimKind.COMPARISON,
                             quantity_ids=("eps_actual", "eps_expectation")), p).accepted


def test_n1r02_an_eligible_eps_pair_does_not_authorise_a_revenue_comparison():
    """Permission is per pair, not report-wide — the review's second half of R02."""
    f = _fields(
        eps_expectation=_f({"value": 31.818, "units": "USD/share",
                            "basis": "non-GAAP adjusted", "period": "fiscal Q4 2026"}),
        revenue_expectation=_f({"value": 53000000000.0, "units": "USD", "basis": "GAAP",
                                "period": "fiscal Q3 2026"}))   # WRONG PERIOD
    p = _packet(f)
    assert p.comparison("eps_actual", "eps_expectation").allowed
    rev = p.comparison("revenue_actual", "revenue_expectation")
    assert not rev.allowed and "periods" in rev.reason
    assert not check_claim(Claim(kind=ClaimKind.COMPARISON,
                                 quantity_ids=("revenue_actual", "revenue_expectation")),
                           p).accepted


def test_n1r02_an_unidentified_expectation_blocks_the_comparison():
    """The stored estimate carries no units, basis or period — the real MU case."""
    p = _packet()
    c = p.comparison("eps_actual", "eps_expectation")
    assert not c.allowed
    assert "identity incomplete" in c.reason
    assert "units" in c.reason and "basis" in c.reason and "period" in c.reason


def test_n1r02_two_different_measures_never_compare():
    q = extract(_fields())
    c = comparable(q["revenue_actual"], q["eps_actual"])
    assert not c.allowed and "different measures" in c.reason


# ============================================================ N1-R03 no keyword bypass

def test_n1r03_phrasing_cannot_create_a_comparison_the_evidence_does_not_support():
    """"Profits surpassed analyst forecasts" evaded the word list. There is now no list to
    evade: a comparison must NAME two comparable quantities."""
    p = _packet()
    v = check_claim(Claim(kind=ClaimKind.REPORTED_FIGURE, quantity_ids=("eps_actual",),
                          comment="Profits surpassed analyst forecasts."), p)
    assert not v.accepted
    assert any("comparison language" in r for r in v.reasons)


def test_n1r03_an_attribution_elsewhere_cannot_license_a_causal_claim_here():
    """The review's bypass: a safe phrase anywhere disarmed the guard for the whole draft."""
    p = _packet()
    out = narrate([
        Claim(kind=ClaimKind.ATTRIBUTED_INTERPRETATION, attributed_to="management",
              source_evidence_id="call:1", comment="demand improved"),
        Claim(kind=ClaimKind.REPORTED_FIGURE, quantity_ids=("revenue_actual",),
              comment="AI drove the share rally because orders grew."),
    ], p, deterministic=DET)
    assert not out.accepted
    bad = [v for v in out.verdicts if not v.accepted]
    assert len(bad) == 1, "only the offending claim is at fault"
    assert any("does not scope to this claim" in r for r in bad[0].reasons)
    assert out.text == DET


def test_n1r03_an_attributed_interpretation_needs_a_named_source_that_resolves():
    p = _packet()
    assert not check_claim(Claim(kind=ClaimKind.ATTRIBUTED_INTERPRETATION,
                                 comment="demand improved"), p).accepted
    assert not check_claim(Claim(kind=ClaimKind.ATTRIBUTED_INTERPRETATION,
                                 attributed_to="management", source_evidence_id="call:999",
                                 comment="demand improved"), p).accepted
    assert check_claim(Claim(kind=ClaimKind.ATTRIBUTED_INTERPRETATION,
                             attributed_to="management", source_evidence_id="call:1",
                             comment="demand improved"), p).accepted


def test_n1r03_a_causal_claim_kind_is_refused_outright():
    p = _packet()
    v = check_claim(Claim(kind=ClaimKind.CAUSAL), p)
    assert not v.accepted and "counterfactual" in " ".join(v.reasons)


def test_n1r03_one_bad_claim_discards_the_whole_narrative():
    """Publishing the survivors would change what the narrative says."""
    p = _packet()
    out = narrate([
        Claim(kind=ClaimKind.REPORTED_FIGURE, quantity_ids=("revenue_actual",)),
        Claim(kind=ClaimKind.COMPARISON, quantity_ids=("eps_actual", "eps_expectation")),
    ], p, deterministic=DET)
    assert not out.accepted and out.text == DET


# ============================================================ N1-R04 deep immutability

def test_n1r04_nested_values_cannot_be_changed():
    p = _packet()
    try:
        p.fields["eps_actual"]["value"]["value"] = 999
        raise AssertionError("nested mutation should be impossible")
    except (TypeError, AttributeError):
        pass
    assert p.fields["eps_actual"]["value"]["value"] == 33.42


def test_n1r04_the_packet_is_detached_from_the_report_it_came_from():
    """Mutating the packet used to mutate the original report payload."""
    fields = _fields()
    r = SimpleNamespace(
        id=12, version=1, subject_key="s", report_type="post_earnings", contract_version=3,
        policy_version="3", cutoff_at=datetime(2026, 10, 4), generated_at=datetime(2026, 10, 4),
        payload={"fields": fields, "evidence": {}})
    p = build_packet(r)
    fields["eps_actual"]["value"]["value"] = 999      # edit the REPORT
    assert p.fields["eps_actual"]["value"]["value"] == 33.42, "the packet is detached"
    assert p.verify(), "and its hash still matches its own contents"


def test_n1r04_verify_detects_a_tampered_packet():
    p = _packet()
    assert p.verify()
    object.__setattr__(p, "packet_hash", "sha256-packet:deadbeef")
    assert not p.verify()
    out = narrate([Claim(kind=ClaimKind.REPORTED_FIGURE, quantity_ids=("revenue_actual",))],
                  p, deterministic=DET)
    assert not out.accepted and out.used_fallback
    assert any("changed after it was sealed" in r
               for v in out.verdicts for r in v.reasons)


def test_n1r04_the_hash_covers_the_rules_and_their_reasons_not_just_the_verdicts():
    """Content hashing does not preserve the policy that gave the content its meaning."""
    p = _packet()
    body = p._hash_body()
    assert body["eligibility_policy_version"] == ELIGIBILITY_POLICY_VERSION
    assert all("reason" in e for e in body["eligibility"])
    assert body["comparisons"], "pairwise results are part of the identity"
    assert body["quantities"]["revenue_actual"]["units"] == "USD"


# ============================================================ rendering

def test_rendering_is_deterministic_and_carries_identity():
    q = extract(_fields())
    assert render(q["revenue_actual"]) == "$54.23B (GAAP, fiscal Q4 2026)"
    assert render(q["eps_actual"]) == "$33.42 per share (non-GAAP adjusted, fiscal Q4 2026)"


def test_an_accepted_narrative_records_the_packet_and_policy_it_passed_under():
    p = _packet()
    out = narrate([Claim(kind=ClaimKind.REPORTED_FIGURE, quantity_ids=("revenue_actual",))],
                  p, deterministic=DET)
    assert out.accepted
    assert out.packet_hash == p.packet_hash
    assert out.policy_version == ELIGIBILITY_POLICY_VERSION


def test_frozen_mappings_do_not_silently_disable_downstream_checks():
    """A trap deep-freezing introduced: `MappingProxyType` is NOT a `dict` subclass, so every
    `isinstance(x, dict)` on packet data became False and the checks guarded by it quietly
    stopped running. Caught by an existing test failing for the wrong reason."""
    from collections.abc import Mapping
    p = _packet()
    assert not isinstance(p.fields, dict), "the packet is genuinely frozen"
    assert isinstance(p.fields, Mapping)
    # The checks guarded by those tests must actually fire against frozen data.
    v = check_claim(Claim(kind=ClaimKind.REPORTED_FIGURE, quantity_ids=("revenue_actual",),
                          comment="The company raised guidance."), p)
    assert not v.accepted
    assert any("not established" in r for r in v.reasons), \
        "the verdict check reads a frozen mapping"


def test_n1r04_detachment_holds_for_a_leaf_the_freezer_has_no_rule_for():
    """`deep_freeze` rebuilds dicts and lists, so those detach even without the copy. A leaf it
    has no rule for — here a set — stays shared unless the packet is deep-copied first. Added
    after a sabotage run passed with the copy removed, which showed the earlier detachment test
    was not load-bearing."""
    fields = _fields()
    fields["revenue_actual"]["value"]["tags"] = {"audited"}
    r = SimpleNamespace(
        id=12, version=1, subject_key="s", report_type="post_earnings", contract_version=3,
        policy_version="3", cutoff_at=datetime(2026, 10, 4), generated_at=datetime(2026, 10, 4),
        payload={"fields": fields, "evidence": {}})
    p = build_packet(r)
    fields["revenue_actual"]["value"]["tags"].add("restated")   # mutate through the REPORT
    assert p.fields["revenue_actual"]["value"]["tags"] == {"audited"}, \
        "the packet must not see a mutation made through the report"
    assert p.verify()
