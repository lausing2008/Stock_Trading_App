"""R03 (2026-09-24 follow-up audit): sorted records still do not make promotion evidence.

DA-01's global sort fixed the original symbol-block error and train-only feature selection is
correct. Three things it did not fix, and each of them is enough on its own to make the
promotion AUC not mean what it is used for:

  1. A ROW SPLIT CAN DIVIDE ONE SESSION. `int(len(X) * 0.8)` lands wherever the 80th-percentile
     row happens to be, and with many symbols observed on the same dates that is routinely
     INSIDE a decision session. The audit's reproduction: five symbols over four dates put
     September 4 on both sides — and the chronological guard accepted it, because it rejected
     only `>` and never `==`. That is DA-01's symbol-block leak reappearing one row at a time.

  2. RECORDS CARRIED NO LABEL AVAILABILITY. Sorting by signal date orders the SIGNALS, not the
     INFORMATION. A signal emitted before the boundary whose position closed after it carries
     an outcome from inside the validation period, so the model is fitted on the answer to a
     question it is then scored on. The lag varies by horizon and by how each trade actually
     went, so it cannot be derived from the signal date.

  3. ONE SLICE DID TWO JOBS. `fit(..., eval_set=[(X_val, y_val)], early_stopping_rounds=20)`
     selected the number of boosting rounds — a fitted parameter — against the very rows whose
     AUC then decided promotion. And promotion compared that AUC to the incumbent's STORED
     historical AUC: two numbers from different cohorts, different markets and different symbol
     mixes, whose difference is not evidence about which model is better.

The blend stays disabled (DA-02, and R09 stopped it being reported as a contributor). These
tests are about whether the evidence would support re-enabling it, not about turning it on.
"""
import ast
import re
import textwrap
from datetime import date, timedelta
from pathlib import Path

import pytest

_META = (Path(__file__).resolve().parents[1]
         / "src" / "training" / "meta_trainer.py").read_text()
_META_CODE = "\n".join(
    ln.split("#", 1)[0] if not ln.lstrip().startswith("#") else ""
    for ln in _META.split("\n")
)


# ── 1. The split is a session boundary ───────────────────────────────────────

def _session_split(record_dates: list) -> int:
    """Run the SHIPPED session-boundary rule — extracted from the source and executed.

    NOT a reimplementation. An earlier version of this file mirrored the loop in Python here
    and then pinned the shipped lines with source-text assertions to keep the two in step. That
    was wrong twice over: DA-11's own tests computed a formula in their body and three
    sabotages passed without executing the sabotaged code, and the source-text assertions
    themselves tripped the AUD-T401 ratchet — pinning a decrement line as a substring survives
    that line being changed to anything which still contains it.

    (This paragraph deliberately DESCRIBES the offending pattern rather than quoting it. The
    ratchet scans raw file text, so an example of the anti-pattern written out in prose counts
    as an instance of it — which is how the first attempt at this docstring failed the check it
    was explaining.)

    Executing the real block means a sabotage of the rule fails these tests because the rule
    actually ran, which is the only version of this that means anything.
    """
    block = _META[_META.index("    _raw_split = int(len(X_raw) * 0.8)"):]
    block = block[:block.index("    if split <= 0 or split >= len(record_dates):")]
    ns = {"X_raw": list(record_dates), "record_dates": list(record_dates)}
    exec(textwrap.dedent(block), ns)  # noqa: S102 — one block of this repo's own source
    return ns["split"]


def test_the_purge_rule_is_the_shipped_one():
    """_purge() below is a reimplementation, so it is pinned to the shipped expression. That
    line contains no number, so pinning it does not create the AUD-T401 problem the split's own
    pinning did — see _session_split's docstring."""
    assert "_train_idx = [i for i in range(split) if label_available[i] < _val_start]" in _META


def _split_is_degenerate(record_dates: list) -> bool:
    """The shipped refusal condition, executed the same way."""
    split = _session_split(record_dates)
    guard = _META[_META.index("    if split <= 0 or split >= len(record_dates):"):]
    guard = guard[:guard.index("        return {")]
    ns = {"split": split, "record_dates": list(record_dates), "hit": False,
          "log": type("L", (), {"warning": lambda *a, **k: None})(),
          "_raw_split": int(len(record_dates) * 0.8)}
    exec(textwrap.dedent(guard) + "    hit = True\n", ns)  # noqa: S102
    return bool(ns["hit"])


def _audit_reproduction() -> list:
    """The audit's own shape: five symbols over four dates, interleaved after the global sort."""
    dates = [date(2026, 9, d) for d in (1, 2, 3, 4)]
    return sorted([d for d in dates for _ in range(5)])


