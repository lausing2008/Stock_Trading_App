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

# ── Digits that are NAMES, not figures ──────────────────────────────────────
# Measured on claude-sonnet-4-6 (docs/audits/2026-10-13-copilot-real-model-eval.md): 19 of 34
# first drafts were refused and almost every refusal was one of these three, not an invented
# figure. Each exemption is as narrow as the case that motivated it; every other digit is still
# refused.

#: Index names. Saying "the packet has no S&P 500 data" is the CORRECT answer to a benchmark
#: question, and refusing it pushed the model toward vaguer wording.
_INDEX_NAMES = re.compile(
    r"S&P\s?500|S&P\s?400|S&P\s?600|Nasdaq[- ]100|NASDAQ[- ]100|Russell\s?(?:1000|2000|3000)|"
    r"Dow\s?30|FTSE\s?100|Nikkei\s?225|Euro\s?Stoxx\s?50|CSI\s?300|Hang\s?Seng\s?Tech",
    re.I)

#: Window labels — ONLY windows the EVIDENCE itself names: the packet's own (5, 20, 50
#: sessions) plus any "N-bar"/"N-session" window appearing in a packet display, such as a
#: report field labelled "63-bar return". "Sessions"/"bars", never "days": a calendar-day label
#: would misdescribe these values.
_WINDOW_RE = r"\b({n})(?:[- ](?:trading|daily))?[- ](?:session|bar)s?\b"
_WINDOW_IN_EVIDENCE = re.compile(r"\b(\d{1,3})(?:[- ](?:trading|daily))?[- ](?:session|bar)s?\b",
                                 re.I)
_PACKET_WINDOWS = {"5", "20", "50"}
#: Plan mode: outcome windows are CALENDAR days ("after 5 calendar days"). Exempt only in that
#: exact wording and only for windows a packet display itself names — never "5 days".
_CAL_WINDOW_RE = r"\b({n})[- ]calendar[- ]days?\b"
_CAL_WINDOW_IN_EVIDENCE = re.compile(r"\b(\d{1,3})[- ]calendar[- ]days?\b", re.I)

#: Period labels are exempt ONLY when the same label occurs in a headline in the packet, i.e.
#: the model is quoting a period a source named. A period the model supplies itself is refused:
#: a wrong fiscal-period label is a real failure here (MU's Q4 stored as "Q3", see
#: docs/incidents/inferred-fiscal-period-mislabels-non-calendar-years.md).
_PERIOD_LABEL = re.compile(r"\b(?:Q[1-4]|H[12]|FY\s?'?\d{2,4})\b")

#: Figures written in WORDS. "a price target of five hundred dollars" passed the digit rule in
#: the eval (q08) — the bypass the N1 review documented as "fifty billion dollars". A magnitude
#: or unit word is refused outright; small counts ("two headlines") remain allowed.
_SPELLED_MAGNITUDE = re.compile(
    r"\b(?:hundred|thousand|million|billion|trillion|percent|per cent|basis points?)\b", re.I)

# ── R1 (independent review, 2026-10-13): quantities WITHOUT digits ─────────────
# "The target is twenty dollars" and "The price will double" were accepted as grounded. A
# quantity needs no digit and no magnitude word. Each family below names an amount, a ratio or
# a period; every one is refused outside a placeholder. What remains allowed is a small count of
# things IN THE PACKET ("two headlines", "one reading"), which is not a market quantity.

#: SMALL counts (one..twelve) are mostly counts of things IN THE PACKET ("two headlines", "the
#: two moving averages", "one reading"), so they are refused only when a UNIT follows ("two
#: points", "one week", "three dollars"). Larger number words ("twenty", "fifty-five") are
#: almost always amounts and are always refused. Measured on held-out questions (2026-10-13):
#: a noun allow-list for small counts refused "Two recent headlines (...)" and "the two should
#: not be treated as equal" — correct counts, not quantities.
_SMALL = r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
_LARGE = (r"zero|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|"
          r"forty|fifty|sixty|seventy|eighty|ninety|dozen|dozens")
