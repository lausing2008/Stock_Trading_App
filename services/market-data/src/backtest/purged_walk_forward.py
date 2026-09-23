"""AUD-PURGEDWF (2026-09-22): purged + embargoed walk-forward splits.

WHY THIS EXISTS. This repo already had the two easy halves — `triple_barrier_labeling.py` for
labels and `position_scaling_gate.walk_forward_train()` for chronological splits — but a repo-wide
grep for "purge" or "embargo" returned nothing. That gap matters most for exactly the labels this
codebase uses: a triple-barrier or N-day-forward label observed at time t is not *resolved* until
some later time t1, so a training row near the end of the train slice has a label that was still
being determined *inside* the validation window. Training on it leaks the validation period's
price action into the model, and the resulting backtest looks better than the strategy is.

The fix is López de Prado's: drop ("purge") any training sample whose label window overlaps the
validation window, and optionally "embargo" a buffer of bars adjacent to validation so that
serial correlation immediately either side of the boundary cannot carry information across it.

TWO DISTINCT MECHANISMS, often conflated:

  * **Purging** is about a sample's LABEL HORIZON. Sample i is purged when its label is still
    unresolved at the moment validation starts (``label_end_idx[i] >= val_start``). Without
    per-sample horizons there is nothing to purge against, which is why `label_end_idx` is a
    required argument rather than an optional nicety.
  * **Embargo** is about SERIAL CORRELATION, not labels, and it is a GAP rather than a filter.
    It drops training rows in the band immediately BEFORE validation begins. Purging alone only
    removes rows whose label formally overlaps; rows just short of that boundary can still be
    correlated with the validation window through momentum, volatility clustering or a shared
    market move. The embargo widens the seam.

    A note on provenance, because the term is often mis-applied: López de Prado's embargo is
    defined for k-fold CV where a test block can sit BEFORE training data, so the band goes
    after the test set. In a strictly-forward walk-forward, training is always entirely before
    validation, so the equivalent and only meaningful band is the one immediately preceding
    validation. An earlier draft of this module placed it after the previous fold's validation
    window — which, with contiguous tiling, IS the current validation window, so it removed
    exactly nothing while appearing to work. It applies in both expanding and rolling modes.

DELIBERATELY NOT A MODEL. This returns index ranges only. It does not fit anything, know what an
estimator is, or care whether the caller is training XGBoost, an HMM or a logistic gate. Coupling
splitting to a specific model is what made `walk_forward_train()` unreusable here.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger("backtest.purged_walk_forward")


@dataclass
class PurgedFold:
    """One train/validation split, with the accounting of what was removed and why.

    `n_purged` and `n_embargoed` are reported separately and deliberately: a fold that loses most
    of its training data to purging is a signal the label horizon is too long for the available
    history, and collapsing both counts into one total would hide that.
    """
    fold_index: int
    train_idx: np.ndarray
    val_idx: np.ndarray
    val_start: int
    val_end: int
    n_train_before_purge: int
    n_purged: int
    n_embargoed: int
    skipped_reason: str | None = None

    @property
    def n_train(self) -> int:
        return len(self.train_idx)

    @property
    def n_val(self) -> int:
        return len(self.val_idx)

    @property
    def purge_fraction(self) -> float:
        """Share of candidate training rows removed. Above ~0.5 the horizon is too long for
        this dataset and the fold's result should not be trusted."""
        return (
            (self.n_purged + self.n_embargoed) / self.n_train_before_purge
            if self.n_train_before_purge else 0.0
        )


@dataclass
class PurgedWalkForward:
    folds: list[PurgedFold] = field(default_factory=list)
    n_samples: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def usable_folds(self) -> list[PurgedFold]:
        return [f for f in self.folds if f.skipped_reason is None]


