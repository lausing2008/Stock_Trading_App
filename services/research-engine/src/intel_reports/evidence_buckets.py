"""Thirteen evidence buckets — §24 of the Market & Stock Intelligence specification.

WHY BUCKETS AND NOT A SCORE. §24 is explicit: "Do NOT simply create one arbitrary AI score."
Nothing here aggregates. A reader sees thirteen directions and the disagreements between them,
because this platform has already measured what collapsing them costs — a win-rate badge that
pooled BUY with SELL, which point opposite ways (-1.22% vs +1.03%, n=18,561).

FOUR RULES EVERY BUCKET KEEPS.

1. UNKNOWN IS NOT NEUTRAL. An unmeasured bucket is a gap in our work; a neutral one is a
   finding about the market. The GateStatus vocabulary is reused verbatim because it already
   separates "refresh a job" from "build an analysis" from "nobody has researched this issuer".
2. SUPPORT QUALITY IS NOT PREDICTIVE CONFIDENCE. Completeness, freshness and source
   independence establish how well evidenced a reading is — not whether it is right. A
   perfectly sourced reading of a weak signal is well supported and still wrong.
   `predictive_confidence` is never set here; it stays NULL until calibrated against outcomes.
3. CONTRADICTIONS ARE WEIGHED, NOT COUNTED, AND NEVER DISCARDED. Each carries `materiality`
   and `source_ref`. Grouping by source affects INDEPENDENCE only: MU's 10-K supplies five
   separate objections, all of which are kept, and which together count once toward "do
   independent sources agree".
4. A PERMANENT UNKNOWN BLOCKS ONLY WHAT NEEDS IT. It does not block storage, price observation
   or outcome measurement.

NO ORM IMPORT: pure, so it can be tested directly against the service conftest's `db` stub.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field as dc_field
from datetime import datetime

# ---- the thirteen -------------------------------------------------------------------------
MARKET, SECTOR, INDUSTRY = "market", "sector", "industry"
FUNDAMENTALS, EARNINGS, REVISIONS = "fundamentals", "earnings", "revisions"
VALUATION, TECHNICAL, RELATIVE_STRENGTH = "valuation", "technical", "relative_strength"
OPTIONS, NEWS, CATALYSTS, RISK, DURABILITY = ("options", "news", "catalysts", "risk",
                                              "durability")

BUCKETS = (MARKET, SECTOR, INDUSTRY, FUNDAMENTALS, EARNINGS, REVISIONS, VALUATION,
           TECHNICAL, RELATIVE_STRENGTH, OPTIONS, NEWS, CATALYSTS, RISK)

#: Directions. UNKNOWN is a thirteenth value and never collapses into NEUTRAL.
STRONG_BULLISH, BULLISH, NEUTRAL, BEARISH, STRONG_BEARISH, UNKNOWN = (
    "STRONG_BULLISH", "BULLISH", "NEUTRAL", "BEARISH", "STRONG_BEARISH", "UNKNOWN")

#: Reused verbatim from the Quality & Value gate contract.
PASS, FAIL = "pass", "fail"
NOT_IMPLEMENTED, NOT_COLLECTED = "not_implemented", "not_collected"
STALE, CONFLICTING, INSUFFICIENT = "stale", "conflicting", "insufficient"

#: A bucket in one of these has nothing measured; it must not read as NEUTRAL.
WORK_REMAINING = (NOT_IMPLEMENTED, NOT_COLLECTED, STALE, CONFLICTING, INSUFFICIENT)

POLICY_VERSION = "obs-1"


@dataclass(frozen=True)
class Claim:
    """One finding, with where it came from and how much it would move the reading."""
    claim: str
    source: str
    source_ref: str | None = None
    as_of: str | None = None
    #: "decisive" changes the direction; "material" changes its strength; "minor" neither.
    materiality: str = "material"
    #: Findings sharing a group came from one document and count ONCE toward independence —
    #: while all of them are preserved and rendered.
    source_group: str | None = None

    def as_dict(self) -> dict:
        return {"claim": self.claim, "source": self.source, "source_ref": self.source_ref,
                "as_of": self.as_of, "materiality": self.materiality,
                "source_group": self.source_group or self.source}


@dataclass
class Bucket:
    name: str
    direction: str
    status: str
    evidence: list = dc_field(default_factory=list)
    contradictions: list = dc_field(default_factory=list)
    #: What this bucket could not settle. SEPARATE from contradictions: an open question about
    #: valuation explains why the valuation bucket has no direction — it is not evidence
    #: against a price reading in another bucket.
    limitations: list = dc_field(default_factory=list)
    strength: str | None = None
    inputs: dict = dc_field(default_factory=dict)

    def __post_init__(self):
        if self.name not in BUCKETS:
            raise ValueError(f"unknown bucket {self.name!r}; the thirteen are {BUCKETS}")
        if self.status in WORK_REMAINING and self.direction != UNKNOWN:
            raise ValueError(
                f"{self.name}: status {self.status} means nothing was measured, so the "
                f"direction must be UNKNOWN — not {self.direction}. An unmeasured bucket is a "
                f"gap in our work; a NEUTRAL one is a finding about the market")

    @property
    def independent_sources(self) -> int:
        """Distinct source groups across evidence AND contradictions.

        Five objections from one filing are five findings backed by ONE source. Counting them
        as five would let `support_quality` read HIGH because a single document said five
        things.
        """
        return len({c.as_dict()["source_group"]
                    for c in list(self.evidence) + list(self.contradictions)})

    @property
    def support_quality(self) -> str | None:
        """How well evidenced this reading is. NOT how likely it is to be right.

        Returns None where nothing was measured: a support grade on an absent reading would
        describe the quality of nothing.
        """
        if self.status in WORK_REMAINING:
            return None
        n = self.independent_sources
        decisive_against = any(c.materiality == "decisive" for c in self.contradictions)
        if n >= 3 and not decisive_against:
            return "HIGH"
        if n >= 2 or not decisive_against:
            return "MEDIUM"
        return "LOW"

    def as_dict(self) -> dict:
        return {
            "bucket": self.name, "direction": self.direction, "status": self.status,
            "strength": self.strength,
            "support_quality": self.support_quality,
            # NEVER set here. Calibration against resolved outcomes is the only thing that
            # may ever populate it.
            "predictive_confidence": None,
            "independent_sources": self.independent_sources,
            "evidence": [c.as_dict() for c in self.evidence],
            "contradictions": [c.as_dict() for c in self.contradictions],
            "limitations": [c.as_dict() for c in self.limitations],
            "inputs": self.inputs,
            "inputs_digest": digest(self.inputs),
        }


def digest(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        .encode("utf-8")).hexdigest()[:32]


def direction_triggers(direction: str, *, support=None, resistance=None) -> dict:
    """What would CONFIRM this reading and what would INVALIDATE it — oriented BY the reading.

    AUD-OBS-TRIGGERORIENTATION (2026-10-08). These were built from a fixed template: "a close
    above resistance confirms, a close below support invalidates", regardless of direction. On
    GLD — read BEARISH — that told a reader a rise to 406.56 would CONFIRM the bearish view and
    a fall to 376.88 would refute it. Exactly backwards. They were generic upside/downside
    boundaries presented as though they were direction-specific.

    The boundaries themselves are symmetric facts about the price range. Which one confirms
    depends entirely on what is being claimed, so this takes the direction as its subject.

    A NON-DIRECTIONAL reading has nothing to confirm or invalidate, and saying otherwise would
    invent a thesis. NEUTRAL gets `establishes` instead — either break would give a direction
    where there is none — and UNKNOWN gets neither, because a gap in our work makes no claim at
    all for a price to bear on.
    """
    up = f"a completed close above {resistance}" if resistance is not None else None
    down = f"a completed close below {support}" if support is not None else None
    if direction in (STRONG_BULLISH, BULLISH):
        return {"direction": direction, "confirms": up, "invalidates": down,
                "establishes": None,
                "basis": "a bullish reading is confirmed by a break UP through resistance and "
                         "invalidated by a break DOWN through support"}
    if direction in (STRONG_BEARISH, BEARISH):
        return {"direction": direction, "confirms": down, "invalidates": up,
                "establishes": None,
                "basis": "a bearish reading is confirmed by a break DOWN through support and "
                         "invalidated by a break UP through resistance — the mirror of the "
                         "bullish case, not the same rule"}
    if direction == NEUTRAL:
        return {"direction": direction, "confirms": None, "invalidates": None,
                "establishes": [x for x in (up, down) if x],
                "basis": "no directional reading is being made, so neither boundary confirms or "
                         "invalidates anything. Either break would ESTABLISH a direction where "
                         "there currently is none"}
    return {"direction": direction, "confirms": None, "invalidates": None, "establishes": None,
            "basis": "no reading was formed — a gap in this platform's coverage, not a view "
                     "about the price — so there is no thesis for a price level to bear on"}


def policy_fingerprint() -> str:
    """Digest of every rule that can change WHAT IS CAPTURED.

    Trigger construction is in here because it is part of the record: an observation stores the
    conditions that would confirm or invalidate it, and those were being built from a template
    that ignored direction. Correcting that without moving this fingerprint would have left
    every existing observation reused unchanged — the same failure the resolver fingerprint was
    widened to prevent, one layer up.
    """
    import ast
    import inspect
    import textwrap
    trig = ast.unparse(ast.parse(textwrap.dedent(inspect.getsource(direction_triggers))))
    return digest({"version": POLICY_VERSION, "buckets": list(BUCKETS),
                   "directions": [STRONG_BULLISH, BULLISH, NEUTRAL, BEARISH,
                                  STRONG_BEARISH, UNKNOWN],
                   "work_remaining": list(WORK_REMAINING),
                   "triggers": trig})


def _aware(dt: datetime):
    """Naive timestamps in this schema are UTC. Never let the HOST's zone decide.

    `is_hk_trading_day` calls `.astimezone()`, which reads a NAIVE datetime as HOST LOCAL
    TIME. On a UTC-8 host naive Sunday 12:00 became Monday in Hong Kong and weekends came back
    as sessions. The answer must not depend on the machine asking.
    """
    from datetime import timezone
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def sessions_back(venue: str, anchor: datetime, n: int, *, is_trading_day,
                  session_bounds=None) -> list:
    """The n trading sessions whose CLOSE is at or before `anchor`.

    LIVES HERE, ORM-FREE, SO A TEST CAN IMPORT THE REAL FUNCTION. It first lived in the route
    module, where the service conftest's `db` stub makes it unimportable — so its test
    re-implemented it, and then could not catch an edit that deleted a line from the original.

    COMPLETION, NOT CALENDAR DISTANCE. The earlier form took "trading days strictly before the
    anchor's day", which answers a different question: on an anchor at 10:00 ET the previous
    day qualified, but so did a day whose close had not happened if the anchor fell earlier.
    Asking the venue when the session actually closed removes the proxy. `session_bounds` is
    injected so this module stays importable without the ORM; omitting it falls back to the
    old day-granular rule, which is why every caller passes it and a test asserts they do.
    """
    from datetime import timedelta
    cutoff = _aware(anchor)
    out: list = []
    day = cutoff
    guard = 0
    while len(out) < n:
        guard += 1
        if guard > 4000:  # ~11 years of calendar days; a calendar gap, not a horizon
            raise RuntimeError(f"no {n} completed {venue} sessions found before {anchor}")
        probe = day.replace(hour=12, minute=0, second=0, microsecond=0)
        if is_trading_day(venue, probe):
            d = probe.date()
            bounds = session_bounds(venue, d) if session_bounds else None
            closed = bounds[1] <= cutoff if bounds else probe.date() < cutoff.date()
            if closed:
                out.append(d.isoformat())
        day -= timedelta(days=1)
    return list(reversed(out))


def sessions_forward(venue: str, anchor: datetime, n: int, *, is_trading_day,
                     not_after: datetime, session_bounds=None) -> list:
    """The n trading sessions an observation made at `anchor` could be measured over.

    CONSTRUCTED, NOT COUNTED. Taking "the next n stored rows" lets a missing daily bar pull the
    endpoint forward onto a later session — a 20-session return quietly measured over 21 — and
    the horizon then differs per symbol according to which rows happen to be present. Building
    the expected dates makes a gap visible as a gap.

    TWO BOUNDARIES, AND THE EARLIER VERSION HAD NEITHER RIGHT:

      * ELIGIBLE — a session counts only if it OPENS strictly after `anchor`, because a session
        already under way when the observation was formed could not have been entered from its
        open. The old rule started at the next CALENDAR day, so a midnight cutoff on June 1
        skipped the whole of June 1 — a full session that began 13.5 hours after the cutoff —
        and silently imposed a one-session delayed entry nobody had stated.
      * COMPLETE — a session counts only if it has CLOSED by `not_after`. The old rule admitted
        a date once 12:00 UTC had passed, which is 08:00 ET: before the US market opens, let
        alone closes. A horizon could resolve against a forming or entirely absent bar.

    Both are now asked of the venue rather than approximated, so the convention a reader is
    told about is the one the code applies.
    """
    from datetime import timedelta
    start = _aware(anchor)
    cap = _aware(not_after)
    out: list = []
    day = start
    guard = 0
    while len(out) < n:
        guard += 1
        if guard > 4000:
            break
        probe = day.replace(hour=12, minute=0, second=0, microsecond=0)
        day += timedelta(days=1)
        if not is_trading_day(venue, probe):
            continue
        d = probe.date()
        bounds = session_bounds(venue, d) if session_bounds else None
        if bounds is None:
            # Day-granular fallback, retained only so the function stays callable without the
            # calendar; every production caller injects `session_bounds`.
            if d <= start.date() or d > cap.date():
                continue
            out.append(d.isoformat())
            continue
        opens, closes = bounds
        if opens <= start:
            continue  # already trading when the observation was formed
        if closes > cap:
            break  # not complete; no later session is either
        out.append(d.isoformat())
    return out


#: Relative tolerance on an adjustment factor. A split or dividend moves it by far more than
#: this; float noise and vendor rounding move it by far less.
ADJUSTMENT_TOLERANCE = 1e-4

# =============================================================================================
# RETURN BASIS — three distinct definitions, never used interchangeably.
#
# A provider's "adjusted close" is NOT one of these. Yahoo's, for instance, is adjusted for
# splits AND dividends, so its ratio moves for both and a single blended factor cannot say
# which occurred. Treating it as a split adjustment silently folds distributions into a price
# return; treating it as a total return assumes a reinvestment convention nobody stated. So the
# basis is chosen HERE and evidenced from action records, and the provider factor is used only
# for what it can honestly support.
# =============================================================================================

#: Unadjusted closes. Valid ONLY across a window with no corporate action at all.
RAW_PRICE = "raw_price"
#: Share-count actions (splits, stock dividends) applied so both endpoints sit on one share
#: basis. Cash distributions are EXCLUDED BY DEFINITION, and that exclusion is disclosed.
SPLIT_ADJUSTED_PRICE = "split_adjusted_price"
#: Split-adjusted AND cash distributions included. Needs an amount for every distribution in
#: the window; one missing amount makes the total unverifiable, not approximate.
TOTAL_RETURN = "total_return"

RETURN_BASES = (RAW_PRICE, SPLIT_ADJUSTED_PRICE, TOTAL_RETURN)

#: Actions that change the share count and therefore MUST be applied to a price return.
SHARE_COUNT_ACTIONS = ("split", "stock_dividend")
#: Actions that pay value out without changing the share count.
DISTRIBUTION_ACTIONS = ("cash_dividend", "spinoff")

#: Named methodology. A change gets a NEW name so earlier figures re-derive under the old one
#: rather than being silently restated.
ADJUSTMENT_METHOD = "cumulative_split_factor_v1"


def split_factors(actions: list, dates: list) -> dict:
    """Cumulative share-count factor to apply to each session's close.

    Walking BACKWARDS from the end of the window: a close before a 2-for-1 split is on an
    old-share basis and must be halved to sit beside closes after it. The last session is the
    reference basis, so its factor is 1.0 by construction and the endpoint is never restated.
    """
    by_date = {}
    for a in actions:
        if a.get("action_type") in SHARE_COUNT_ACTIONS and a.get("split_ratio"):
            by_date.setdefault(a["ex_date"], 1.0)
            by_date[a["ex_date"]] *= float(a["split_ratio"])
    out, factor = {}, 1.0
    for d in reversed(dates):
        out[d] = factor
        if d in by_date:  # sessions BEFORE the ex-date are on the older basis
            factor /= by_date[d]
    return out


def adjustment_evidence(series: dict, *, basis: str = SPLIT_ADJUSTED_PRICE,
                        actions: dict | None = None, coverage: dict | None = None) -> dict:
    """Can this window's return be computed on `basis`, and on what evidence?

    `series`   {instrument: [{date, close, adj_close}]} covering EVERY session the return
               spans, INCLUDING the reference session — an action between the reference close
               and the first measured session corrupts the result just as badly as one in the
               middle.
    `actions`  {instrument: [sourced action records]} — the PREFERRED evidence, because it says
               what happened rather than only that something did.
    `coverage` {instrument: {source, covers_from, covers_to, method, retrieved_at}} — the claim
               that the action history is complete for this span. Without it, "no actions" is
               ambiguous between "none occurred" and "nobody looked", and those are the
               difference between a verified basis and an unverified one.

    `consistent` is TRI-STATE and never defaults to the benign case:
      None   — not establishable from the evidence held. Unchecked is not checked-and-fine.
      False  — an action occurred that this basis cannot absorb from what is held.
      True   — the basis holds, and `factors` carries the adjustment to apply.

    Both instruments are judged on the same footing: an excess return built from a sound stock
    basis and an unsound benchmark basis is still wrong.
    """
    if basis not in RETURN_BASES:
        raise ValueError(f"unknown return basis {basis!r}")
    actions, coverage = actions or {}, coverage or {}
    out = {"basis": basis, "method": ADJUSTMENT_METHOD, "factors": {}, "actions": [],
           "disclosures": [], "evidence": {}, "unverified": []}

    for label, bars in series.items():
        dates = [b["date"] for b in bars]
        cov = coverage.get(label)
        acts = [a for a in actions.get(label, [])
                if dates and dates[0] <= a.get("ex_date", "") <= dates[-1]]
        out["actions"].extend({**a, "instrument": label} for a in acts)

        # THE EVIDENCED SPAN, NEVER THE REQUESTED ONE. A fetch cannot speak for actions that had
        # not happened when it ran, so a request through a future date verifies nothing past the
        # retrieval date.
        covered = bool(cov and dates and cov.get("evidenced_from") <= dates[0]
                       and cov.get("evidenced_to") >= dates[-1])
        if covered:
            # NAMED `completeness`, NOT `basis`. Calling it `basis` shadowed this function's
            # `basis` PARAMETER — the return basis — so `basis == SPLIT_ADJUSTED_PRICE` compared
            # a completeness value against a return basis and was always False, silently
            # disabling the raw-price refusal, the dividend disclosure and the whole
            # total-return branch. Caught by the dividend tests, which is the only reason it did
            # not ship: nothing about the shadowing itself raises.
            completeness = cov.get("completeness_basis") or "response_only"
            out["evidence"][label] = {
                "verified_by": "sourced_action_history", "source": cov.get("source"),
                "method": cov.get("method"), "retrieved_at": cov.get("retrieved_at"),
                "evidenced": [cov.get("evidenced_from"), cov.get("evidenced_to")],
                "requested": [cov.get("requested_from"), cov.get("requested_to")],
                "completeness_basis": completeness,
                "completeness_note": cov.get("completeness_note"),
                "actions_in_window": len(acts)}
            if not acts and completeness != "source_guarantee":
                out["disclosures"].append(
                    f"{label}: {cov.get('source')} returned no corporate action for this "
                    f"window and publishes no completeness guarantee. That establishes what was "
                    f"RETURNED, not that none occurred")
            share = [a for a in acts if a.get("action_type") in SHARE_COUNT_ACTIONS]
            dist = [a for a in acts if a.get("action_type") in DISTRIBUTION_ACTIONS]
            if basis == RAW_PRICE and (share or dist):
                out["unverified"].append(
                    f"{label}: {len(share) + len(dist)} corporate action(s) in the window, so "
                    f"unadjusted closes do not share one basis")
                continue
            if share and any(not a.get("split_ratio") for a in share):
                out["unverified"].append(
                    f"{label}: a share-count action on "
                    f"{next(a['ex_date'] for a in share if not a.get('split_ratio'))} has no "
                    f"recorded ratio, so the adjustment cannot be computed")
                continue
            out["factors"][label] = split_factors(acts, dates)
            if basis == SPLIT_ADJUSTED_PRICE and dist:
                total = sum(a.get("cash_amount") or 0 for a in dist)
                out["disclosures"].append(
                    f"{label}: {len(dist)} distribution(s) totalling {total:.4f} per share are "
                    f"EXCLUDED — a split-adjusted price return does not include them")
            if basis == TOTAL_RETURN:
                missing = [a["ex_date"] for a in dist if a.get("cash_amount") is None]
                if missing:
                    out["unverified"].append(
                        f"{label}: distribution(s) on {', '.join(missing[:3])} have no recorded "
                        f"amount, so a total return cannot be computed")
                    continue
                out["disclosures"].append(
                    f"{label}: {len(dist)} distribution(s) included at the recorded cash amount")
            continue

        # FALLBACK: the provider's adjustment factor. It can establish only ONE thing honestly —
        # that NOTHING happened — because a flat factor means the provider applied no adjustment
        # of any kind. A factor that MOVES cannot be decomposed into a split and a distribution,
        # so it can never be converted into a basis of our choosing.
        factors, gaps = [], []
        for b in bars:
            close, adj = b.get("close"), b.get("adj_close")
            if close in (None, 0) or adj is None:
                gaps.append(b.get("date"))
            else:
                factors.append((b.get("date"), adj / close))
        if gaps or len(factors) < 2:
            out["unverified"].append(
                f"{label}: no corporate-action history covers this window and "
                + (f"{len(gaps)} session(s) have no adj_close ({', '.join(str(g) for g in gaps[:3])})"
                   if gaps else "fewer than two sessions carry a provider factor"))
            continue
        moved = [d for (d, f) in factors[1:]
                 if factors[0][1] == 0
                 or abs(f - factors[0][1]) / abs(factors[0][1]) > ADJUSTMENT_TOLERANCE]
        if moved:
            out["unverified"].append(
                f"{label}: the provider adjustment factor moves (first on {moved[0]}) and a "
                f"single blended factor cannot be decomposed into a split and a distribution, "
                f"so it cannot establish {basis}")
            continue
        out["factors"][label] = {b["date"]: 1.0 for b in bars}
        out["evidence"][label] = {
            "verified_by": "provider_adjustment_factor",
            "source": "stored adj_close", "method": "flat-factor check",
            "note": "a flat factor shows the provider applied NO adjustment across this "
                    "window, which rules out an action it recognised. It does not describe "
                    "the provider's methodology and is not relied on for anything else.",
            "actions_in_window": 0}

    if out["unverified"]:
        verdict, reason = None, ("the adjustment basis could not be established — "
                                 + "; ".join(out["unverified"][:3]))
    elif not out["evidence"]:
        verdict, reason = None, "no instrument supplied any adjustment evidence"
    else:
        verdict = True
        kinds = sorted({e["verified_by"] for e in out["evidence"].values()})
        reason = (f"basis {basis} established for {len(out['evidence'])} instrument(s) via "
                  f"{', '.join(kinds)}"
                  + (f"; {len(out['actions'])} corporate action(s) in window" if out["actions"]
                     else "; no corporate action in window"))
        if out["disclosures"]:
            reason += ". " + " ".join(out["disclosures"])
    out["consistent"] = verdict
    out["reason"] = reason
    return out


#: What the adjustment evidence ESTABLISHES, which is a different question from whether a
#: figure could be computed from it.
EVIDENCE_VERIFIED = "verified"        # a source that guarantees an exhaustive list for the span
EVIDENCE_PROVISIONAL = "provisional"  # a source returned actions; completeness is not established
EVIDENCE_UNVERIFIED = "unverified"    # the basis could not be established at all

EVIDENCE_LABEL = {
    EVIDENCE_VERIFIED:
        "Verified — corporate-action coverage is guaranteed exhaustive for this window",
    EVIDENCE_PROVISIONAL:
        "Provisional — based on returned corporate actions; completeness unverified",
    EVIDENCE_UNVERIFIED:
        "Unverified — no adjustment basis could be established for this window",
}

#: HOW TO CLOSE IT. Not "accept it because the alternative is inconvenient": the remedy is a
#: source that documents exhaustiveness, or a second independent source agreeing.
PROVISIONAL_REMEDY = ("A source that documents completeness for the span, or corroboration from "
                      "a second independent source, would make this verified. Until then the "
                      "figure is usable and labelled, and is never pooled with verified results.")


def evidence_status(adjustment: dict | None) -> str:
    """Classify what the adjustment evidence ESTABLISHES — never what was convenient.

    A source returning no corporate actions establishes that it RETURNED none. It does not
    establish that none occurred, and keeping the calculation available is not a reason to call
    it verified. These are separate judgements and are recorded separately.
    """
    if not adjustment or adjustment.get("consistent") is not True:
        return EVIDENCE_UNVERIFIED
    ev = adjustment.get("evidence") or {}
    if not ev:
        return EVIDENCE_UNVERIFIED
    # The WEAKEST instrument decides: an excess return resting on a verified stock basis and a
    # provisional benchmark basis is provisional.
    for e in ev.values():
        if e.get("verified_by") != "sourced_action_history":
            return EVIDENCE_PROVISIONAL      # a flat provider factor is corroboration, not proof
        if e.get("completeness_basis") != "source_guarantee":
            return EVIDENCE_PROVISIONAL
    return EVIDENCE_VERIFIED


def performance_eligibility(resolution_state: str | None, evidence: str | None,
                            *, capture_invalidated: bool = False) -> str:
    """Which performance pool this figure may enter, if any.

    THE THIRD QUESTION, and the one a reader of an aggregate depends on. A provisional figure is
    not a worse verified figure; it belongs to a different population, and averaging the two
    produces a number describing neither.
    """
    if capture_invalidated or not (resolution_state or "").startswith("RESOLVED"):
        return "ineligible"
    if evidence == EVIDENCE_VERIFIED:
        return "verified"
    if evidence == EVIDENCE_PROVISIONAL:
        return "provisional"
    return "ineligible"


def apply_adjustment(closes: dict, factors: dict | None) -> dict:
    """Restate closes onto one share basis. Inside the fingerprinted contract, because an
    adjustment that changes a figure may not sit outside the thing that versions figures."""
    if not factors:
        return dict(closes)
    return {d: (None if v is None else v * factors.get(d, 1.0)) for d, v in closes.items()}


def closes_by_date(rows, dates: list) -> dict:
    """Closes keyed BY SESSION DATE, with every expected date present.

    A date absent from `rows` maps to None — a MISSING BAR, not a reason to reach further
    forward. The earlier form took "the next n stored rows", so one missing daily bar pulled
    the endpoint onto a later session and a 20-session return was quietly measured over 21,
    differently per symbol depending on which rows happened to exist.

    Pure and ORM-free so it can be fingerprinted and tested: price selection changes outcome
    figures, so it belongs inside the calculation contract.
    """
    got = {}
    for r in rows:
        d = r["date"] if isinstance(r, dict) else r[0]
        v = r["close"] if isinstance(r, dict) else r[1]
        got[d] = float(v) if v is not None else None
    return {d: got.get(d) for d in dates}


def unmeasured(name: str, status: str, why: str, *, source: str = "platform") -> Bucket:
    """A bucket with nothing measured — direction UNKNOWN, and the reason recorded."""
    return Bucket(name=name, direction=UNKNOWN, status=status,
                  evidence=[Claim(claim=why, source=source, materiality="minor")])


# =====================================================================================
# Builders — each reads what the platform actually holds, and says so when it holds nothing.
#
# For MU today most of these are unmeasured, and that is the correct output rather than a
# defect. The ones that CAN read something are technical (the direction screen), fundamentals
# and earnings (stored statements and events), and the three fed by `issuer_assessments`.
# =====================================================================================

_DIR_FROM_SETUP = {"breakout": BULLISH, "breakout_watch": NEUTRAL,
                   "breakdown": BEARISH, "breakdown_watch": NEUTRAL,
                   "range": NEUTRAL}


def technical_bucket(setup: dict | None, *, session: str | None = None) -> Bucket:
    """From the direction screen. A range position is a reading; it is not a forecast."""
    if not setup or setup.get("direction") in (None, "unknown"):
        return unmeasured(TECHNICAL, NOT_COLLECTED,
                          "no usable completed-session prices for this issuer",
                          source="daily price series")
    d = _DIR_FROM_SETUP.get(setup["direction"], UNKNOWN)
    if d is UNKNOWN:
        return unmeasured(TECHNICAL, NOT_COLLECTED,
                          f"unrecognised setup {setup['direction']!r}")
    ev = [Claim(claim=setup.get("label", ""), source="daily price series",
                source_ref=f"20-session range to {session}" if session else None,
                as_of=session, materiality="material", source_group="price")]
    for f in (setup.get("factors") or [])[:3]:
        ev.append(Claim(claim=f, source="daily price series", as_of=session,
                        materiality="minor", source_group="price"))
    against = [Claim(
        claim="a range position describes where the close sits, not where it goes — these "
              "rules are uncalibrated and carry no probability",
        source="screen policy", materiality="material", source_group="policy")]
    for lim in (setup.get("limitations") or [])[:2]:
        against.append(Claim(claim=lim, source="screen policy", materiality="minor",
                             source_group="policy"))
    return Bucket(name=TECHNICAL, direction=d, status=PASS, evidence=ev,
                  contradictions=against,
                  strength="MODERATE" if d is not NEUTRAL else "WEAK",
                  inputs={k: setup.get(k) for k in
                          ("direction", "label", "close", "support", "resistance",
                           "volume_ratio", "session")})


def from_assessment(name: str, assessment: dict | None, *, absent: str) -> Bucket:
    """A bucket fed by a stored `issuer_assessments` row.

    An assessment that concluded "insufficient" is FINISHED WORK and still unmeasured for
    direction — those are not in tension, and the status says which.
    """
    if not assessment:
        return unmeasured(name, NOT_COLLECTED, absent, source="issuer assessments")
    verdict = (assessment.get("verdict") or "").lower()
    status = {"supported": PASS, "contradicted": FAIL}.get(verdict, INSUFFICIENT)
    src = f"assessment v{assessment.get('version')}"
    ev, against = [], []
    for f in (assessment.get("findings") or []):
        grp = f.get("source") or src
        if f.get("claim"):
            ev.append(Claim(claim=f["claim"], source=f.get("source") or src,
                            source_ref=f.get("source_ref"),
                            as_of=assessment.get("cutoff"),
                            materiality="material", source_group=grp))
        if f.get("counterevidence"):
            against.append(Claim(claim=f["counterevidence"], source=f.get("source") or src,
                                 source_ref=f.get("source_ref"),
                                 as_of=assessment.get("cutoff"),
                                 materiality="material", source_group=grp))
    limitations = []
    if assessment.get("unresolved"):
        # A LIMITATION, NOT COUNTEREVIDENCE. An open valuation question explains why THIS
        # bucket has no direction; it is not evidence against a price reading in another
        # bucket. Treating it as a contradiction made it the "strongest counterevidence" to a
        # technical direction it has no bearing on.
        limitations.append(Claim(claim=assessment["unresolved"], source=src,
                                 as_of=assessment.get("cutoff"),
                                 materiality="limitation", source_group=src))
    inputs = {"verdict": verdict, "version": assessment.get("version"),
              "cutoff": assessment.get("cutoff")}
    if status is INSUFFICIENT:
        # Direction must be UNKNOWN: the assessment did not reach one.
        return Bucket(name=name, direction=UNKNOWN, status=INSUFFICIENT,
                      evidence=ev, contradictions=against, limitations=limitations,
                      inputs=inputs)
    return Bucket(name=name, direction=BULLISH if status is PASS else BEARISH,
                  status=status, evidence=ev, contradictions=against,
                  limitations=limitations, strength="MODERATE", inputs=inputs)


def fundamentals_bucket(fin: dict | None) -> Bucket:
    """From stored annual statements. Staleness is a status, not a silent discount."""
    if not fin or (fin.get("annual_periods") or 0) < 2:
        return unmeasured(FUNDAMENTALS, INSUFFICIENT,
                          f"{(fin or {}).get('annual_periods') or 0} annual statement(s) "
                          f"stored; two are needed to measure a change",
                          source="financial statements")
    reported_age = fin.get("reported_year_age_days")
    if reported_age is not None and reported_age > 400:
        return unmeasured(
            FUNDAMENTALS, STALE,
            f"the newest stored annual period ended {reported_age} days ago, beyond one "
            f"reporting year; whether a later annual report has been filed is not checked here",
            source="financial statements")
    rev, prior = fin.get("revenue"), fin.get("revenue_prior")
    if rev is None or not prior:
        return unmeasured(FUNDAMENTALS, NOT_COLLECTED,
                          "no revenue figure for one of the two newest stored periods",
                          source="financial statements")
    growth = (rev - prior) / abs(prior) * 100.0
    return Bucket(
        name=FUNDAMENTALS, direction=BULLISH if growth > 0 else BEARISH, status=PASS,
        strength="MODERATE",
        evidence=[Claim(claim=f"revenue {'grew' if growth > 0 else 'fell'} {abs(growth):.1f}% "
                              f"in the latest stored year",
                        source="stored annual statements", as_of=fin.get("period_end"),
                        source_group="statements")],
        contradictions=[Claim(
            claim="calculated from the stored provider series: neither year's accounting basis "
                  "is stored, so the two rows are not shown to be comparable",
            source="stored annual statements", materiality="material",
            source_group="statements")],
        inputs={k: fin.get(k) for k in ("annual_periods", "revenue", "revenue_prior",
                                        "period_end", "retrieval_age_days",
                                        "reported_year_age_days")})


# =====================================================================================
# The summary — the conclusion, above the audit detail.
# =====================================================================================

#: Buckets that may contribute a DIRECTION to the summary. Narrow on purpose: these are the
#: only ones the platform can currently measure a direction from, and a summary direction
#: drawn from a bucket that cannot produce one would be invented.
DIRECTIONAL = (TECHNICAL, FUNDAMENTALS)

_RANK = {STRONG_BULLISH: 2, BULLISH: 1, NEUTRAL: 0, BEARISH: -1, STRONG_BEARISH: -2}


def summarise(subject: str, buckets: list, *, horizon_label: str,
              horizon_sessions: int) -> dict:
    """Direction, three factors, the strongest counterevidence, and what would change it.

    THE DIRECTION IS NOT AN AVERAGE. Measured buckets that disagree produce NEUTRAL with the
    disagreement named — not a midpoint, which would read as a conviction nobody holds.
    """
    measured = [b for b in buckets if b.name in DIRECTIONAL and b.status not in WORK_REMAINING]
    dirs = {b.direction for b in measured}
    if not measured:
        direction, why = UNKNOWN, "no bucket has a measured direction"
    elif len(dirs) == 1:
        direction, why = dirs.pop(), f"{len(measured)} measured bucket(s) agree"
    else:
        ranks = [_RANK.get(b.direction, 0) for b in measured]
        direction = NEUTRAL
        why = ("measured buckets disagree ("
               + ", ".join(f"{b.name}={b.direction}" for b in measured)
               + ") — reported as NEUTRAL rather than averaged into a conviction nobody holds")
        if all(r > 0 for r in ranks) or all(r < 0 for r in ranks):
            direction = BULLISH if ranks[0] > 0 else BEARISH

    # Three factors: the most material supporting claims across every bucket that has any.
    order = {"decisive": 0, "material": 1, "minor": 2}
    factors = sorted(
        ((b, c) for b in buckets for c in b.evidence if b.status not in WORK_REMAINING),
        key=lambda bc: order.get(bc[1].materiality, 3))[:3]

    # THE STRONGEST COUNTEREVIDENCE COMES FROM A MEASURED BUCKET. A contradiction inside a
    # bucket that reached no direction is a reason that bucket is unmeasured — presenting it
    # as the case against a price reading attributes it to a conclusion it does not address.
    against = sorted(((b, c) for b in buckets if b.status not in WORK_REMAINING
                      for c in b.contradictions),
                     key=lambda bc: order.get(bc[1].materiality, 3))
    strongest = against[0] if against else None
    research_limits = [{"bucket": b.name, **c.as_dict()}
                       for b in buckets for c in b.limitations]

    unmeasured_names = [b.name for b in buckets if b.status in WORK_REMAINING]
    return {
        "subject": subject,
        "direction": direction,
        "direction_basis": why,
        "horizon": horizon_label,
        "horizon_sessions": horizon_sessions,
        "support_quality": _worst([b.support_quality for b in measured]),
        "predictive_confidence": None,
        "predictive_confidence_note":
            "not calibrated — a stored observation makes future measurement possible and "
            "establishes no skill by itself",
        "three_factors": [{"bucket": b.name, **c.as_dict()} for b, c in factors],
        "strongest_counterevidence": ({"bucket": strongest[0].name, **strongest[1].as_dict()}
                                      if strongest else None),
        # Open questions from every bucket, measured or not. These say what the research could
        # not settle; they are not arguments against the direction above.
        "research_limitations": research_limits,
        "unmeasured_buckets": unmeasured_names,
        "unmeasured_note": f"{len(unmeasured_names)} of {len(buckets)} buckets have nothing "
                           f"measured. They do not block price observation or outcome "
                           f"measurement — only conclusions that need them.",
    }


def _worst(grades: list) -> str | None:
    present = [g for g in grades if g]
    if not present:
        return None
    for g in ("LOW", "MEDIUM", "HIGH"):
        if g in present:
            return g
    return None
