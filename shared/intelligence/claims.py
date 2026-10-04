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


#: WHICH ROLE A QUANTITY MUST PLAY for each claim kind. Identity and comparability were not
#: enough: an EPS *estimate* is correctly identified, correctly typed and entirely comparable —
#: and publishing it as "Reported EPS $31.82" states a number the company never reported. A
#: figure's role is a separate fact from what it measures.
_REQUIRED_ROLE = {
    "reported_figure": ("actual",),
    "guidance_level": ("guidance",),
}

#: Claim kinds whose text is produced ENTIRELY by us. They carry no free comment at all: an
#: arbitrary sentence appended to a correctly rendered figure inherits none of the rendering's
#: guarantees, and "Revenue was fifty billion dollars" needs no digits to contradict the
#: $54.23B printed immediately before it.
_NO_FREE_COMMENT = {"reported_figure", "comparison", "guidance_level", "guidance_change",
                    "price_reaction", "period_identity"}


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

    # FREE PROSE IS OUTSIDE THE DETERMINISTIC GUARANTEE, so it is not allowed inside a claim
    # whose text we generate. Checked before anything else, because it applies regardless of
    # whether the rest of the claim is sound.
    if c.comment and c.kind.value in _NO_FREE_COMMENT:
        reasons.append(
            f"a {c.kind.value} claim carries no free comment: its sentence is rendered from the "
            f"packet, and appended prose inherits none of that guarantee. Commentary belongs in "
            f"an attributed_interpretation bound to a recorded statement.")

    if c.kind is ClaimKind.REPORTED_FIGURE:
        if not qs:
            reasons.append("a reported figure must name at least one quantity")
        for q in qs:
            roles = _REQUIRED_ROLE["reported_figure"]
            if q.kind not in roles:
                reasons.append(
                    f"{q.qid} is an {q.kind.upper()}, not a reported result. Publishing it as "
                    f"one states a figure the issuer never reported.")
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
            if q.kind not in _REQUIRED_ROLE["guidance_level"]:
                reasons.append(f"{q.qid} is an {q.kind.upper()}, not guidance")

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
        # AN ATTRIBUTION IS A QUOTATION, NOT A LABEL. A resolvable evidence id was not enough:
        # "Chief executive: Demand caused the rally" cited a record containing no statement and
        # no speaker, so the attribution was decoration over the platform's own assertion —
        # which is exactly the causal claim that is never eligible.
        reasons.extend(_check_attribution(c, packet))
    else:
        reasons.append(f"unrecognised claim kind {c.kind}")

    _check_comment(c, reasons, packet)
    _scoped_packet_checks(c, reasons, packet)
    return ClaimVerdict(claim=c, accepted=not reasons, reasons=reasons,
                        rendered=_render_claim(c, qs, packet) if not reasons else "")


#: Where a recorded statement keeps its speaker and its words.
_SPEAKER_KEYS = ("speaker", "attributed_to", "author")
_PASSAGE_KEYS = ("statement", "quote", "passage", "text")


def _normalise(t: str) -> str:
    """Lowercase, strip punctuation, and COLLAPSE whitespace.

    The collapse is load-bearing: a sentence-ending period becomes a space, so joining two
    sentences produced one space where normalising the same two together produced two, and an
    exact multi-sentence quotation compared unequal to itself.
    """
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", t.lower())).strip()


def _dig(record, keys):
    """Look for a key at the top of an evidence record or inside its `value`."""
    for src in (record, record.get("value") if isinstance(record, Mapping) else None):
        if isinstance(src, Mapping):
            for k in keys:
                if src.get(k):
                    return str(src[k])
    return None


def _check_attribution(c: Claim, packet: EvidencePacket) -> list[str]:
    out: list[str] = []
    if not c.attributed_to:
        out.append("an attributed interpretation must name who said it")
    if not c.comment:
        out.append("an attributed interpretation must carry the statement being attributed")
    if not c.source_evidence_id:
        out.append("an attributed interpretation must cite the record of the statement")
        return out
    record = packet.evidence.get(c.source_evidence_id)
    if record is None:
        out.append(f"{c.source_evidence_id} does not resolve in this packet")
        return out

    speaker = _dig(record, _SPEAKER_KEYS)
    passage = _dig(record, _PASSAGE_KEYS)
    if not passage:
        out.append(f"{c.source_evidence_id} records no statement "
                   f"({'/'.join(_PASSAGE_KEYS)}), so there is nothing to attribute. A citation "
                   f"that resolves to a document is not a citation of something said in it.")
    if not speaker:
        out.append(f"{c.source_evidence_id} records no speaker "
                   f"({'/'.join(_SPEAKER_KEYS)})")
    if speaker and c.attributed_to and _normalise(c.attributed_to) not in _normalise(speaker) \
            and _normalise(speaker) not in _normalise(c.attributed_to):
        out.append(f"the claim attributes this to {c.attributed_to!r} while the record names "
                   f"{speaker!r}")
    if passage and c.comment and not _is_whole_sentences_of(c.comment, passage):
        out.append(
            "the attributed text is not a complete sentence (or run of consecutive sentences) "
            "from the recorded statement. CONTAINMENT IS NOT FIDELITY: 'improve through the "
            "quarter' is a substring of 'Demand did not improve through the quarter' and means "
            "the opposite of it. Quote whole statements so negation and qualification travel "
            "with the words.")
    return out


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    return [p for p in (x.strip() for x in parts) if p]


def _is_whole_sentences_of(quote: str, passage: str) -> bool:
    """True only when the quote is one or more CONSECUTIVE whole sentences of the passage.

    A substring test cannot preserve meaning. Dropping a leading negation or a trailing
    qualification reverses a statement while every word in it remains verbatim, so the unit of
    quotation is the sentence, not the character range.
    """
    want = _normalise(quote)
    if not want:
        return False
    sents = [_normalise(x) for x in _sentences(passage)]
    for i in range(len(sents)):
        run = ""
        for j in range(i, len(sents)):
            run = f"{run} {sents[j]}".strip()
            if run == want:
                return True
            if len(run) > len(want):
                break
    return False


def _render_claim(c: Claim, qs, packet: EvidencePacket) -> str:
    """THE FACTUAL CLAUSE IS OURS, not the model's."""
    if c.kind is ClaimKind.REPORTED_FIGURE:
        body = "; ".join(f"{q.metric.replace('_', ' ')} {render(q)}" for q in qs)
        out = f"Reported {body}."
        # A RECORDED DISAGREEMENT TRAVELS WITH THE FIGURE, deterministically. Previously this
        # depended on a narrator choosing not to write "confirmed" — which is the wrong place
        # for the guarantee to live now that prose is gone.
        notes = []
        for q in qs:
            f = packet.fields.get(q.source_field) or {}
            val = f.get("value")
            if isinstance(val, Mapping) and val.get("conflict"):
                notes.append(f"{q.metric.replace('_', ' ')}: {val['conflict']}")
        if notes:
            out += " Sources disagree — " + "; ".join(notes) + "."
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
        # Marked as reported speech. The platform is not making this claim and says so.
        return (f"{c.attributed_to} stated: \u201c{c.comment}\u201d "
                f"(reported statement, not a finding of this report; "
                f"source {c.source_evidence_id}).")
    else:
        out = ""
    return out


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
