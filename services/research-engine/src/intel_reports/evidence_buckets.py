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


def policy_fingerprint() -> str:
    """Digest of every rule that can change a bucket reading."""
    return digest({"version": POLICY_VERSION, "buckets": list(BUCKETS),
                   "directions": [STRONG_BULLISH, BULLISH, NEUTRAL, BEARISH,
                                  STRONG_BEARISH, UNKNOWN],
                   "work_remaining": list(WORK_REMAINING)})


def sessions_back(venue: str, anchor: datetime, n: int, *, is_trading_day) -> list:
    """The n completed trading sessions ending strictly before `anchor`'s local day.

    LIVES HERE, ORM-FREE, SO A TEST CAN IMPORT THE REAL FUNCTION. It first lived in the route
    module, where the service conftest's `db` stub makes it unimportable — so its test
    re-implemented it, and then could not catch an edit that deleted a line from the original.

    MIDDAY AND TIMEZONE-AWARE. Two separate traps:
      * midnight — `is_trading_day` resolves the instant into the venue's local calendar, so a
        midnight anchor lands on the previous local day and shifts the whole window by one.
      * naive — `is_hk_trading_day` calls `.astimezone()`, which interprets a NAIVE datetime as
        HOST LOCAL TIME. On a UTC-8 host, naive Sunday 12:00 became Monday in Hong Kong and
        weekends came back as sessions. The answer must not depend on the machine asking.
    """
    from datetime import timedelta, timezone
    out: list = []
    day = (anchor.replace(hour=12, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
           - timedelta(days=1))
    while len(out) < n:
        if is_trading_day(venue, day):
            out.append(day.date().isoformat())
        day -= timedelta(days=1)
    return list(reversed(out))


def sessions_forward(venue: str, anchor: datetime, n: int, *, is_trading_day,
                     not_after: datetime) -> list:
    """The n trading sessions strictly AFTER `anchor`, capped at `not_after`.

    CONSTRUCTED, NOT COUNTED. Taking "the next n stored rows" lets a missing daily bar pull the
    endpoint forward onto a later session — a 20-session return quietly measured over 21 — and
    the horizon then differs per symbol according to which rows happen to be present. Building
    the expected dates makes a gap visible as a gap.

    `not_after` excludes sessions that have not completed, so a horizon cannot resolve against
    a forming or future bar.
    """
    from datetime import timedelta, timezone
    out: list = []
    day = (anchor.replace(hour=12, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
           + timedelta(days=1))
    cap = not_after.replace(tzinfo=timezone.utc) if not_after.tzinfo is None else not_after
    while len(out) < n and day <= cap:
        if is_trading_day(venue, day):
            out.append(day.date().isoformat())
        day += timedelta(days=1)
    return out


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
