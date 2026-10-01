"""Section 9's reconciliation acceptance cases, as executable identities.

These are the checks that decide whether a cohort's counts are allowed to produce a rate at all.
The point is ordering: **reconcile first, publish second.** An identity that does not hold means
the denominator is not what it claims to be, and the correct output is then UNKNOWN with a
reason — not a number computed from counts known to be inconsistent.

Each function returns a `Reconciliation`, never a bare bool, because "these counts disagree" is
only useful alongside *by how much* and *which identity broke*.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Reconciliation:
    ok: bool
    identity: str
    detail: str

    def require(self) -> None:
        """Raise if the identity failed. For call sites that genuinely cannot proceed."""
        if not self.ok:
            raise ValueError(f"reconciliation failed [{self.identity}]: {self.detail}")


def gate_funnel(reached: int, passed: int, rejected: int, errored: int = 0) -> Reconciliation:
    """`gate reached = passed + rejected + explicit error/abstention`.

    The residual is the thing to notice. If reached exceeds the three outcomes, some candidates
    entered the gate and left without being recorded as anything — which is precisely how
    anti-chase became unmeasurable in the first place: it returned early inside `_should_enter()`
    without writing a tally key, so it never appeared among the ten recorded skip reasons at all.

    An abstention is an OUTCOME and belongs in the identity. Dropping abstentions silently
    shrinks the denominator and inflates every rate computed from it.
    """
    for name, v in (("reached", reached), ("passed", passed), ("rejected", rejected),
                    ("errored", errored)):
        if v < 0:
            return Reconciliation(False, "gate_funnel", f"{name} is negative ({v})")
    total = passed + rejected + errored
    if total != reached:
        return Reconciliation(
            False, "gate_funnel",
            f"reached={reached} but passed+rejected+errored={total} "
            f"(residual {reached - total}); some candidates left the gate unrecorded")
    return Reconciliation(True, "gate_funnel", f"reached={reached} accounted for exactly")


def authority_split(rejected_total: int, authoritative: int, shadow_only: int) -> Reconciliation:
    """`rejected = authoritative + shadow_only`, and the two are never summed into one rate.

    M04. A shadow rejection did not stop anything; an authoritative one did. Adding them produces
    a "block rate" for a gate that blocked less than it claims, and the error is invisible
    downstream because both are honest counts of real events.
    """
    for name, v in (("rejected_total", rejected_total), ("authoritative", authoritative),
                    ("shadow_only", shadow_only)):
        if v < 0:
            return Reconciliation(False, "authority_split", f"{name} is negative ({v})")
    if authoritative + shadow_only != rejected_total:
        return Reconciliation(
            False, "authority_split",
            f"rejected_total={rejected_total} but authoritative({authoritative}) + "
            f"shadow_only({shadow_only})={authoritative + shadow_only}")
    return Reconciliation(True, "authority_split",
                          f"{authoritative} authoritative, {shadow_only} shadow-only, kept apart")


def cohort_total(resolved: int, unresolved: int, declared_total: int) -> Reconciliation:
    """`cohort totals include unresolved/missing outcomes`.

    Dropping unresolved rows from the denominator is survivorship by another name: the rows that
    have resolved are not a random sample of the rows that exist, because fast resolutions and
    slow ones differ systematically.
    """
    if resolved < 0 or unresolved < 0:
        return Reconciliation(False, "cohort_total", "counts cannot be negative")
    if resolved + unresolved != declared_total:
        return Reconciliation(
            False, "cohort_total",
            f"declared_total={declared_total} but resolved({resolved}) + "
            f"unresolved({unresolved})={resolved + unresolved}; unresolved rows must stay in "
            f"the denominator and be reported separately")
    return Reconciliation(True, "cohort_total",
                          f"{resolved} resolved, {unresolved} unresolved, both in denominator")


def delivery_attempts(unique_notifications: int, attempts: int,
                      accepted: int) -> Reconciliation:
    """`delivery attempts can exceed unique notifications without duplicating them`.

    Retries are expected and must not inflate a notification count — but acceptance can never
    exceed the unique notifications that existed, and section 6 is explicit that acceptance is
    not inbox arrival.
    """
    if min(unique_notifications, attempts, accepted) < 0:
        return Reconciliation(False, "delivery_attempts", "counts cannot be negative")
    if attempts < unique_notifications:
        return Reconciliation(
            False, "delivery_attempts",
            f"attempts({attempts}) < unique notifications({unique_notifications}); every "
            f"notification needs at least one attempt")
    if accepted > unique_notifications:
        return Reconciliation(
            False, "delivery_attempts",
            f"accepted({accepted}) > unique notifications({unique_notifications}); retries must "
            f"not be counted as additional deliveries")
    return Reconciliation(True, "delivery_attempts",
                          f"{attempts} attempts over {unique_notifications} notifications, "
                          f"{accepted} accepted (acceptance is not arrival)")


def order_lifecycle(submitted_qty: float, filled: float, working: float,
                    cancelled_or_rejected: float, tolerance: float = 1e-9) -> Reconciliation:
    """`submitted quantity = filled + working + cancelled/rejected remainder`.

    An unknown broker response is not zero. If a submission's disposition is genuinely unknown it
    belongs in `working` until reconciled, never silently dropped — dropping it is how a phantom
    position or a duplicate order becomes invisible.
    """
    for name, v in (("submitted", submitted_qty), ("filled", filled), ("working", working),
                    ("cancelled_or_rejected", cancelled_or_rejected)):
        if v < 0:
            return Reconciliation(False, "order_lifecycle", f"{name} is negative ({v})")
    total = filled + working + cancelled_or_rejected
    if abs(total - submitted_qty) > tolerance:
        return Reconciliation(
            False, "order_lifecycle",
            f"submitted={submitted_qty} but filled+working+cancelled/rejected={total} "
            f"(residual {submitted_qty - total})")
    return Reconciliation(True, "order_lifecycle", f"submitted {submitted_qty} fully accounted")


def equity(starting: float, ending: float, cash: float, positions_value: float,
           tolerance: float = 0.01) -> Reconciliation:
    """`equity reconciles cash, positions and costs`.

    Section 6: a portfolio return includes marked OPEN positions, losses, fees and cash. A figure
    summed from closed winners is not a portfolio return, and the difference is exactly the part
    that tends to be unflattering.
    """
    composed = cash + positions_value
    if abs(composed - ending) > tolerance:
        return Reconciliation(
            False, "equity",
            f"ending equity={ending} but cash({cash}) + positions({positions_value})="
            f"{composed} (residual {ending - composed})")
    if starting <= 0:
        return Reconciliation(
            False, "equity",
            f"starting equity={starting}; a return has no denominator without positive "
            f"starting capital")
    return Reconciliation(True, "equity", f"{ending} = cash {cash} + positions {positions_value}")
