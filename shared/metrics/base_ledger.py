"""M13-BASE: the base training ledger — raw stored bars to final reporting rows.

`attrition.AttritionLedger` measures ONE branch: the outcome-augmentation rows. It was
built for that branch and it answers that branch's question well. It cannot say why a fit
reported 11 rows, because the rows it tracks were never in the main lineage.

This ledger tracks the main lineage, as a chain of reconciled stages:

    loaded bars -> completed/unique bars -> required features -> available labels
    -> dead-zone selection -> deduplication -> split allocation -> embargo
    -> threshold/report sets

Three rules this module exists to enforce, each one a mistake that is easy to make when
counts are recorded ad hoc:

1. **Disjoint counts reconcile; diagnostic counts do not.** A row can be missing a required
   feature AND have no forward label AND sit inside the dead zone. Summing those three
   populations double-counts, and a ledger that tries to reconcile against the sum reports
   a phantom shortfall. So a stage carries two separate maps: `dropped`, which is a
   first-match attribution and MUST sum to `rows_in - rows_out`, and `diagnostics`, which
   is every reason that applied to a row whether or not it was the reason of record, and is
   never reconciled against anything. `reconciles` is computed from `dropped` alone.

2. **Weighted augmentation is not an observation count.** Outcome rows enter the final fit
   at double weight. Adding them to a row count produces a number that is neither the
   number of distinct observations nor the effective sample size. They are reported in
   their own block, beside — never inside — `unique_base_observations`.

3. **An absent number is `None`, not 0.** A stage that could not be measured records
   `rows_out=None` and a reason; the ledger is then `complete=False`. A stage that measured
   zero records 0. Those are different facts and the ledger never lets them collapse.

A ledger is also written for fits that produce NO artifact: every abort records the stage
it aborted at and why. "Explains only the models that saved" is the failure mode that made
the original small-sample question unanswerable in the first place.

This module is pure: no I/O, no pandas, no model imports. An audit probe can import it to
read a stored ledger without importing the training package.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# The lineage, in the order rows actually flow through it. A ledger need not fill every
# stage (an abort stops early), but it may not invent one outside this list, and it may not
# record them out of order — both would make the chain check meaningless.
STAGE_ORDER: tuple[str, ...] = (
    "loaded_bars",
    "completed_unique_bars",
    "required_features",
    "available_labels",
    "dead_zone_selection",
    "deduplication",
    "split_allocation",
    "embargo",
    "threshold_report_sets",
)

# Outcomes. `saved` means an artifact was written. `skipped` is a deliberate refusal to fit
# (the function returned a reason). `aborted` is an exception. `in_progress` means the
# ledger was emitted without ever being closed — itself a defect worth seeing.
OUTCOMES = ("saved", "skipped", "aborted", "in_progress")


class LedgerError(ValueError):
    """Raised for a ledger that cannot mean what it says."""


@dataclass
class Stage:
    name: str
    rows_in: int
    rows_out: int | None
    dropped: dict[str, int] = field(default_factory=dict)
    diagnostics: dict[str, int] = field(default_factory=dict)
    date_range: tuple[str, str] | None = None
    class_support: dict[str, int] | None = None
    partition: dict[str, int] | None = None
    unmeasured_reason: str | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if self.name not in STAGE_ORDER:
            raise LedgerError(f"unknown stage {self.name!r}; expected one of {STAGE_ORDER}")
        if self.rows_in < 0:
            raise LedgerError(f"{self.name}: rows_in cannot be negative")
        if self.rows_out is None and not self.unmeasured_reason:
            raise LedgerError(
                f"{self.name}: rows_out is None, which means NOT MEASURED — supply "
                f"unmeasured_reason. If the stage measured zero, pass 0."
            )
        if self.rows_out is not None:
            if self.rows_out < 0:
                raise LedgerError(f"{self.name}: rows_out cannot be negative")
            if self.rows_out > self.rows_in:
                raise LedgerError(
                    f"{self.name}: rows_out {self.rows_out} exceeds rows_in {self.rows_in}; "
                    f"a stage in this lineage filters, it does not create rows. Augmentation "
                    f"is recorded separately — see record_augmentation()."
                )
        if self.partition is not None:
            # A partition divides the SURVIVORS into named slices; it is not a loss book.
            # Requiring it to sum exactly is the point: a "split" whose parts do not add up
            # to what entered them is how a reported denominator drifts from its population.
            total = sum(self.partition.values())
            if self.rows_out is not None and total != self.rows_out:
                raise LedgerError(
                    f"{self.name}: partition sums to {total} but rows_out is {self.rows_out}"
                )
        for label, book in (("dropped", self.dropped), ("diagnostics", self.diagnostics)):
            for reason, n in book.items():
                if not isinstance(n, int) or isinstance(n, bool) or n < 0:
                    raise LedgerError(f"{self.name}: {label}[{reason!r}] must be a non-negative int")

    @property
    def lost(self) -> int | None:
        return None if self.rows_out is None else self.rows_in - self.rows_out

    @property
    def explained(self) -> int:
        return sum(self.dropped.values())

    @property
    def unexplained(self) -> int | None:
        """Rows this stage lost without naming a reason. NOT computed from diagnostics."""
        return None if self.lost is None else self.lost - self.explained

    @property
    def reconciles(self) -> bool:
        """True only when the DISJOINT attribution accounts for the loss exactly.

        An over-attribution (dropped summing past the loss) is as much a defect as an
        under-attribution: it means two buckets claimed the same row.
        """
        return self.unexplained == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.name,
            "rows_in": self.rows_in,
            "rows_out": self.rows_out,
            "lost": self.lost,
            "dropped": dict(self.dropped),
            "dropped_is_disjoint": True,
            "diagnostics": dict(self.diagnostics),
            "diagnostics_overlap": True,
            "unexplained": self.unexplained,
            "reconciles": self.reconciles,
            "date_range": list(self.date_range) if self.date_range else None,
            "class_support": dict(self.class_support) if self.class_support else None,
            "partition": dict(self.partition) if self.partition else None,
            "unmeasured_reason": self.unmeasured_reason,
            "note": self.note,
        }


@dataclass
class BaseTrainingLedger:
    """One fit's main-lineage row accounting. Observational only — see the module docstring."""

    subject: str
    style: str = ""
    horizon: int | None = None
    model: str = ""
    stages: list[Stage] = field(default_factory=list)
    outcome: str = "in_progress"
    abort_stage: str | None = None
    abort_reason: str | None = None
    augmentation: dict[str, Any] | None = None
    cohort: dict[str, Any] | None = None

    # --- recording -------------------------------------------------------------------
    def record(
        self,
        name: str,
        *,
        rows_in: int,
        rows_out: int | None,
        dropped: dict[str, int] | None = None,
        diagnostics: dict[str, int] | None = None,
        date_range: tuple[str, str] | None = None,
        class_support: dict[str, int] | None = None,
        partition: dict[str, int] | None = None,
        unmeasured_reason: str | None = None,
        note: str = "",
    ) -> Stage:
        if self.outcome != "in_progress":
            raise LedgerError(f"ledger for {self.subject} is already {self.outcome}; cannot record more stages")
        if name not in STAGE_ORDER:
            # Checked here as well as in Stage: the ordering test below indexes STAGE_ORDER,
            # and an unknown name would surface as a bare tuple.index ValueError instead of
            # the ledger error that says what is wrong.
            raise LedgerError(f"unknown stage {name!r}; expected one of {STAGE_ORDER}")
        if name in {s.name for s in self.stages}:
            raise LedgerError(f"{name} recorded twice for {self.subject}")
        if self.stages and STAGE_ORDER.index(name) <= STAGE_ORDER.index(self.stages[-1].name):
            raise LedgerError(
                f"{name} recorded after {self.stages[-1].name}; stages must follow STAGE_ORDER"
            )
        stage = Stage(
            name=name, rows_in=rows_in, rows_out=rows_out,
            dropped=dict(dropped or {}), diagnostics=dict(diagnostics or {}),
            date_range=date_range, class_support=class_support, partition=partition,
            unmeasured_reason=unmeasured_reason, note=note,
        )
        self.stages.append(stage)
        return stage

    def record_augmentation(
        self, *, unique_base_observations: int, augmentation_rows: int,
        weight_multiple: float, note: str = "",
    ) -> None:
        """Weighted augmentation, reported BESIDE the base counts and never added to them.

        `effective_weighted_rows` is deliberately labelled as a weight total, not a sample
        size: 10 rows at 2x are 10 observations carrying 20 units of weight, and no amount
        of weighting makes them 20 independent observations.
        """
        self.augmentation = {
            "unique_base_observations": int(unique_base_observations),
            "augmentation_rows": int(augmentation_rows),
            "weight_multiple": float(weight_multiple),
            "augmentation_weight_total": float(augmentation_rows) * float(weight_multiple),
            "augmentation_weight_total_is_not_a_sample_size": True,
            "note": note,
        }

    def record_cohort(self, cohort: dict[str, Any]) -> None:
        """The full eligible-opportunity evaluation cohort for the test window.

        Training selects only rows outside the dead zone. Serving is asked about every bar.
        The cohort is the evaluation population that matches what serving faces: every row
        in the test window with complete required features and an available forward label,
        INCLUDING the small moves training dropped.
        """
        self.cohort = dict(cohort)

    # --- closing ---------------------------------------------------------------------
    def finish(self, outcome: str, *, reason: str | None = None) -> None:
        if outcome not in OUTCOMES or outcome == "in_progress":
            raise LedgerError(f"outcome must be one of {OUTCOMES[:3]}, got {outcome!r}")
        if outcome in ("skipped", "aborted"):
            if not reason:
                raise LedgerError(f"{outcome} requires a reason — an unexplained non-fit is the case this ledger exists for")
            self.abort_reason = reason
            self.abort_stage = self.stages[-1].name if self.stages else "loaded_bars"
        self.outcome = outcome

    # --- reading ---------------------------------------------------------------------
    @property
    def chain_breaks(self) -> list[str]:
        """Adjacent stages whose rows_out and rows_in disagree.

        A break is not the same as a stage that fails to reconcile: this one says the
        lineage itself is discontinuous — a population entered a stage that never left the
        one before it.
        """
        breaks: list[str] = []
        for prev, nxt in zip(self.stages, self.stages[1:]):
            if prev.rows_out is None:
                continue
            if prev.rows_out != nxt.rows_in:
                breaks.append(f"{prev.name}.rows_out={prev.rows_out} != {nxt.name}.rows_in={nxt.rows_in}")
        return breaks

    @property
    def complete(self) -> bool:
        """Every stage measured. Independent of whether they reconcile, and independent
        of the outcome: an aborted fit can have a complete ledger up to its abort."""
        return all(s.rows_out is not None for s in self.stages)

    @property
    def reconciles(self) -> bool:
        return (not self.chain_breaks) and all(s.reconciles for s in self.stages)

    @property
    def survivors(self) -> int | None:
        if not self.stages:
            return None
        return self.stages[-1].rows_out

    @property
    def biggest_loss(self) -> str | None:
        measured = [s for s in self.stages if s.lost]
        if not measured:
            return None
        return max(measured, key=lambda s: s.lost or 0).name

    def snapshot(self) -> dict[str, Any]:
        """`to_dict()` for embedding INSIDE the artifact the fit is about to write.

        At that moment the ledger cannot know its own outcome — the save has not happened.
        Leaving it reading `in_progress` would describe the fit as unfinished when what is
        unfinished is the ledger, so a snapshot says exactly what the artifact's existence
        proves: the fit reached the point of writing one. The authoritative outcome is the
        logged ledger, which is written after the function returns or raises.
        """
        d = self.to_dict()
        if d["outcome"] == "in_progress":
            d["outcome"] = "reached_artifact_write"
            d["outcome_note"] = "snapshot taken before the save; see the train.base_ledger log line for the final outcome"
        return d

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "style": self.style,
            "horizon": self.horizon,
            "model": self.model,
            "outcome": self.outcome,
            "abort_stage": self.abort_stage,
            "abort_reason": self.abort_reason,
            "stages": [s.to_dict() for s in self.stages],
            "stages_recorded": [s.name for s in self.stages],
            "stages_not_reached": [n for n in STAGE_ORDER if n not in {s.name for s in self.stages}],
            "survivors": self.survivors,
            "biggest_loss": self.biggest_loss,
            "complete": self.complete,
            "reconciles": self.reconciles,
            "chain_breaks": self.chain_breaks,
            "augmentation": self.augmentation,
            "cohort": self.cohort,
        }


