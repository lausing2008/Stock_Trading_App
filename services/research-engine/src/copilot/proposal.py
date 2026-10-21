"""An LLM-PROPOSED trade plan whose every price is traceable to stored market evidence.

The rules-based Game Plan is fixed code. The LLM plan is the model's own choice of levels, but
the model never writes a price: each level is an EVIDENCE ANCHOR from the packet (the latest
close, the 20-bar low/high, the 50-bar average, the year high/low) plus an offset in ATRs, e.g.

    <plan_proposal kind="pullback" entry="level.low20+0.25atr" stop="level.low20-1atr"
                   target1="level.high20" target2="level.high252"/>

Code computes each price, then independently checks direction, geometry, reward:risk, sizing,
freshness and the same mandatory gates the rules use. A proposal that fails is SHOWN with its
reasons and cannot be saved. Different numbers are not evidence of a better plan.
"""
from __future__ import annotations

import math
import re

from . import plan as P

ANCHORS = ("level.close", "level.low20", "level.high20", "level.sma50", "level.high252",
           "level.low252")
KINDS = ("pullback", "breakout", "limit", "hold", "wait")
MAX_ATR_MULT = 3.0           # an offset beyond 3 ATRs is no longer "near" the anchor
MIN_STOP_ATR = 0.25          # a stop inside a quarter ATR is noise, not a level
MAX_STOP_ATR = 4.0
MAX_ENTRY_ATR_FROM_CLOSE = 3.0
LIMIT_VALID_SESSIONS = 3

_TAG = re.compile(r"<plan_proposal\b([^>]*)/>", re.I)
_ATTR = re.compile(r'([a-z0-9_]+)\s*=\s*"([^"]*)"', re.I)
_EXPR = re.compile(r"^\s*(level\.[a-z0-9_]+)\s*(?:([+-])\s*(\d+(?:\.\d+)?)\s*atrs?)?\s*$", re.I)

FORMAT_HELP = ('Append exactly one <plan_proposal kind="pullback|breakout|limit|wait" entry="ANCHOR" '
               'stop="ANCHOR-Katr" target1="ANCHOR" target2="ANCHOR"/> tag. ANCHOR is one of '
               + ", ".join(ANCHORS) + f', optionally +/- up to {MAX_ATR_MULT:g} ATRs '
               '(e.g. level.low20-0.5atr). kind="wait" takes no levels. For a position you already '
               'hold use kind="hold" with stop, target1 and target2 only (the entry is the stated cost).')


def extract(text: str):
    """(clean_text, spec | None, format_error | None)."""
    tags = _TAG.findall(text)
    clean = _TAG.sub("", text)
    if len(tags) != 1 or "<plan_proposal" in clean.lower():
        return clean, None, FORMAT_HELP
    attrs = {k.lower(): v for k, v in _ATTR.findall(tags[0])}
    kind = attrs.get("kind", "").lower()
    if kind not in KINDS:
        return clean, None, FORMAT_HELP
    if kind == "wait":
        return clean, {"kind": "wait"}, None
    spec = {"kind": kind}
    for name in (("stop", "target1", "target2") if kind == "hold" else ("entry", "stop", "target1", "target2")):
        m = _EXPR.match(attrs.get(name, ""))
        if not m or m.group(1).lower() not in ANCHORS:
            return clean, None, f"{name} must be an anchor from the list, optionally +/- K atr. " + FORMAT_HELP
        mult = float(m.group(3) or 0) * (-1 if m.group(2) == "-" else 1)
        if abs(mult) > MAX_ATR_MULT:
            return clean, None, f"{name} offset {mult:g} ATR exceeds {MAX_ATR_MULT:g}. " + FORMAT_HELP
        spec[name] = {"anchor": m.group(1).lower(), "atr_multiple": mult}
    return clean, spec, None


def _size(plan, entry: float, per: float | None):
    """The rules' sizing contract (F05): floor(budget / risk), capped by a stated account."""
    pos = plan.position
    budget = pos.risk_budget if pos is not None else None
    if budget is None or not per:
        return budget, None, None
    shares = math.floor(budget / per)
    if pos.account is not None and shares * entry > pos.account:
        return budget, math.floor(pos.account / entry), (
            f"Capped at what the stated account of {P.money(pos.account, plan.currency)} can buy at "
            f"the entry reference; not a broker buying-power check.")
    return budget, shares, None


