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

from dataclasses import dataclass, field as dc_field
from enum import Enum


class GateStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    #: No evidence either way. Blocks eligibility; closed by acquiring data.
    UNKNOWN = "unknown"
    #: Evidence of a disqualifying condition. Blocks eligibility; NOT a data gap.
    BLOCKED = "blocked"
    #: Outside this company's applicable template (a bank's valuation, say).
    NOT_APPLICABLE = "not_applicable"


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

#: Every gate that must explicitly PASS before entry research is a state this can reach.
#: CATALYSTS and PORTFOLIO_FIT are absent on purpose: a catalyst is optional context and
#: inventing a deadline for price convergence is worse than having none, and portfolio fit needs
#: an authorized holdings read that general research must not require.
REQUIRED_FOR_ENTRY = (BUSINESS_QUALITY, COMPETITIVE_DURABILITY, VALUATION,
                      ENTRY_CONDITION, VALUE_TRAP_RISK)

#: The subset whose failure is about the BUSINESS rather than its price. Passing these without a
#: valuation discount is a real, nameable state — a good company you would not buy here.
QUALITY_GATES = (BUSINESS_QUALITY, COMPETITIVE_DURABILITY, VALUE_TRAP_RISK)


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

    def __post_init__(self):
        if self.name not in ALL_GATES:
            raise ValueError(f"unknown gate {self.name!r}; declared gates are {ALL_GATES}")
        if self.status is not GateStatus.PASS and not self.reasons:
            raise ValueError(f"gate {self.name!r} is {self.status.value} with no reason given")

    def as_dict(self) -> dict:
        return {"gate": self.name, "status": self.status.value,
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
               if g.name in REQUIRED_FOR_ENTRY and g.status is GateStatus.UNKNOWN]
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

    why.append("every required gate passed at this evaluation's cutoff")
    return Evaluation(symbol, State.ENTRY_REVIEW_READY, gates, tuple(why))


# =====================================================================================
# The gates the stored evidence can actually produce today.
#
# docs/audits/2026-10-06-quality-value-readiness-inventory.md measured which inputs exist. Two
# of seven gates are computable now, and the two that DECIDE eligibility — durability and
# valuation — are not. These functions return that honestly rather than approximating it.
# =====================================================================================

def durability_gate(evidence=None) -> Gate:
    """Competitive durability. UNKNOWN until sourced evidence exists — never inferred.

    A statement series cannot evidence a moat. Persistently high margins are equally consistent
    with a cycle, and this platform stores nothing about switching costs, cost position,
    network effects, intangibles or distribution. So this gate reports a data gap, which is what
    it is, rather than reading durability out of the numbers that happen to be present.
    """
    sources = (evidence or {}).get("sources") or []
    if not sources:
        return Gate(COMPETITIVE_DURABILITY, GateStatus.UNKNOWN, (
            "no sourced evidence of switching costs, cost advantage, network effects, "
            "intangibles or distribution is stored for this issuer",
            "margin or return persistence in the statement series is NOT evidence of "
            "durability — for a cyclical business it is equally consistent with the cycle",
        ))
    return Gate(COMPETITIVE_DURABILITY, GateStatus.UNKNOWN, (
        f"{len(sources)} source(s) are attached but no durability assessment has been made "
        "from them; attaching a source is not the same as reading it",))


def valuation_gate(evidence=None) -> Gate:
    """Valuation discount. UNKNOWN until an aggregate equity value is computed and frozen.

    THE PER-SHARE ROUTE IS CLOSED, and this is a measured fact rather than a design preference:
    no share count is stored anywhere usable (see the readiness inventory). The pilot therefore
    compares a whole-equity value against market capitalisation and never computes a per-share
    figure — removing a quantity there is no evidence for from the arithmetic, at the stated
    cost that an aggregate discount cannot become a per-share target.
    """
    ev = evidence or {}
    cap, value = ev.get("market_cap"), ev.get("equity_value")
    if value is None:
        return Gate(VALUATION, GateStatus.UNKNOWN, (
            "no equity value has been computed: the normalized-earnings and cash-flow "
            "assumptions a defensible value needs are not yet specified or frozen",
            "a price below any model estimate would be a hypothesis about that model, not a "
            "fact about intrinsic value",))
    if cap is None:
        return Gate(VALUATION, GateStatus.UNKNOWN, (
            "no market capitalisation is stored for this issuer, so there is nothing to "
            "compare the equity value against",))
    return Gate(VALUATION, GateStatus.UNKNOWN, (
        "an equity value and a market capitalisation are both present, but the required "
        "discount policy has not been frozen, and selecting a threshold after seeing the "
        "discount is how a screen is fitted to its own sample",))


