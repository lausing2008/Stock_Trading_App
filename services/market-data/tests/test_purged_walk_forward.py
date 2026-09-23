"""AUD-PURGEDWF — purged + embargoed walk-forward splits.

This repo already had chronological splits (position_scaling_gate.walk_forward_train) and
triple-barrier labels, but a repo-wide grep for "purge"/"embargo" returned nothing. That gap
matters precisely for the labels used here: a triple-barrier or N-day-forward label observed at
t is not RESOLVED until some later t1, so a training row near the end of the train slice has a
label still being determined inside the validation window. Training on it leaks validation price
action, and the backtest looks better than the strategy is — the flattering direction, which is
the hardest kind of bug to notice.

The decisive test is test_no_training_label_resolves_inside_validation: it asserts the invariant
directly rather than checking that some count changed.
"""
import numpy as np
import pytest

from src.backtest.purged_walk_forward import (
    assert_no_leakage,
    purged_walk_forward_splits,
)


def _fixed_horizon(n: int, h: int) -> np.ndarray:
    """label_end_idx for a constant H-bar-forward label."""
    return np.arange(n) + h


# ── The invariant the module exists to guarantee ─────────────────────────────────────────────

def test_no_training_label_resolves_inside_validation():
    """The whole point. Checked on every usable fold, for a long horizon where the naive
    (unpurged) split would certainly leak."""
    n, h = 600, 30
    r = purged_walk_forward_splits(_fixed_horizon(n, h), n_splits=5, min_train=100, min_val=40)
    assert r.usable_folds, "expected usable folds for this size"
    for fold in r.usable_folds:
        assert_no_leakage(fold, _fixed_horizon(n, h))


def test_an_unpurged_split_would_have_leaked():
    """Control: proves the purge is doing real work rather than the geometry making it moot.
    Without purging, the last H training rows all resolve inside validation."""
    n, h = 600, 30
    r = purged_walk_forward_splits(_fixed_horizon(n, h), n_splits=5, min_train=100, min_val=40)
    fold = r.usable_folds[0]
    naive_train = np.arange(0, fold.val_start)
    leaked = (_fixed_horizon(n, h)[naive_train] >= fold.val_start).sum()
    assert leaked == h, f"expected exactly {h} leaking rows without purging, got {leaked}"
    assert fold.n_purged == h


def test_purged_rows_are_exactly_the_horizon_for_a_fixed_label():
    n, h = 500, 10
    r = purged_walk_forward_splits(_fixed_horizon(n, h), n_splits=4, min_train=80, min_val=40)
    for fold in r.usable_folds:
        assert fold.n_purged == h


def test_a_zero_horizon_label_purges_nothing():
    """A label known immediately cannot overlap anything — purging must not remove rows
    gratuitously, which would quietly shrink every training set."""
    n = 400
    r = purged_walk_forward_splits(np.arange(n), n_splits=4, min_train=80, min_val=40)
    assert all(f.n_purged == 0 for f in r.usable_folds)


def test_variable_horizons_purge_per_row_not_by_a_constant():
    """Triple-barrier labels resolve at different times per row; purging must respect that."""
    n = 300
    ends = np.arange(n) + 1
    ends[:100] = np.arange(100) + 60  # early rows resolve much later
    r = purged_walk_forward_splits(ends, n_splits=3, min_train=100, min_val=40)
    for fold in r.usable_folds:
        assert_no_leakage(fold, ends)


# ── Validation windows ───────────────────────────────────────────────────────────────────────

def test_validation_windows_are_forward_and_non_overlapping():
    r = purged_walk_forward_splits(_fixed_horizon(600, 10), n_splits=5, min_train=100, min_val=40)
    folds = r.usable_folds
    for a, b in zip(folds, folds[1:]):
        assert b.val_start >= a.val_end, "validation windows must not overlap"