class SafeLedger:
    """A ledger that cannot fail a fit.

    `BaseTrainingLedger` validates hard on purpose — a stage recorded twice, out of order,
    or with a partition that does not sum is a defect worth raising on in a test. In
    production that same strictness would turn an instrumentation bug into a lost model,
    which is a far worse outcome than a missing stage. So every call made from inside
    `train_model` goes through this proxy: it forwards, and on failure it hands the step
    name and the exception to `on_error` and returns None. Attribute READS (`.outcome`)
    pass straight through, so the boundary still sees the real state. An `on_error` that
    itself raises is swallowed too — reporting a failure may not become a second one.
    """

    def __init__(self, inner: BaseTrainingLedger, on_error=None):
        self._inner = inner
        self._on_error = on_error

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def _call(*a, **kw):
            try:
                return attr(*a, **kw)
            except Exception as exc:
                if self._on_error is not None:
                    try:
                        self._on_error(name, exc)
                    except Exception:
                        pass
                return None
        return _call


def eligible_cohort_for_window(trace: dict, start: str | None, end: str | None) -> dict:
    """The full eligible-opportunity cohort for the evaluation window.

    Training evaluates itself on the rows it SELECTED: moves large enough to clear the
    dead zone. Serving is asked about every bar, including the ones whose future move
    turned out to be small. An accuracy measured only over clear moves cannot speak for
    that population, and the gap is not a rounding error — the dead zone is fitted to half
    the symbol's own expected move, so the excluded rows are the ambiguous majority by
    design.

    This preserves the excluded rows for the test window so that evaluation over the full
    eligible population is possible later WITHOUT a retrain. It changes no label, no
    weight and no selection: the training set is still the dead-zone-filtered one.

    Returns a record with `rows: None` and a stated reason when the window or the trace is
    unavailable — never an empty cohort presented as a measured zero.
    """
    cohort = dict((trace or {}).get("cohort") or {})
    out = {
        "definition": cohort.get("definition"),
        "window": {"start": start, "end": end},
        "scope": "test window only; the full-series counts beside it are for context",
        "full_series": {k: cohort.get(k) for k in
                        ("n_eligible", "n_selected_for_training",
                         "n_eligible_excluded_from_training", "date_range")},
        "rows": None,
        "unavailable_reason": None,
    }
    rows = cohort.get("rows") or {}
    dates = rows.get("date")
    if not dates:
        out["unavailable_reason"] = "build_features could not map rows to bar dates"
        return out
    if not start or not end:
        out["unavailable_reason"] = "the test window's date range could not be determined"
        return out
    idx = [i for i, d in enumerate(dates) if start <= d <= end]
    sel = [bool(rows["selected_for_training"][i]) for i in idx]
    out["rows"] = {
        "date": [dates[i] for i in idx],
        "fwd_ret": [float(rows["fwd_ret"][i]) for i in idx],
        "y_dir": [int(rows["y_dir"][i]) for i in idx],
        "selected_for_training": sel,
    }
    out["n_eligible_in_window"] = len(idx)
    out["n_selected_in_window"] = int(sum(sel))
    out["n_excluded_small_moves_in_window"] = len(idx) - int(sum(sel))
    out["class_support_eligible_in_window"] = {
        "pos": int(sum(out["rows"]["y_dir"])),
        "neg": len(idx) - int(sum(out["rows"]["y_dir"])),
    }
    return out