_NUMBER_WORD = f"{_SMALL}|{_LARGE}"
_NUMBER_WORD_RE = re.compile(rf"\b(?:{_NUMBER_WORD})(?:[- ](?:{_NUMBER_WORD}))*\b", re.I)
_LARGE_RE = re.compile(rf"\b(?:{_LARGE})\b", re.I)
_UNIT_AFTER = re.compile(
    r"[- ](?:points?|weeks?|days?|months?|years?|sessions?|bars?|shares?|times|x|percentage[- ]points?|"
    r"basis|cents?|dollars?|quarters?|lots?|units?|hundred|thousand|million|billion)\b", re.I)
#: Multipliers say how much something changed ("will double", "rose threefold").
_MULTIPLIER = re.compile(
    r"\b(?:double[sd]?|doubling|triple[sd]?|tripling|quadruple[sd]?|halve[sd]?|halving|twice|"
    r"thrice|\w+fold)\b", re.I)
#: A multiplier the answer declines to assert ("cannot confirm whether it has doubled") is not a
#: claim. Measured: the held-out "Has it doubled this year?" was refused for exactly that.
_UNASSERTED_BEFORE = re.compile(
    r"(?:\bnot\b|\bno\b|n't|\bnever\b|\bwhether\b|\bif\b)[^.;:!?]{0,40}$", re.I)
#: "third-party" is not a fraction (measured: "a third-party headline" was refused).
_FRACTION = re.compile(
    r"\b(?:half|halves|(?:a|one)[- ]third(?![- ]party)|two[- ]thirds|a\s+quarter\s+of|"
    r"three[- ]quarters|a\s+fifth|a\s+tenth|fraction\s+of)\b", re.I)
#: A PLURAL currency word implies an amount ("twenty dollars", "0700.HK dollars"); a singular
#: one is usually descriptive ("a dollar figure is not available") and is refused only after a
#: number word.
_CURRENCY_PLURAL = re.compile(r"\b(?:dollars|cents|bucks|euros|pounds)\b", re.I)
_CURRENCY_SINGULAR = re.compile(r"\b(?:dollar|cent|euro|pound|yuan|renminbi|yen)\b", re.I)
#: A fiscal period spelled out — the same invented-period risk as "Q4" (see _PERIOD_LABEL).
_ORDINAL_PERIOD = re.compile(
    r"\b(?:first|second|third|fourth)[- ](?:quarter|half|fiscal)\b|\bfiscal\s+year\b", re.I)


def _quantity_words(text: str) -> list[str]:
    """Quantities written without digits. Counts of packet items are not quantities."""
    found: list[str] = []
    for m in _NUMBER_WORD_RE.finditer(text):
        w = m.group(0)
        if _LARGE_RE.search(w) or _UNIT_AFTER.match(text, m.end()):
            found.append(w)
    for m in _MULTIPLIER.finditer(text):
        if not _UNASSERTED_BEFORE.search(text[:m.start()]):
            found.append(m.group(0))
    found += [m.group(0) for m in _FRACTION.finditer(text)]
    found += [m.group(0) for m in _CURRENCY_PLURAL.finditer(text)]
    for m in _CURRENCY_SINGULAR.finditer(text):
        if re.search(rf"\b(?:{_NUMBER_WORD})\s*$", text[:m.start()], re.I):
            found.append(m.group(0))
    found += [m.group(0) for m in _ORDINAL_PERIOD.finditer(text)]
    return found


#: A probability phrase is refused unless it is NEGATED — the platform's own display strings say
#: "not a probability of profit", and a model repeating that disclaimer is doing the right thing.
_NEGATION_BEFORE = re.compile(r"(?:\bnot\b|\bno\b|n't|\bnever\b|\bnor\b)[^.;:!?]{0,24}$", re.I)


# ── Plan mode: no model-authored price predictions ─────────────────────────────
# The platform stores no price forecast (docs/features/2026-10-14-copilot-plan-mode-design.md
# §1). An answer may report the engine's CALLS and how past calls turned out; conditional
# levels come from the code-rendered rules ({{rule.e1}}). Model prose may not assert where the
# price WILL go — conditionally or not.
#
# REVIEW C4 (2026-10-15): the first version exempted a whole SENTENCE when it contained "if",
# "once", "unless" or "when", so "If you are wondering about next week, the stock will rise"
# passed. A condition word does not show the prediction depends on it. Conditions are no longer
# an exemption at all; only bounded denials of the prediction ("I cannot say whether it will
# rise") are exempt. Unrelated negation or "whether" cannot excuse a forecast. "Will not fall"
# is a prediction too, and is refused.
_FUTURE_MOVE = re.compile(
    r"\b(?:will(?:\s+not|\s+never)?|won't|(?:is|are|'s|'re)\s+(?:going|set|poised|expected|likely|"
    r"bound|sure|certain)\s+to|should|bound\s+to|sure\s+to|certain\s+to|expected\s+to)\s+"
    r"(?:likely\s+|probably\s+|soon\s+|continue\s+to\s+|keep\s+)?(?:rise|rises|fall|falls|drop|"
    r"climb|rally|decline|go\s+(?:up|down|higher|lower)|move\s+(?:up|down|higher|lower)|hit|reach|"
    r"break\s+(?:out|down|above|below)|bounce|recover|rebound|surge|soar|plunge|crash|tank|"
    r"outperform|underperform|gain|lose\s+value|trend\s+(?:up|down|higher|lower)|head\s+(?:up|"
    r"down|higher|lower))\b", re.I)