def discount(equity_value: float, market_cap: float) -> float | None:
    """Aggregate discount to equity value. Never per-share, and never over a zero denominator."""
    if not equity_value or equity_value <= 0 or market_cap is None:
        return None
    return (equity_value - market_cap) / equity_value


def upside(equity_value: float, market_cap: float) -> float | None:
    """Potential upside from market capitalisation. A DIFFERENT denominator from `discount`.

    Shown alongside the discount with both denominators named: the same gap reads as 20% off the
    value and 25% of upside, and presenting one number as if it were the other overstates by
    exactly the amount the reader is trying to judge.
    """
    if not market_cap or market_cap <= 0 or equity_value is None:
        return None
    return (equity_value - market_cap) / market_cap


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
        return Gate(BUSINESS_QUALITY, GateStatus.UNKNOWN,
                    (f"{periods} annual statement(s) stored; at least two are needed before any "
                     f"change over comparable periods can be measured",), {"periods": periods})

    reasons, retrieval, reported = [], ev.get("retrieval_age_days"), ev.get("reported_year_age_days")
    if retrieval is None:
        reasons.append("the series carries no retrieval time, so its age cannot be established")
    elif retrieval > MAX_RETRIEVAL_AGE_DAYS:
        reasons.append(f"the series was last retrieved {retrieval} days ago, beyond the "
                       f"{MAX_RETRIEVAL_AGE_DAYS}-day limit for an actionable state")
    if reported is not None and reported > MAX_REPORTED_YEAR_AGE_DAYS:
        reasons.append(f"the newest stored annual period ended {reported} days ago, so a later "
                       f"fiscal year has almost certainly been reported and is absent")
    if ev.get("revenue") is None:
        reasons.append("no revenue figure is stored for the newest period")

    ctx = {"periods": periods, "retrieval_age_days": retrieval,
           "reported_year_age_days": reported}
    if reasons:
        return Gate(BUSINESS_QUALITY, GateStatus.UNKNOWN, tuple(reasons), ctx)
    return Gate(BUSINESS_QUALITY, GateStatus.PASS, (), ctx)


def value_trap_gate(evidence=None) -> Gate:
    """Disqualifying risk. UNKNOWN whenever a CRITICAL class of risk is simply not observed.

    THE DIRECTION THAT MATTERS. It would be easy to compute leverage and cash burn from the
    statements, find them fine, and return PASS — and that reads as "no value trap" when what
    was actually established is "no value trap of the two kinds we can see". Structural demand
    decline, customer concentration, restatements and accounting issues are not stored anywhere
    in this platform, so an unobserved critical risk is reported as unobserved.
    """
    ev = evidence or {}
    observed, unobserved = [], []
    for name, key in (("leverage", "net_debt_to_equity"), ("cash burn", "free_cashflow")):
        (observed if ev.get(key) is not None else unobserved).append(name)

    hard = []
    nde, fcf = ev.get("net_debt_to_equity"), ev.get("free_cashflow")
    if nde is not None and nde > 2.0:
        hard.append(f"net debt is {nde:.1f}x equity")
    if fcf is not None and fcf < 0 and (ev.get("free_cashflow_prior") or 0) < 0:
        hard.append("free cash flow is negative in both of the two newest stored years")
    if hard:
        return Gate(VALUE_TRAP_RISK, GateStatus.BLOCKED, tuple(hard), ev)

    missing = ["structural demand decline", "customer concentration", "restatements and "
               "accounting issues", "refinancing schedule"] + unobserved
    return Gate(VALUE_TRAP_RISK, GateStatus.UNKNOWN, (
        "no evidence is stored for these critical risk classes: " + ", ".join(missing),
        f"the {len(observed)} class(es) that could be checked from the statements "
        f"({', '.join(observed) or 'none'}) showed nothing disqualifying — which establishes "
        "only that, not the absence of a value trap",), ev)


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
        return Gate(ENTRY_CONDITION, GateStatus.UNKNOWN, (
            "fewer than two completed sessions or no 20-session average is available, so the "
            "stabilization rule cannot be evaluated",), ev)
    if ev.get("session_complete") is False:
        return Gate(ENTRY_CONDITION, GateStatus.UNKNOWN, (
            "the latest session has not completed; a forming bar is not a close",), ev)
    above = [c for c in closes[:2] if c > avg]
    if len(above) < 2:
        return Gate(ENTRY_CONDITION, GateStatus.FAIL, (
            f"{len(above)} of the two most recent completed closes are above the 20-session "
            f"average of {avg:.2f}; the rule requires both",), ev)
    return Gate(ENTRY_CONDITION, GateStatus.PASS, (), ev)