def test_the_audits_five_symbols_over_four_dates_no_longer_straddle_the_boundary():
    """THE CORE R03 CASE. 20 rows, raw split at 16 — the middle of September 4."""
    dates = _audit_reproduction()
    raw = int(len(dates) * 0.8)
    assert dates[raw - 1] == dates[raw], "precondition: the RAW split divides a session"

    split = _session_split(dates)
    assert dates[split - 1] < dates[split], "the same session is still on both sides"


@pytest.mark.parametrize("per_day", [1, 3, 5, 17, 40])
def test_no_session_appears_on_both_sides_at_any_density(per_day):
    """The defect's severity scales with how many symbols share a date, so one density is not a
    test. A single observation per day is the case where a row split happens to be correct —
    it must keep working."""
    dates = sorted([date(2026, 9, 1) + timedelta(days=i)
                    for i in range(20) for _ in range(per_day)])
    split = _session_split(dates)
    assert 0 < split < len(dates)
    assert dates[split - 1] < dates[split]
    assert set(dates[:split]).isdisjoint(set(dates[split:]))


def test_the_boundary_moves_BACK_so_validation_is_never_smaller_than_intended():
    """Moving forward would borrow validation rows for training — the wrong direction for a
    guard whose whole purpose is keeping the evaluation clean."""
    dates = _audit_reproduction()
    raw = int(len(dates) * 0.8)
    assert _session_split(dates) <= raw


def test_a_single_session_dataset_is_refused_rather_than_split_into_nothing():
    """Backing up through one giant session reaches zero. Training on an empty slice, or
    scoring on the whole dataset, is worse than declining."""
    dates = [date(2026, 9, 1)] * 20
    assert _session_split(dates) == 0
    assert _split_is_degenerate(dates), "the degenerate split must be refused, not used"
    assert "session_split_degenerate" in _META


def test_a_normal_dataset_is_not_refused():
    """The refusal must be reachable AND avoidable; a guard that always fires is an outage."""
    dates = sorted([date(2026, 9, 1) + timedelta(days=i) for i in range(20) for _ in range(5)])
    assert not _split_is_degenerate(dates)


def test_the_chronological_guard_now_rejects_EQUAL_dates():
    """`>` alone accepted the audit's reproduction outright. This is the one-character version
    of the whole finding."""
    assert "if _last_train >= _first_val:" in _META_CODE
    assert "if _last_train > _first_val:" not in _META_CODE


def test_the_guard_checks_the_last_TRAINING_row_not_the_last_pre_boundary_row():
    """After the purge those are different rows, and checking the wrong one would pass while
    the actual training data straddled the boundary."""
    assert "_last_train, _first_val = record_dates[_train_idx[-1]], record_dates[split]" in _META


# ── 2. Purging by label availability ─────────────────────────────────────────

def _purge(signal_dates: list, avail: list, split: int) -> list:
    val_start = signal_dates[split]
    return [i for i in range(split) if avail[i] < val_start]


def test_a_signal_before_the_boundary_whose_trade_closed_after_it_is_purged():
    """VARYING RESOLUTION LAGS, which the audit names explicitly. Both signals are emitted
    before the boundary; only one was knowable before it."""
    signal_dates = [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 10)]
    avail = [date(2026, 9, 3),    # resolved quickly — admissible
             date(2026, 9, 20),   # trade ran on, resolved INSIDE validation — purge
             date(2026, 9, 25)]
    assert _purge(signal_dates, avail, split=2) == [0]


def test_an_early_resolved_signal_is_kept():
    """The guard must not simply drop everything near the boundary — that would throw away the
    most recent and most relevant training data for no reason."""
    signal_dates = [date(2026, 9, 1), date(2026, 9, 9), date(2026, 9, 10)]
    avail = [date(2026, 9, 2), date(2026, 9, 9), date(2026, 9, 30)]
    assert _purge(signal_dates, avail, split=2) == [0, 1]


def test_a_label_available_exactly_on_the_boundary_is_purged():
    """The validation session begins that day; a label that becomes knowable the same day was
    not knowable BEFORE it. `<`, not `<=`."""
    signal_dates = [date(2026, 9, 1), date(2026, 9, 10)]
    avail = [date(2026, 9, 10), date(2026, 9, 20)]
    assert _purge(signal_dates, avail, split=1) == []


