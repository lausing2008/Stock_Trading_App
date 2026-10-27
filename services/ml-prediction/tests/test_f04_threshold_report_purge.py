"""F04 (2026-10-26 RZY/AI review) — no purge between the threshold and reporting halves.

`train_model()` embargoes every train/early-stop/calibration/test boundary, then split the test
slice in half — threshold selection, then reporting — with NO gap. A threshold row's H-bar label
could therefore mature inside the reporting period, so the threshold-selected precision/accuracy
were not fully out of sample even under `threshold_evaluation_mode == "holdout"`.

Witness: docs/audits/evidence/2026-10-26-rzy-ai-intraday-review/ml-threshold-boundary-witness.json
(40 consecutive sessions, 5-session labels: the last threshold label matured 2026-02-06, after
the first reporting feature date 2026-02-02).

Behavioural, on the real helper `train_model()` calls — not on source text. trainer.py cannot be
imported here (xgboost/lightgbm/torch at module level, see test_cv_single_class_fold.py), so the
function is extracted from the source by AST and executed.
"""
import ast
from pathlib import Path

import pandas as pd
import pytest

_SRC = (Path(__file__).resolve().parents[1] / "src" / "training" / "trainer.py").read_text()


def _load(name):
    node = next(n for n in ast.parse(_SRC).body
                if isinstance(n, ast.FunctionDef) and n.name == name)
    ns: dict = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "<trainer>", "exec"), ns)
    return ns[name]


_threshold_report_bounds = _load("_threshold_report_bounds")


def test_train_model_uses_the_purged_bounds():
    """The helper only matters if the split actually calls it."""
    body = ast.unparse(next(n for n in ast.parse(_SRC).body
                            if isinstance(n, ast.FunctionDef) and n.name == "train_model"))
    assert "_thresh_split, _report_start = _threshold_report_bounds(len(X_test), horizon)" in body
    assert "y_test.iloc[_report_start:]" in body and "preds[_report_start:]" in body


def test_the_witness_case_no_threshold_label_matures_inside_reporting():
    """The audit's construction: 40 consecutive sessions, 5-session labels."""
    sessions = pd.bdate_range("2026-01-05", periods=40)
    horizon = 5
    thr_end, rep_start = _threshold_report_bounds(len(sessions), horizon)

    last_threshold_label_matures = sessions[thr_end - 1 + horizon]
    first_reporting_feature = sessions[rep_start]
    assert last_threshold_label_matures <= first_reporting_feature
    assert rep_start - thr_end == horizon


@pytest.mark.parametrize("n_test,horizon", [(65, 10), (55, 20), (40, 5), (30, 15), (200, 20)])
def test_the_gap_is_the_full_horizon_and_the_halves_partition_the_rest(n_test, horizon):
    thr_end, rep_start = _threshold_report_bounds(n_test, horizon)
    assert rep_start - thr_end == horizon
    n_report = n_test - rep_start
    # As with the original `n // 2`, an odd row goes to the reporting half.
    assert 0 <= n_report - thr_end <= 1, "the gap is taken from the middle, halves stay balanced"


def test_long_at_typical_history_still_reaches_the_holdout_floor():
    """~750 rows -> a 55-row LONG test slice. Purging the reporting half alone would leave 8
    rows (below the 10-row holdout floor) and suppress every such model; the middle gap leaves 18."""
    thr_end, rep_start = _threshold_report_bounds(55, 20)
    assert 55 - rep_start == 18 >= 10


def test_a_slice_that_cannot_afford_the_gap_yields_too_few_report_rows():
    """Never a partial gap presented as clean: the reporting set shrinks (possibly to nothing),
    the caller's 10-row holdout gate fails, and the existing suppressed fallback applies."""
    for n_test in (1, 5, 12, 25):
        thr_end, rep_start = _threshold_report_bounds(n_test, 20)
        assert thr_end >= 1
        assert rep_start <= n_test
        assert n_test - rep_start < 10
        if rep_start < n_test:
            assert rep_start - thr_end == 20
