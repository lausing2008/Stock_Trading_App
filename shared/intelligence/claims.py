"""The interface a narrator writes to: typed claims that NAME quantities, never write numbers.

WHY THE INTERFACE CHANGED. The first validator took free prose and graded it. That is the wrong
shape for the problem: prohibited-phrase lists are trivially evaded ("profits surpassed analyst
forecasts" misses a list containing "beat"), an attribution in one sentence cannot be scoped to
another ("According to management..." anywhere in a draft disarmed the causal guard for the
whole text), and a model that writes its own digits will eventually write the wrong ones.

So the model no longer supplies facts. It supplies a STRUCTURE — which claim, about which
quantities — and the factual clause is rendered deterministically from the packet. Prose is
permitted only as scoped interpretation attached to a single claim, and it is checked against
that claim alone. This cannot be bypassed by phrasing, because the phrasing is not what carries
the fact.

Free prose remains fallible where it is allowed. That is stated, not papered over: the scoped
check is a backstop on an interpretation clause, never a proof of semantic safety.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field as dc_field

from .evidence_packet import ClaimKind, EvidencePacket
from .quantities import literal_numbers, render

#: Causal language. Checked per claim, and only ever permitted where the claim is an
#: ATTRIBUTED_INTERPRETATION carrying its own named source.
_CAUSAL = ("because", "caused by", "due to", "drove", "driven by", "as a result of",
           "led to", "priced in", "on the back of", "thanks to", "reflects the",
           "attributable to", "owing to", "stems from", "triggered by")

#: Comparison language. Deliberately broader than the first list, which is the SECONDARY
#: defence — the primary one is that a comparison claim must name two comparable quantities.
_COMPARISON = ("beat", "beats", "missed", "miss", "above", "below", "ahead of", "short of",
               "outperformed", "topped", "exceeded", "surpassed", "fell short", "better than",
               "worse than", "stronger than", "weaker than", "upside", "downside", "surprise")

#: Change-of-guidance language, as distinct from stating a guidance level.
_GUIDANCE_CHANGE = ("raised", "raise", "lifted", "lowered", "cut", "maintained", "reaffirmed",
                    "upgraded", "downgraded", "increased its", "reduced its")


@dataclass(frozen=True)
class Claim:
    """One structured assertion. The narrator produces these; it does not produce sentences."""
    kind: ClaimKind
    #: Quantity ids from the packet. The ONLY way a figure enters the output.
    quantity_ids: tuple[str, ...] = ()
    #: Optional interpretation, scoped to THIS claim and checked against it alone.
    comment: str | None = None
    #: Required for an attributed interpretation: who said it, and where it is recorded.
    attributed_to: str | None = None
    source_evidence_id: str | None = None


@dataclass
class ClaimVerdict:
    claim: Claim
    accepted: bool
    reasons: list[str] = dc_field(default_factory=list)
    rendered: str = ""


@dataclass
class NarrationResult:
    accepted: bool
    verdicts: list[ClaimVerdict] = dc_field(default_factory=list)
    text: str = ""
    used_fallback: bool = False
    packet_hash: str = ""
    policy_version: int = 0


def _mentions(text: str, words) -> str | None:
    low = f" {text.lower()} "
    for w in words:
        if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", low):
            return w
    return None


#: Wording that presents a disputed figure as settled.
_SETTLED = ("confirmed", "verified", "all sources agree", "unambiguous", "definitively",
            "beyond doubt")

#: Wording that presents a snapshot as the current state of the world.
_PRESENT = ("is now", "currently", "as of today", "the latest results", "most recent",
            "at present", "today")


def _scoped_packet_checks(c: Claim, reasons: list[str], packet: EvidencePacket) -> None:
    """Checks that depend on the packet's own content, applied to THIS claim's comment."""
    if not c.comment:
        return
    low = c.comment.lower()

    # A snapshot that is no longer current must not be written as though it were.
    if packet.historical and _mentions(c.comment, _PRESENT):
        reasons.append("this packet is not the current view of its subject; it must be written "
                       "as what was known at its cutoff")

    # Where sources disagree, certainty is the defect — not the digits, which are rendered.
    for key, f in packet.fields.items():
        val = f.get("value")
        if isinstance(val, Mapping) and "conflict" in val:
            hit = _mentions(c.comment, _SETTLED)
            if hit:
                reasons.append(f"{key} carries a recorded source disagreement "
                               f"({val['conflict']}), so {hit!r} overstates it")

    # The platform's own verdicts are part of the evidence; a comment may not contradict them.
    verdicts = (packet.fields.get("three_verdicts") or {}).get("value")
    if isinstance(verdicts, Mapping):
        fwd = str(verdicts.get("forward_outlook", "")).lower()
        if ("not established" in fwd or "cannot be formed" in fwd) and \
                _mentions(c.comment, _GUIDANCE_CHANGE):
            reasons.append("the packet's own forward verdict says the guidance comparison is "
                           "not established")

    # A field the packet reports as missing may not be asserted as present.
    for key, phrase in (("revenue_actual", "revenue"), ("eps_actual", "eps")):
        f = packet.fields.get(key) or {}
        if f.get("state") in ("UNAVAILABLE", "UNKNOWN") and \
                re.search(rf"\b{phrase}\b\s+(of|was|came in at)\s", low):
            reasons.append(f"{key} is {f.get('state')} in this packet")


def _check_comment(c: Claim, reasons: list[str], packet: EvidencePacket) -> None:
    """Scoped prose check. SECONDARY to the structure, and said to be fallible."""
    if not c.comment:
        return
    stray = literal_numbers(c.comment)
    if stray:
        reasons.append(
            f"the comment writes its own figures ({', '.join(stray)}); quantities are rendered "
            f"from the packet and must be named, not typed")
    hit = _mentions(c.comment, _CAUSAL)
    if hit and c.kind is not ClaimKind.ATTRIBUTED_INTERPRETATION:
        reasons.append(f"causal language {hit!r} in a {c.kind.value} claim; a cause is never "
                       f"eligible from this evidence, and an attribution elsewhere in the "
                       f"narrative does not scope to this claim")
    hit = _mentions(c.comment, _COMPARISON)
    if hit and c.kind not in (ClaimKind.COMPARISON, ClaimKind.ATTRIBUTED_INTERPRETATION):
        reasons.append(f"comparison language {hit!r} in a {c.kind.value} claim")
    hit = _mentions(c.comment, _GUIDANCE_CHANGE)
    if hit and c.kind is not ClaimKind.GUIDANCE_CHANGE:
        reasons.append(f"change-of-guidance language {hit!r} in a {c.kind.value} claim")


def check_claim(c: Claim, packet: EvidencePacket) -> ClaimVerdict:
    reasons: list[str] = []
    qs = []
    for qid in c.quantity_ids:
        q = packet.quantities.get(qid)
        if q is None:
            reasons.append(f"{qid} is not a quantity in packet {packet.report_id}")
        else:
            qs.append(q)

    if c.kind is ClaimKind.REPORTED_FIGURE:
        if not qs:
            reasons.append("a reported figure must name at least one quantity")
        for q in qs:
            if q.kind == "guidance":
                reasons.append(f"{q.qid} is GUIDANCE, a forecast — it cannot be stated as a "
                               f"reported result")
            if not q.identified:
                reasons.append(f"{q.qid} is missing {', '.join(q.missing_identity())}")

    elif c.kind is ClaimKind.COMPARISON:
        if len(qs) != 2:
            reasons.append("a comparison must name exactly two quantities")
        else:
            cmp_ = packet.comparison(qs[0].qid, qs[1].qid)
            if cmp_ is None:
                reasons.append(f"no comparability was computed for {qs[0].qid} vs {qs[1].qid}")
            elif not cmp_.allowed:
                reasons.append(cmp_.reason)

    elif c.kind is ClaimKind.GUIDANCE_LEVEL:
        if not qs:
            reasons.append("a guidance level must name a guidance quantity")
        for q in qs:
            if q.kind != "guidance":
                reasons.append(f"{q.qid} is not guidance")

    elif c.kind is ClaimKind.GUIDANCE_CHANGE:
        if not packet.allows(ClaimKind.GUIDANCE_CHANGE):
            reasons.append(packet.reason(ClaimKind.GUIDANCE_CHANGE))

    elif c.kind is ClaimKind.PRICE_REACTION:
        if not packet.allows(ClaimKind.PRICE_REACTION):
            reasons.append(packet.reason(ClaimKind.PRICE_REACTION))

    elif c.kind is ClaimKind.PERIOD_IDENTITY:
        if not packet.allows(ClaimKind.PERIOD_IDENTITY):
            reasons.append(packet.reason(ClaimKind.PERIOD_IDENTITY))

    elif c.kind is ClaimKind.CAUSAL:
        reasons.append(packet.reason(ClaimKind.CAUSAL))

    elif c.kind is ClaimKind.ATTRIBUTED_INTERPRETATION:
        # Permitted, and ONLY as someone else's stated view with a resolvable record of it.
        if not c.attributed_to:
            reasons.append("an attributed interpretation must name who said it")
        if not c.source_evidence_id:
            reasons.append("an attributed interpretation must cite the record of the statement")
        elif c.source_evidence_id not in packet.evidence:
            reasons.append(f"{c.source_evidence_id} does not resolve in this packet")
    else:
        reasons.append(f"unrecognised claim kind {c.kind}")

    _check_comment(c, reasons, packet)
    _scoped_packet_checks(c, reasons, packet)
    return ClaimVerdict(claim=c, accepted=not reasons, reasons=reasons,
                        rendered=_render_claim(c, qs, packet) if not reasons else "")


def _render_claim(c: Claim, qs, packet: EvidencePacket) -> str:
    """THE FACTUAL CLAUSE IS OURS, not the model's."""
    if c.kind is ClaimKind.REPORTED_FIGURE:
        body = "; ".join(f"{q.metric.replace('_', ' ')} {render(q)}" for q in qs)
        out = f"Reported {body}."
    elif c.kind is ClaimKind.COMPARISON:
        a, b = qs
        out = (f"{a.metric.replace('_', ' ')} {render(a, with_identity=False)} against "
               f"{render(b, with_identity=False)} ({a.basis}, {a.period}).")
    elif c.kind is ClaimKind.GUIDANCE_LEVEL:
        body = "; ".join(f"{q.metric.replace('_', ' ')} {render(q)}" for q in qs)
        out = f"Guidance issued with these results: {body}."
    elif c.kind is ClaimKind.PRICE_REACTION:
        r = (packet.fields.get("return_1d") or {}).get("value") or {}
        out = (f"The shares returned {r.get('pct'):+.2f}% over {r.get('window')}, a "
               f"close-to-close window that may span more than one interval and include "
               f"pre-announcement trading.")
    elif c.kind is ClaimKind.PERIOD_IDENTITY:
        f = (packet.fields.get("fiscal_period") or {}).get("value") or {}
        out = f"These are {f.get('label')} results (period ended {f.get('period_end')})."
    elif c.kind is ClaimKind.ATTRIBUTED_INTERPRETATION:
        out = f"{c.attributed_to}: {c.comment}"
        return out
    else:
        out = ""
    return f"{out} {c.comment}".strip() if c.comment else out


def narrate(claims, packet: EvidencePacket, *, deterministic: str) -> NarrationResult:
    """Accept a whole set of claims, or publish the deterministic text. Never a partial set.

    ALL OR NOTHING, on purpose. Publishing the surviving claims would quietly change what the
    narrative says — dropping the qualifying sentence from a pair leaves the unqualified one
    standing alone, which is worse than not narrating.
    """
    if not packet.verify():
        return NarrationResult(
            accepted=False, text=deterministic, used_fallback=True,
            packet_hash=packet.packet_hash, policy_version=packet.eligibility_policy_version,
            verdicts=[ClaimVerdict(claim=Claim(kind=ClaimKind.REPORTED_FIGURE), accepted=False,
                                   reasons=["the packet does not match its recorded hash; its "
                                            "contents changed after it was sealed"])])
    verdicts = [check_claim(c, packet) for c in claims]
    ok = bool(verdicts) and all(v.accepted for v in verdicts)
    return NarrationResult(
        accepted=ok,
        verdicts=verdicts,
        text=" ".join(v.rendered for v in verdicts if v.rendered) if ok else deterministic,
        used_fallback=not ok,
        packet_hash=packet.packet_hash,
        policy_version=packet.eligibility_policy_version,
    )