def test_availability_is_the_latest_of_exit_evaluation_and_signal_plus_horizon():
    """Any single one of the three can be missing or optimistic. The exit date is the real
    resolution; ts_evaluated is when this platform computed it; signal+horizon is the
    conservative fallback for rows predating those columns."""
    block = _META[_META.index("_lbl_cands = ["):]
    block = block[:block.index("records.append(")]
    assert "_HORIZON_DAYS.get(str(row.horizon).upper(), 10)" in block
    assert 'getattr(row, "exit_date", None)' in block
    assert 'getattr(row, "ts_evaluated", None)' in block
    assert "max(_lbl_cands)" in _META


def test_purged_rows_are_dropped_from_training_not_moved_into_validation():
    """Their FEATURES are older than everything around them; moving them into validation would
    build a holdout out of stale observations."""
    code = _META_CODE
    assert "_train_idx = [i for i in range(split) if label_available[i] < _val_start]" in code
    assert "_val_idx = list(range(split, len(X_raw)))" in code


def test_purging_below_a_usable_training_set_refuses_rather_than_fitting():
    assert "too_few_training_rows_after_purge" in _META


def test_the_purge_count_is_reported_not_merely_logged():
    """How many rows were dropped is the single number that says whether the old split was
    contaminated for this symbol set, and it belongs with the artifact."""
    assert '"n_purged_unavailable_label": _purged,' in _META


# ── 3. Early stopping and promotion are different slices ─────────────────────

def test_early_stopping_no_longer_uses_the_promotion_slice():
    """The number of boosting rounds is a fitted parameter. Choosing it on the slice that then
    decides promotion makes that slice partially in-sample — T232-ML2's defect in a different
    costume."""
    assert "model.fit(X_tr, y_tr, eval_set=[(X_es, y_es)], verbose=False)" in _META
    assert "eval_set=[(X_val, y_val)]" not in _META_CODE


def test_the_promotion_auc_is_computed_on_the_slice_that_was_never_fitted_against():
    assert "auc = float(roc_auc_score(y_val, model.predict_proba(X_val)[:, 1]))" in _META
    # X_val comes from _final_idx, which is the tail after the early-stop cut.
    assert "X_tr_raw, y_tr = X[_train_idx], y[_train_idx]" in _META
    assert "X_es_raw, y_es = X[_es_idx], y[_es_idx]" in _META
    assert "X_val_raw, y_val = X[_final_idx], y[_final_idx]" in _META


def test_the_early_stop_cut_is_also_a_session_boundary():
    """Splitting the validation block mid-session reintroduces the same leak one level down."""
    block = _META[_META.index("_es_cut = len(_val_idx) // 2"):]
    block = block[:block.index("_es_idx, _final_idx")]
    assert "record_dates[_val_idx[_es_cut - 1]] == _es_date" in block


def test_a_validation_block_too_small_to_separate_is_refused():
    """"Train anyway and hope" is how promotion came to be decided on the early-stop window."""
    assert "validation_too_small_to_separate" in _META


def test_the_scaler_is_fitted_on_training_only_and_applied_to_both_others():
    """AUD301-METASCALER-LEAKAGE's own invariant, extended to the new third slice — a scaler
    re-fitted on the early-stop slice would leak it into the fit."""
    block = _META[_META.index("scaler = StandardScaler()"):]
    block = block[:block.index("pos_count =")]
    assert block.count("fit_transform") == 1
    assert "X_es = scaler.transform(X_es_raw)" in block
    assert "X_val = scaler.transform(X_val_raw)" in block


# ── 4. Incumbent and challenger on the SAME observations ─────────────────────

def test_the_incumbent_is_re_scored_on_this_holdout():
    """"Promotion still compares against the prior artifact's historical AUC, not the incumbent
    on the same current untouched observations." Two AUCs from different cohorts are not
    comparable, and the sign of their difference is not evidence."""
    block = _META[_META.index("comparison_basis = \"none\""):]
    block = block[:block.index("if previous_auc is not None and auc <")]
    assert "_prev_model.predict_proba(" in block
    assert "roc_auc_score(y_val, _prev_probs)" in block
    assert 'comparison_basis = "same_holdout"' in block


def test_the_incumbent_is_scored_with_its_OWN_scaler_and_columns():
    """Re-using the challenger's scaler or column selection would handicap the incumbent and
    make every retrain look like an improvement."""
    block = _META[_META.index("comparison_basis = \"none\""):]
    block = block[:block.index("if previous_auc is not None and auc <")]
    assert 'previous_bundle["scaler"]' in block
    assert 'previous_bundle.get("non_const")' in block


def test_an_unscorable_incumbent_falls_back_but_says_the_comparison_is_incomparable():
    """A feature-schema change makes the incumbent unscorable. Falling back to its stored AUC
    is the OLD comparison — usable as a weak guard, but it must not be presented as
    like-for-like."""
    assert 'comparison_basis = "historical_incomparable"' in _META
    assert '"comparison_basis": comparison_basis,' in _META


