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

from collections.abc import Mapping

import copy
import hashlib
import json
from dataclasses import dataclass, field as dc_field
from enum import Enum
from types import MappingProxyType
from typing import Any

from .quantities import Comparability, Quantity, comparable, extract

from .report_contract import CONTRACT_VERSION, FieldState

#: Bumped when the packet's SHAPE changes. A narrative records the packet version it was written
#: against, so a packet built under different rules is never silently re-validated under today's.
PACKET_VERSION = 2

#: Bumped when the ELIGIBILITY RULES change, independently of the packet shape. Content hashing
#: addresses the content; it does not preserve the policy that gave the content its meaning, so
#: a narrative accepted under one rule set must not silently re-validate under another.
ELIGIBILITY_POLICY_VERSION = 2


class ClaimKind(str, Enum):
    """The kinds of statement a narrator can attempt. Each is separately eligible."""
    REPORTED_FIGURE = "reported_figure"          # "revenue was $54.23B"
    COMPARISON = "comparison"                    # "above/below the estimate"
    GUIDANCE_LEVEL = "guidance_level"            # "guides Q1 revenue to $61.5B"
    GUIDANCE_CHANGE = "guidance_change"          # "raised guidance"
    PRICE_REACTION = "price_reaction"            # "the shares rose 3.03%"
    CAUSAL = "causal"                            # "the shares rose BECAUSE ..."
    PERIOD_IDENTITY = "period_identity"          # "fiscal Q4 2026"
    #: Someone else's stated view, carrying who said it and where that is recorded. This is the
    #: ONLY route by which an explanation of a move may appear, and it is never the platform's
    #: own claim.
    ATTRIBUTED_INTERPRETATION = "attributed_interpretation"


@dataclass(frozen=True)
class Eligibility:
    """Whether one kind of claim may be made, and the reason when it may not.

    THE REASON IS THE POINT. "Not eligible" alone invites a narrator (or a developer) to treat
    the rule as an obstacle. The reason names the missing evidence, so the fix is visible.
    """
    claim: ClaimKind
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
    #: Every identified figure, keyed by a stable id. Nothing downstream sees a bare number.
    quantities: dict[str, Quantity] = dc_field(default_factory=dict)
    #: Comparability decided PAIRWISE, so an eligible EPS comparison cannot authorise a
    #: revenue one and a GAAP estimate cannot authorise a comparison against a non-GAAP actual.
    comparisons: tuple[Comparability, ...] = ()
    eligibility_policy_version: int = ELIGIBILITY_POLICY_VERSION
    #: Set when the packet describes a report that is no longer the current view of its subject.
    historical: dict | None = None
    packet_hash: str = ""

    def allows(self, claim: ClaimKind) -> bool:
        return any(e.claim is claim and e.allowed for e in self.eligibility)

    def comparison(self, left: str, right: str) -> Comparability | None:
        """Whether these two SPECIFIC quantities may be compared."""
        for c in self.comparisons:
            if {c.left, c.right} == {left, right}:
                return c
        return None

    def verify(self) -> bool:
        """Re-derive the hash from the current contents. False means it was tampered with."""
        return self.packet_hash == packet_hash(self._hash_body())

    def _hash_body(self) -> dict:
        return {
            "packet_version": self.packet_version, "report_id": self.report_id,
            "report_version": self.report_version, "subject_key": self.subject_key,
            "report_type": self.report_type, "contract_version": self.contract_version,
            "policy_version": self.policy_version, "cutoff_at": self.cutoff_at,
            "generated_at": self.generated_at, "fields": thaw(self.fields),
            "evidence": thaw(self.evidence), "historical": thaw(self.historical),
            # The RULES are hashed too, with their reasons — not just which kinds were allowed.
            "eligibility_policy_version": self.eligibility_policy_version,
            "eligibility": [{"claim": e.claim.value, "allowed": e.allowed,
                             "reason": e.reason, "requires": list(e.requires)}
                            for e in self.eligibility],
            "comparisons": [{"left": c.left, "right": c.right, "allowed": c.allowed,
                             "reason": c.reason} for c in self.comparisons],
            "quantities": {k: {"metric": q.metric, "value": q.value, "units": q.units,
                               "basis": q.basis, "period": q.period, "kind": q.kind}
                           for k, q in sorted(self.quantities.items())},
        }

    def reason(self, claim: ClaimKind) -> str:
        for e in self.eligibility:
            if e.claim is claim:
                return e.reason
        return f"{claim.value} is not a recognised claim kind"


