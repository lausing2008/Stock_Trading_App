"""M13-TRACE — the reported metrics' real denominator, and per-fold class support.

THE DENOMINATOR MISMATCH, measured in production 2026-10-02:

    MU GROWTH   stored n_test 27   rows behind the reported metrics: 14
    MU LONG     stored n_test 21   rows behind the reported metrics: 11

`n_test` is `len(X_test)` BEFORE the test block is subdivided into a threshold-selection half
and a reporting half. The stored AUC, precision and recall come from the reporting half. So
every statement of the form "n_test is small" understated how small the actual evidence was, by
roughly half.

NARROWED 2026-10-02, twice:

* "Roughly half" is a property of the HOLDOUT branch only. On the `in_sample_fallback`
  branch the metrics are computed over the whole test slice, so `n_test` is their real
  denominator and nothing is understated. Nor does it transfer to artifacts written by
  earlier versions of trainer.py, whose split semantics have to be read off the code
  version that produced them. `n_metric_rows` is the denominator either way.
* Both symbols currently store 755 daily bars. That does NOT establish that history was
  sufficient — it establishes that filtering and allocation also matter. It says nothing
  about how many of those bars existed, or were usable, at the time of each historical fit.
  M13-BASE's ledger is what measures the rest; see test_m13_base_training_ledger.py.

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


def _assigned_number(name: str) -> float:
    """The VALUE assigned to `name` in trainer.py, parsed — not the text of the assignment.

    T401: a source-text assertion on the assignment's TEXT keeps passing when the code
    subtracts something from the value on the same line, because the substring survives.
    A threshold is a number, so this reads the number.

    (Written without quoting the anti-pattern: the ratchet scans this file too, and an
    example embedded in a docstring is indistinguishable from a real offender. The same
    self-match caught me once before, in the credential-leak test that matched its own
    explanation of why masking is avoided.)
    """
    for node in ast.walk(ast.parse(_TRAINER)):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} is not assigned at all in trainer.py")


def _compared_numbers(attr: str, within: str | None = None) -> set[float]:
    """Every numeric literal `attr` is compared against, parsed from the comparisons.

    Scoped to one function when `within` is given: `cv_auc_mean` is compared against two
    different numbers for two different purposes — 0.52 SUPPRESSES an artifact, 0.55 only
    logs that it is near-random — and a test that pooled them would pass if either moved
    to the other's value.
    """
    tree = ast.parse(_TRAINER)
    if within is not None:
        tree = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == within)
    out: set[float] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        name = (left.id if isinstance(left, ast.Name)
                else left.attr if isinstance(left, ast.Attribute) else None)
        if name != attr:
            continue
        for comp in node.comparators:
            if isinstance(comp, ast.Constant) and isinstance(comp.value, (int, float)):
                out.add(float(comp.value))
    return out


def test_no_suppression_threshold_was_changed_by_this_trace():
    """Instrumentation only. Loosening a gate to raise the usable-model count is the one
    response this evidence must not produce.

    Asserted on the parsed VALUES rather than the source text: the text form of this test
    pushed the T401 ratchet from 180 to 182 when it was first written, and would have gone
    on passing had the numbers themselves been edited."""
    assert _compared_numbers("cv_auc_mean", within="_compute_oos_suppression") == {0.52}, (
        "the CV AUC SUPPRESSION floor moved, or gained a second comparison")
    assert _compared_numbers("cv_auc_mean", within="train_model") == {0.55}, (
        "the near-random WARNING threshold moved; it suppresses nothing and must not be "
        "confused with the 0.52 gate")
    assert _assigned_number("_MIN_REPORT_ROWS") == 10, "the reporting floor is unchanged"