_FUTURE_NEGATED = re.compile(
    r"(?:\b(?:cannot|can't|can not|could not|couldn't|do not|don't|does not|doesn't|"
    r"did not|didn't|never)\s+(?:say|predict|forecast|claim|show|establish|know|tell|"
    r"guarantee|conclude)\s+(?:(?:that|whether|if)\s+)?|"
    r"\bnothing\s+(?:here\s+)?(?:shows|establishes|says|predicts)\s+)"
    r"(?:(?:the|this)\s+)?(?:stock|price|shares|it|[A-Z]{1,5})\s*$", re.I)


def _unconditional_forecasts(text: str) -> list[str]:
    found = []
    for clause in re.split(r"[.;:!?,\n]|—|–", text):
        for m in _FUTURE_MOVE.finditer(clause):
            prefix, suffix = clause[:m.start()], clause[m.end():]
            denied = _FUTURE_NEGATED.search(prefix)
            # A bounded indirect question with an explicit denial, not arbitrary "whether".
            denied = denied or (re.fullmatch(r"\s*whether\s+it\s+", prefix, re.I)
                                and re.match(r"\s+is not something (?:the packet|the data) shows\s*$",
                                             suffix, re.I))
            if not denied:
                found.append(m.group(0))
    return found


@dataclass
class GroundingResult:
    accepted: bool
    answer: str = ""
    limitations: list[str] = dc_field(default_factory=list)
    used_keys: list[str] = dc_field(default_factory=list)
    reasons: list[str] = dc_field(default_factory=list)


_ANSWER_TAG = re.compile(r"<answer>(.*?)</answer>", re.S | re.I)
_LIMITATION_TAG = re.compile(r"<limitation>(.*?)</limitation>", re.S | re.I)


def parse_draft(raw: str) -> dict | None:
    """The model's draft as {"answer", "limitations"}.

    TAGS FIRST, JSON AS A FALLBACK. The first protocol was a JSON object, and the eval measured
    the failure that invites: Sonnet 4.6 quoted a headline inside the "answer" string with
    unescaped double quotes, producing invalid JSON on every repetition of the same question.
    Tagged sections have nothing to escape. Structured outputs would also fix it but are not
    available on every model this platform can be configured with (not on Sonnet 4.6).
    """
    if not raw:
        return None
    m = _ANSWER_TAG.search(raw)
    if m:
        return {"answer": m.group(1).strip(),
                "limitations": [x.strip() for x in _LIMITATION_TAG.findall(raw) if x.strip()]}
    lim_at = raw.lower().find("<limitation>")
    if lim_at > 0 and raw[:lim_at].strip():
        # Measured: a draft that used <limitation> tags but forgot to open <answer>. The text
        # before the first limitation IS the answer; it still passes through every check.
        return {"answer": raw[:lim_at].strip(),
                "limitations": [x.strip() for x in _LIMITATION_TAG.findall(raw) if x.strip()]}
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


def as_draft(answer: str, limitations: list[str] | None = None) -> str:
    """The output format, for re-presenting a stored answer in the conversation history."""
    lims = "".join(f"\n<limitation>{x}</limitation>" for x in (limitations or []))
    return f"<answer>\n{answer}\n</answer>{lims}"


def _allowed_literals(packet: CopilotPacket) -> set[str]:
    """Digit sequences that are identifiers, not figures: the symbol itself (0700.HK)."""
    return {m.strip() for m in literal_numbers(packet.symbol)}


