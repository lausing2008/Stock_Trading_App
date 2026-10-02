"""The M01-M25 pending-work register as structured data.

Milestone A lists a "status register" beside the metric registry, and section 8 adds the rule
that matters most here: **"Each pending item records a concrete count/diversity/occurrence/
implementation trigger instead of 'check next month'."**

That rule is enforced below. A `Trigger` must name what would change the answer; a date alone is
rejected. This is the difference between a backlog that resolves and one that is re-read every
month and re-deferred without anyone noticing that nothing moved.

Three separate status axes (section 2), because conflating them is how "deployed" came to be
read as "working":

    engineering — does the code exist and is it running?
    research    — does the evidence support a conclusion?
    decision    — has anyone authorised anything?

A `reported_deployed` item with `collecting` research and `no_action` decision is a perfectly
coherent state, and the most common one here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Engineering(str, Enum):
    PROPOSED = "proposed"
    IMPLEMENTED = "implemented"
    TESTED = "tested"
    REPORTED_DEPLOYED = "reported_deployed"
    INDEPENDENTLY_VERIFIED = "independently_verified"
    REGRESSED = "regressed"


class Research(str, Enum):
    NOT_MEASURABLE = "not_measurable"
    COLLECTING = "collecting"
    INCONCLUSIVE = "inconclusive"
    REJECTED = "rejected"
    SUPPORTED_FOR_DEFINED_SCOPE = "supported_for_defined_scope"


class Decision(str, Enum):
    NO_ACTION = "no_action"
    APPROVAL_PENDING = "approval_pending"
    APPROVED_FOR_SCOPE = "approved_for_scope"
    ACTIVATED = "activated"
    ROLLED_BACK = "rolled_back"


class TriggerKind(str, Enum):
    """What would actually change the answer."""
    COUNT = "count"                  # n observations accumulated
    DIVERSITY = "diversity"          # independent names/clusters, not raw n
    OCCURRENCE = "occurrence"        # a specific event has to happen at all
    IMPLEMENTATION = "implementation"  # someone has to build it; nothing is waiting on data
    APPROVAL = "approval"            # a human decision, not evidence
    EXTERNAL = "external"            # the market/world has to do something


@dataclass(frozen=True)
class Trigger:
    kind: TriggerKind
    description: str
    current: str = "unknown"

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError("Trigger.description is empty.")
        # Section 8's rule, enforced. "Check next month" is not a trigger: it names a date, not a
        # condition, so a month later nobody can tell whether anything changed.
        lowered = self.description.lower()
        for vague in ("check next month", "revisit later", "check back", "see how it goes",
                      "wait and see", "tbd"):
            if vague in lowered:
                raise ValueError(
                    f"Trigger.description is a date, not a condition ({vague!r}). Name the "
                    f"count, diversity, occurrence, implementation or approval that would "
                    f"change the answer.")


@dataclass(frozen=True)
class WorkItem:
    id: str
    priority: str
    summary: str
    next_action: str
    exit_evidence: str
    owner: str
    engineering: Engineering
    research: Research
    decision: Decision
    trigger: Trigger
    blocked_by: tuple[str, ...] = field(default_factory=tuple)
    notes: str = ""

    def __post_init__(self) -> None:
        for name in ("id", "priority", "summary", "next_action", "exit_evidence", "owner"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"WorkItem.{name} is empty for {self.id!r}.")
        # An item cannot be settled research while still collecting the thing that would settle
        # it, and an ACTIVATED decision needs research that supports it. This catches the
        # specific failure of promoting on a count floor: section 8 says minimum row/name/session
        # floors "only allow review, never automatic promotion".
        if self.decision is Decision.ACTIVATED and \
                self.research is not Research.SUPPORTED_FOR_DEFINED_SCOPE:
            raise ValueError(
                f"{self.id}: decision is ACTIVATED but research is {self.research.value}. "
                f"Promotion requires supported_for_defined_scope evidence.")

    @property
    def is_data_gated(self) -> bool:
        """Waiting on the world, not on anyone's effort. No amount of code closes these."""
        return self.trigger.kind in (TriggerKind.COUNT, TriggerKind.DIVERSITY,
                                     TriggerKind.OCCURRENCE, TriggerKind.EXTERNAL)

    @property
    def is_actionable_now(self) -> bool:
        """Buildable today: someone has to do it, and nothing upstream blocks it."""
        return self.trigger.kind is TriggerKind.IMPLEMENTATION and not self.blocked_by


