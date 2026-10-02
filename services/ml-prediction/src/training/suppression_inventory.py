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
        an empty fleet has no coverage, which differs from a fleet with zero coverage."""
        if self.total == 0:
            return None
        return (self.total - self.unknown_validity) / self.total

    def to_dict(self) -> dict:
        return {
            "rule_version": self.rule_version,
            "total": self.total, "suppressed": self.suppressed,
            "unsuppressed": self.unsuppressed,
            "invalid": self.invalid, "unknown_validity": self.unknown_validity,
            "validity_coverage": self.coverage,
            "invalid_unsuppressed": self.invalid_unsuppressed,
            "unsuppressed_without_quality_evidence": self.unsuppressed_without_quality_evidence,
            "resweep_would_unsuppress_invalid": self.resweep_would_unsuppress_invalid,
            "would_suppress": len(self.would_suppress),
            "would_unsuppress": len(self.would_unsuppress),
            "by_reason": dict(self.by_reason),
            "failed_to_read": self.failed_to_read,
            "invariants_hold": self.holds,
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