def purged_walk_forward_splits(
    label_end_idx: np.ndarray | list[int],
    n_splits: int = 5,
    min_train: int = 50,
    min_val: int = 20,
    embargo: int = 0,
    expanding: bool = True,
) -> PurgedWalkForward:
    """Build purged (and optionally embargoed) walk-forward splits over `n` chronological rows.

    `label_end_idx[i]` is the row index at which sample i's label becomes KNOWN — for a fixed
    H-bar-forward label that is ``i + H``; for triple-barrier it is whichever barrier fired
    first, so it varies per row. Callers with a constant horizon can pass
    ``np.arange(n) + H``.

    Rows must already be in chronological order. This does not sort — matching
    `walk_forward_train()`'s own stated contract, and because silently re-sorting would mask a
    caller bug that is far more dangerous than an obvious exception.

    `expanding=True` grows the training window from the start of the series; `False` uses a
    rolling window whose length matches the first fold's. `embargo` drops that many bars
    immediately before validation in either mode — see the module docstring for why that is the
    correct band in a strictly-forward split.
    """
    label_end_idx = np.asarray(label_end_idx, dtype=np.int64)
    n = len(label_end_idx)
    result = PurgedWalkForward(n_samples=n)

    if n < min_train + min_val:
        result.folds.append(PurgedFold(
            fold_index=0, train_idx=np.array([], dtype=np.int64),
            val_idx=np.array([], dtype=np.int64), val_start=0, val_end=0,
            n_train_before_purge=0, n_purged=0, n_embargoed=0,
            skipped_reason=(
                f"only {n} rows; need at least min_train({min_train}) + min_val({min_val})"
            ),
        ))
        return result

    # Validation windows tile the tail of the series after the first training block.
    val_span = n - min_train
    fold_size = max(min_val, val_span // max(1, n_splits))

    for k in range(n_splits):
        val_start = min_train + k * fold_size
        val_end = min(val_start + fold_size, n)
        if val_start >= n or (val_end - val_start) < min_val:
            break

        train_candidates = (
            np.arange(0, val_start)
            if expanding
            else np.arange(max(0, val_start - min_train - k * fold_size), val_start)
        )
        n_before = len(train_candidates)

        # PURGE: a training row whose label was still resolving when validation began has, by
        # construction, seen validation-window price action.
        overlaps = label_end_idx[train_candidates] >= val_start
        kept = train_candidates[~overlaps]
        n_purged = int(overlaps.sum())

        # EMBARGO: widen the seam. Purging removes rows whose LABEL overlaps validation; rows
        # just short of that boundary can still be correlated with it through momentum or a
        # shared market move, so drop the `embargo` bars immediately preceding val_start.
        n_embargoed = 0
        if embargo > 0:
            in_band = kept >= (val_start - embargo)
            n_embargoed = int(in_band.sum())
            kept = kept[~in_band]

        fold = PurgedFold(
            fold_index=k, train_idx=kept, val_idx=np.arange(val_start, val_end),
            val_start=val_start, val_end=val_end,
            n_train_before_purge=n_before, n_purged=n_purged, n_embargoed=n_embargoed,
            skipped_reason=(
                f"only {len(kept)} training rows survive purging (need {min_train})"
                if len(kept) < min_train else None
            ),
        )
        if fold.purge_fraction > 0.5 and fold.skipped_reason is None:
            result.warnings.append(
                f"fold {k}: {fold.purge_fraction:.0%} of training rows purged — the label "
                "horizon is long relative to this dataset; treat the result as weak evidence."
            )
        result.folds.append(fold)
        log.info(
            "purged_wf.fold n=%d train=%d (purged=%d embargoed=%d) val=%d",
            k, fold.n_train, n_purged, n_embargoed, fold.n_val,
        )

    if not result.usable_folds:
        result.warnings.append(
            "no usable folds: every split lost too much training data to purging. With an "
            "H-bar label you need materially more than H*n_splits rows of history."
        )
    return result


def assert_no_leakage(fold: PurgedFold, label_end_idx: np.ndarray | list[int]) -> None:
    """Raise if any training row's label window reaches into validation.

    A cheap invariant worth asserting at call sites rather than trusting: a silent leak inflates
    backtest results in the flattering direction, which is the hardest kind of bug to notice.
    """
    label_end_idx = np.asarray(label_end_idx, dtype=np.int64)
    if len(fold.train_idx) == 0:
        return
    worst = int(label_end_idx[fold.train_idx].max())
    if worst >= fold.val_start:
        raise AssertionError(
            f"fold {fold.fold_index} leaks: a training label resolves at index {worst}, "
            f"which is inside validation starting at {fold.val_start}"
        )