def _t(kind, desc, current="unknown"):
    return Trigger(kind=kind, description=desc, current=current)


#: The register. Transcribed from section 3 of the measurement framework; each entry's `trigger`
#: is the concrete condition section 8 requires, which the prose table did not always state.
REGISTER: tuple[WorkItem, ...] = (
    WorkItem("M01", "P0", "Recovery reserve->consume lifecycle reported deployed",
             "Test reservation expiry during a slow worker, crashes before/after durable entry, "
             "unknown broker submission, concurrent workers and owner-checked release.",
             "Reconciles against unique execution intent/trade; no duplicate grant use or "
             "phantom consumption.", "market-data/execution",
             Engineering.REPORTED_DEPLOYED, Research.NOT_MEASURABLE, Decision.NO_ACTION,
             _t(TriggerKind.IMPLEMENTATION, "behavioural lifecycle tests written and passing")),
    WorkItem("M02", "decision", "Old portfolio 2 and 5 recovery markers reportedly remain",
             "Refresh read-only trade/order evidence; clear ONLY portfolio 2 by "
             "compare-and-delete if no entry or pending/unknown order exists.",
             "Recorded old value/TTL and action, with explicit operator approval.",
             "operator + risk owner",
             Engineering.REPORTED_DEPLOYED, Research.NOT_MEASURABLE, Decision.APPROVAL_PENDING,
             _t(TriggerKind.APPROVAL, "operator approves clearing portfolio 2; markers expire "
                                      "~2026-10-06, after which the decision is moot"),
             notes="Portfolio 5 stays untouched pending recovery-strategy review (seven losing "
                   "recovery trades, -$452.92). No deletion performed."),
    WorkItem("M03", "P0", "Conviction bound to sig.ts; gate_passed producer reported deployed",
             "Verify symbol, market, horizon, input/policy version, TTL and missing/stale "
             "behaviour in every consumer.",
             "Missing evidence recomputes or abstains; never a positive decision by default.",
             "signal/decision/market-data",
             Engineering.REPORTED_DEPLOYED, Research.NOT_MEASURABLE, Decision.NO_ACTION,
             _t(TriggerKind.IMPLEMENTATION, "every consumer audited for stale/missing handling")),
    WorkItem("M04", "P1", "Authoritative anti-chase counters reported deployed",
             "Verify reached/rejected/survived by authoritative source; keep shadow separate.",
             "Forward results accumulated before any effectiveness claim.",
             "market-data analytics",
             Engineering.REPORTED_DEPLOYED, Research.COLLECTING, Decision.NO_ACTION,
             _t(TriggerKind.COUNT, "forward authoritative rejections accumulate; rate is "
                                   "computable now via metrics.anti_chase"),
             notes="Rate is OPERATIONAL only. Effectiveness needs vetoed candidates' "
                   "counterfactual outcomes - a separate paired study."),
    WorkItem("M05", "P1", "Dark-pool relative threshold rarely binds",
             "Point-in-time baseline replay or forward baseline capture; compare absolute-only, "
             "relative-only and combined without changing production thresholds.",
             "Incremental unique-event selection plus held-out economic/load comparison.",
             "news/UW + research",
             Engineering.REPORTED_DEPLOYED, Research.INCONCLUSIVE, Decision.NO_ACTION,
             _t(TriggerKind.IMPLEMENTATION, "point-in-time baseline capture exists"),
             notes="Measured 2026-10-01: 15/9,707 evaluable prints rejected; for 55 of 57 "
                   "symbols 5x median sits below the $1M floor so the bar cannot bind. A bar "
                   "that never binds was not shown not to work."),
    WorkItem("M06", "hold", "Confidence feedback: do not promote",
             "Evaluate consumer-matched market/horizon/direction slices on the primary outcome.",
             "Calibration separated from strength score, on supported slices.", "signal/ML",
             Engineering.IMPLEMENTED, Research.INCONCLUSIVE, Decision.NO_ACTION,
             _t(TriggerKind.COUNT, "sufficient rows per _CONF_BAND within a 180-day window, "
                                   "sliced as production slices"),
             notes="The pooled 'flat 36.5-43.9%' reading was an artifact; sliced there are 59 "
                   "supported slices with wide dispersion."),
    WorkItem("M07", "hold", "Options-flow row gate met; anti-predictive claim withdrawn",
             "Match the production 10-session outcome; equal-weight independent symbol/session "
             "events and resolve mixed-direction groups explicitly.",
             "Benchmark-relative and actual options outcomes reported separately.",
             "UW/research",
             Engineering.REPORTED_DEPLOYED, Research.INCONCLUSIVE, Decision.NO_ACTION,
             _t(TriggerKind.COUNT, "independent symbol/session events at the 10-session horizon"),
             notes="Equal symbol/date weighting REVERSED the 5d gap. No automatic inversion."),
    WorkItem("M08", "collecting", "Prebreakout concentrated in few names",
             "Revisit on symbol diversity with leave-one-name-out sensitivity.",
             "Mature outcomes across a diverse name set.", "research",
             Engineering.REPORTED_DEPLOYED, Research.COLLECTING, Decision.NO_ACTION,
             _t(TriggerKind.DIVERSITY, ">=20 distinct names with none above ~15% of fires",
                "8 names, AI alone 42% of fires"),
             notes="Diversity floors are screening rules, not proof."),
    WorkItem("M09", "hold", "GEX control small; signed result differs from raw-return claim",
             "Keep gate off; compare baseline versus gated portfolios with thesis-signed "
             "outcomes.", "Baseline-vs-gated portfolio comparison including avoided trades.",
             "UW/research",
             Engineering.IMPLEMENTED, Research.INCONCLUSIVE, Decision.NO_ACTION,
             _t(TriggerKind.COUNT, "~30 resolved uncorroborated control events = REVIEW "
                                   "trigger, explicitly not a promotion threshold", "13"),
             notes="Thesis-signed: corroborated -1.87% vs uncorroborated +2.28%. My first "
                   "comparison pooled opposite theses and had the sign backwards."),
    WorkItem("M10", "collecting", "Squeeze ignition: 6 events, mean about -5.3%",
             "Retain configuration; collect independent events and assess concentration.",
             "Independent events with mature outcomes.", "research",
             Engineering.REPORTED_DEPLOYED, Research.COLLECTING, Decision.NO_ACTION,
             _t(TriggerKind.COUNT, "independent ignition events beyond 6, QUBT counted once",
                "6 events, QUBT twice"),
             notes="Do not loosen thresholds just to create observations."),
    WorkItem("M11", "collecting", "Short-squeeze corroboration: 16 overall, 5 in September",
             "Count the actual short_squeeze family member, not 481 mostly gamma-unwind events.",
             "Target-specific diversity and maturity.", "UW/research",
             Engineering.REPORTED_DEPLOYED, Research.COLLECTING, Decision.NO_ACTION,
             _t(TriggerKind.COUNT, "short_squeeze family fires specifically", "16 overall, 5 Sept")),
    WorkItem("M12", "collecting", "Regime robustness: one bear sample",
             "Separate regime coverage from average performance.",
             "Conclusions restricted to observed regimes.", "research/risk",
             Engineering.IMPLEMENTED, Research.NOT_MEASURABLE, Decision.NO_ACTION,
             _t(TriggerKind.EXTERNAL, "another bear regime occurs", "1 bear sample"),
             notes="Gates live automation. Cannot be satisfied by building anything."),
    WorkItem("M13", "P1", "ML n_outcome_rows attrition unresolved",
             "Record rows at join, feature availability, label maturity, dedup, purge, "
             "calibration and promotion stages.", "Every exclusion has a reason and reconciles.",
             "ML", Engineering.IMPLEMENTED, Research.NOT_MEASURABLE, Decision.NO_ACTION,
             _t(TriggerKind.IMPLEMENTATION, "an instrumented training report is produced from "
                                            "a real retrain, AND purge/calibration/promotion "
                                            "exclusions get their own ledgers",
                "ledger built and wired 2026-10-01; NOT deployed"),
             notes="AttritionLedger records rows at loaded / min_sample / shared_features / "
                   "dedup / min_after_dedup, each exclusion with a REASON, and reconciles: "
                   "rows_in == rows_out + dropped, with an `unexplained` bucket rather than a "
                   "silent balance. Emitted even when NOTHING survived - the 490-of-548 case "
                   "the old single count could not explain. Reproduces the historical 43 -> 6 "
                   "collapse and names the stage and reason. Read it after a retrain; a "
                   "`reconciles: false` line is a defect in the instrumentation, not the data. "
                   "An UNEXPLAINED bucket now DEGRADES reconciliation - naming a gap closes the "
                   "arithmetic but must not pass for closing the gap. NOT FULLY CLOSED: the "
                   "tooling is tested, fleet coverage and historical attrition are not "
                   "explained. SCOPE is narrower than this item's wording - only the "
                   "outcome-augmentation path is instrumented; purge, calibration and promotion "
                   "exclusions are NOT measured and need their own ledgers."),
    WorkItem("M14", "P1", "OOS-suppression rollout not independently settled",
             "Inventory active artifacts/consumers; verify invalid models cannot publish or be "
             "unsuppressed by resweeps.", "Versioned suppression reasons and coverage effects.",
             "ML/operations", Engineering.TESTED, Research.NOT_MEASURABLE, Decision.NO_ACTION,
             _t(TriggerKind.IMPLEMENTATION, "run the READ-ONLY inventory against the "
                                            "production model directory, recording artifact "
                                            "hashes, rule version, unknown-validity counts and "
                                            "read errors - no retrain required",
                "inventory built and tested 2026-10-01; NOT yet run against production"),
             notes="suppression_inventory.build_inventory() calls the REAL "
                   "_compute_oos_suppression (injected, never reimplemented) and reports the "
                   "populations separately: suppressed, invalid (evaluation_valid False), "
                   "UNKNOWN validity (None - pre-R02 artifacts, a coverage gap and not a clean "
                   "bill of health), invalid-but-unsuppressed, and - the safety net - any model "
                   "a resweep would UNSUPPRESS despite being invalid, which is the R02 failure "
                   "caught as data. Consumer chain verified: predict_latest returns a flat 0.5 "
                   "and the ensemble zeroes the weight, so signal-engine's 40% shrink is a "
                   "SECOND penalty, not a suppressed model leaking through at 60%."),
    WorkItem("M15", "collecting", "Portfolio concentration sample limited",
             "Test limits now with synthetic positions; measure realised concentration as "
             "exposure accumulates.", "Limits demonstrably bind under synthetic exposure.",
             "portfolio/risk", Engineering.TESTED, Research.INCONCLUSIVE,
             Decision.NO_ACTION,
             _t(TriggerKind.IMPLEMENTATION, "the five measured gaps are addressed or "
                                            "explicitly accepted as design decisions"),
             notes="MEASURED 2026-10-01 with synthetic positions against the REAL entry path "
                   "(docs/audits/2026-10-01-portfolio-concentration-synthetic-tests.md). Caps "
                   "bind on open exposure, and exits are NOT gated by entry caps - the result "
                   "that most needed checking. Five gaps found: concurrent entries in one scan "
                   "opened 20.02% against a 15% sector cap; pending/unfilled orders are "
                   "invisible; a missing mark values a position at entry price (50% "
                   "understatement); currency is summed unconverted (latent - portfolios are "
                   "single-market); no ordered-vs-filled or option-assignment representation. "
                   "FINDING 1 WAS A DEFECT AND IS FIXED (M15-DEFECT-CONCURRENT-CAP, "
                   "docs/incidents/concentration-cap-stale-snapshot.md): atomic exposure "
                   "reservation under a portfolio row lock, affecting organic entries AND "
                   "conditional orders; witness preserved; verified on PostgreSQL under "
                   "contention (8 threads x 4%% vs a 15%% cap -> exactly 3 granted). The other "
                   "four stay open and must share the eventual execution model rather than a "
                   "competing one. MIXED-WRITER EVIDENCE OBTAINED: the organic entry and the "
                   "real conditional_orders._execute_buy were raced through their actual paths "
                   "- organic opened, conditional refused with sector_cap, reservations "
                   "reconcile. CLOSURE IS SCOPED TO THE PAPER-ENTRY PATHS: an internal entry "
                   "is atomic across a crash (rollback leaves no trade and no stranded "
                   "capacity), and in-flight capacity is protected by PostgreSQL's ROW LOCK "
                   "rather than by `committing` visibility - measured, a second worker reads "
                   "the row as `reserved`. OPEN: the BROKER boundary. _place_broker_entry runs "
                   "inside _open_paper_trade, which never commits, so a crash after broker "
                   "acceptance leaves a real order with no local record and capacity that "
                   "expires - pre-existing, belongs with M25. STILL OPEN: fresh marks (unvaluable fails "
                   "closed but is unreachable from the live path; the FALLBACK is what occurs "
                   "and is permitted - now measured in shadow), pending broker exposure, "
                   "timestamped FX before any mixed-currency book, and order/assignment "
                   "representation as a broker/options prerequisite."),
    WorkItem("M16", "occurrence-gated", "Broker fill re-poll needs multi-cycle observation",
             "Sandbox partial/pending fill across cycles, restart and reconciliation.",
             "Accepted order distinguished from fill; no duplicate trades/cash.",
             "broker adapter", Engineering.REPORTED_DEPLOYED, Research.NOT_MEASURABLE,
             Decision.NO_ACTION,
             _t(TriggerKind.OCCURRENCE, "a broker-linked position open across more than one "
                                        "scheduler cycle")),
    WorkItem("M17", "deferred", "Eight-cell ablation awaits simpler comparison",
             "Finish one two-arm study first.", "A completed two-arm result.", "research",
             Engineering.PROPOSED, Research.NOT_MEASURABLE, Decision.NO_ACTION,
             _t(TriggerKind.IMPLEMENTATION, "one two-arm study completed; margin features exist"),
             notes="Blockers are the simpler two-arm result and the absent margin features. An "
                   "outbox is not among them."),
    WorkItem("M18", "deferred", "Watchlist/style rerun remains open",
             "Review intentional exclusions versus stale membership; shadow an alternative.",
             "Incremental outcomes, not hypothetical conversion of rejected checks.",
             "ranking/research", Engineering.PROPOSED, Research.NOT_MEASURABLE,
             Decision.NO_ACTION,
             _t(TriggerKind.IMPLEMENTATION, "mandate-consistent alternative shadowed")),
    WorkItem("M19", "P0", "MU earnings facts arrived fast; result pipeline blocked in snapshot",
             "Verify phase-aware preview/results/guidance delivery; investigate structured NULL "
             "EPS mapping against the provider response.",
             "Provider response -> selected event -> attempted update -> committed row, "
             "connected in one trace.", "event/news/market-data",
             Engineering.REPORTED_DEPLOYED, Research.NOT_MEASURABLE, Decision.NO_ACTION,
             _t(TriggerKind.OCCURRENCE, "the next instrumented sync runs, and a real earnings "
                                        "release exercises phase delivery end to end"),
             notes="MU-02 v1 deployed, v2 committed. Live delivery UNVERIFIED: container health "
                   "proves deployment, not notification."),
    WorkItem("M20", "P1", "Outbox, technical-alert retries and immutable event IDs pending",
             "Implement reliable delivery lifecycle, preferences, lease ownership, expiry and "
             "meaningful retry content.",
             "Provider acceptance measured separately from delivery.",
             "market-data/notifications", Engineering.REPORTED_DEPLOYED, Research.NOT_MEASURABLE,
             Decision.NO_ACTION,
             _t(TriggerKind.IMPLEMENTATION, "a scheduler job calls the producer and worker, "
                                            "and one alert family is cut over with the "
                                            "rollout flag moved off `off`",
                "schema, state machine and the earnings producer/worker are DEPLOYED (2026-10-01); "
                "no job calls them and the flag is off"),
             notes="DELIVERY RELIABILITY ONLY. An earlier note here claimed this also closes "
                   "earnings_phase.KNOWN_UNRESOLVED_RISK (the in-window retrospective article). "
                   "That was wrong: an outbox delivers whatever event the classifier SELECTED, "
                   "reliably, including a wrong selection. Event identification is M19/M23 and "
                   "needs its own acceptance criteria. Does not promise exactly-once delivery. "
                   "PARTIAL 2026-10-01. DONE: schema, state machine, earnings-phase producer "
                   "and worker, verified on SQLite and on PostgreSQL as a REQUIRED CI check. "
                   "REMAINING, explicitly: (1) scheduler wiring - no job calls the producer or "
                   "worker; (2) the cutover runbook and a real cutover, including the rollout "
                   "flag move off `off`; (3) migration of the other alert families; (4) "
                   "provider-callback ingestion for delivery_status. Production activation and "
                   "any historical recovery send remain separate decisions."),
    WorkItem("M21", "approval pending", "Outage recount: 65 actionable, 18 consumed-and-current",
             "Refresh signal state and recipients; prepare the exact digest for review.",
             "Operator approves one digest with a unique incident key.",
             "notifications/operator", Engineering.IMPLEMENTED, Research.NOT_MEASURABLE,
             Decision.APPROVAL_PENDING,
             _t(TriggerKind.APPROVAL, "operator approves the digest content and send"),
             notes="All 18 are SELL. Never reset last_signal: the transition logic recognises "
                   "None->BUY but not None->SELL, so clearing would silently do nothing."),
    WorkItem("M22", "design", "Jev engine/flag and A/B plan documented",
             "Validate provider contract, costs, classification and failure behaviour; begin "
             "shadow only under reviewed implementation.",
             "A frozen Jev policy evaluated in paired shadow against an unchanged baseline.",
             "Jev adapter/admin/research", Engineering.IMPLEMENTED, Research.NOT_MEASURABLE,
             Decision.NO_ACTION,
             _t(TriggerKind.IMPLEMENTATION, "JEV-02 schema/queue/client and JEV-03 policy "
                                            "evaluator built; durable decision, assignment and "
                                            "outcome records available"),
             notes="JEV-01 (admin flag, default OFF) is implemented and makes no provider "
                   "request. OPENROUTER_API_KEY is deliberately not configured, so the client "
                   "can be built and tested against recorded responses but not exercised live. "
                   "NOT blocked on M20: shadow/paper experiments need durable decisions, "
                   "assignments and outcomes, not email. The outbox becomes a prerequisite only "
                   "if an arm measures notification availability or delivery-dependent "
                   "behaviour."),
    WorkItem("M23", "open design", "Event-linked resolution of material negative news",
             "Link resolutions to originating events; unrelated positive news must not clear "
             "unresolved risk.", "A newer positive article cannot resolve an unrelated lawsuit.",
             "news/ML", Engineering.PROPOSED, Research.NOT_MEASURABLE, Decision.NO_ACTION,
             _t(TriggerKind.IMPLEMENTATION, "event-linked risk ledger exists"),
             notes="storage.py::_mark_hot currently documents that an unrelated newer positive "
                   "story can clear a negative flag."),
    WorkItem("M24", "follow-up", "Per-job migration readiness improved, not universal",
             "Inventory prerequisites for outbox, intent uniqueness, mark evidence and metrics "
             "workers.", "Missing/unknown prerequisites block dependent work precisely.",
             "platform", Engineering.TESTED, Research.NOT_MEASURABLE, Decision.NO_ACTION,
             _t(TriggerKind.IMPLEMENTATION, "the matrix is run READ-ONLY against the production "
                                            "schema, so real capability coverage is read "
                                            "rather than inferred",
                "matrix built and tested 2026-10-01; exposed on /health; NOT yet run in prod"),
             notes="Capability matrix over outbox enqueue/drain, broker submission, exposure "
                   "reservation and submission reconciliation. A LEDGER ENTRY IS NOT A "
                   "CAPABILITY: a table can exist with its unique constraint missing - every "
                   "insert succeeds, the migration reads as applied, idempotency is gone. "
                   "Three states; `unknown` BLOCKS a risk-increasing action and does NOT block "
                   "a risk-reducing one, so a degraded entry path can never trap a position or "
                   "blind reconciliation. /health gains a `capabilities` block while `status` "
                   "stays ok - a process with an unmet prerequisite is healthy and DEGRADED. "
                   "Keep liveness distinct from capability readiness."),
    WorkItem("M25", "release prerequisite", "Deferred broker/order and options-accounting items",
             "Reconcile A01-A03 lifecycle scope and research-vs-executable options marks against "
             "current code.", "Reconciled before any live-capital proposal.",
             "execution/options/risk", Engineering.IMPLEMENTED, Research.NOT_MEASURABLE,
             Decision.NO_ACTION,
             _t(TriggerKind.IMPLEMENTATION, "A01-A03 and options marks reconciled against "
                                            "current code"),
             notes="BROKER COMMIT BOUNDARY: IMPLEMENTED BEHIND A DISABLED FLAG 2026-10-01. "
                   "FLAG OFF PRESERVES THE EXISTING CRASH GAP - deploying alone does NOT "
                   "close this. Awaiting sandbox lifecycle verification and activation; "
                   "`strictly safer` is a claim that should FOLLOW that evidence. "
                   "_place_broker_entry ran inside _open_paper_trade, which never commits, so a "
                   "real order was submitted from an uncommitted transaction and a crash after "
                   "acceptance left an accepted order with no local row. broker_submission.py "
                   "records durable intent, commits, dispatches, and reconciles unknowns - "
                   "never auto-retrying one, since a blind retry is how a duplicate REAL order "
                   "happens. CORRECTED: a call returning without an order id is UNKNOWN, not "
                   "failed - the historical path swallows every exception from place_order, so "
                   "a swallowed timeout after acceptance and a clean rejection are "
                   "indistinguishable, and calling it failed would license a REPLACEMENT order. "
                   "Only explicit evidence sets `rejected`. Stable client order id and the "
                   "submission path are committed with the intent, so a flag change or restart "
                   "cannot route one intent through both paths. The "
                   "flag moves WHERE an order is placed relative to the commit and is a "
                   "person's decision. STILL OPEN: A01-A03 reconciliation, research-vs-"
                   "executable options marks, pending-order exposure, FX, assignment. "
                   "Historical fix claims do not close this gate."),
)

BY_ID = {w.id: w for w in REGISTER}


def blocked_items() -> tuple[WorkItem, ...]:
    return tuple(w for w in REGISTER if w.blocked_by)


def data_gated() -> tuple[WorkItem, ...]:
    """Waiting on the world. Proposing engineering for these is the mistake the
    `project_pending_data_verification` memory exists to prevent."""
    return tuple(w for w in REGISTER if w.is_data_gated)


def actionable_now() -> tuple[WorkItem, ...]:
    return tuple(w for w in REGISTER if w.is_actionable_now)


def awaiting_approval() -> tuple[WorkItem, ...]:
    return tuple(w for w in REGISTER if w.decision is Decision.APPROVAL_PENDING)


def validate_register() -> None:
    """Every `blocked_by` must name a real item, and nothing may block itself."""
    for w in REGISTER:
        for dep in w.blocked_by:
            if dep not in BY_ID:
                raise ValueError(f"{w.id} is blocked_by unknown item {dep!r}")
            if dep == w.id:
                raise ValueError(f"{w.id} blocks itself")
