"""M13 — a stage-by-stage row ledger, so "where did the rows go" has an answer.

THE PROBLEM. Outcome-augmentation rows vanish between loading and fitting, and the only visible
number is the one at the end. Measured before the AUD-ML3-OUTCOMEDEDUP fix: **490 of 548
artifacts had `n_outcome_rows == 0`**, and the 58 non-zero ones had a MEDIAN OF 6 against 37-43
rows actually loaded. The final count was the only thing recorded, so a 37 -> 6 collapse and a
37 -> 37 pass-through were indistinguishable, and a bug that made an entire feature inert looked
exactly like "this symbol has little history".

A single surviving count cannot distinguish:
  * the data never existed,
  * it existed and was correctly excluded,
  * it existed and was wrongly excluded.

Those demand different responses, so they need different records.

THE RULE, which this class enforces rather than documents: **every row that leaves a stage is
either carried forward or dropped with a REASON.** `rows_in == rows_out + sum(dropped)`. A
ledger that does not reconcile is reported as not reconciling — it is never quietly balanced,
because a silent adjustment is how the original defect stayed invisible. An `unexplained` bucket
closes the arithmetic but DEGRADES `reconciles`, so naming a gap cannot pass for closing it.

SCOPE, stated because it is narrower than the register's wording. This currently instruments the
OUTCOME-AUGMENTATION path only: loaded, min_sample, shared_features, dedup, min_after_dedup.
Label maturity is partly covered by the training-cutoff filter, and **purge, calibration and
promotion exclusions are NOT measured here** — they happen in other code paths and need their
own ledgers before M13's full claim can be made.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Stage:
    name: str
    rows_in: int
    rows_out: int
    dropped: dict[str, int] = field(default_factory=dict)
    note: str = ""

    @property
    def total_dropped(self) -> int:
        return sum(self.dropped.values())

    @property
    def reconciles(self) -> bool:
        return self.rows_in == self.rows_out + self.total_dropped

    def to_dict(self) -> dict:
        return {"stage": self.name, "in": self.rows_in, "out": self.rows_out,
                "dropped": dict(self.dropped), "reconciles": self.reconciles,
                **({"note": self.note} if self.note else {})}


class AttritionLedger:
    """Records what each stage received, passed on, and discarded — and why.

    Deliberately NOT a logger. A log line per stage is unqueryable after the fact and cannot be
    checked for consistency; this reconciles, and is small enough to store on the artifact that
    the training run produces.
    """

    def __init__(self, subject: str):
        self.subject = subject
        self.stages: list[Stage] = []

    def record(self, name: str, *, rows_in: int, rows_out: int,
               dropped: dict[str, int] | None = None, note: str = "") -> Stage:
        if rows_in < 0 or rows_out < 0:
            raise ValueError(f"{name}: row counts cannot be negative")
        dropped = {k: v for k, v in (dropped or {}).items() if v}
        for reason, n in dropped.items():
            if n < 0:
                raise ValueError(f"{name}: dropped[{reason!r}] cannot be negative")
        # An unexplained loss is the thing this class exists to prevent. Record it as its own
        # reason rather than letting the stage silently fail to reconcile: a named
        # `unexplained` bucket is actionable, a quiet discrepancy is not.
        gap = rows_in - rows_out - sum(dropped.values())
        if gap > 0:
            dropped["unexplained"] = dropped.get("unexplained", 0) + gap
        stage = Stage(name=name, rows_in=rows_in, rows_out=rows_out, dropped=dropped, note=note)
        self.stages.append(stage)
        return stage

    @property
    def reconciles(self) -> bool:
        """Every stage balances, the chain composes, AND nothing was unexplained.

        THE UNEXPLAINED BUCKET MUST DEGRADE THIS. `record()` closes an unexplained gap by
        naming it, which makes the arithmetic balance — so without this clause a ledger could
        reconcile cleanly while concealing rows nobody can account for. Balancing the books by
        inventing a line item is not reconciliation.
        """
        if self.unexplained:
            return False
        if not all(s.reconciles for s in self.stages):
            return False
        for prev, nxt in zip(self.stages, self.stages[1:]):
            if nxt.rows_in != prev.rows_out:
                return False
        return True

    @property
    def unexplained(self) -> int:
        return sum(s.dropped.get("unexplained", 0) for s in self.stages)

    def survival_rate(self) -> float | None:
        """Final rows over initial rows, or None when nothing entered.

        None, not 0.0: a pipeline that received no rows has no survival rate, which is a
        different statement from one that discarded everything it received.
        """
        if not self.stages or self.stages[0].rows_in == 0:
            return None
        return self.stages[-1].rows_out / self.stages[0].rows_in

    def biggest_loss(self) -> tuple[str, str, int] | None:
        """The (stage, reason, count) that cost the most rows — where to look first."""
        worst = None
        for s in self.stages:
            for reason, n in s.dropped.items():
                if worst is None or n > worst[2]:
                    worst = (s.name, reason, n)
        return worst

    def to_dict(self) -> dict:
        return {
            "subject": self.subject,
            "stages": [s.to_dict() for s in self.stages],
            "reconciles": self.reconciles,
            "unexplained": self.unexplained,
            "survival_rate": self.survival_rate(),
            "biggest_loss": self.biggest_loss(),
        }