def _headline_periods(packet: CopilotPacket) -> set[str]:
    return {m.group(0) for h in packet.headlines for m in _PERIOD_LABEL.finditer(h["title"])}


def _evidence_windows(packet: CopilotPacket) -> set[str]:
    found = {m.group(1) for q in packet.quantities.values()
             for m in _WINDOW_IN_EVIDENCE.finditer(q.display)}
    return _PACKET_WINDOWS | found


def _calendar_windows(packet: CopilotPacket) -> set[str]:
    return {m.group(1) for q in packet.quantities.values()
            for m in _CAL_WINDOW_IN_EVIDENCE.finditer(q.display)}


_PLATFORM_PHRASE = re.compile(r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten)\s+bars?\s+earlier\b", re.I)


def _platform_phrases(packet: CopilotPacket) -> set[str]:
    return {m.group(0).lower() for q in packet.quantities.values() for m in _PLATFORM_PHRASE.finditer(q.display)}


def _strip_names(text: str, packet: CopilotPacket) -> str:
    """Remove the digit-bearing NAMES the rules above exempt; what remains must hold no digit."""
    text = _INDEX_NAMES.sub(" ", text)
    cal = _calendar_windows(packet)
    if cal:
        text = re.sub(_CAL_WINDOW_RE.format(n="|".join(sorted(map(re.escape, cal)))), " ", text,
                      flags=re.I)
    windows = "|".join(sorted(map(re.escape, _evidence_windows(packet))))
    text = re.sub(_WINDOW_RE.format(n=windows), " ", text, flags=re.I)
    # "SMA20" / "SMA 50": the same windows, abbreviated (measured on Sonnet 4.6). SMA only —
    # the packet computes simple averages, so "EMA20" would name a value that is not in it.
    text = re.sub(rf"\bSMA[- ]?(?:{windows})\b", " ", text, flags=re.I)
    # "52-week high/low" is the conventional NAME of the year range, exempt only when the packet
    # carries that range (measured 2026-10-21: 2 answers refused for it). "up 52%" still fails.
    if "level.high252" in packet.quantities or "level.low252" in packet.quantities:
        text = re.sub(r"\b52[- ]week[- ](?=high|low|range)", " ", text, flags=re.I)
    quoted = _headline_periods(packet)
    return _PERIOD_LABEL.sub(lambda m: " " if m.group(0) in quoted else m.group(0), text)


#: A track record's WINDOW named beside that record. Measured on the plan-mode held-out run
#: (2026-10-14, Sonnet 4.6): 7 of 9 refusals were "SWING BUY, 5-day: {{record.swing.5d}}" or
#: "over five days: {{record.swing.5d}}" — an ACCURATE label (those records are 5 calendar
#: days), refused by a rule written for model-invented figures. Exempt ONLY within the same
#: sentence or bullet as a record/baseline placeholder whose key carries that window, so
#: "5-day return" beside a 5-BAR return is still refused.
_RECORD_KEY_WINDOW = re.compile(r"\{\{\s*(?:record|baseline)\.[a-z]+\.(\d{1,3})d(?:\.stock)?\s*\}\}")
_WINDOW_WORDS = {"5": "five", "7": "seven", "10": "ten", "14": "fourteen", "20": "twenty"}


def _strip_record_windows(text: str) -> str:
    out = []
    for seg in re.split(r"(\n|(?<=[.;!?])\s+)", text):
        ns = set(_RECORD_KEY_WINDOW.findall(seg))
        for n in ns:
            alt = re.escape(n) + (f"|{_WINDOW_WORDS[n]}" if n in _WINDOW_WORDS else "")
            seg = re.sub(rf"\b(?:{alt})(?:[- ]calendar)?[- ]days?\b", " ", seg, flags=re.I)
        out.append(seg)
    return "".join(out)


#: "i bought" opening a QUOTED example of how to phrase a position ("say \"I bought 100 shares
#: at …\"") is not a report of an order. Measured on the same run (p06/p07/p14).
_QUOTED_FIRST_PERSON = re.compile(r"[\"“'‘]\s*i (?:bought|sold)\b", re.I)


