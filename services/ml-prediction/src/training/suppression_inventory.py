"""M14 — what the suppression flag currently covers, and what a resweep would change.

WHY AN INVENTORY RATHER THAN A COUNT. `oos_suppressed` is written once at training time and
baked into each .joblib bundle. Before AUD-ML3-STALESUPPRESSION there was no re-evaluation path
at all, and the fleet quietly diverged from the rule: 11 artifacts had `test_auc` of EXACTLY 0.0
or 1.0 while unsuppressed, and 45 were over 30 days old and unsuppressed, the oldest 82 days.
`test_auc == 0.0` is a PERFECTLY INVERTED ranking — every positive scored below every negative —
so those models were contributing actively harmful signal at live fusion weight.

A single "n suppressed" figure cannot distinguish a fleet that is correctly mostly-unsuppressed
from one that has drifted away from its own rule. This reports the populations separately.

THE INVARIANT THIS EXISTS TO CHECK. A resweep re-applies TODAY'S rule to YESTERDAY'S stored
numbers. It must never UNSUPPRESS a model whose own recorded evaluation is invalid — that is
precisely what R02 found the sweep about to do on its first scheduled run, silently undoing the
leakage suppression that had just been added.

`evaluation_valid is None` is UNKNOWN, not valid: artifacts written before R02 carry no such
key. They are counted separately rather than folded into either side, because "we never measured
this" and "we measured it and it was fine" are different facts — and only the second is evidence.

PURE. Takes already-loaded bundle metadata so it can be exercised without a model directory.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

#: THREE QUESTIONS, NOT ONE. CM_long is why this distinction exists: `evaluation_valid: true`
#: with `cv_auc_mean` and `overfit_gap` both absent and twelve test rows was being read as a
#: model that had passed, when it is a model nothing could measure.
#:
#:   VALIDITY   — was the evaluation free of known leakage? (`evaluation_valid`)
#:   SUFFICIENCY— were enough independent observations and required diagnostics available?
#:   ELIGIBILITY— has it demonstrated acceptable performance against a baseline?
#:
#: `evaluation_valid: true` answers ONLY the first. "Not suppressed" is the absence of a reason
#: to reject, which is not the presence of a reason to trust — and the gap between those two is
#: where a model serves on no evidence at all.
#:
#: Minimum independent observations before a held-out score means anything. Chosen as a floor
#: for VISIBILITY, not as a promotion threshold: it marks which models have too little evidence
#: to judge, and judging them is a separate decision.
MIN_TEST_ROWS_FOR_EVIDENCE = 50

#: Diagnostics without which the primary and symmetric quality gates cannot fire at all.
REQUIRED_DIAGNOSTICS = ("cv_auc_mean", "overfit_gap")


def evidence_sufficiency(metrics) -> tuple[str, list[str]]:
    """Could this model have been judged at all? Returns `(state, missing)`.

    `sufficient` / `insufficient` / `unknown`. Deliberately separate from the suppression rule:
    a model can be unsuppressed (no reason to reject) and still have insufficient evidence (no
    basis to accept). Reporting only the first is what let CM_long look proven.
    """
    missing = [d for d in REQUIRED_DIAGNOSTICS if metrics.get(d) is None]
    n_test = metrics.get("n_test")
    if n_test is None:
        missing = missing + ["n_test"]
        return "unknown", missing
    if missing:
        return "insufficient", missing
    if isinstance(n_test, (int, float)) and n_test < MIN_TEST_ROWS_FOR_EVIDENCE:
        return "insufficient", [f"n_test={n_test} < {MIN_TEST_ROWS_FOR_EVIDENCE}"]
    return "sufficient", []


#: Bumped when the suppression RULE changes. A reason recorded under an older version was
#: produced by a different rule and is not comparable with a newer one — the same discipline the
#: metric registry applies to formulas.
SUPPRESSION_RULE_VERSION = "r02-2026-09-28"


@dataclass
class Inventory:
    total: int = 0
    suppressed: int = 0
    unsuppressed: int = 0
    #: `evaluation_valid is False` — the model's own evaluation is known not to be out of sample.
    invalid: int = 0
    #: `evaluation_valid is None` — never measured. A coverage gap, not a clean bill of health.
    unknown_validity: int = 0
    #: DEFECT if non-empty: an invalid model that is not currently suppressed.
    invalid_unsuppressed: list[str] = field(default_factory=list)
    #: DEFECT if non-empty: a resweep that would UNSUPPRESS an invalid model.
    resweep_would_unsuppress_invalid: list[str] = field(default_factory=list)
    #: Unsuppressed models split by whether the evidence could support the judgement at all.
    #: `serving_on_insufficient_evidence` is NOT a claim that a model is inaccurate — it is the
    #: statement that nothing established it is accurate.
    serving_on_insufficient_evidence: list[str] = field(default_factory=list)
    insufficiency_reasons: dict[str, list[str]] = field(default_factory=dict)
    #: FOUND IN PRODUCTION 2026-10-02. Unsuppressed models for which EVERY quality condition was
    #: unevaluable — `cv_auc_mean` and `overfit_gap` both absent. They are serving not because
    #: they passed the bar but because nothing could measure them against it, which is the
    #: opposite of what an unsuppressed flag is taken to mean.
    unsuppressed_without_quality_evidence: list[str] = field(default_factory=list)
    would_suppress: list[str] = field(default_factory=list)
    would_unsuppress: list[str] = field(default_factory=list)
    by_reason: dict[str, int] = field(default_factory=dict)
    failed_to_read: list[str] = field(default_factory=list)
    rule_version: str = SUPPRESSION_RULE_VERSION

    @property
    def holds(self) -> bool:
        """The two invariants that must never be violated."""
        return not self.invalid_unsuppressed and not self.resweep_would_unsuppress_invalid

    @property
    def coverage(self) -> float | None:
        """Share of artifacts whose validity is actually KNOWN. None when there are none —
        an empty fleet has no coverage, which differs from a fleet with zero coverage.

        UNREADABLE ARTIFACTS COUNT AS NOT-KNOWN. The first version subtracted only
        `unknown_validity`, so a bundle that could not be opened at all was silently counted in
        the KNOWN numerator — a one-unreadable-artifact probe reported coverage 1.0. An artifact
        whose validity could not be read is the clearest possible case of validity not being
        known, and inflating a coverage figure with it is the exact shape of false assurance
        this inventory exists to prevent.
        """
        if self.total == 0:
            return None
        known = self.total - self.unknown_validity - len(self.failed_to_read)
        return max(known, 0) / self.total

    @property
    def complete(self) -> bool:
        """Did every artifact actually get read? Separate from `holds`: the invariants can hold
        across the artifacts that WERE readable while the inventory itself is incomplete, and
        conflating the two would let an unreadable fleet report as sound."""
        return not self.failed_to_read

    def to_dict(self) -> dict:
        return {
            "rule_version": self.rule_version,
            "total": self.total, "suppressed": self.suppressed,
            "unsuppressed": self.unsuppressed,
            "invalid": self.invalid, "unknown_validity": self.unknown_validity,
            "validity_coverage": self.coverage,
            "invalid_unsuppressed": self.invalid_unsuppressed,
            "unsuppressed_without_quality_evidence": self.unsuppressed_without_quality_evidence,
            "serving_on_insufficient_evidence": len(self.serving_on_insufficient_evidence),
            "insufficiency_reasons_sample": dict(
                list(self.insufficiency_reasons.items())[:5]),
            "resweep_would_unsuppress_invalid": self.resweep_would_unsuppress_invalid,
            "would_suppress": len(self.would_suppress),
            "would_unsuppress": len(self.would_unsuppress),
            "by_reason": dict(self.by_reason),
            "failed_to_read": self.failed_to_read,
            "invariants_hold": self.holds,
            # Reported separately on purpose — see `complete`.
            "inventory_complete": self.complete,
            "unreadable": len(self.failed_to_read),
        }


def build_inventory(bundles: Iterable[tuple[str, Mapping | None]], *, decide) -> Inventory:
    """`bundles` is (name, bundle) pairs; `decide` is the real `_compute_oos_suppression`.

    The decision function is INJECTED rather than imported so this reports what the production
    rule actually says — not what a second copy of it here would say. A reimplemented rule is
    how an inventory ends up disagreeing with the thing it inventories.
    """
    inv = Inventory()
    for name, bundle in bundles:
        inv.total += 1
        if bundle is None:
            inv.failed_to_read.append(name)
            continue
        metrics = bundle.get("metrics") or {}
        stored = bool(bundle.get("oos_suppressed", False))
        validity = metrics.get("evaluation_valid")

        if stored:
            inv.suppressed += 1
        else:
            inv.unsuppressed += 1
            # Absence of evidence is not evidence of quality. `cv_auc_mean` is the PRIMARY
            # gate and `overfit_gap` the symmetric trust check; with both absent, a model can
            # only fail the dead-recall condition, and a model that predicts every positive on
            # a tiny split passes that trivially.
            if metrics.get("cv_auc_mean") is None and metrics.get("overfit_gap") is None:
                inv.unsuppressed_without_quality_evidence.append(name)
            state, missing = evidence_sufficiency(metrics)
            if state != "sufficient":
                inv.serving_on_insufficient_evidence.append(name)
                inv.insufficiency_reasons[name] = missing
        if validity is False:
            inv.invalid += 1
            if not stored:
                inv.invalid_unsuppressed.append(name)
        elif validity is None:
            inv.unknown_validity += 1

        should, reason = decide(
            metrics.get("cv_auc_mean"),
            # A missing recall/precision is NOT 0.0 — that would trip the dead-recall condition
            # on a model we simply have no metric for. -1.0 is outside [0, 1] so it cannot match.
            metrics.get("recall", -1.0),
            metrics.get("precision", -1.0),
            metrics.get("overfit_gap"),
            validity,
        )
        if should:
            inv.by_reason[reason or "unspecified"] = \
                inv.by_reason.get(reason or "unspecified", 0) + 1
        if should and not stored:
            inv.would_suppress.append(name)
        elif stored and not should:
            inv.would_unsuppress.append(name)
            if validity is False:
                # The R02 failure, caught as data rather than as a comment.
                inv.resweep_would_unsuppress_invalid.append(name)
    return inv