def deep_freeze(obj):
    """A structure that cannot be edited after the fact, at any depth.

    THE DEFECT THIS CLOSES. `@dataclass(frozen=True)` blocks attribute assignment and nothing
    else. The packet held the report's own nested dicts, so
    `packet.fields['eps_actual']['value']['value'] = 999` succeeded, the recorded hash stayed
    unchanged — and because the dicts were SHARED, the original report payload was mutated too.
    A packet whose contents can change under its own identity is not evidence of anything.

    The structure is deep-copied first (so the report is detached from the packet) and then
    wrapped read-only at every level.
    """
    if isinstance(obj, Mapping):
        return MappingProxyType({k: deep_freeze(v) for k, v in obj.items()})
    if isinstance(obj, (list, tuple)):
        return tuple(deep_freeze(x) for x in obj)
    return obj


def thaw(obj):
    """A plain, mutable copy — for callers that need to serialise or edit a derived view."""
    if isinstance(obj, Mapping):
        return {k: thaw(v) for k, v in obj.items()}
    if isinstance(obj, tuple):
        return [thaw(x) for x in obj]
    return obj


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


def pairwise_comparisons(quantities: dict) -> tuple[Comparability, ...]:
    """Decide every actual/expectation pair on its own merits.

    The earlier rule asked whether the report recorded AN accounting basis anywhere. That let a
    GAAP estimate authorise a comparison against a non-GAAP actual, and let one eligible pair
    authorise every other. Each pair is now judged by metric, units, basis and period.
    """
    actuals = [q for q in quantities.values() if q.kind == "actual"]
    expectations = [q for q in quantities.values() if q.kind == "expectation"]
    return tuple(comparable(a, e) for a in actuals for e in expectations
                 if a.metric == e.metric)