def _check_text(text: str, packet: CopilotPacket, where: str, reasons: list[str],
                used: list[str]) -> None:
    for key in _PLACEHOLDER.findall(text):
        if key not in packet.quantities:
            reasons.append(f"{where} cites {{{{{key}}}}}, which is not a quantity in the "
                           f"evidence packet")
        elif key not in used:
            used.append(key)
    text_w = _strip_record_windows(text)
    bare = _strip_names(_PLACEHOLDER.sub(" ", text_w).replace(packet.symbol, " "), packet)
    allowed = _allowed_literals(packet)
    nums = [n for n in literal_numbers(bare) if n not in allowed]
    if nums:
        reasons.append(f"{where} writes figures directly ({', '.join(nums[:5])}); figures must "
                       f"come from {{{{key}}}} placeholders")
    prose = _PLACEHOLDER.sub(" ", text_w)
    # The platform's OWN wording repeated verbatim (e.g. the 50-bar average "five bars earlier")
    # is not a model-authored quantity; any other spelled count still fails.
    for phrase in _platform_phrases(packet):
        prose = re.sub(rf"\b{re.escape(phrase)}\b", " ", prose, flags=re.I)
    spelled = _SPELLED_MAGNITUDE.findall(prose) + _quantity_words(prose)
    if spelled:
        reasons.append(f"{where} writes a quantity in words ({', '.join(spelled[:3])}); "
                       f"figures must come from {{{{key}}}} placeholders")
    low = text.lower()
    for w in _PROBABILITY_FRAMING:
        for m in re.finditer(re.escape(w), low):
            if not _NEGATION_BEFORE.search(low[:m.start()]):
                reasons.append(f"{where} uses '{w}', which presents a score as a probability "
                               f"of profit")
                break
    fc = _unconditional_forecasts(_PLACEHOLDER.sub(" ", text))
    if fc:
        reasons.append(f"{where} predicts a price move ('{fc[0]}'); the platform stores no price "
                       f"forecast — cite the engine's calls and records or the code-written "
                       f"rule keys; do not write a price prediction")
    low_orders = _QUOTED_FIRST_PERSON.sub(" ", low)
    for w in _ORDER_LANGUAGE:
        if w in low_orders:
            reasons.append(f"{where} uses '{w}'; this assistant cannot place or report orders")


def check(draft: dict | None, packet: CopilotPacket) -> GroundingResult:
    if draft is None:
        return GroundingResult(False, reasons=["the model did not return an <answer> section"])
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


#: What the deterministic fallback shows, with the label each bare value needs.
FALLBACK_KEYS = (
    ("price.close", "Latest close"), ("price.date", "Session date"),
    ("return.5b", "5-bar return"), ("return.20b", "20-bar return"),
    ("trend.structure", "Trend"), ("signal.action", "AI signal"),
    ("signal.strength", "Signal strength"), ("signal.time", "Signal generated"),
    ("kscore.score", "K-Score"), ("earnings.date", "Next earnings date"),
    ("report.version", "Latest report"),
)


#: Plan lines shown in the fallback, in reading order. They are code-written, so they need no
#: model to be shown — a plan question still gets its plan when the wording fails the check.
_PLAN_ORDER = ("call.", "record.", "baseline.", "moves.", "position.", "rule.")


def plan_fallback_keys(packet: CopilotPacket) -> list[str]:
    if packet.plan is None:
        return []
    keys = list(packet.plan.items)
    return [k for p in _PLAN_ORDER for k in keys if k.startswith(p)]


def fallback_keys(packet: CopilotPacket) -> list[str]:
    return [k for k, _ in FALLBACK_KEYS if k in packet.quantities] + plan_fallback_keys(packet)


def fallback_answer(packet: CopilotPacket, reasons: list[str]) -> str:
    """What the user sees when no draft passed: the evidence itself, rendered deterministically.

    Deliberately not the rejected draft with its numbers removed — a sentence written around a
    figure the model invented is not made honest by deleting the figure.
    """
    lines = ["I couldn't produce an answer that passed the evidence check, so here is the "
             "evidence on file instead."]
    for key, label in FALLBACK_KEYS:
        q = packet.quantities.get(key)
        if q is not None:
            lines.append(f"• {label}: {q.display}" + (" [stale]" if q.state == "STALE" else ""))
    plan_keys = plan_fallback_keys(packet)
    if plan_keys:
        lines.append("Plan (calculated by the platform, not by the model):")
        for k in plan_keys:
            q = packet.quantities[k]
            lines.append(f"• {q.display}" + (" [stale]" if q.state == "STALE" else ""))
    return "\n".join(lines)
