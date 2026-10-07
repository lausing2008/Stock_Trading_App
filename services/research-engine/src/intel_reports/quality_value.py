"""Quality & Value: composing gates into a research state, deterministically.

WHAT THIS IS, AND IS NOT. It decides whether a company's stored evidence supports further entry
RESEARCH. It is not a buy signal, it produces no order, and `ENTRY_REVIEW_READY` is a research
state — the name is deliberately not "buy" or "ready to enter".

THE ONE RULE EVERYTHING ELSE SERVES: a required gate cannot be compensated for. There is no
combined score here and there is no weighting, because any weighting lets a strong technical
reading outvote absent fundamental evidence, which is exactly the failure a quality-and-value
screen exists to avoid. Gates compose by AND over a declared required set, and nothing else.

THREE DISTINCTIONS THE STATUS ENUM EXISTS TO KEEP.

1. UNKNOWN IS NOT FAIL. "We hold no evidence about this company's switching costs" and "this
   company has no switching costs" are different findings with different remedies — the first
   is closed by acquiring data, the second by changing your mind. Both block eligibility; only
   one is a fact about the company.
2. UNKNOWN IS NOT PASS, which is the direction this platform has got wrong repeatedly: a check
   written as "stop if it looks wrong" never fires when the number is missing, so it becomes
   "allow" at precisely the inputs it existed to catch. `compose()` requires an explicit PASS.
3. BLOCKED IS NOT UNKNOWN. A critical risk that is PRESENT (not merely unmeasured) removes
   eligibility however good everything else looks, and it must not be reported as a data gap
   that more ingestion would close.

NO ORM IMPORT. This module is pure so it can be tested directly; the service conftest stubs
`db` as a plain module, and anything importing `db.models` cannot be imported from a test.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field as dc_field
from datetime import datetime
from enum import Enum


def naive_utc(dt: datetime) -> datetime:
    """Drop the offset so an age can be taken against this codebase's naive stored timestamps.

    FOUND BY RUNNING THIS AGAINST PRODUCTION, not by a test: `FinancialStatement.fetched_at` is
    a naive `DateTime`, and subtracting it from an aware `datetime.now(timezone.utc)` raises
    TypeError — which would have made every call to this endpoint a 500. The report generators
    already settled this convention (`_naive_utc_now()` in generators.py); this endpoint was
    written against the aware one and did not match.

    Normalising here rather than only at the caller means a future aware timestamp arriving
    from anywhere cannot reintroduce it.
    """
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


class GateStatus(str, Enum):
    """Why a gate did not pass — because "no evidence" was hiding five different problems.

    The first version reported every non-passing gate as UNKNOWN, so a company whose statements
    are one year stale and a dimension the platform has never implemented rendered as the same
    yellow badge. They are not the same: one is closed by a refresh job, one by building an
    assessment, and reading either as "no relevant information exists in the world" is simply
    wrong. A reader cannot prioritise work they cannot distinguish.

    Every member below except PASS blocks eligibility. What differs is WHO closes it and HOW,
    which is what `REMEDY` names.
    """
    PASS = "pass"
    FAIL = "fail"
    #: The assessment does not exist as a capability yet. Nothing about this issuer is at fault
    #: and no amount of ingestion closes it — someone has to build the analysis.
    NOT_IMPLEMENTED = "not_implemented"
    #: The capability exists; no data has been gathered for THIS issuer.
    NOT_COLLECTED = "not_collected"
    #: Data exists and is too old to support the conclusion. Closed by a refresh, not research.
    STALE = "stale"
    #: Sources disagree, and resolving which is right is its own work. NOT an average.
    CONFLICTING = "conflicting"
    #: Data is present and current and still cannot settle the question — too few periods, or
    #: a measure that does not bear on what is being asked.
    INSUFFICIENT = "insufficient"
    #: Evidence of a disqualifying condition. Blocks eligibility; NOT a data gap.
    BLOCKED = "blocked"
    #: The template does not apply to this instrument at all — an ETF has no gross margin, and
    #: net debt against equity is not a solvency reading for a bank.
    NOT_APPLICABLE = "not_applicable"
    #: The gate was not evaluated for this company at all.
    #:
    #: A DECLARED ABSENCE, NOT A MISSING RESULT. Catalysts and portfolio fit are optional and
    #: this run does not evaluate them, so their coverage row was all zeros and the
    #: reconciliation check flagged "0 ≠ 200" as though 200 companies had gone missing. They had
    #: not: the gate simply did not run for them. Counting that explicitly is what lets the row
    #: reconcile while still saying, truthfully, that nothing was assessed.
    NOT_ASSESSED = "not_assessed"


#: What would close each state, in the reader's terms. Rendered beside the badge so the page
#: says what work remains rather than only that something is missing.
REMEDY = {
    # "no ingestion closes this" was half the story: these need sourced evidence AND an
    # assessment that reads it. Ingestion alone is insufficient, not irrelevant.
    GateStatus.NOT_IMPLEMENTED: "Requires sourced evidence and an implemented assessment",
    GateStatus.NOT_COLLECTED: "Collect the evidence for this issuer",
    GateStatus.STALE: "Refresh the stored data",
    GateStatus.CONFLICTING: "Reconcile the disagreeing sources",
    GateStatus.INSUFFICIENT: "More periods or a different measure are needed",
    GateStatus.BLOCKED: "Nothing — this is a finding about the company, not a gap",
    GateStatus.NOT_APPLICABLE: "Nothing — a different template is needed for this instrument",
    GateStatus.NOT_ASSESSED: "Evaluate this gate — it is optional and was not run",
    GateStatus.FAIL: "Nothing — the condition is simply not met today",
    GateStatus.PASS: "",
}

#: How each state should READ, for every surface that renders one.
#:
#: SERVED TO THE FRONTEND RATHER THAN DUPLICATED THERE. The first version hardcoded five
#: statuses in the page and defaulted anything else to "No evidence" — so adding four states to
#: this enum silently relabelled all of them as the one thing they were introduced to stop
#: saying. A mapping that lives in two places drifts the moment one of them grows.
STATUS_LABEL = {
    GateStatus.PASS: "Pass",
    GateStatus.FAIL: "Fail",
    GateStatus.NOT_IMPLEMENTED: "Not implemented",
    GateStatus.NOT_COLLECTED: "Not collected",
    GateStatus.STALE: "Stale",
    GateStatus.CONFLICTING: "Conflicting",
    GateStatus.INSUFFICIENT: "Insufficient",
    GateStatus.BLOCKED: "Blocked",
    GateStatus.NOT_APPLICABLE: "Not applicable",
    GateStatus.NOT_ASSESSED: "Not assessed",
}


def status_catalog() -> dict:
    """Every state, its label, its remedy and whether it counts as work remaining.

    Exhaustive over the enum BY CONSTRUCTION — it iterates GateStatus — so a state added later
    appears on every surface without anyone remembering to add it.
    """
    return {st.value: {"label": STATUS_LABEL[st], "remedy": REMEDY[st],
                       "is_work_remaining": st in WORK_REMAINING,
                       "is_pass": st is GateStatus.PASS}
            for st in GateStatus}


#: Every state that is a GAP in our work rather than a finding about the company. Counting these
#: separately is what distinguishes an unfinished pipeline from a universe of poor businesses.
WORK_REMAINING = (GateStatus.NOT_IMPLEMENTED, GateStatus.NOT_COLLECTED, GateStatus.STALE,
                  GateStatus.CONFLICTING, GateStatus.INSUFFICIENT)


class State(str, Enum):
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    QUALITY_WATCH = "quality_watch"
    VALUE_CANDIDATE = "value_candidate"
    ENTRY_REVIEW_READY = "entry_review_ready"
    ENTRY_EXPIRED = "entry_expired"
    VALUATION_REVIEW = "valuation_review"
    THESIS_AT_RISK = "thesis_at_risk"


#: Gate names. Declared as constants so a typo cannot silently create an eighth gate that
#: `REQUIRED_FOR_ENTRY` then never checks.
BUSINESS_QUALITY = "business_quality"
COMPETITIVE_DURABILITY = "competitive_durability"
VALUATION = "valuation"
ENTRY_CONDITION = "entry_condition"
VALUE_TRAP_RISK = "value_trap_risk"
CATALYSTS = "catalysts"
PORTFOLIO_FIT = "portfolio_fit"

ALL_GATES = (BUSINESS_QUALITY, COMPETITIVE_DURABILITY, VALUATION, ENTRY_CONDITION,
             VALUE_TRAP_RISK, CATALYSTS, PORTFOLIO_FIT)

#: WHAT A PASS ON EACH GATE ACTUALLY ESTABLISHES, and what it does not.
#:
#: This exists because the gate NAMES overclaim and the constant names cannot be changed without
#: churn. "business_quality: pass" reads as "this is a quality business"; what was established is
#: that the fundamentals the platform happens to hold cleared the configured checks — with the
#: accounting basis unknown and four classes of critical risk unobserved, that is a readiness
#: statement, not a judgement about the business. "entry_condition: pass" reads as "ready to
#: buy"; what was established is that a price rule was satisfied on completed sessions.
#:
#: Every surface that renders a gate renders `establishes` beside it. A label that overstates is
#: the same defect as a number that overstates, and it is cheaper to fix here than in prose.
GATE_CLAIM = {
    BUSINESS_QUALITY: {
        "label": "Fundamental checks",
        "establishes": "the stored fundamentals passed the configured completeness and "
                       "freshness checks",
        "does_not_establish": "that this is a high-quality business. No accounting basis is "
                              "stored, so the periods are not shown to be comparable, and "
                              "nothing here measures returns on capital or reinvestment",
    },
    COMPETITIVE_DURABILITY: {
        "label": "Competitive durability",
        "establishes": "nothing yet — no sourced evidence of durability is stored",
        "does_not_establish": "durability from margin or return persistence, which for a "
                              "cyclical business is equally consistent with the cycle",
    },
    VALUATION: {
        "label": "Valuation discount",
        "establishes": "nothing yet — no equity value has been computed under a frozen policy",
        "does_not_establish": "that a price below any model estimate is cheap. That is a "
                              "hypothesis about the model, not a fact about intrinsic value",
    },
    ENTRY_CONDITION: {
        "label": "Price-stabilization rule",
        "establishes": "a versioned price rule was satisfied on completed sessions",
        "does_not_establish": "that the stock is a suitable entry. A timing rule says nothing "
                              "about the business, the price paid, or this holder's situation",
    },
    VALUE_TRAP_RISK: {
        "label": "Value-trap risk",
        "establishes": "nothing yet — four critical risk classes are unobserved, and the two "
                       "computable figures carry no validated threshold",
        "does_not_establish": "the absence of a value trap. Leverage and cash burn looking "
                              "ordinary establishes only that, not safety",
    },
    CATALYSTS: {
        "label": "Catalysts",
        "establishes": "scheduled events are on file",
        "does_not_establish": "that any of them will move the price, or when",
    },
    PORTFOLIO_FIT: {
        "label": "Portfolio fit",
        "establishes": "nothing — this requires an authorized holdings read, out of pilot scope",
        "does_not_establish": "any position size or concentration judgement",
    },
}

#: Every gate that must explicitly PASS before entry research is a state this can reach.
#: CATALYSTS and PORTFOLIO_FIT are absent on purpose: a catalyst is optional context and
#: inventing a deadline for price convergence is worse than having none, and portfolio fit needs
#: an authorized holdings read that general research must not require.
REQUIRED_FOR_ENTRY = (BUSINESS_QUALITY, COMPETITIVE_DURABILITY, VALUATION,
                      ENTRY_CONDITION, VALUE_TRAP_RISK)

#: The subset whose failure is about the BUSINESS rather than its price. Passing these without a
#: valuation discount is a real, nameable state — a good company you would not buy here.
QUALITY_GATES = (BUSINESS_QUALITY, COMPETITIVE_DURABILITY, VALUE_TRAP_RISK)


#: The policy's human name. Bump when the SHAPE changes (a new gate, a new state); the
#: fingerprint below catches everything else on its own.
POLICY_VERSION = "qv-1"


def policy_fingerprint() -> str:
    """A digest of every rule that can change a verdict.

    WHY A FINGERPRINT AND NOT JUST A VERSION STRING. A stored evaluation is only evidence of
    what the screen concluded if the rules that produced it are recoverable. A hand-maintained
    version number drifts the first time someone adjusts a threshold without remembering to
    bump it — and then two rows that say `qv-1` were produced by different screens, which is
    worse than no version at all because it looks trustworthy.

    So the identity is computed from the rules themselves: the required set, the quality subset,
    every claim sentence and every threshold. Change any of them and new evaluations land under
    a new fingerprint beside the old rows rather than on top of them. Nothing is ever updated in
    place, so "what did this screen conclude on that date, under which rules" stays answerable.
    """
    payload = json.dumps({
        "version": POLICY_VERSION,
        "all_gates": list(ALL_GATES),
        "required_for_entry": list(REQUIRED_FOR_ENTRY),
        "quality_gates": list(QUALITY_GATES),
        "gate_claim": GATE_CLAIM,
        "max_retrieval_age_days": MAX_RETRIEVAL_AGE_DAYS,
        "max_reported_year_age_days": MAX_REPORTED_YEAR_AGE_DAYS,
        "max_valuation_alignment_days": MAX_VALUATION_ALIGNMENT_DAYS,
        "leverage_not_applicable": list(LEVERAGE_NOT_APPLICABLE_INDUSTRIES),
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Gate:
    """One dimension's verdict, with the reason that produced it.

    `reasons` is never empty for a non-PASS status: a gate that blocks without saying why turns
    the dashboard into an oracle, and "why not eligible" is a required view.
    """
    name: str
    status: GateStatus
    reasons: tuple = ()
    evidence: dict = dc_field(default_factory=dict)
    #: A remedy specific to THIS gate, overriding the status's generic one.
    #:
    #: WHY AN OVERRIDE EXISTS. The catalog's remedy is keyed by status, which is right when the
    #: status is all we know. Once a real assessment is connected it knows better: a completed
    #: durability review that returned `insufficient` should not tell a reader "more periods or
    #: a different measure are needed" — it should name what IT left unassessed. A remedy that
    #: is merely grammatical is worse than none, because it looks like guidance.
    remedy: str = ""

    def __post_init__(self):
        if self.name not in ALL_GATES:
            raise ValueError(f"unknown gate {self.name!r}; declared gates are {ALL_GATES}")
        if self.status is not GateStatus.PASS and not self.reasons:
            raise ValueError(f"gate {self.name!r} is {self.status.value} with no reason given")

    def as_dict(self) -> dict:
        claim = GATE_CLAIM[self.name]
        return {"gate": self.name, "status": self.status.value,
                "label": claim["label"],
                "remedy": self.remedy or None,
                # Carried on EVERY gate, passing or not, so no renderer can show a verdict
                # without the sentence that bounds it.
                "establishes": claim["establishes"] if self.status is GateStatus.PASS else None,
                "does_not_establish": claim["does_not_establish"],
                "reasons": list(self.reasons), "evidence": self.evidence}


@dataclass(frozen=True)
class Evaluation:
    symbol: str
    state: State
    gates: tuple
    #: Why the state is what it is, in the order a reader needs it. Blocking causes first.
    explanation: tuple = ()

    def gate(self, name: str):
        return next((g for g in self.gates if g.name == name), None)

    def blocking(self) -> tuple:
        """The gates standing between this evaluation and entry research."""
        return tuple(g for g in self.gates
                     if g.name in REQUIRED_FOR_ENTRY and g.status is not GateStatus.PASS)

    def as_dict(self) -> dict:
        return {"symbol": self.symbol, "state": self.state.value,
                "gates": [g.as_dict() for g in self.gates],
                "explanation": list(self.explanation),
                "blocking": [g.name for g in self.blocking()]}


def compose(symbol: str, gates, *, entry_zone_held: bool = True) -> Evaluation:
    """Reduce gate verdicts to one state. No score, no weights, no compensation.

    `entry_zone_held` is separate from the ENTRY_CONDITION gate on purpose: the gate says the
    condition was satisfied at the evaluation cutoff, and this says whether the price is STILL
    inside the zone now. A setup whose price has gapped away is expired, not ready — keeping an
    old "ready" label on it is how a stale research state becomes a live-looking prompt.
    """
    gates = tuple(gates)
    seen = [g.name for g in gates]
    if len(seen) != len(set(seen)):
        raise ValueError(f"{symbol}: a gate is evaluated twice: {seen}")
    by_name = {g.name: g for g in gates}

    missing = [n for n in REQUIRED_FOR_ENTRY if n not in by_name]
    blocked = [g for g in gates if g.status is GateStatus.BLOCKED]
    why = []

    # A disqualifying condition outranks everything, including a large apparent discount.
    if blocked:
        for g in blocked:
            why.extend(f"{g.name}: {r}" for r in g.reasons)
        return Evaluation(symbol, State.THESIS_AT_RISK, gates, tuple(why))

    if missing:
        why.append("required gates were never evaluated: " + ", ".join(missing))
        return Evaluation(symbol, State.INSUFFICIENT_EVIDENCE, gates, tuple(why))

    unknown = [g for g in gates
               if g.name in REQUIRED_FOR_ENTRY and g.status in WORK_REMAINING]
    failed = [g for g in gates
              if g.name in REQUIRED_FOR_ENTRY and g.status is GateStatus.FAIL]

    # UNKNOWN FIRST, and the ordering is the point: a company with both a data gap and a failed
    # gate is reported as insufficiently evidenced, because the failure was judged on incomplete
    # evidence and may not survive the missing input.
    if unknown:
        for g in unknown:
            why.extend(f"{g.name}: {r}" for r in g.reasons)
        return Evaluation(symbol, State.INSUFFICIENT_EVIDENCE, gates, tuple(why))

    quality_ok = all(by_name[n].status is GateStatus.PASS
                     for n in QUALITY_GATES if n in by_name)

    if failed:
        for g in failed:
            why.extend(f"{g.name}: {r}" for r in g.reasons)
        # A sound business at an unattractive price is a WATCH, not a rejection: nothing about
        # the company is wrong, only the price, and the price is the part that moves.
        if quality_ok and {g.name for g in failed} == {VALUATION}:
            return Evaluation(symbol, State.QUALITY_WATCH, gates, tuple(why))
        if quality_ok and {g.name for g in failed} <= {ENTRY_CONDITION}:
            return Evaluation(symbol, State.VALUE_CANDIDATE, gates, tuple(why))
        return Evaluation(symbol, State.INSUFFICIENT_EVIDENCE, gates, tuple(why))

    if not entry_zone_held:
        return Evaluation(symbol, State.ENTRY_EXPIRED, gates,
                          ("every gate passed at the cutoff, but the price has since moved "
                           "outside the entry zone, so the setup is expired rather than ready",))

    why.append("every required gate passed at this evaluation's cutoff — which establishes "
               "that the configured checks were met, NOT that this is a suitable investment")
    return Evaluation(symbol, State.ENTRY_REVIEW_READY, gates, tuple(why))


# =====================================================================================
# The gates the stored evidence can actually produce today.
#
# docs/audits/2026-10-06-quality-value-readiness-inventory.md measured which inputs exist. Two
# of seven gates are computable now, and the two that DECIDE eligibility — durability and
# valuation — are not. These functions return that honestly rather than approximating it.
# =====================================================================================

#: How an assessment's OWN verdict maps to a gate status.
#:
#: CONNECTING AN ASSESSMENT MUST NOT MEAN THE GATE PASSES. A completed durability review that
#: found the evidence insufficient is a real, finished piece of work AND a reason to stay
#: ineligible — those are not in tension. Only `supported` passes; `insufficient` reports
#: INSUFFICIENT (the work is done, the answer is "not enough"), `contradicted` FAILS, and
#: `context_only` also reports INSUFFICIENT because context is not a conclusion.
VERDICT_STATUS = {
    "supported": GateStatus.PASS,
    "insufficient": GateStatus.INSUFFICIENT,
    "contradicted": GateStatus.FAIL,
    "context_only": GateStatus.INSUFFICIENT,
}


def from_assessment(gate_name: str, assessment: dict | None, *, absent_reasons: tuple) -> Gate:
    """Build a gate from a stored assessment, or report that none is connected.

    `assessment` is the serialised IssuerAssessment row. Its `verdict` decides the status; the
    row merely existing decides nothing. An unrecognised verdict is INSUFFICIENT with the
    verdict named, never a silent pass — the same rule the status catalog follows for the page.
    """
    if not assessment:
        return Gate(gate_name, GateStatus.NOT_IMPLEMENTED, absent_reasons)

    verdict = (assessment.get("verdict") or "").strip().lower()
    status = VERDICT_STATUS.get(verdict)
    if status is None:
        return Gate(gate_name, GateStatus.INSUFFICIENT, (
            f"the stored assessment carries an unrecognised verdict {verdict!r}, so what it "
            f"concluded cannot be read; it is NOT treated as a pass",), assessment)

    reasons = []
    if assessment.get("summary"):
        reasons.append(assessment["summary"])
    for f in (assessment.get("findings") or []):
        claim, src = f.get("claim"), f.get("source")
        against = f.get("counterevidence")
        line = claim or ""
        if src:
            line += f"  [source: {src}]"
        if against:
            line += f"  — against: {against}"
        if line:
            reasons.append(line)
    for n in (assessment.get("not_assessed") or []):
        reasons.append(f"NOT ASSESSED: {n}")
    # THE REMEDY COMES FROM THE ASSESSMENT, not from the status. It knows what it did not do.
    outstanding = list(assessment.get("not_assessed") or [])
    if status is GateStatus.PASS:
        reasons, remedy = (), ""         # a Gate that passes carries no reasons, by contract
    elif outstanding:
        shown = "; ".join(outstanding[:3])
        more = f" (+{len(outstanding) - 3} more)" if len(outstanding) > 3 else ""
        remedy = (f"Assess what this review did not: {shown}{more}")
    else:
        remedy = (f"Revise the assessment — it concluded '{verdict}' and names nothing "
                  f"outstanding, so what would change it is not recorded")
    return Gate(gate_name, status, tuple(reasons) or ("the stored assessment states no reason",),
                {**assessment, "assessment_version": assessment.get("version"),
                 "assessment_cutoff": assessment.get("cutoff")},
                remedy=remedy)


def durability_gate(evidence=None, assessment=None) -> Gate:
    """Competitive durability. UNKNOWN until sourced evidence exists — never inferred.

    A statement series cannot evidence a moat. Persistently high margins are equally consistent
    with a cycle, and this platform stores nothing about switching costs, cost position,
    network effects, intangibles or distribution. So this gate reports a data gap, which is what
    it is, rather than reading durability out of the numbers that happen to be present.
    """
    if assessment is not None:
        return from_assessment(COMPETITIVE_DURABILITY, assessment, absent_reasons=())
    sources = (evidence or {}).get("sources") or []
    if not sources:
        return Gate(COMPETITIVE_DURABILITY, GateStatus.NOT_IMPLEMENTED, (
            "no sourced evidence of switching costs, cost advantage, network effects, "
            "intangibles or distribution is stored for this issuer",
            "margin or return persistence in the statement series is NOT evidence of "
            "durability — for a cyclical business it is equally consistent with the cycle",
        ))
    return Gate(COMPETITIVE_DURABILITY, GateStatus.NOT_IMPLEMENTED, (
        f"{len(sources)} source(s) are attached but no durability assessment has been made "
        "from them; attaching a source is not the same as reading it",))


def valuation_gate(evidence=None, assessment=None) -> Gate:
    """Valuation discount. UNKNOWN until an aggregate equity value is computed and frozen.

    THE PER-SHARE ROUTE IS CLOSED, and this is a measured fact rather than a design preference:
    no share count is stored anywhere usable (see the readiness inventory). The pilot therefore
    compares a whole-equity value against market capitalisation and never computes a per-share
    figure — removing a quantity there is no evidence for from the arithmetic, at the stated
    cost that an aggregate discount cannot become a per-share target.
    """
    if assessment is not None:
        return from_assessment(VALUATION, assessment, absent_reasons=())
    ev = evidence or {}
    cap, value = ev.get("market_cap"), ev.get("equity_value")
    if value is None:
        return Gate(VALUATION, GateStatus.NOT_IMPLEMENTED, (
            "no equity value has been computed: the normalized-earnings and cash-flow "
            "assumptions a defensible value needs are not yet specified or frozen",
            "a price below any model estimate would be a hypothesis about that model, not a "
            "fact about intrinsic value",))
    if cap is None:
        return Gate(VALUATION, GateStatus.NOT_COLLECTED, (
            "no market capitalisation is stored for this issuer, so there is nothing to "
            "compare the equity value against",))
    return Gate(VALUATION, GateStatus.NOT_IMPLEMENTED, (
        "an equity value and a market capitalisation are both present, but the required "
        "discount policy has not been frozen, and selecting a threshold after seeing the "
        "discount is how a screen is fitted to its own sample",))


#: How far apart the valuation's own cutoff and the market-cap observation may be before the
#: discount stops being a comparison of two things at the same moment. Deliberately short: the
#: stored caps range over 25 days, which is long enough for a buyback or an issuance.
MAX_VALUATION_ALIGNMENT_DAYS = 3


class ValuationMisaligned(Exception):
    """The two sides of the comparison do not describe the same moment or the same claim."""


def equity_discount(equity_value, market_cap, *, value_as_of=None, cap_as_of=None,
                    basis="equity") -> dict:
    """Aggregate discount of market capitalisation to an estimated EQUITY value.

    THREE ALIGNMENT RULES, each of which can refuse rather than return a number.

    1. EQUITY AGAINST EQUITY, NEVER ENTERPRISE VALUE. An enterprise valuation is a claim about
       the whole capital structure and needs a sourced bridge — debt, cash, leases, pensions,
       minorities, preferred — to become a claim about the equity. None of those is stored with
       a source here, so an EV compared against market capitalisation is comparing a different
       quantity, and the difference is whatever the unbuilt bridge would have been. `basis`
       must say `equity`, and anything else raises instead of silently converting.
    2. THE TWO SIDES MUST DESCRIBE THE SAME MOMENT. A value computed from statements and a cap
       observed weeks later differ by every intervening buyback, issuance and price move, all
       of which land in the discount as if they were valuation. Both timestamps travel with the
       result and a gap beyond MAX_VALUATION_ALIGNMENT_DAYS refuses.
    3. NEVER PER SHARE. There is no reliable share count (see the readiness inventory), so this
       returns an aggregate and deliberately provides no route to a per-share entry target.

    Returns both denominators, named. The same gap is 20% off the value and 25% of upside, and
    quoting one as the other overstates by exactly the amount the reader is judging.
    """
    if basis != "equity":
        raise ValuationMisaligned(
            f"basis is {basis!r}: an enterprise value needs a sourced debt, cash and other-claims "
            f"bridge before it can be compared with market capitalisation, and none is stored")
    if equity_value is None or market_cap is None:
        return {"available": False,
                "reason": "equity value or market capitalisation is missing; a missing input is "
                          "not a zero discount",
                "discount_to_value": None, "upside_to_price": None}
    if equity_value <= 0:
        return {"available": False,
                "reason": f"the equity value is {equity_value}, so a discount against it has no "
                          f"meaning; a non-positive valuation is a refusal, not a 100% discount",
                "discount_to_value": None, "upside_to_price": None}
    if market_cap <= 0:
        return {"available": False,
                "reason": "market capitalisation is not positive, so there is no price to "
                          "compare against",
                "discount_to_value": None, "upside_to_price": None}

    alignment_days = None
    if value_as_of is not None and cap_as_of is not None:
        alignment_days = abs((naive_utc(value_as_of) - naive_utc(cap_as_of)).days)
        if alignment_days > MAX_VALUATION_ALIGNMENT_DAYS:
            return {"available": False,
                    "reason": f"the valuation and the market capitalisation are "
                              f"{alignment_days} days apart, beyond the "
                              f"{MAX_VALUATION_ALIGNMENT_DAYS}-day alignment limit; the gap "
                              f"between them would absorb any buyback, issuance or price move "
                              f"in between",
                    "discount_to_value": None, "upside_to_price": None,
                    "alignment_days": alignment_days}
    else:
        return {"available": False,
                "reason": "one or both sides carry no timestamp, so the comparison cannot be "
                          "shown to describe the same moment",
                "discount_to_value": None, "upside_to_price": None}

    return {
        "available": True,
        "basis": "equity value against market capitalisation — NOT enterprise value",
        "discount_to_value": (equity_value - market_cap) / equity_value,
        "discount_denominator": "the estimated equity value",
        "upside_to_price": (equity_value - market_cap) / market_cap,
        "upside_denominator": "the current market capitalisation",
        "equity_value": equity_value, "market_cap": market_cap,
        "value_as_of": naive_utc(value_as_of).isoformat(),
        "cap_as_of": naive_utc(cap_as_of).isoformat(),
        "alignment_days": alignment_days,
        "per_share": "NOT PROVIDED. No reliable share count is stored, so this aggregate "
                     "cannot be converted into a per-share entry target, and is not comparable "
                     "across a share issuance.",
    }


#: A statement series older than this in RETRIEVAL cannot support an actionable entry state.
#: Separate from the reported-year gap below: one is fixed by refetching, the other by the
#: issuer filing. Conflating them produced a wrong claim in an earlier audit of mine.
MAX_RETRIEVAL_AGE_DAYS = 45
#: An annual series whose newest period is older than this is missing a reported year for any
#: issuer on an annual reporting cycle.
MAX_REPORTED_YEAR_AGE_DAYS = 400


def business_quality_gate(evidence=None) -> Gate:
    """Resilience from the stored annual series: enough comparable periods, and recent enough.

    This is the one gate today's evidence can genuinely decide, and it still refuses two ways:
    too few periods to measure a change at all, and a series too old in either sense to describe
    the business as it now is.
    """
    ev = evidence or {}
    periods = ev.get("annual_periods") or 0
    if periods < 2:
        return Gate(BUSINESS_QUALITY, GateStatus.INSUFFICIENT,
                    (f"{periods} annual statement(s) stored; at least two are needed before any "
                     f"change over comparable periods can be measured",), {"periods": periods})

    reasons, retrieval, reported = [], ev.get("retrieval_age_days"), ev.get("reported_year_age_days")
    stale_retrieval = stale_reported = False
    if retrieval is None:
        reasons.append("the series carries no retrieval time, so its age cannot be established")
    elif retrieval > MAX_RETRIEVAL_AGE_DAYS:
        stale_retrieval = True
        reasons.append(f"the series was last retrieved {retrieval} days ago, beyond the "
                       f"{MAX_RETRIEVAL_AGE_DAYS}-day limit for an actionable state")
    if reported is not None and reported > MAX_REPORTED_YEAR_AGE_DAYS:
        stale_reported = True
        # WORDING WITHDRAWN. This used to say a later fiscal year "has almost certainly been
        # reported and is absent". Checked against EDGAR for MU: its newest 10-K is still
        # FY2025, so no later ANNUAL REPORT had been filed — what existed was an unaudited 8-K.
        # The age is a fact; what the issuer has since filed is a separate question this gate
        # does not look at.
        reasons.append(f"the newest stored annual period ended {reported} days ago, which is "
                       f"beyond one reporting year — whether a later annual report has since "
                       f"been filed is not checked here")
    if ev.get("revenue") is None:
        reasons.append("no revenue figure is stored for the newest period")

    ctx = {"periods": periods, "retrieval_age_days": retrieval,
           "reported_year_age_days": reported}
    if reasons:
        # THE STATE NAMES WHO CLOSES IT. Age is closed by a refresh job; an absent figure by
        # collecting it. Reporting both as "no evidence" told a reader neither.
        state = (GateStatus.STALE if (stale_retrieval or stale_reported)
                 else GateStatus.NOT_COLLECTED)
        return Gate(BUSINESS_QUALITY, state, tuple(reasons), ctx)
    return Gate(BUSINESS_QUALITY, GateStatus.PASS, (), ctx)


#: Business models where net debt against equity is not a solvency reading at all. For a bank
#: or insurer, deposits and reserves are liabilities of the operating model, so the ratio is a
#: category error rather than a high number — the plan already requires dedicated templates for
#: them before eligibility.
LEVERAGE_NOT_APPLICABLE_INDUSTRIES = (
    "bank", "insurance", "insurer", "capital markets", "credit services",
    "mortgage", "asset management", "financial conglomerate", "reit")


def value_trap_gate(evidence=None, assessment=None) -> Gate:
    """Disqualifying risk. UNKNOWN, with the two observable figures reported as observations.

    WHY THIS NO LONGER BLOCKS ON A RATIO — measured against production, 2026-10-06. An earlier
    version of this gate returned BLOCKED on net debt above 2x equity or two consecutive
    negative free-cash-flow years, and flagged 48 of 200 companies as a thesis at risk. Reading
    the list is what refuted it:

      * LMT — net debt 2.62x equity with free cash flow of $6.9bn, up from $5.3bn. Buybacks
        shrink equity, so the ratio rises as the company returns money it is plainly earning.
      * CM — a bank. Net debt against equity is not a solvency reading for a bank at all.
      * VST, CWEN, NATL — power and utilities, where high structural leverage against positive
        free cash flow is the capital model, not a warning.
      * ORCL — free cash flow negative two years running because operating cash flow is being
        spent on capacity. The statements cannot separate that from distress.

    So the thresholds were not measuring a value trap; they were measuring capital intensity and
    buybacks. BLOCKED is reserved for EVIDENCE OF A DISQUALIFYING CONDITION, and a ratio
    crossing a number chosen without validation is not that. The plan says it directly: do not
    ship an arbitrary threshold as empirically proven, and freeze provisional ones in shadow
    mode before prospective evaluation.

    The figures are still computed and still shown — as observations, labelled unvalidated. What
    changed is that they no longer render a verdict.
    """
    if assessment is not None:
        return from_assessment(VALUE_TRAP_RISK, assessment, absent_reasons=())
    ev = evidence or {}
    industry = (ev.get("industry") or "").lower()
    leverage_applies = not any(k in industry for k in LEVERAGE_NOT_APPLICABLE_INDUSTRIES)

    # The one route to BLOCKED: a disqualifying finding supplied by something that actually
    # established one. Nothing in the platform supplies these yet, and the path stays live so
    # that when a source does, it is not a new mechanism.
    disqualifying = tuple(ev.get("disqualifying") or ())
    if disqualifying:
        return Gate(VALUE_TRAP_RISK, GateStatus.BLOCKED, disqualifying, ev)

    observations = []
    nde, fcf, prior = (ev.get("net_debt_to_equity"), ev.get("free_cashflow"),
                       ev.get("free_cashflow_prior"))
    if nde is not None and leverage_applies:
        observations.append(f"net debt is {nde:.2f}x equity (an observation; no threshold on "
                            f"this ratio has been validated, and buybacks raise it by shrinking "
                            f"the denominator)")
    elif nde is not None:
        observations.append(f"net debt against equity is not a solvency reading for a "
                            f"{ev.get('industry')}, so it is not interpreted here")
    if fcf is not None and fcf < 0 and (prior or 0) < 0:
        observations.append("free cash flow is negative in both of the two newest stored years "
                            "(an observation; these statements cannot separate heavy investment "
                            "from distress)")

    reasons = [
        "no evidence is stored for these critical risk classes: structural demand decline, "
        "customer concentration, restatements and accounting issues, refinancing schedule",
        "the figures that CAN be computed from the statements are reported as observations "
        "below, not as a verdict — none of them has a validated threshold",
    ]
    return Gate(VALUE_TRAP_RISK, GateStatus.NOT_IMPLEMENTED,
                tuple(reasons + observations), {**ev, "observations": observations})


def entry_condition_gate(evidence=None) -> Gate:
    """A versioned price-stabilization rule over COMPLETED sessions only.

    The rule under test is two completed closes above the recomputed 20-session average. It is a
    testable heuristic and is labelled one; nothing here claims it is optimal, and it is
    deliberately the only timing input, because combining several correlated indicators
    manufactures agreement rather than evidence.
    """
    ev = evidence or {}
    closes, avg = ev.get("recent_closes") or [], ev.get("sma20")
    if avg is None or len(closes) < 2:
        return Gate(ENTRY_CONDITION, GateStatus.INSUFFICIENT, (
            "fewer than two completed sessions or no 20-session average is available, so the "
            "stabilization rule cannot be evaluated",), ev)
    if ev.get("session_complete") is False:
        return Gate(ENTRY_CONDITION, GateStatus.INSUFFICIENT, (
            "the latest session has not completed; a forming bar is not a close",), ev)
    above = [c for c in closes[:2] if c > avg]
    if len(above) < 2:
        return Gate(ENTRY_CONDITION, GateStatus.FAIL, (
            f"{len(above)} of the two most recent completed closes are above the 20-session "
            f"average of {avg:.2f}; the rule requires both",), ev)
    return Gate(ENTRY_CONDITION, GateStatus.PASS, (), ev)