def compute_eligibility(fields: dict, quantities: dict | None = None,
                        comparisons: tuple = ()) -> tuple[Eligibility, ...]:
    """What may be claimed from these fields. Computed, never asserted in a prompt.

    The COMPARISON entry here is a summary of the pairwise results: it says whether ANY pair is
    comparable. A narrator still has to name the pair, and that pair is checked individually —
    this entry can never stand in for the specific one.
    """
    quantities = quantities if quantities is not None else extract(fields)
    comparisons = comparisons or pairwise_comparisons(quantities)
    out: list[Eligibility] = []

    has_figures = _ok(fields, "official_figures") or _ok(fields, "revenue_actual") \
        or _ok(fields, "eps_actual")
    out.append(Eligibility(
        ClaimKind.REPORTED_FIGURE, has_figures,
        "figures are present with units and a stated basis" if has_figures else
        "no reported figure is resolved in this packet",
        requires=("official_figures|revenue_actual|eps_actual",)))

    out.append(Eligibility(
        ClaimKind.PERIOD_IDENTITY, _ok(fields, "fiscal_period"),
        "the fiscal period is source-confirmed" if _ok(fields, "fiscal_period") else
        "no source-confirmed fiscal period is in this packet; the stored label is derived from "
        "the calendar month and is wrong for non-calendar fiscal years",
        requires=("fiscal_period",)))

    # A COMPARISON is decided PAIR BY PAIR. This summary says only whether any pair qualifies.
    ok_pairs = [c for c in comparisons if c.allowed]
    blocked = [c for c in comparisons if not c.allowed]
    if ok_pairs:
        why = "comparable pair(s): " + "; ".join(f"{c.left} vs {c.right}" for c in ok_pairs)
    elif blocked:
        why = ("no pair is comparable — " +
               "; ".join(f"{c.left} vs {c.right}: {c.reason}" for c in blocked))
    else:
        why = "a comparison needs both an actual and an expectation of the same measure; none pair up"
    out.append(Eligibility(
        ClaimKind.COMPARISON, bool(ok_pairs), why,
        requires=("matching metric", "units", "basis", "period")))

    out.append(Eligibility(
        ClaimKind.GUIDANCE_LEVEL, _ok(fields, "guidance_current"),
        "the company's current guidance is present from its own release"
        if _ok(fields, "guidance_current") else
        "no current guidance is joined to this packet",
        requires=("guidance_current",)))

    # A guidance CHANGE is a different claim from a guidance LEVEL and needs a different input.
    gc = _val(fields, "guidance_change")
    prior = _ok(fields, "prior_guidance")
    change_ok = prior and _ok(fields, "guidance_change")
    out.append(Eligibility(
        ClaimKind.GUIDANCE_CHANGE, bool(change_ok),
        "a comparable prior forecast for the same period is present" if change_ok else
        ("current guidance is available and the PRIOR guidance for the same target period on "
         "the same accounting basis is not, so raised/maintained/lowered/first-issued cannot be "
         "distinguished" if isinstance(gc, Mapping) and gc.get("current_guidance_available")
         else "no guidance comparison is available in this packet"),
        requires=("prior_guidance", "guidance_current")))

    r1 = _val(fields, "return_1d")
    has_window = isinstance(r1, Mapping) and r1.get("window") and _ok(fields, "return_1d")
    out.append(Eligibility(
        ClaimKind.PRICE_REACTION, bool(has_window),
        "a return is present AND names the window it spans" if has_window else
        "no matured return with a named window is in this packet",
        requires=("return_1d.window",)))

    # NEVER. Not "not yet": nothing in a packet of prices and figures can establish that one
    # caused the other, and no amount of additional evidence of this kind would change that.
    out.append(Eligibility(
        ClaimKind.CAUSAL, False,
        "a causal claim is never eligible from this evidence. A price move coinciding with a "
        "release is a coincidence in time; attributing it requires a counterfactual this "
        "platform does not have. Say 'coincided with' or attribute the interpretation to a "
        "named source.",
        requires=()))
    return tuple(out)


def build_packet(report, *, superseded_by=None, corrected_by=None) -> EvidencePacket:
    """Freeze one stored report into a packet.

    DETACHED FIRST, THEN FROZEN. The packet used to hold the report's own nested dicts, so
    editing the packet edited the report. It is deep-copied before anything else touches it.

    WHY THE COPY IS NOT REDUNDANT, though it looks it. `deep_freeze` rebuilds every dict and
    list it walks, so for those types it already detaches. It does NOT rebuild a leaf it has no
    rule for — a set, or any object — which would otherwise remain shared with the report and
    mutable through it. A sabotage check initially passed with the copy removed, because the
    only fixtures had dict and list leaves; the copy is what makes the guarantee hold for
    everything else.
    """
    payload = report.payload or {}
    raw_fields = copy.deepcopy(payload.get("fields", {}) or {})
    raw_evidence = copy.deepcopy(
        payload.get("evidence", {}) or payload.get("evidence_book", {}) or {})

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

    quantities = extract(raw_fields)
    comparisons = pairwise_comparisons(quantities)
    elig = compute_eligibility(raw_fields, quantities, comparisons)

    packet = EvidencePacket(
        packet_version=PACKET_VERSION,
        report_id=report.id,
        report_version=report.version,
        subject_key=report.subject_key,
        report_type=report.report_type,
        contract_version=report.contract_version,
        policy_version=report.policy_version,
        cutoff_at=report.cutoff_at.isoformat() if report.cutoff_at else "",
        generated_at=report.generated_at.isoformat() if report.generated_at else "",
        fields=deep_freeze(raw_fields),
        evidence=deep_freeze(raw_evidence),
        historical=deep_freeze(historical) if historical else None,
        eligibility=elig,
        quantities=MappingProxyType(dict(quantities)),
        comparisons=comparisons,
        eligibility_policy_version=ELIGIBILITY_POLICY_VERSION,
    )
    object.__setattr__(packet, "packet_hash", packet_hash(packet._hash_body()))
    return packet
