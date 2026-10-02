"""M24 — capability readiness: can this action actually be performed, and if not, what is blocked?

WHY NOT A MIGRATION LEDGER. `migration_applied(name)` answers "did this statement run". That is
not the same question as "can the outbox enqueue", and the difference is where EC-03 lived: a
service started, passed its healthcheck, and ran every job whose premise was a migration that
had failed. Startup is not proof of prerequisites, and a ledger entry is not proof of capability
— a table can exist with the constraint missing, or exist and be unreadable by this role.

THREE-STATE, NEVER TWO. `unknown` is a first-class answer: a database that cannot be reached
has not told us the capability is absent. Collapsing unknown to `not_ready` would block work on
a transient outage; collapsing it to `ready` would run work on an unverified prerequisite. Both
are wrong in different directions, so the caller decides — and for a RISK-INCREASING action the
correct decision is to defer.

LIVENESS STAYS SEPARATE FROM READINESS. A process with an unmet prerequisite is not unhealthy;
it is healthy and degraded. Failing the container healthcheck would cascade through
`depends_on: service_healthy` into a refusal to start, which is the 2026-09-17 outage. The
capability says what is blocked; the process keeps running.

ENTRY READINESS IS NOT EXIT READINESS. A prerequisite that blocks opening a position must never
block closing one. Protective exits and reconciliation declare their own dependencies, and an
entry-only prerequisite that disabled them would turn a degraded state into a trapped position.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class Readiness(str, Enum):
    READY = "ready"
    NOT_READY = "not_ready"
    #: Could not be established. NOT a synonym for either of the others.
    UNKNOWN = "unknown"


class Level(str, Enum):
    """WHAT a check establishes — because these are not interchangeable, and the most common
    error is reading the cheapest one as the strongest.

    A successful SELECT does not establish write readiness. A present table does not establish
    that this role may write to it. A granted privilege does not establish that the intended
    write and its recovery path actually work. Each level answers one question and leaves the
    next one open.
    """
    SCHEMA = "schema"                 # required structures and constraints exist
    PRIVILEGE = "privilege"           # this service account holds the declared permissions
    CONNECTIVITY = "connectivity"     # read access works, at this moment
    SANDBOX_WRITE = "sandbox_write"   # the intended write and recovery behaviour actually work
    CONSUMER_ENFORCEMENT = "consumer_enforcement"  # a missing prerequisite blocks the action


#: Levels a routine readiness probe may run. SANDBOX_WRITE is excluded on purpose: it performs a
#: real write, and a health endpoint that writes on every scrape is a liability rather than a
#: check. It belongs to an explicitly scoped lifecycle test.
PASSIVE_LEVELS = (Level.SCHEMA, Level.PRIVILEGE, Level.CONNECTIVITY)


class Impact(str, Enum):
    """What a capability gates. Kept explicit so an entry-only prerequisite cannot silently
    disable a protective exit."""
    ENTRY = "entry"                  # opening or increasing exposure — risk INCREASING
    DELIVERY = "delivery"            # sending notifications
    EXIT = "exit"                    # closing or reducing exposure — risk REDUCING
    RECONCILIATION = "reconciliation"  # establishing what actually happened


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class Requirement:
    """One checkable prerequisite. `check` returns (Readiness, evidence).

    `level` is what a pass actually establishes. It is recorded so a report can never imply
    more than it tested — the difference between "the table is there" and "this account can
    write to it" is the whole distance between a schema check and an outage.
    """
    name: str
    description: str
    check: object = None
    level: "Level" = None

    def evaluate(self) -> tuple[Readiness, str]:
        if self.check is None:
            return Readiness.UNKNOWN, "no check implemented"
        try:
            state, evidence = self.check()
        except Exception as exc:                        # noqa: BLE001
            # An erroring check has NOT established absence. A permission error, a dropped
            # connection and a missing table are different facts, and only one of them means
            # the capability is absent.
            return Readiness.UNKNOWN, f"{type(exc).__name__}: {exc}"[:300]
        return state, evidence


@dataclass
class Capability:
    name: str
    #: What becomes impossible when this is not ready. In operator language, not a table name.
    blocks: str
    impact: Impact
    requirements: list[Requirement] = field(default_factory=list)
    #: What to do with work that cannot proceed. The default is the only safe one.
    recovery: str = ("pending work is preserved and retried once the prerequisite returns; "
                     "nothing is dropped and nothing is attempted twice")

    def evaluate(self, *, now: datetime | None = None) -> dict:
        now = now or utcnow()
        results = []
        worst = Readiness.READY
        for req in self.requirements:
            state, evidence = req.evaluate()
            results.append({"requirement": req.name, "state": state.value,
                            "evidence": evidence, "description": req.description,
                            "level": (req.level.value if req.level else "unspecified")})
            # NOT_READY dominates UNKNOWN dominates READY. A definite absence is more
            # informative than an unestablished one and should be reported as the reason.
            if state is Readiness.NOT_READY:
                worst = Readiness.NOT_READY
            elif state is Readiness.UNKNOWN and worst is not Readiness.NOT_READY:
                worst = Readiness.UNKNOWN
        levels = sorted({r["level"] for r in results})
        return {
            "capability": self.name,
            "state": worst.value,
            # WHAT WAS ACTUALLY ESTABLISHED. A capability whose only level is `schema` is not
            # "ready to operate" — it is "structurally present". Naming the untested levels
            # stops the first being read as the second.
            "levels_checked": levels,
            "levels_not_checked": [l.value for l in Level if l.value not in levels],
            "blocks": self.blocks if worst is not Readiness.READY else None,
            "impact": self.impact.value,
            "recovery": self.recovery,
            "checked_at": now.isoformat(),
            "requirements": results,
        }


def gate(evaluation: dict, *, risk_increasing: bool) -> tuple[bool, str]:
    """May the dependent action proceed?

    THE ASYMMETRY IS THE POINT. For a RISK-INCREASING action — opening a position, sending mail
    — `unknown` blocks: proceeding on an unverified prerequisite is how an unapplied migration
    silently stamped every pending alert as delivered. For a RISK-REDUCING action — a protective
    exit, a reconciliation read — `unknown` does NOT block: refusing to close a position because
    a database could not be reached turns a degraded state into a trapped one.
    """
    state = evaluation.get("state")
    if state == Readiness.READY.value:
        return True, "ready"
    if state == Readiness.UNKNOWN.value and not risk_increasing:
        return True, ("prerequisite unknown, but this action REDUCES risk — proceeding, because "
                      "refusing would trap existing exposure")
    return False, f"{state}: {evaluation.get('blocks') or evaluation.get('capability')}"


def report(capabilities: list[Capability], *, now: datetime | None = None) -> dict:
    """The whole matrix. Shaped for a health block: a degraded capability is listed in operator
    language, and the overall status it belongs to stays `ok`."""
    now = now or utcnow()
    evals = [c.evaluate(now=now) for c in capabilities]
    degraded = [e for e in evals if e["state"] != Readiness.READY.value]
    return {
        "checked_at": now.isoformat(),
        "capabilities": evals,
        "all_ready": not degraded,
        # Operator language, not table names — the EC-03 lesson: a monitor reading
        # `{"ok": false}` learns that something is wrong but not what it costs.
        "degraded": [f"{e['capability']}: {e['blocks']}" for e in degraded],
        "entry_blocked": [e["capability"] for e in degraded
                          if e["impact"] == Impact.ENTRY.value],
        # Every capability's untested levels, so a green report states its own limits.
        "levels_not_checked": sorted({l for e in evals for l in e["levels_not_checked"]}),
        # Reported separately, and expected to be EMPTY: an exit or reconciliation blocked by a
        # prerequisite is a far more serious condition than a blocked entry.
        "exit_or_reconciliation_blocked": [
            e["capability"] for e in degraded
            if e["impact"] in (Impact.EXIT.value, Impact.RECONCILIATION.value)],
    }