def build(plan, spec: dict, *, price_stale: str | None) -> dict:
    """The code-checked proposal. state: conditional | blocked | rejected | wait."""
    m = lambda v: P.money(v, plan.currency)
    base = dict(id="llm", key="proposal.llm", title="LLM-proposed plan", source="llm_proposal",
                kind=spec.get("kind"), derivation=[], reasons=[], playbook=[], eligibility_needs=[])
    if spec.get("kind") == "wait":
        return dict(base, state="wait", reasons=["The model proposed waiting: no entry now."])
    reasons, blocks = [], []
    if (spec["kind"] == "hold") != (plan.kind == "holding") or plan.kind not in ("entry", "holding"):
        return dict(base, state="rejected", reasons=[
            'Use kind="hold" for a held position and pullback/breakout/limit for a new entry; '
            'model-proposed levels are not available in this mode.'])
    if plan.unsupported or plan.position_refusals:
        return dict(base, state="rejected", reasons=["The rules-based plan refused these inputs, so no proposal can be checked."])
    if plan.atr is None or not plan.anchors:
        return dict(base, state="rejected", reasons=["Too little price history to compute anchors and ATR."])
    prices, derivation = {}, []
    for name in [n for n in ("entry", "stop", "target1", "target2") if n in spec]:
        a = spec[name]
        anchor = plan.anchors.get(a["anchor"])
        item = plan.items.get(a["anchor"])
        if anchor is None:
            return dict(base, state="rejected", reasons=[f"{name}: anchor {a['anchor']} is not available for this symbol."])
        if item is not None and item.stale:
            blocks.append(f"{name} anchor {a['anchor']} is stale ({item.stale}).")
        price = round(anchor + a["atr_multiple"] * plan.atr, 4)
        prices[name] = price
        derivation.append(dict(level=name, anchor=a["anchor"], anchor_price=anchor,
                               atr_multiple=a["atr_multiple"], atr=plan.atr, price=price))
    if price_stale:
        blocks = [f"Prices are stale: {price_stale}."]   # one reason; every anchor shares it
    close, atr = plan.anchors["level.close"], plan.atr
    if spec["kind"] == "hold":
        return _build_hold(plan, base, prices, derivation, blocks, close, atr, m)
    entry, stop, t1, t2 = prices["entry"], prices["stop"], prices["target1"], prices["target2"]
    # Geometry: a long trade, ordered levels.
    if not (entry > 0 and stop > 0 and stop < entry < t1 < t2):
        reasons.append(f"Levels must satisfy stop < entry < target 1 < target 2 (got stop {m(stop)}, "
                       f"entry {m(entry)}, targets {m(t1)} / {m(t2)}).")
    per = entry - stop if entry > stop else None
    if per is not None:
        if per < MIN_STOP_ATR * atr:
            reasons.append(f"Stop is {per / atr:.2f} ATR from the entry; below {MIN_STOP_ATR:g} ATR it sits inside normal daily noise.")
        if per > MAX_STOP_ATR * atr:
            reasons.append(f"Stop is {per / atr:.2f} ATR from the entry; beyond {MAX_STOP_ATR:g} ATR it is not a structural stop.")
    if abs(entry - close) > MAX_ENTRY_ATR_FROM_CLOSE * atr:
        reasons.append(f"Entry {m(entry)} is more than {MAX_ENTRY_ATR_FROM_CLOSE:g} ATR from the latest close {m(close)}.")
    kind = spec["kind"]
    if kind == "breakout" and entry < close:
        reasons.append(f"A breakout level must be at or above the latest close ({m(close)}); {m(entry)} has already been passed.")
    if kind in ("pullback", "limit") and entry > close:
        reasons.append(f"A {kind} entry must be at or below the latest close ({m(close)}); {m(entry)} is above it.")
    rr1 = (t1 - entry) / per if per else None
    if rr1 is not None and rr1 + 1e-9 < P.MIN_REWARD_RISK:
        reasons.append(f"Target 1 is {rr1:.2f}R; the minimum is {P.MIN_REWARD_RISK:g}R.")
    if reasons:
        return dict(base, state="rejected", reasons=reasons, derivation=derivation,
                    entry=entry, stop=stop, targets=[{"price": t1, "label": "Model-chosen", "reward_risk": rr1},
                                                     {"price": t2, "label": "Model-chosen", "reward_risk": (t2 - entry) / per if per else None}])
    # Mandatory gates the rules apply to every new long (not model-overridable).
    if plan.trend_blocked:
        blocks.append("Latest close is below a falling 50-bar average; no new long under the trend rule.")
    budget, shares, sizing_note = _size(plan, entry, per)
    if shares == 0:
        blocks.append("The risk budget or stated account cannot cover one whole share.")
    max_fill = P._no_chase_limit(t1, stop)
    vol = plan.avg_volume20 or 0
    trigger = {
        "pullback": (f"Buy only after a dip to {m(entry)} and a daily close that holds at or above it; "
                     f"stop {m(stop)}. Do not fill above {m(max_fill)}."),
        "breakout": (f"Buy after a daily close above {m(entry)} on volume above {P.BREAKOUT_VOLUME_MULT:g} × "
                     f"the 20-bar average ({vol * P.BREAKOUT_VOLUME_MULT:,.0f} shares); fill no higher than "
                     f"{m(max_fill)}, otherwise wait for a retest within {P.BREAKOUT_RETEST_SESSIONS} sessions."),
        "limit": (f"Limit order at {m(entry)}, valid {LIMIT_VALID_SESSIONS} sessions; cancel it if a daily "
                  f"close is below {m(stop)} first."),
    }[kind]
    targets = [{"price": t1, "label": "Model-chosen objective", "reward_risk": rr1},
               {"price": t2, "label": "Model-chosen stretch objective", "reward_risk": (t2 - entry) / per}]
    state = "blocked" if blocks else "conditional"
    playbook = ([{"case": "wait", "kind": "wait", "title": "Now: wait — not eligible",
                  "text": "No entry from this proposal now: " + " ".join(blocks)}] if blocks else [
        {"case": "rises", "kind": "entry" if kind == "breakout" else "wait", "title": "If it rises",
         "text": (trigger if kind == "breakout" else
                  f"If the price rises without coming back to {m(entry)}, there is no fill — do not chase "
                  f"above {m(max_fill)}, where target 1 ({m(t1)}) is less than {P.MIN_REWARD_RISK:g}R away.")},
        {"case": "falls", "kind": "entry" if kind != "breakout" else "wait", "title": "If it falls",
         "text": (trigger + f" A daily close below {m(stop)} before a fill cancels the proposal."
                  if kind != "breakout" else
                  f"A fall away from {m(entry)} leaves the breakout untriggered; a daily close below {m(stop)} "
                  f"after a fill exits.")},
        {"case": "sideways", "kind": "wait", "title": "If it goes sideways",
         "text": (f"No trigger, no trade. This proposal is tied to the prices as of {plan.as_of or 'unknown'}; "
                  f"regenerate it after new daily closes.")},
        {"case": "after_entry", "kind": "manage", "title": "Only after an eligible fill (hypothetical)",
         "text": (f"Recalculate risk at the actual fill. Once a daily close is {P.BREAKEVEN_ATR:g} ATR above "
                  f"the entry (above {m(entry + P.BREAKEVEN_ATR * atr)}), move the stop up to the entry. At "
                  f"target 1 ({m(t1)}) a common approach takes part of the position and trails the rest at the "
                  f"latest close − {P.TRAIL_ATR:g} × ATR toward target 2 ({m(t2)}).")},
    ])
    earnings = plan.items.get("rule.e5")
    return dict(base, state=state, reasons=blocks, derivation=derivation, entry=entry, stop=stop,
                targets=targets, risk_per_share=per, risk_budget=budget, shares=shares,
                shares_basis="risk_budget", capital=entry * shares if shares is not None else None,
                cash_at_stop=(stop - entry) * shares if shares is not None else None,
                max_fill=max_fill, sizing_note=sizing_note, trigger=trigger,
                invalidation=f"Before a fill, a daily close below {m(stop)} cancels it; after a fill, it exits.",
                earnings=earnings.display if earnings else "Next earnings date is unavailable.",
                risk_note="Stop gaps, slippage and fees can exceed the calculated loss. Sizing does not check buying power.",
                recovery_levels=[], playbook=playbook, evidence_ids=[], price_as_of=plan.as_of)


