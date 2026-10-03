"""The report contract: what a field may say, and what it may never silently mean.

WHY A CONTRACT MODULE AND NOT FOUR GENERATORS. Every failure mode these reports have to avoid
is a field that quietly became something else — a missing input rendered as a neutral reading, a
retrieval timestamp standing in for an observation time, an extraction confidence read as market
confidence, a model name treated as evidence of calibration. None of those is caught by testing
a generator; each is caught by making the illegal state unrepresentable at the point the value
is constructed. So the states, the evidence record and the statement classes live here, shared
by every producer, and a generator that cannot source a dimension has to SAY which of the five
reasons applies.

THE RULE THE WHOLE FILE EXISTS FOR: absence is never neutral. `None` is not 0.0, "no data" is
not "unchanged", and a dimension nobody looked at is not a dimension that came back empty.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

#: Bumped when the MEANING of a stored report changes — a new required field, a changed
#: statement class, a different horizon definition. Stored on every report so an old snapshot is
#: still readable as what it meant when it was written, rather than reinterpreted under today's
#: rules. A rendering change does not bump this; a semantic one does.
CONTRACT_VERSION = 1


class ReportType(str, Enum):
    MARKET_OUTLOOK = "market_outlook"
    STOCK_OUTLOOK = "stock_outlook"
    PRE_EARNINGS = "pre_earnings"
    POST_EARNINGS = "post_earnings"


class FieldState(str, Enum):
    """Why a field has no value. There is deliberately no generic "missing".

    The distinction is the product: UNAVAILABLE ("this platform cannot source it") tells a
    reader not to wait, STALE ("we have it, it is too old to use") tells them to come back,
    CONFLICTING ("two sources disagree") tells them the number they want does not exist yet,
    and NOT_APPLICABLE ("this does not apply here") tells them to stop looking. Collapsing them
    into one word throws away the only information an empty cell can carry.
    """
    OK = "OK"
    UNKNOWN = "UNKNOWN"                   # not established; may be knowable
    UNAVAILABLE = "UNAVAILABLE"           # no source for it on this platform today
    STALE = "STALE"                       # present but past its usable age
    CONFLICTING = "CONFLICTING"           # sources disagree and none is authoritative
    NOT_APPLICABLE = "NOT_APPLICABLE"     # the question does not apply to this subject


class StatementClass(str, Enum):
    """What KIND of claim a sentence is. A reader must never have to guess."""
    OBSERVED_FACT = "observed_fact"
    DETERMINISTIC_CALCULATION = "deterministic_calculation"
    INTERPRETATION = "interpretation"
    CONDITIONAL_SCENARIO = "conditional_scenario"
    MODEL_FORECAST = "model_forecast"


class ReportStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    PRELIMINARY = "preliminary"
    SUPERSEDED = "superseded"


class EarningsStage(str, Enum):
    FIRST_FLASH = "FIRST_FLASH"
    RECONCILED_RESULTS = "RECONCILED_RESULTS"
    CALL_UPDATE = "CALL_UPDATE"
    SESSION_REVIEW = "SESSION_REVIEW"


#: Horizons are EXCHANGE SESSIONS, not calendar time, and they are defined here rather than
#: borrowed from a model's label. The templates are explicit that "1-4 weeks" must not be
#: silently mapped onto an existing SWING model whose real label horizon may differ — a horizon
#: is a property of the claim, and a model is evidence for it only if its own target matches.
HORIZONS: dict[str, dict[str, Any]] = {
    "short":  {"label": "1-5 sessions",    "min_sessions": 1,  "max_sessions": 5},
    "medium": {"label": "~1-4 weeks",      "min_sessions": 5,  "max_sessions": 20},
    "long":   {"label": "~1-3 months",     "min_sessions": 20, "max_sessions": 63},
}


@dataclass
class Evidence:
    """One sourced fact, with every time that matters kept separate.

    FIVE TIMESTAMPS, NOT ONE. The templates are emphatic and the reason is look-ahead: a
    retrieval time cannot tell you whether a fact was knowable at a cutoff. `published_at` is
    when the source said it; `first_available_at` is when this platform could first have seen
    it; `observed_period` is what the value is ABOUT. A pre-earnings report frozen at a cutoff
    may only contain evidence whose first_available_at precedes that cutoff — which is
    checkable precisely because these are different fields.
    """
    evidence_id: str
    source: str                                  # URL or internal record id
    value: Any = None
    units: str | None = None
    currency: str | None = None
    basis: str | None = None                     # GAAP | adjusted | adjusted-close | ...
    published_at: datetime | None = None
    first_available_at: datetime | None = None
    retrieved_at: datetime | None = None
    observed_period: str | None = None           # the period/date the value describes
    revision: str | None = None
    state: FieldState = FieldState.OK
    note: str | None = None

    def to_dict(self) -> dict:
        return _jsonable(asdict(self))


@dataclass
class Field:
    """A reported value together with WHY it is missing when it is.

    Constructing one with no value and no reason raises: "empty because nobody looked" and
    "empty because the provider has nothing" are different reports, and a template that renders
    both as a blank cell has destroyed the difference.
    """
    value: Any = None
    state: FieldState = FieldState.OK
    reason: str | None = None
    units: str | None = None
    evidence_ids: list[str] = field(default_factory=list)
    statement: StatementClass = StatementClass.OBSERVED_FACT

    def __post_init__(self):
        if self.state is FieldState.OK and self.value is None:
            raise ValueError(
                "a field with no value must carry a FieldState explaining why — OK with a null "
                "value is exactly the silent-neutral case this contract exists to prevent")
        if self.state is not FieldState.OK and not self.reason:
            raise ValueError(f"state {self.state.value} requires a reason")

    def to_dict(self) -> dict:
        return _jsonable(asdict(self))


def unknown(reason: str, **kw) -> Field:
    return Field(value=None, state=FieldState.UNKNOWN, reason=reason, **kw)


def unavailable(reason: str, **kw) -> Field:
    return Field(value=None, state=FieldState.UNAVAILABLE, reason=reason, **kw)


def stale(value: Any, reason: str, **kw) -> Field:
    return Field(value=value, state=FieldState.STALE, reason=reason, **kw)


def conflicting(values: Any, reason: str, **kw) -> Field:
    return Field(value=values, state=FieldState.CONFLICTING, reason=reason, **kw)


def not_applicable(reason: str, **kw) -> Field:
    return Field(value=None, state=FieldState.NOT_APPLICABLE, reason=reason, **kw)


def observed(value: Any, **kw) -> Field:
    return Field(value=value, state=FieldState.OK,
                 statement=StatementClass.OBSERVED_FACT, **kw)


def calculated(value: Any, **kw) -> Field:
    return Field(value=value, state=FieldState.OK,
                 statement=StatementClass.DETERMINISTIC_CALCULATION, **kw)


def interpreted(value: Any, **kw) -> Field:
    return Field(value=value, state=FieldState.OK,
                 statement=StatementClass.INTERPRETATION, **kw)


def _jsonable(obj):
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def input_fingerprint(inputs: dict) -> str:
    """A stable hash of the INPUTS a report was built from.

    Idempotence runs on this, not on wall-clock time: an unchanged input snapshot should reuse
    the existing report rather than pay for a fresh generation, and a genuine input change
    should produce a NEW version rather than overwrite the old one. Sorted keys so the hash does
    not depend on dict ordering.
    """
    return hashlib.sha256(
        json.dumps(_jsonable(inputs), sort_keys=True, default=str).encode()).hexdigest()[:32]


def coverage(fields: dict[str, Field]) -> dict:
    """Count the report's own field states, so 'partial' is measured rather than asserted."""
    counts: dict[str, int] = {}
    for f in fields.values():
        counts[f.state.value] = counts.get(f.state.value, 0) + 1
    return {
        "by_state": counts,
        "total": len(fields),
        "ok": counts.get(FieldState.OK.value, 0),
    }


def status_from_coverage(cov: dict) -> ReportStatus:
    """COMPLETE only when every field resolved. Anything else is PARTIAL and says so.

    Deliberately not a threshold like "80% is complete": a reader deciding whether to act needs
    to know that something is missing, and a report that calls itself complete while hiding a
    gap is worse than one that admits the gap.
    """
    if cov["total"] and cov["ok"] == cov["total"]:
        return ReportStatus.COMPLETE
    return ReportStatus.PARTIAL
