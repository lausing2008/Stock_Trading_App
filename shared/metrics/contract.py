"""The measurement contract: what a number must declare before it may be reported.

WHY THIS EXISTS. Three headline findings in the 2026-09-30 checkpoint were withdrawn, and all
three failed the same way — the reported quantity was not the quantity the production code
consumes:

  * confidence "flat 36.5-43.9%" pooled across slices production splits (59 supported slices with
    wide dispersion once sliced as the consumer slices);
  * "bearish flow is anti-predictive" counted option-chain rows the consumer dedupes, and equal
    symbol/date weighting REVERSED the gap;
  * the GEX comparison pooled raw returns across a bullish and a bearish thesis, so its sign was
    backwards.

None of those was a coding error. Each was a number published without declaring its population,
weighting or outcome definition, which made it impossible to notice the mismatch by reading it.

`docs/features/2026-09-30-measurement-framework-and-improvement-backlog.md` section 4 turns that
into a rule: **match population, decision authority, time window, outcome definition and
weighting before reporting a rate.** This module makes the rule mechanical. A metric that does
not declare every dimension cannot be constructed, so it cannot be published.

This is deliberately a CONTRACT, not a metrics service. It computes nothing and stores nothing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class MetricKind(str, Enum):
    """Section 2: "Separate operational correctness, prediction quality, execution quality and
    investment outcomes; one cannot certify the others."

    The kind is not decoration — it decides whether an outcome definition is REQUIRED or
    FORBIDDEN (see `MetricDefinition.__post_init__`). A deployed counter proving a job ran is an
    OPERATIONAL fact and must not be dressed up with a target definition it never measured; an
    investment claim without one is not a claim at all.
    """
    OPERATIONAL = "operational"
    PREDICTION = "prediction"
    EXECUTION = "execution"
    INVESTMENT = "investment"


class Cohort(str, Enum):
    """Section 5: "Never overwrite candidate outcomes with trade outcomes."

    These three are routinely conflated and must never share a denominator. A delivered-alert
    count is not a trade count; a skipped candidate's counterfactual return is not a realised
    one.
    """
    CANDIDATE_HYPOTHETICAL = "candidate_hypothetical"
    ALERT_AVAILABLE = "alert_available"
    EXECUTED = "executed"
    OPERATIONAL = "operational"


class DecisionAuthority(str, Enum):
    """Who actually decided. M04's whole correction was that a shadow rejection and an
    authoritative rejection had been summed into one rate; they answer different questions and
    may never be added together."""
    AUTHORITATIVE = "authoritative"     # the decision production actually acted on
    SHADOW_ONLY = "shadow_only"         # computed, recorded, never acted on
    LEGACY_POLICY = "legacy_policy"
    RISK_VETO = "risk_veto"
    RECOVERY_POLICY = "recovery_policy"
    NOT_APPLICABLE = "not_applicable"   # operational metrics decide nothing


class Weighting(str, Enum):
    """Section 7: "Do not treat thousands of contracts or scans as independent observations."

    Row weighting is the default that produced two of the three withdrawn findings. It stays
    available because it is sometimes correct, but it must be stated, so that a reader can see
    that 1,000 rows were 1,000 rows and not 1,000 bets.
    """
    ROW = "row"
    EVENT = "event"
    SYMBOL_SESSION = "symbol_session"
    PORTFOLIO_SESSION = "portfolio_session"


@dataclass(frozen=True)
class MetricWindow:
    """Inclusive start, EXCLUSIVE end, with an explicit timezone.

    The exclusive end is not a style preference. `docs/incidents/utc-vs-et-date-boundary.md`
    records that a naive UTC `.date()` reads one day ahead for 4-5 hours every evening; a window
    whose end is ambiguous silently acquires or loses a trading session depending on when the
    report ran. Both instants must be timezone-aware.
    """
    start: datetime
    end: datetime
    timezone: str
    session_calendar: str      # e.g. "US", "HK" — which exchange's sessions the window maps to

    def __post_init__(self) -> None:
        for name, value in (("start", self.start), ("end", self.end)):
            if value.tzinfo is None:
                raise ValueError(
                    f"MetricWindow.{name} is naive. A naive instant cannot be mapped to an "
                    f"exchange session, and reading it as UTC is the documented "
                    f"utc-vs-et-date-boundary defect.")
        if self.end <= self.start:
            raise ValueError("MetricWindow.end must be strictly after .start (end is exclusive).")


@dataclass(frozen=True)
class MetricDefinition:
    """One reportable quantity, with every dimension section 4 requires.

    Every field is mandatory. There is no partially-specified metric: the failure this guards
    against is a number that looks complete while leaving its population implicit.
    """
    metric_id: str
    version: str                    # bump when the FORMULA changes; results do not pool across versions
    owner: str                      # responsible component, not a person
    kind: MetricKind
    cohort: Cohort
    grain: str                      # what one row of the denominator IS
    numerator: str
    denominator: str
    unit: str
    decision_authority: DecisionAuthority
    weighting: Weighting
    dependence: str                 # how units are correlated, in words; "" is rejected
    code_versions: tuple[str, ...]  # artifact/config/flag identities this result is bound to
    outcome: str | None = None      # target definition + direction + maturity; see __post_init__
    benchmark: str | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        for name in ("metric_id", "version", "owner", "grain", "numerator", "denominator",
                     "unit", "dependence"):
            if not str(getattr(self, name)).strip():
                raise ValueError(
                    f"MetricDefinition.{name} is empty. Section 4 requires every dimension to be "
                    f"declared; an unstated one is the defect this class exists to prevent.")
        if not self.code_versions:
            raise ValueError(
                "MetricDefinition.code_versions is empty. A rate that cannot name the code it "
                "was produced by cannot be compared against a later one.")

        # The kind/outcome rule. An operational metric that declares a target definition is
        # claiming to measure strategy quality it never looked at — exactly the "a deployed
        # counter can still have insufficient outcomes" confusion in section 2.
        has_outcome = bool(self.outcome and self.outcome.strip())
        if self.kind is MetricKind.OPERATIONAL and has_outcome:
            raise ValueError(
                f"{self.metric_id}: an OPERATIONAL metric must not declare an outcome "
                f"definition. Operational correctness cannot certify prediction, execution or "
                f"investment quality.")
        if self.kind is not MetricKind.OPERATIONAL and not has_outcome:
            raise ValueError(
                f"{self.metric_id}: a {self.kind.value} metric must declare its outcome "
                f"definition (target, direction convention and maturity). Without one the number "
                f"is not interpretable.")

        # Authority must agree with kind. An operational count decides nothing; a non-operational
        # metric that cannot say who decided cannot be checked against the consumer.
        if self.kind is MetricKind.OPERATIONAL:
            if self.decision_authority is not DecisionAuthority.NOT_APPLICABLE:
                raise ValueError(
                    f"{self.metric_id}: an OPERATIONAL metric must use "
                    f"DecisionAuthority.NOT_APPLICABLE.")
        elif self.decision_authority is DecisionAuthority.NOT_APPLICABLE:
            raise ValueError(
                f"{self.metric_id}: a {self.kind.value} metric must name its decision "
                f"authority. M04's withdrawn rate summed shadow and authoritative rejections.")


@dataclass(frozen=True)
class Quality:
    """Section 4's quality row, and section 6's "Missing telemetry displays unknown, never zero."

    These counts travel WITH the value because a rate computed over a cohort that was 90% missing
    is not the same number as one computed over a complete cohort, and nothing downstream can
    tell them apart once the counts are dropped.
    """
    missing: int = 0
    unresolved: int = 0
    censored: int = 0
    stale: int = 0
    excluded: int = 0
    exclusion_reasons: tuple[str, ...] = field(default_factory=tuple)
    effective_support: int | None = None   # independent units, after dependence; None = unknown

    def __post_init__(self) -> None:
        for name in ("missing", "unresolved", "censored", "stale", "excluded"):
            if getattr(self, name) < 0:
                raise ValueError(f"Quality.{name} cannot be negative.")
        if self.excluded and not self.exclusion_reasons:
            raise ValueError(
                "Quality.excluded is non-zero with no exclusion_reasons. A silent filter is "
                "indistinguishable from the bug it replaced — every exclusion needs a reason "
                "(section 3, M13).")


@dataclass(frozen=True)
class MetricValue:
    """A computed metric, or an explicit UNKNOWN. Never a zero standing in for an unknown.

    `value is None` means UNKNOWN and is a first-class result: the denominator was zero, the
    inputs did not reconcile, or the telemetry needed was never collected. `render()` prints
    "unknown" for it, so a dashboard cannot accidentally show a confident 0.0%.
    """
    definition: MetricDefinition
    window: MetricWindow
    value: float | None
    numerator_count: int | None
    denominator_count: int | None
    quality: Quality = field(default_factory=Quality)
    unknown_reason: str = ""
    computed_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.value is None:
            if not self.unknown_reason.strip():
                raise ValueError(
                    f"{self.definition.metric_id}: an unknown value must carry an "
                    f"unknown_reason. 'Unknown' without a cause is indistinguishable from a bug.")
            return
        if self.unknown_reason.strip():
            raise ValueError(
                f"{self.definition.metric_id}: a known value must not carry an unknown_reason.")
        # A zero denominator yields UNDEFINED, never 0.0 and never infinity. This is the same
        # rule section 6 states for profit factor: "if no losses, display undefined/insufficient
        # evidence rather than an impressive infinity".
        if self.denominator_count == 0:
            raise ValueError(
                f"{self.definition.metric_id}: denominator is 0, so the value is UNDEFINED. "
                f"Construct it with value=None and an unknown_reason instead of reporting 0.0.")

    @property
    def is_known(self) -> bool:
        return self.value is not None

    def render(self, places: int = 4) -> str:
        if self.value is None:
            return "unknown"
        return f"{self.value:.{places}f}"

    def render_support(self) -> str:
        """Always shown beside the value. A rate without its denominator is the shape of every
        finding withdrawn in this project so far."""
        n = self.numerator_count if self.numerator_count is not None else "?"
        d = self.denominator_count if self.denominator_count is not None else "?"
        eff = self.quality.effective_support
        tail = "" if eff is None else f", effective support {eff}"
        return f"{n}/{d} [{self.definition.weighting.value}-weighted{tail}]"
