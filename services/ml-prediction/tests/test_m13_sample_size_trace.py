"""M13-TRACE — the reported metrics' real denominator, and per-fold class support.

THE DENOMINATOR MISMATCH, measured in production 2026-10-02:

    MU GROWTH   stored n_test 27   rows behind the reported metrics: 14
    MU LONG     stored n_test 21   rows behind the reported metrics: 11

`n_test` is `len(X_test)` BEFORE the test block is subdivided into a threshold-selection half
and a reporting half. The stored AUC, precision and recall come from the reporting half. So
every statement of the form "n_test is small" understated how small the actual evidence was, by
roughly half.

Both symbols have 755 stored daily bars, so this is not missing history.

These are source-level checks: exercising the real split needs a full fit with xgboost and a
price history, which is a training run rather than a unit test. What they pin is that the
counts are recorded and that the legacy field keeps its meaning.
"""
import ast
import pathlib

_TRAINER = (pathlib.Path(__file__).resolve().parents[1] / "src" / "training"
            / "trainer.py").read_text()


def _metrics_keys() -> set[str]:
    """Keys of the metrics dict literal that carries `n_test`, parsed rather than grepped."""
    tree = ast.parse(_TRAINER)
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = [k.value for k in node.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            if "n_test" in keys and "n_train" in keys:
                return set(keys)
    raise AssertionError("the metrics dict was not found")


def test_the_actual_metric_denominator_is_recorded():
    """`n_metric_rows` is the number of rows the stored auc/precision/recall were computed on.
    Without it a reader has no way to know the figures rest on half the rows `n_test` implies."""
    assert "n_metric_rows" in _metrics_keys()


def test_both_halves_of_the_test_block_are_recorded_separately():
    """Threshold selection and reporting are different populations doing different jobs;
    one number cannot describe both."""
    keys = _metrics_keys()
    assert "n_threshold_rows" in keys
    assert "n_report_rows" in keys


def test_the_legacy_n_test_field_keeps_its_meaning():
    """Existing artifacts and consumers depend on `n_test` being the pre-subdivision count.
    Redefining it in place would silently change every stored comparison."""
    assert "n_test" in _metrics_keys()
    i = _TRAINER.index('"n_test": int(len(X_test)),')
    assert "LEGACY SEMANTICS PRESERVED" in _TRAINER[i - 400:i]


def test_class_support_behind_the_metrics_is_recorded():
    """Ten rows with one positive and ten with five are not equivalent evidence, and an AUC
    cannot distinguish them after the fact."""
    assert "metric_class_support" in _metrics_keys()


# ── CV folds ──────────────────────────────────────────────────────────────────────────────────

def test_every_fold_records_counts_and_class_support():
    """An absent `cv_auc_mean` can mean CV never ran, or that every fold was single-class and
    skipped. The code permits both and the stored metrics could not tell them apart — which is
    exactly the ambiguity behind CM_long's missing diagnostics."""
    keys = _metrics_keys()
    assert "cv_folds" in keys
    assert "cv_folds_skipped" in keys


def test_a_skipped_fold_records_WHY_it_was_skipped():
    assert '"skipped": "single_class_training_slice"' in _TRAINER


def test_a_fold_that_trains_but_yields_no_auc_is_distinguished_from_a_skipped_one():
    """A single-class VALIDATION slice still trains; it just contributes no AUC. Collapsing
    that into 'skipped' would misreport how much cross-validation actually happened."""
    assert '"auc_usable"' in _TRAINER
    assert "cv_folds_auc_usable" in _metrics_keys()


def test_no_suppression_threshold_was_changed_by_this_trace():
    """Instrumentation only. Loosening a gate to raise the usable-model count is the one
    response this evidence must not produce."""
    assert "cv_auc_mean < 0.52" in _TRAINER or "0.52" in _TRAINER
    assert "_MIN_REPORT_ROWS = 10" in _TRAINER, "the reporting floor is unchanged"
