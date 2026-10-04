"""N1 — checking a draft narrative against the packet it claims to be written from.

THE RULE THIS ENFORCES. A narrative is accepted only if every number in it appears in the
packet, every claim KIND it makes is eligible, and it contradicts neither the packet's own
figures nor its verdicts. Failing any of those returns the DETERMINISTIC text instead — never
a repaired or partially-accepted draft, because a sentence that has been edited to pass a
check is no longer the output that was checked.

WHY CONTRADICTION IS CHECKED SEPARATELY FROM SUPPORT. A sentence can cite only real numbers and
still assert the opposite of the report: "revenue missed" beside a packet whose comparison is
not eligible uses no invented figure at all. Support and consistency are different properties
and a validator that checks one silently passes failures of the other.

NO MODEL IS CALLED HERE. This grades text that something else produced.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field

from .evidence_packet import Claim, EvidencePacket
from .report_contract import FieldState

#: Words that assert a comparison outcome. Each needs Claim.COMPARISON to be eligible.
_COMPARISON_WORDS = (
    "beat", "beats", "missed", "miss", "above expectations", "below expectations",
    "ahead of estimates", "short of estimates", "outperformed", "topped", "fell short",
    "exceeded", "surprise to the upside", "surprise to the downside",
)

#: Words asserting a guidance CHANGE as opposed to a guidance LEVEL.
_GUIDANCE_CHANGE_WORDS = (
    "raised guidance", "raised its guidance", "lifted guidance", "lowered guidance",
    "cut guidance", "maintained guidance", "reaffirmed guidance", "guidance raise",
    "beat and raise", "raised its outlook", "upgraded its outlook",
)

#: Words asserting causation. Always ineligible.
_CAUSAL_WORDS = (
    "because", "caused by", "due to the", "drove the", "driven by", "as a result of",
    "led to the", "priced in", "on the back of", "thanks to",
)

#: Hedged alternatives that are NOT causal claims, so they must not trip the causal check.
_CAUSAL_SAFE = ("coincided with", "reported catalyst", "according to", "management attributed")

_NUM = re.compile(r"-?\$?\d[\d,]*\.?\d*\s*%?")


@dataclass
class Violation:
    kind: str
    detail: str
    excerpt: str = ""


@dataclass
class ValidationResult:
    accepted: bool
    violations: list[Violation] = dc_field(default_factory=list)
    packet_hash: str = ""
    #: The text to publish. The draft when accepted; the deterministic text when not.
    text: str = ""
    used_fallback: bool = False


def _numbers(text: str) -> set[str]:
    out = set()
    for m in _NUM.finditer(text):
        t = m.group(0).strip().replace(",", "").replace("$", "").rstrip("%").rstrip(".")
        if t and t not in ("", "-"):
            out.add(t)
    return out


def _scaled(v: float) -> set[float]:
    """A value and the scalings a writer legitimately uses for it.

    54230000000.0 is correctly written "54.23" (billion). Matching on the raw digits alone would
    reject every correct sentence, which teaches a reader to ignore the validator.
    """
    out = {float(v)}
    for scale in (1e3, 1e6, 1e9, 1e12):
        if abs(v) >= scale:
            out.add(float(v) / scale)
    return out


def _packet_values(packet: EvidencePacket) -> set[float]:
    """Every number the packet contains, as floats, with their common scalings.

    NUMERIC, NOT TEXTUAL. Comparing strings made "54.0" a different number from "54" and
    "3.030" a different number from "3.03" — a validator that rejects correct text is worse
    than none, because it gets switched off.
    """
    out: set[float] = set()

    def add(v):
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float)):
            out.update(_scaled(float(v)))
            return
        if isinstance(v, str):
            for t in _numbers(v):
                try:
                    out.update(_scaled(float(t)))
                except ValueError:
                    pass
            return
        if isinstance(v, dict):
            for x in v.values():
                add(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                add(x)

    for f in packet.fields.values():
        add(f.get("value"))
    return out


def _matches(token: str, values: set[float], *, tol: float = 1e-6) -> bool:
    try:
        n = float(token)
    except ValueError:
        return True   # not a number we can judge; the text checks cover wording
    return any(abs(n - v) <= tol * max(1.0, abs(v)) for v in values)


def _fmt(v: float) -> str:
    s = f"{v:.10f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _mentions(text: str, words) -> str | None:
    low = text.lower()
    for w in words:
        if w in low:
            return w
    return None


def validate(draft: str, packet: EvidencePacket, *, deterministic: str) -> ValidationResult:
    """Grade a draft against its packet. Accepts or falls back; never repairs."""
    v: list[Violation] = []
    low = draft.lower()

    # 1. EVERY NUMBER MUST BE IN THE PACKET.
    known = _packet_values(packet)
    for n in _numbers(draft):
        if not _matches(n, known):
            v.append(Violation("unsupported_number",
                               f"{n} does not appear anywhere in packet {packet.report_id}", n))

    # 2. EVERY CLAIM KIND MUST BE ELIGIBLE.
    hit = _mentions(draft, _COMPARISON_WORDS)
    if hit and not packet.allows(Claim.COMPARISON):
        v.append(Violation("ineligible_comparison", packet.reason(Claim.COMPARISON), hit))
    hit = _mentions(draft, _GUIDANCE_CHANGE_WORDS)
    if hit and not packet.allows(Claim.GUIDANCE_CHANGE):
        v.append(Violation("ineligible_guidance_change",
                           packet.reason(Claim.GUIDANCE_CHANGE), hit))
    hit = _mentions(draft, _CAUSAL_WORDS)
    if hit and not _mentions(draft, _CAUSAL_SAFE):
        v.append(Violation("causal_claim", packet.reason(Claim.CAUSAL), hit))

    # 3. A PRICE MOVE MUST CARRY ITS WINDOW. A bare percentage reads as the announcement's
    #    effect; this one spans more than one interval and includes pre-announcement trading.
    r1 = (packet.fields.get("return_1d") or {}).get("value")
    if isinstance(r1, dict) and r1.get("pct") is not None:
        pct = _fmt(float(r1["pct"]))
        if any(_matches(t, _scaled(float(r1["pct"]))) for t in _numbers(draft)):
            window = str(r1.get("window") or "")
            parts = [p for p in re.findall(r"\d{4}-\d{2}-\d{2}", window)]
            if not any(p in draft for p in parts) and "close-to-close" not in low \
                    and "close to close" not in low:
                v.append(Violation(
                    "price_move_without_window",
                    "the return is stated without the window it spans; it is a close-to-close "
                    "return that can include pre-announcement trading and does not isolate the "
                    "announcement", pct))

    # 4. A HISTORICAL SNAPSHOT MUST BE FRAMED AS ONE.
    if packet.historical:
        present = ("is now", "currently", "as of today", "the latest results", "most recent")
        hit = _mentions(draft, present)
        if hit:
            v.append(Violation(
                "historical_presented_as_current",
                "this packet is not the current view of its subject and must be written as what "
                "was known at its cutoff", hit))

    # 5. CONTRADICTION WITH THE PACKET'S OWN VERDICTS.
    verdicts = (packet.fields.get("three_verdicts") or {}).get("value")
    if isinstance(verdicts, dict):
        fwd = str(verdicts.get("forward_outlook", "")).lower()
        if "not established" in fwd or "cannot be formed" in fwd:
            hit = _mentions(draft, _GUIDANCE_CHANGE_WORDS)
            if hit:
                v.append(Violation(
                    "contradicts_verdict",
                    "the packet's own forward verdict says the guidance comparison is not "
                    "established", hit))

    # 6. A FIELD THE PACKET REPORTS AS MISSING MUST NOT BE ASSERTED AS PRESENT.
    for key, phrase in (("revenue_actual", "revenue"), ("eps_actual", "eps")):
        f = packet.fields.get(key) or {}
        if f.get("state") in (FieldState.UNAVAILABLE.value, FieldState.UNKNOWN.value):
            if re.search(rf"\b{phrase}\b\s+(of|was|came in at)\s", low):
                v.append(Violation(
                    "asserts_missing_field",
                    f"{key} is {f.get('state')} in this packet", phrase))

    # 7. A FIGURE SOURCES DISAGREE ON MUST NOT BE PRESENTED AS SETTLED.
    #    Both values are in the packet, so a number check passes either one. The defect is the
    #    certainty, not the digits — "confirmed by all sources" over a recorded disagreement is
    #    a false claim built entirely from true numbers.
    _SETTLED = ("confirmed", "verified", "all sources agree", "as reported by all",
                "unambiguous", "definitively")
    for key, f in packet.fields.items():
        val = f.get("value")
        if not isinstance(val, dict) or "conflict" not in val:
            continue
        hit = _mentions(draft, _SETTLED)
        if hit:
            v.append(Violation(
                "conflict_presented_as_settled",
                f"{key} carries a recorded source disagreement: {val['conflict']}", hit))
        # Stating the PROVIDER's side as the figure, without saying it is disputed, presents
        # the non-authoritative value as the answer.
        pv = val.get("provider_value")
        authoritative = val.get("value")
        # Only flag when the draft uses the PROVIDER's figure and not the issuer's: both are in
        # the packet, so the number check alone cannot tell which side was quoted.
        quoted_provider = pv is not None and any(
            _matches(t, _scaled(float(pv))) for t in _numbers(draft)) and not (
            authoritative is not None and any(
                _matches(t, _scaled(float(authoritative))) for t in _numbers(draft)))
        if quoted_provider \
                and not _mentions(draft, ("differ", "disagree", "conflict", "provider")):
            v.append(Violation(
                "disputed_value_stated_as_fact",
                f"{key}: this is the provider's figure, which the issuer's own release "
                f"contradicts; the disagreement is not mentioned", _fmt(float(pv))))

    accepted = not v
    return ValidationResult(
        accepted=accepted, violations=v, packet_hash=packet.packet_hash,
        text=draft if accepted else deterministic, used_fallback=not accepted)
