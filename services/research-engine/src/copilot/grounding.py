"""Check a model draft against its packet, then render it. All or nothing.

THE RULE. The model may write words. It may not write figures: every number in an answer comes
from a `{{key}}` placeholder that names a packet quantity, and the text for that quantity is the
packet's own display string. A draft containing a digit outside a placeholder is REFUSED, not
repaired — the same refusal `intelligence.quantities.literal_numbers` exists for in the report
narration work, because a model that writes its own numbers will eventually write the wrong
ones, and nothing downstream can tell which.

WHAT THIS DOES NOT ESTABLISH. It proves every figure in the answer is a packet figure and every
citation resolves. It does not prove the words around them are a fair reading — "the shares are
cheap" next to a correctly rendered P/E is still an opinion. Interpretation is labelled as the
assistant's, and the limitations travel with every answer.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field as dc_field

from intelligence.quantities import literal_numbers

from .packet import CopilotPacket

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.:-]{1,64})\s*\}\}")

#: Words that present an engine score as a probability of making money. The platform's own
#: audits found signal confidence bands flat, so this framing is not supported by evidence.
_PROBABILITY_FRAMING = ("chance of profit", "probability of profit", "likely to make money",
                        "guaranteed", "risk-free", "can't lose", "cannot lose")

#: Recommending an order is outside a read-only assistant's scope.
_ORDER_LANGUAGE = ("place an order", "i have placed", "i've placed", "i bought", "i sold",
                   "order submitted")

MAX_ANSWER_CHARS = 4000


@dataclass
class GroundingResult:
    accepted: bool
    answer: str = ""
    limitations: list[str] = dc_field(default_factory=list)
    used_keys: list[str] = dc_field(default_factory=list)
    reasons: list[str] = dc_field(default_factory=list)


def parse_draft(raw: str) -> dict | None:
    """The model's JSON object, tolerating a code fence or leading prose around it."""
    if not raw:
        return None
    s = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", s, re.S)
    if fence:
        s = fence.group(1)
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(s[start:end + 1])
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def _allowed_literals(packet: CopilotPacket) -> set[str]:
    """Digit sequences that are identifiers, not figures: the symbol itself (0700.HK)."""
    return {m.strip() for m in literal_numbers(packet.symbol)}


def _check_text(text: str, packet: CopilotPacket, where: str, reasons: list[str],
                used: list[str]) -> None:
    for key in _PLACEHOLDER.findall(text):
        if key not in packet.quantities:
            reasons.append(f"{where} cites {{{{{key}}}}}, which is not a quantity in the "
                           f"evidence packet")
        elif key not in used:
            used.append(key)
    bare = _PLACEHOLDER.sub(" ", text).replace(packet.symbol, " ")
    allowed = _allowed_literals(packet)
    nums = [n for n in literal_numbers(bare) if n not in allowed]
    if nums:
        reasons.append(f"{where} writes figures directly ({', '.join(nums[:5])}); figures must "
                       f"come from {{{{key}}}} placeholders")
    low = text.lower()
    for w in _PROBABILITY_FRAMING:
        if w in low:
            reasons.append(f"{where} uses '{w}', which presents a score as a probability of "
                           f"profit")
    for w in _ORDER_LANGUAGE:
        if w in low:
            reasons.append(f"{where} uses '{w}'; this assistant cannot place or report orders")


def check(draft: dict | None, packet: CopilotPacket) -> GroundingResult:
    if draft is None:
        return GroundingResult(False, reasons=["the model did not return the required JSON "
                                               "object"])
    answer = draft.get("answer")
    lims = draft.get("limitations") or []
    reasons: list[str] = []
    used: list[str] = []
    if not isinstance(answer, str) or not answer.strip():
        return GroundingResult(False, reasons=["the draft has no answer text"])
    if len(answer) > MAX_ANSWER_CHARS:
        reasons.append(f"the answer is longer than {MAX_ANSWER_CHARS} characters")
    if not isinstance(lims, list) or not all(isinstance(x, str) for x in lims):
        reasons.append("limitations must be a list of strings")
        lims = []
    _check_text(answer, packet, "the answer", reasons, used)
    for i, lim in enumerate(lims[:8], 1):
        _check_text(lim, packet, f"limitation {i}", reasons, used)
    if reasons:
        return GroundingResult(False, reasons=reasons, used_keys=used)
    return GroundingResult(True, answer=render(answer, packet),
                           limitations=[render(x, packet) for x in lims[:8]], used_keys=used)


def render(text: str, packet: CopilotPacket) -> str:
    """Substitute each placeholder with the packet's display string — ours, never the model's."""
    def sub(m):
        q = packet.quantities[m.group(1)]
        return f"{q.display} [stale]" if q.state == "STALE" else q.display
    return _PLACEHOLDER.sub(sub, text)


def fallback_answer(packet: CopilotPacket, reasons: list[str]) -> str:
    """What the user sees when no draft passed: the evidence itself, rendered deterministically.

    Deliberately not the rejected draft with its numbers removed — a sentence written around a
    figure the model invented is not made honest by deleting the figure.
    """
    lines = ["I couldn't produce an answer that passed the evidence check, so here is the "
             "evidence on file instead."]
    for key in ("price.close", "price.date", "return.5s", "return.20s", "trend.structure",
                "signal.action", "signal.strength", "signal.time", "kscore.score",
                "earnings.date", "report.version"):
        q = packet.quantities.get(key)
        if q is not None:
            lines.append(f"• {key}: {q.display}" + (" [stale]" if q.state == "STALE" else ""))
    return "\n".join(lines)
