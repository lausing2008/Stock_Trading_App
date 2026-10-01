"""M04 — the anti-chase funnel, the first metric built on the measurement contract.

WHY THIS ONE FIRST. It is the register item whose *previous* number was wrong in the exact way
the contract exists to prevent. The long-quoted figure was that 30.1% of BUY signals carry
`roc_10 >= 10` against a predicted ~17% incremental block rate — two numbers that cannot be
compared, because anti-chase sits AFTER the watchlist and conviction gates, so the population it
is exposed to is a fraction of all BUY signals. The denominator was the defect, not the gate.

The authoritative counters now exist in `paper_entry_scan_logs.skip_tally`
(`anti_chase_reached`, `anti_chase_rejected`, `anti_chase_rejected_authoritative`,
`anti_chase_rejected_shadow_only`, `anti_chase_reached_via_{de,fallback,legacy}`), so the rate
can finally be computed against the population the gate actually saw.

WHAT THIS STILL CANNOT ANSWER, stated here rather than discovered later:

  * **It is an ATTEMPT rate, not an event rate.** `skip_tally` records per-scan counts with no
    symbol identity, so the same symbol rejected on twenty consecutive scan cycles contributes
    twenty. Section 6 is explicit that "repeated checks are not unique bets". The unique-event
    variant is therefore returned as UNKNOWN with a reason — not silently as the attempt rate.
  * **It is OPERATIONAL.** It measures what share of reached entries this gate stopped. It says
    nothing about whether stopping them helped; that needs the vetoed candidates' counterfactual
    outcomes, which is the separate paired study in section 7.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .contract import (Cohort, DecisionAuthority, MetricDefinition, MetricKind, MetricValue,
                       MetricWindow, Quality, Weighting)
from .reconciliation import authority_split, gate_funnel
from .registry import REGISTRY

#: The decision sources where `_should_enter()`'s verdict is what actually controlled the entry.
#: When the decision engine is primary and reachable (`gate_source == "de"`) this function's
#: verdict was a shadow comparison, and counting it would record a block that never happened.
AUTHORITATIVE_SOURCES = ("fallback", "legacy")
SHADOW_SOURCES = ("de",)

_COMMON = dict(
    owner="market-data/paper_trading_engine",
    kind=MetricKind.OPERATIONAL,
    cohort=Cohort.OPERATIONAL,
    grain="one candidate evaluation that reached the anti-chase gate within one scan cycle",
    unit="ratio",
    decision_authority=DecisionAuthority.NOT_APPLICABLE,
    dependence=("Scan cycles repeat every 1-5 minutes over the same watchlist, so evaluations "
                "of one symbol across cycles are highly correlated. Treat as attempts, never as "
                "independent bets."),
    code_versions=("paper_trading_engine:anti_chase_authoritative_counters",),
)

AUTHORITATIVE_REJECTION_RATE = REGISTRY.register(MetricDefinition(
    metric_id="anti_chase.authoritative_rejection_rate",
    version="1",
    numerator="skip_tally['anti_chase_rejected_authoritative'] summed over rows in window",
    denominator="skip_tally['anti_chase_reached'] summed over rows in window",
    weighting=Weighting.ROW,
    notes=("Rejections that actually controlled an entry, over the population the gate saw. "
           "NOT over all BUY signals: anti-chase runs after the watchlist and conviction gates."),
    **_COMMON,
))

SHADOW_REJECTION_RATE = REGISTRY.register(MetricDefinition(
    metric_id="anti_chase.shadow_rejection_rate",
    version="1",
    numerator="skip_tally['anti_chase_rejected_shadow_only'] summed over rows in window",
    denominator="skip_tally['anti_chase_reached'] summed over rows in window",
    weighting=Weighting.ROW,
    notes=("Reported SEPARATELY and never added to the authoritative rate. A shadow rejection "
           "stopped nothing; summing the two describes a gate that blocked more than it did."),
    **_COMMON,
))

UNIQUE_EVENT_REJECTION_RATE = REGISTRY.register(MetricDefinition(
    metric_id="anti_chase.unique_event_rejection_rate",
    version="1",
    numerator="distinct (symbol, event) authoritative rejections",
    denominator="distinct (symbol, event) evaluations reaching the gate",
    weighting=Weighting.EVENT,
    notes=("NOT COMPUTABLE from skip_tally, which carries no symbol identity. Registered so the "
           "dashboard shows an explicit unknown rather than quietly displaying the attempt rate "
           "in its place."),
    **_COMMON,
))

_NO_SYMBOL_IDENTITY = (
    "paper_entry_scan_logs.skip_tally stores per-scan counts with no symbol identity, so "
    "distinct events cannot be separated from repeated evaluations of the same symbol. "
    "Collecting this needs per-candidate rows, not a tally.")


def _sum_key(tallies: Iterable[Mapping[str, Any]], key: str) -> int:
    total = 0
    for t in tallies:
        v = (t or {}).get(key, 0)
        if isinstance(v, bool) or not isinstance(v, int):
            continue          # a malformed tally contributes nothing; it is counted as missing
        total += v
    return total


def _count_malformed(tallies: Iterable[Mapping[str, Any]], keys: tuple[str, ...]) -> int:
    bad = 0
    for t in tallies:
        t = t or {}
        for k in keys:
            v = t.get(k, 0)
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                bad += 1
                break
    return bad


_COUNT_KEYS = ("anti_chase_reached", "anti_chase_rejected",
               "anti_chase_rejected_authoritative", "anti_chase_rejected_shadow_only")


def anti_chase_funnel(tallies: Iterable[Mapping[str, Any]],
                      window: MetricWindow) -> dict[str, MetricValue]:
    """Compute the M04 funnel from raw `skip_tally` dicts.

    Takes dicts rather than a session so it is testable without a database — and so the same
    function serves a live query, a replay and a fixture identically.

    RECONCILES BEFORE IT PUBLISHES. If `authoritative + shadow_only != rejected`, or if
    `rejected > reached`, the counts are internally inconsistent and every rate derived from them
    is UNKNOWN with the failing identity as its reason. Returning a number computed from counts
    known to disagree would be worse than returning nothing, because it would look fine.
    """
    tallies = list(tallies)
    reached = _sum_key(tallies, "anti_chase_reached")
    rejected = _sum_key(tallies, "anti_chase_rejected")
    authoritative = _sum_key(tallies, "anti_chase_rejected_authoritative")
    shadow = _sum_key(tallies, "anti_chase_rejected_shadow_only")
    malformed = _count_malformed(tallies, _COUNT_KEYS)
    quality = Quality(
        missing=malformed,
        excluded=malformed,
        exclusion_reasons=("malformed_tally_counts",) if malformed else (),
        # Attempts, not independent units. Stated as unknown rather than guessed: without symbol
        # identity there is no defensible effective-support number.
        effective_support=None,
    )

    def unknown(defn: MetricDefinition, reason: str) -> MetricValue:
        return MetricValue(definition=defn, window=window, value=None,
                           numerator_count=None, denominator_count=None,
                           quality=quality, unknown_reason=reason)

    # Identity 1: the authority split must close. M04's whole correction.
    split = authority_split(rejected, authoritative, shadow)
    # Identity 2: rejections cannot exceed the population that reached the gate. Expressed
    # through the shared funnel identity, with survivors as the residual.
    funnel = gate_funnel(reached=reached, passed=max(reached - rejected, 0),
                         rejected=rejected, errored=0) if rejected <= reached else \
        gate_funnel(reached=reached, passed=0, rejected=rejected, errored=0)

    results: dict[str, MetricValue] = {}
    for defn, numerator in ((AUTHORITATIVE_REJECTION_RATE, authoritative),
                            (SHADOW_REJECTION_RATE, shadow)):
        if not split.ok:
            results[defn.metric_id] = unknown(defn, f"{split.identity}: {split.detail}")
            continue
        if not funnel.ok:
            results[defn.metric_id] = unknown(defn, f"{funnel.identity}: {funnel.detail}")
            continue
        if reached == 0:
            # UNDEFINED, not 0.0. No candidate reached the gate, so it has no rate — which is a
            # different statement from "the gate rejected nothing".
            results[defn.metric_id] = unknown(
                defn, "no candidate reached the anti-chase gate in this window; a rate over an "
                      "empty population is undefined, not zero")
            continue
        results[defn.metric_id] = MetricValue(
            definition=defn, window=window, value=numerator / reached,
            numerator_count=numerator, denominator_count=reached, quality=quality)

    results[UNIQUE_EVENT_REJECTION_RATE.metric_id] = unknown(
        UNIQUE_EVENT_REJECTION_RATE, _NO_SYMBOL_IDENTITY)
    return results


def reach_by_source(tallies: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """`anti_chase_reached_via_{source}` broken out, so the denominator is always visible.

    Returned as raw counts rather than a rate on purpose: the split between authoritative and
    shadow sources is what makes the headline rate interpretable, and burying it inside a ratio
    is how it got lost the first time.
    """
    tallies = list(tallies)
    out = {}
    for source in (*AUTHORITATIVE_SOURCES, *SHADOW_SOURCES):
        out[source] = _sum_key(tallies, f"anti_chase_reached_via_{source}")
    return out