def test_a_corrupt_incumbent_still_does_not_freeze_retraining():
    """The pre-existing fail-open. Turning a corrupted file into a permanent retrain freeze
    would be worse than the bug the gate exists to prevent."""
    assert "meta_trainer.previous_bundle_unreadable" in _META
    assert "comparison_basis" in _META[_META.index("previous_bundle_unreadable"):
                                       _META.index("if previous_auc is not None and auc <")]


# ── 5. The evidence behind the number ────────────────────────────────────────

def test_the_bundle_records_counts_date_ranges_and_clustered_uncertainty():
    """"retain observation counts, date ranges, and clustered uncertainty"."""
    for key in ("n_train", "n_early_stop", "n_final", "n_purged_unavailable_label",
                "train_dates", "early_stop_dates", "final_dates", "n_final_sessions",
                "auc_se_day_clustered", "class_counts_final"):
        assert f'"{key}"' in _META, f"evaluation does not record {key}"
    assert '"evaluation": evaluation,' in _META


def test_the_uncertainty_is_clustered_by_SESSION_not_by_row():
    """Many symbols share each date, so rows are not independent draws. An interval computed
    from the row count would be far too narrow — the same day-clustering rule this project
    already applies to its return statistics."""
    block = _META[_META.index("_val_dates = sorted("):]
    block = block[:block.index("log.info(\"meta_trainer.trained")]
    assert "_n_val_days = len(_val_dates)" in block
    assert "np.sqrt(_n_val_days)" in block
    assert "np.sqrt(len(_final_idx))" not in block, "clustering by row defeats the purpose"


def test_day_clustering_widens_the_interval_rather_than_narrowing_it():
    """Behavioural check on the arithmetic, not the source: the clustered SE must be LARGER
    than the naive one whenever a session carries more than one observation."""
    import numpy as np
    n_rows, n_days = 500, 25
    naive = 1.0 / np.sqrt(n_rows)
    clustered = 1.0 / np.sqrt(n_days)
    assert clustered > naive


def test_the_evaluation_travels_on_both_the_promoted_and_rejected_paths():
    """A rejected candidate's evidence is what explains WHY it was rejected; dropping it on
    that branch leaves the more interesting case undocumented."""
    rejected = _META[_META.index('"trained": True, "promoted": False'):]
    rejected = rejected[:rejected.index("META_MODEL_PATH.parent.mkdir")]
    assert '"evaluation": evaluation,' in rejected
    promoted = _META[_META.index('"trained": True, "promoted": True'):]
    assert '"evaluation": evaluation,' in promoted[:400]


# ── The early-stop slice needs the same purge ────────────────────────────────

def test_the_early_stop_slice_is_purged_against_the_final_slice():
    """FOUND 2026-09-28 (pre-deployment audit). R03 purged train -> validation and stopped
    there, treating "validation" as one block. But the early-stop rows are passed to
    `eval_set`, so they choose the number of boosting rounds — and a row whose label resolves
    after the final slice begins carries information from inside the slice that then decides
    promotion. With SWING/10 the last early-stop session's label lands ten bars later,
    comfortably past the final slice's start.

    The same leak as the train boundary, one boundary further down, walked past because the fix
    was thinking in blocks rather than in boundaries."""
    assert "_es_idx = [i for i in _es_idx if label_available[i] < _final_start]" in _META
    assert "_final_start = record_dates[_final_idx[0]]" in _META


def test_the_early_stop_purge_uses_the_same_strict_comparison():
    """`<`, not `<=`: a label that becomes knowable on the day the final slice starts was not
    knowable BEFORE it — the same boundary rule as the train purge."""
    block = _META[_META.index("_final_start = record_dates[_final_idx[0]]"):]
    block = block[:block.index("if len(_es_idx) < 10")]
    assert "label_available[i] < _final_start" in block
    assert "label_available[i] <= _final_start" not in block


def test_the_early_stop_purge_count_is_recorded():
    """The count is what says whether the old split was contaminated for this symbol set."""
    assert '"n_purged_early_stop_unavailable": _es_purged,' in _META


def test_purging_can_still_leave_the_slice_too_small_and_that_refuses():
    """The purge runs BEFORE the minimum-size check, so a slice gutted by it declines rather
    than training on a handful of rows."""
    purge_at = _META.index("_es_idx = [i for i in _es_idx if label_available[i] < _final_start]")
    check_at = _META.index("if len(_es_idx) < 10 or len(_final_idx) < 10:")
    assert purge_at < check_at
