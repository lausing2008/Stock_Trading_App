"""N1 — the frozen evidence packet a narrator is allowed to read, and nothing else.

WHY A PACKET AND NOT "PASS IT THE REPORT". A report is a live object: regenerate it and fields
move. A narration written against one and stored beside another is unauditable — the sentence
survives, the numbers it was written from do not, and there is no way afterwards to tell whether
the narrator was wrong or the inputs changed underneath it. A packet is taken once, hashed, and
never rewritten; a narrative references the packet hash it was written from, so the pair can
always be re-checked.

WHAT A NARRATOR MAY SAY IS DECIDED HERE, NOT BY THE NARRATOR. Eligibility is computed from the
evidence: a comparison is permitted only where both sides exist on a comparable basis, a
guidance CHANGE only where a comparable prior forecast exists, and a causal claim never. That
inverts the usual arrangement, where a model is asked in a prompt to be careful and its output
is checked afterwards for having not been. A rule the generator can evaluate is a rule; an
instruction in a prompt is a hope.

NOTHING HERE CALLS A MODEL. This module builds packets, states what may be claimed from them,
and checks a finished draft against them. Narration itself stays off.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field as dc_field
from enum import Enum
from typing import Any

from .report_contract import CONTRACT_VERSION, FieldState

#: Bumped when the packet's SHAPE changes. A narrative records the packet version it was written
#: against, so a packet built under different rules is never silently re-validated under today's.
PACKET_VERSION = 1


class Claim(str, Enum):
    """The kinds of statement a narrator can attempt. Each is separately eligible."""
    REPORTED_FIGURE = "reported_figure"          # "revenue was $54.23B"
    COMPARISON = "comparison"                    # "above/below the estimate"
    GUIDANCE_LEVEL = "guidance_level"            # "guides Q1 revenue to $61.5B"
    GUIDANCE_CHANGE = "guidance_change"          # "raised guidance"
    PRICE_REACTION = "price_reaction"            # "the shares rose 3.03%"
    CAUSAL = "causal"                            # "the shares rose BECAUSE ..."
    PERIOD_IDENTITY = "period_identity"          # "fiscal Q4 2026"


@dataclass(frozen=True)
class Eligibility:
    """Whether one kind of claim may be made, and the reason when it may not.

    THE REASON IS THE POINT. "Not eligible" alone invites a narrator (or a developer) to treat
    the rule as an obstacle. The reason names the missing evidence, so the fix is visible.
    """
    claim: Claim
    allowed: bool
    reason: str
    requires: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidencePacket:
    """An immutable view of one report, addressed by content.

    Pinned to the report it came from AND to the moment that report could see: a packet that
    did not record `cutoff_at` cannot later be distinguished from one taken over fresher data.
    """
    packet_version: int
    report_id: int
    report_version: int
    subject_key: str
    report_type: str
    contract_version: int
    policy_version: str
    cutoff_at: str
    generated_at: str
    #: Field name -> {value, state, units, basis, evidence_ids, label, section, timeframe}
    fields: dict[str, dict]
    #: Evidence id -> its recorded times and source, so a citation can be resolved offline.
    evidence: dict[str, dict]
    eligibility: tuple[Eligibility, ...]
    #: Set when the packet describes a report that is no longer the current view of its subject.
    historical: dict | None = None
    packet_hash: str = ""

    def allows(self, claim: Claim) -> bool:
        return any(e.claim is claim and e.allowed for e in self.eligibility)

    def reason(self, claim: Claim) -> str:
        for e in self.eligibility:
            if e.claim is claim:
                return e.reason
        return f"{claim.value} is not a recognised claim kind"


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))


def packet_hash(body: dict) -> str:
    """Content address of the packet, excluding the hash field itself."""
    return "sha256-packet:" + hashlib.sha256(_canonical(body).encode()).hexdigest()[:48]


def _ok(fields: dict, key: str) -> bool:
    f = fields.get(key)
    return bool(f) and f.get("state") == FieldState.OK.value


def _val(fields: dict, key: str) -> Any:
    f = fields.get(key) or {}
    return f.get("value")


def _basis_is_established(fields: dict) -> bool:
    """A reported basis is not the same as a COMPARABLE one.

    The issuer stating "non-GAAP" for its own figure tells us nothing about the basis of the
    estimate it would be compared against. Both sides are required, and the estimate's basis is
    not stored — so this is False wherever a comparison would need it.
    """
    b = _val(fields, "accounting_basis")
    if not isinstance(b, dict):
        return False
    return b.get("estimate_basis") not in (None, "", "UNKNOWN")


def compute_eligibility(fields: dict) -> tuple[Eligibility, ...]:
    """What may be claimed from these fields. Computed, never asserted in a prompt."""
    out: list[Eligibility] = []

    has_figures = _ok(fields, "official_figures") or _ok(fields, "revenue_actual") \
        or _ok(fields, "eps_actual")
    out.append(Eligibility(
        Claim.REPORTED_FIGURE, has_figures,
        "figures are present with units and a stated basis" if has_figures else
        "no reported figure is resolved in this packet",
        requires=("official_figures|revenue_actual|eps_actual",)))

    out.append(Eligibility(
        Claim.PERIOD_IDENTITY, _ok(fields, "fiscal_period"),
        "the fiscal period is source-confirmed" if _ok(fields, "fiscal_period") else
        "no source-confirmed fiscal period is in this packet; the stored label is derived from "
        "the calendar month and is wrong for non-calendar fiscal years",
        requires=("fiscal_period",)))

    # A COMPARISON needs both sides AND a comparable basis. Having an actual and an estimate is
    # not sufficient: a GAAP actual against an adjusted estimate is a difference, not a beat.
    both_sides = (_ok(fields, "eps_actual") and _ok(fields, "eps_expectation")) or \
                 (_ok(fields, "revenue_actual") and _ok(fields, "revenue_expectation"))
    comparable = both_sides and _basis_is_established(fields)
    out.append(Eligibility(
        Claim.COMPARISON, comparable,
        "both sides are present on an established basis" if comparable else
        ("both an actual and an expectation are present, but the expectation's accounting basis "
         "is not stored, so the difference is NOT a validated beat or miss"
         if both_sides else
         "a comparison needs both an actual and an expectation; one or both are missing"),
        requires=("*_actual", "*_expectation", "accounting_basis.estimate_basis")))

    out.append(Eligibility(
        Claim.GUIDANCE_LEVEL, _ok(fields, "guidance_current"),
        "the company's current guidance is present from its own release"
        if _ok(fields, "guidance_current") else
        "no current guidance is joined to this packet",
        requires=("guidance_current",)))

    # A guidance CHANGE is a different claim from a guidance LEVEL and needs a different input.
    gc = _val(fields, "guidance_change")
    prior = _ok(fields, "prior_guidance")
    change_ok = prior and _ok(fields, "guidance_change")
    out.append(Eligibility(
        Claim.GUIDANCE_CHANGE, bool(change_ok),
        "a comparable prior forecast for the same period is present" if change_ok else
        ("current guidance is available and the PRIOR guidance for the same target period on "
         "the same accounting basis is not, so raised/maintained/lowered/first-issued cannot be "
         "distinguished" if isinstance(gc, dict) and gc.get("current_guidance_available")
         else "no guidance comparison is available in this packet"),
        requires=("prior_guidance", "guidance_current")))

    r1 = _val(fields, "return_1d")
    has_window = isinstance(r1, dict) and r1.get("window") and _ok(fields, "return_1d")
    out.append(Eligibility(
        Claim.PRICE_REACTION, bool(has_window),
        "a return is present AND names the window it spans" if has_window else
        "no matured return with a named window is in this packet",
        requires=("return_1d.window",)))

    # NEVER. Not "not yet": nothing in a packet of prices and figures can establish that one
    # caused the other, and no amount of additional evidence of this kind would change that.
    out.append(Eligibility(
        Claim.CAUSAL, False,
        "a causal claim is never eligible from this evidence. A price move coinciding with a "
        "release is a coincidence in time; attributing it requires a counterfactual this "
        "platform does not have. Say 'coincided with' or attribute the interpretation to a "
        "named source.",
        requires=()))
    return tuple(out)


def build_packet(report, *, superseded_by=None, corrected_by=None) -> EvidencePacket:
    """Freeze one stored report into a packet. Reads only what the report already holds."""
    payload = report.payload or {}
    fields = payload.get("fields", {})
    evidence = payload.get("evidence", {}) or payload.get("evidence_book", {}) or {}

    historical = None
    if superseded_by or corrected_by:
        historical = {
            "is_current_view": False,
            "superseded_by": superseded_by,
            "corrected_by": corrected_by,
            # The wording a narrator MUST use, supplied rather than left to it.
            "required_framing": (
                "This describes a snapshot taken at the cutoff below, which is no longer the "
                "current view of this subject. Write it in the PAST tense and as what was known "
                "THEN. Do not present it as current, and do not restate it as though later "
                "information were available."),
        }

    body = {
        "packet_version": PACKET_VERSION,
        "report_id": report.id,
        "report_version": report.version,
        "subject_key": report.subject_key,
        "report_type": report.report_type,
        "contract_version": report.contract_version,
        "policy_version": report.policy_version,
        "cutoff_at": report.cutoff_at.isoformat() if report.cutoff_at else "",
        "generated_at": report.generated_at.isoformat() if report.generated_at else "",
        "fields": fields,
        "evidence": evidence,
        "historical": historical,
    }
    elig = compute_eligibility(fields)
    return EvidencePacket(
        **body,
        eligibility=elig,
        packet_hash=packet_hash(body | {"eligibility": [
            {"claim": e.claim.value, "allowed": e.allowed} for e in elig]}),
    )