def _build_hold(plan, base, prices, derivation, blocks, close, atr, m):
    """Held position: the model chooses the stop and objectives; the entry is the stated cost."""
    pos = plan.position
    if pos is None or pos.entry is None:
        return dict(base, state="rejected", reasons=["A held-position proposal needs the stated entry cost."])
    cost, stop, t1, t2 = pos.entry, prices["stop"], prices["target1"], prices["target2"]
    reasons = []
    floor = max(cost, close)
    if not (stop > 0 and floor < t1 < t2):
        reasons.append(f"Targets must be above both your cost and the latest close ({m(floor)}) and "
                       f"ordered, with a positive stop (got stop {m(stop)}, targets {m(t1)} / {m(t2)}).")
    if stop >= close:
        reasons.append(f"A stop at or above the latest close ({m(close)}) would already be triggered.")
    elif close - stop > MAX_STOP_ATR * atr:
        reasons.append(f"Stop is {(close - stop) / atr:.2f} ATR below the latest close; beyond "
                       f"{MAX_STOP_ATR:g} ATR it is not a structural stop.")
    if pos.stop is not None and stop < pos.stop:
        reasons.append(f"The proposed stop is below your stated stop ({m(pos.stop)}); a stop is never lowered.")
    per = cost - stop if cost > stop else None
    targets = [{"price": t1, "label": "Model-chosen objective", "reward_risk": (t1 - cost) / per if per else None},
               {"price": t2, "label": "Model-chosen stretch objective", "reward_risk": (t2 - cost) / per if per else None}]
    if reasons:
        return dict(base, state="rejected", reasons=reasons, derivation=derivation, entry=cost,
                    stop=stop, targets=targets)
    shares = pos.shares
    trail = close - P.TRAIL_ATR * atr
    return dict(base, state="blocked" if blocks else "conditional", reasons=blocks, derivation=derivation,
                entry=cost, stop=stop, targets=targets, risk_per_share=per, risk_budget=None, shares=shares,
                shares_basis="held", capital=cost * shares if shares else None,
                cash_at_stop=(stop - cost) * shares if shares else None, max_fill=None, sizing_note=None,
                trigger=f"Hold while daily closes stay above {m(stop)}; raise the stop as closes rise, never lower it.",
                invalidation=f"A daily close below {m(stop)} meets the exit rule.",
                earnings=(plan.items["rule.h7"].display if "rule.h7" in plan.items else "Next earnings date is unavailable."),
                risk_note="An overnight gap can fill below the stop. Original risk is unknown when the stop is at or above cost.",
                recovery_levels=[], evidence_ids=[], price_as_of=plan.as_of,
                playbook=([{"case": "wait", "kind": "wait", "title": "Now: prices not current",
                            "text": " ".join(blocks)}] if blocks else [
                    {"case": "rises", "kind": "manage", "title": "If it rises",
                     "text": (f"Keep holding; a textbook trailing stop follows the latest close − {P.TRAIL_ATR:g} × ATR "
                              f"(today {m(trail)}) and is raised only when higher. Objective 1 is {m(t1)}, "
                              f"objective 2 {m(t2)}.")},
                    {"case": "falls", "kind": "exit", "title": "If it falls",
                     "text": f"A daily close below {m(stop)} meets the exit rule: sell rather than wait for a recovery."},
                    {"case": "sideways", "kind": "manage", "title": "If it goes sideways",
                     "text": f"Hold while daily closes stay above {m(stop)}; a sideways market does not move the stop."}]))