def test_training_always_precedes_validation():
    r = purged_walk_forward_splits(_fixed_horizon(600, 10), n_splits=5, min_train=100, min_val=40)
    for fold in r.usable_folds:
        assert fold.train_idx.max() < fold.val_start


# ── Honest failure rather than a misleading result ───────────────────────────────────────────

def test_too_little_data_is_reported_not_silently_empty():
    r = purged_walk_forward_splits(_fixed_horizon(30, 5), n_splits=5, min_train=100, min_val=40)
    assert r.usable_folds == []
    assert r.folds[0].skipped_reason is not None
    assert "need at least" in r.folds[0].skipped_reason


def test_a_horizon_too_long_for_the_data_warns_rather_than_quietly_returning_junk():
    """The GROWTH-model problem in miniature: a 90-bar label on a short series purges most of
    the training set. That must surface as a warning, not a confident-looking number."""
    n, h = 260, 90
    r = purged_walk_forward_splits(_fixed_horizon(n, h), n_splits=4, min_train=60, min_val=30)
    assert r.warnings, "a heavily-purged run must warn"
    assert any("purged" in w or "usable folds" in w for w in r.warnings)


def test_purge_fraction_is_reported_per_fold():
    n, h = 400, 50
    r = purged_walk_forward_splits(_fixed_horizon(n, h), n_splits=3, min_train=100, min_val=40)
    for fold in r.usable_folds:
        assert 0.0 <= fold.purge_fraction <= 1.0
        assert fold.purge_fraction > 0


# ── Embargo: real where it applies, honest where it does not ─────────────────────────────────

def test_embargo_widens_the_seam_before_validation():
    """Embargo drops bars immediately BEFORE validation. With a 5-bar label, purging alone
    removes 5 rows; a 20-bar embargo must remove the rest of that 20-bar band."""
    n, h, emb = 800, 5, 20
    r = purged_walk_forward_splits(
        _fixed_horizon(n, h), n_splits=5, min_train=100, min_val=50, embargo=emb,
    )
    for fold in r.usable_folds:
        assert fold.n_purged + fold.n_embargoed == emb, (
            "purge and embargo together must clear the full embargo band"
        )
        assert fold.train_idx.max() < fold.val_start - emb


def test_embargo_applies_in_rolling_mode_too():
    """Training is always before validation in BOTH modes, so the preceding band exists in
    both. An earlier draft made this a no-op in rolling mode, which was simply wrong."""
    r = purged_walk_forward_splits(
        _fixed_horizon(600, 5), n_splits=3, min_train=100, min_val=50, embargo=15,
        expanding=False,
    )
    assert r.usable_folds
    for fold in r.usable_folds:
        assert fold.train_idx.max() < fold.val_start - 15


def test_zero_embargo_removes_nothing():
    r = purged_walk_forward_splits(_fixed_horizon(600, 5), n_splits=4, min_train=100, min_val=40)
    assert all(f.n_embargoed == 0 for f in r.usable_folds)


# ── The leak detector itself must be able to fail ────────────────────────────────────────────

def test_assert_no_leakage_actually_raises_on_a_leak():
    """A guard that cannot fail is not a guard — this repo has three incidents on that theme."""
    n, h = 400, 20
    r = purged_walk_forward_splits(_fixed_horizon(n, h), n_splits=3, min_train=100, min_val=40)
    fold = r.usable_folds[0]
    fold.train_idx = np.arange(0, fold.val_start)  # re-introduce the unpurged rows
    with pytest.raises(AssertionError, match="leaks"):
        assert_no_leakage(fold, _fixed_horizon(n, h))


def test_assert_no_leakage_tolerates_an_empty_training_set():
    n, h = 400, 20
    r = purged_walk_forward_splits(_fixed_horizon(n, h), n_splits=3, min_train=100, min_val=40)
    fold = r.usable_folds[0]
    fold.train_idx = np.array([], dtype=np.int64)
    assert_no_leakage(fold, _fixed_horizon(n, h))