def run_with_ledger(fn, args, kwargs, *, on_close=None):
    """Call `fn` with a fresh ledger and close that ledger however the call ends.

    Three endings, three outcomes, and the two that produce no model are the ones that
    matter most: a ledger that only described successful fits would leave "why is there no
    artifact at all" exactly as unanswerable as it was before.

    `fn` is expected to accept a `_ledger` keyword and to take `symbol`, `style`, `horizon`
    and `model_name` among its parameters; those name the ledger's subject. If `fn` closed
    the ledger itself (a deliberate skip), that outcome is left alone.
    """
    import inspect  # kept local: this module stays import-light for audit probes

    bound = inspect.signature(fn).bind(*args, **kwargs)
    bound.apply_defaults()
    ledger = BaseTrainingLedger(
        subject=str(bound.arguments.get("symbol", "")),
        style=str(bound.arguments.get("style", "")),
        horizon=bound.arguments.get("horizon"),
        model=str(bound.arguments.get("model_name", "")),
    )
    kwargs = dict(kwargs)
    kwargs["_ledger"] = ledger
    try:
        result = fn(*args, **kwargs)
    except BaseException as exc:
        if ledger.outcome == "in_progress":
            ledger.finish("aborted", reason=f"{type(exc).__name__}: {exc}")
        if on_close is not None:
            on_close(ledger)
        raise
    if ledger.outcome == "in_progress":
        if isinstance(result, dict) and result.get("skipped"):
            ledger.finish("skipped", reason=str(result.get("reason") or "unspecified"))
        else:
            ledger.finish("saved")
    if on_close is not None:
        on_close(ledger)
    return result
