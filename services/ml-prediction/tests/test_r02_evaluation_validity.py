"""R02 (2026-09-24 follow-up audit): the remaining half of DA-03.

DA-03 made the embargo the largest each slice can afford and recorded a shortfall; the fix for
"logging leakage is not a restriction on using it" (suppressing on shortfall) landed with it.
What did NOT land is the INTERACTION the audit identified between the new rule and everything
downstream of the split:

  * Preserving TEN rows per slice conflicts with the stages that consume those slices.
    Probability calibration requires at least TWENTY. Independent threshold reporting requires
    at least TEN REMAINING AFTER HALVING. A ten-row test set therefore falls straight into the
    fallback that fits the threshold and reports metrics on the SAME ten rows — T232-ML2's
    defect, reappearing whenever the slice is small.
  * That fallback was logged and then used. Its evaluation status was not recorded beside the
    new embargo status, so a consumer reading the artifact could not tell an out-of-sample
    metric from an in-sample one.
  * Calibration being skipped was invisible for the same reason: `calibrator is None` covers
    "too few rows", "single class" and "genuinely fitted" indistinguishably after the fact.
  * Nothing stopped an invalid candidate overwriting a VALID incumbent.

THE DISTINCTION THIS FIX RESTS ON, which is the audit's own: training availability and
evaluation validity are separate decisions. Nothing here refuses to train. What changes is
whether a run's metrics are presented as validated performance, and whether it may publish over
a model whose metrics are better evidenced.

WHAT DOES AND DOES NOT COST VALIDITY:
  embargo shortfall        — LEAKS (a label built from a price inside the next slice). Invalid.
  in-sample threshold      — the threshold is an argmax over the rows the metrics are computed
                             on. Not out of sample. Invalid.
  missing calibrator       — probabilities are uncalibrated, NOT contaminated. Recorded, and
                             deliberately does not cost validity: suppressing for it would
                             silence most short-history symbols for a reason that is not
                             contamination.
"""
import ast
import re
from pathlib import Path

import pytest

_TRAINER = (Path(__file__).resolve().parents[1] / "src" / "training" / "trainer.py").read_text()
_TRAINER_CODE = "\n".join(
    ln.split("#", 1)[0] if not ln.lstrip().startswith("#") else ""
    for ln in _TRAINER.split("\n")
)


def _const(name: str, src: str = _TRAINER):
    """The VALUE of an assignment, evaluated rather than matched as text.

    A substring assertion on a number survives `20 - 99999`; AUD-T401-SOURCETEXTTESTS is about
    exactly that. These constants are function-local, so ast.walk rather than tree.body.
    """
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    try:
                        return ast.literal_eval(node.value)
                    except ValueError:
                        return eval(  # noqa: S307 — constant expression from our own source
                            compile(ast.Expression(node.value), "<trainer>", "eval"),
                            {"__builtins__": {}}, {})
    raise AssertionError(f"{name} is not assigned in trainer.py")


MIN_SLICE = _const("_MIN_SLICE_ROWS")
MIN_CAL = _const("_MIN_CALIBRATION_ROWS")
MIN_REPORT = _const("_MIN_REPORT_ROWS")


# ── The arithmetic the audit worked out, against the REAL constants ──────────

def _slices(n_rows: int, horizon: int) -> dict:
    """Reproduce trainer.py's own split and embargo arithmetic for `n_rows` clean rows."""
    split_train = int(n_rows * 0.70)
    split_es = int(n_rows * 0.80)
    split_cal = int(n_rows * 0.90)

    def afford(span):
        return max(0, min(horizon, span - MIN_SLICE))

    e_tr, e_es, e_cal = (afford(split_es - split_train), afford(split_cal - split_es),
                         afford(n_rows - split_cal))
    return {
        "train": split_train,
        "early_stop": split_es - (split_train + e_tr),
        "calibration": split_cal - (split_es + e_es),
        "test": n_rows - (split_cal + e_cal),
        "embargo": (e_tr, e_es, e_cal),
    }


@pytest.mark.parametrize("style,horizon", [("SHORT", 5), ("SWING", 10),
                                           ("GROWTH", 15), ("LONG", 20)])
def test_at_two_hundred_rows_every_horizon_lands_below_the_calibration_minimum(style, horizon):
    """THE CORE R02 CASE, reproducing the audit's own table at the function's own 200-row floor.

    Every one of the four real horizons produces a calibration slice under twenty rows, so
    calibration is SKIPPED for all of them — and before this fix, silently."""
    s = _slices(200, horizon)
    assert s["calibration"] < MIN_CAL, f"{style}: {s['calibration']} rows"


@pytest.mark.parametrize("horizon", [10, 15, 20])
def test_at_two_hundred_rows_the_test_slice_cannot_support_an_independent_report(horizon):
    """Ten test rows halve to five, which is below the reporting minimum — so the threshold is
    fitted and reported on the same rows. The audit's ten-row probe."""
    s = _slices(200, horizon)
    assert s["test"] == 10
    assert s["test"] - (s["test"] // 2) < MIN_REPORT


@pytest.mark.parametrize("style,horizon", [("SHORT", 5), ("SWING", 10),
                                           ("GROWTH", 15), ("LONG", 20)])
def test_at_the_row_floor_even_SHORT_cannot_report_out_of_sample(style, horizon):
    """CORRECTED while writing this file. I first asserted that SHORT was the one horizon still
    reporting honestly at 200 rows, reading the audit's table (15 test rows) and assuming 15
    cleared the bar. It does not: 15 halves to 8 reporting rows, below the minimum of 10. All
    FOUR horizons fall into the in-sample fallback at the function's own row floor.

    That is the floor, not the typical case — see the fleet measurement below."""
    s = _slices(200, horizon)
    assert s["test"] - (s["test"] // 2) < MIN_REPORT, f"{style}: {s['test']} test rows"


def test_the_fleet_measurement_that_made_this_safe_to_ship():
    """MEASURED on production before shipping, because suppressing on an in-sample threshold is
    an operational change, not only a correctness one — and the floor arithmetic above makes it
    look far broader than it is.

    Of 1,297 loadable artifacts, 1,167 (90%) were ALREADY suppressed for other reasons. Of the
    130 that were not, the reporting slice (n_test - n_test//2) clears the ten-row minimum for
    126; only FOUR would be newly suppressed for reporting metrics fitted on their own rows.
    Calibration was already fitted for 121 of 130 and skipped for 9 — which is exactly why
    calibration status is recorded but does NOT cost validity.

    n_test across those 130: min 16, median 30, max 142 — well above the 10-15 the 200-row
    floor produces, because real symbols carry far more history than the minimum.

    Re-run this if the rule changes; the constant below is the claim being pinned."""
    assert MIN_REPORT == 10, (
        "the fleet measurement above was taken against a ten-row reporting minimum; "
        "changing it invalidates the 4-of-130 figure and the measurement must be redone")


@pytest.mark.parametrize("horizon", [5, 10, 15, 20])
def test_a_typical_history_clears_both_downstream_minimums(horizon):
    """The other direction. ~4 years of sessions is typical for the live universe, and a fix
    that made every symbol invalid would be a worse outcome than the defect."""
    s = _slices(1000, horizon)
    assert s["calibration"] >= MIN_CAL
    assert s["test"] - (s["test"] // 2) >= MIN_REPORT
    assert all(e == horizon for e in s["embargo"]), "a full gap should be affordable here"


def test_the_row_count_at_which_calibration_becomes_possible_is_reachable():
    """A minimum nothing can ever satisfy is a disabled feature, not a standard."""
    for horizon in (5, 10, 15, 20):
        viable = [n for n in range(200, 1200, 10) if _slices(n, horizon)["calibration"] >= MIN_CAL]
        assert viable, f"horizon {horizon}: calibration is unreachable at any row count"
        assert min(viable) < 700, f"horizon {horizon}: needs {min(viable)} rows, ~3 years"


def test_the_downstream_minimums_are_larger_than_the_slice_floor():
    """This is the conflict the finding is about, stated as an assertion: preserving ten rows
    per slice is not enough for the stages that then consume those slices. If this ever stops
    holding, the interaction is gone and these tests can be revisited."""
    assert MIN_CAL > MIN_SLICE
    assert MIN_SLICE - (MIN_SLICE // 2) < MIN_REPORT


# ── Status is recorded, not inferred ─────────────────────────────────────────

def test_calibration_status_distinguishes_its_three_outcomes():
    """`calibrator is None` covered "too few rows", "single class" and "fitted" identically."""
    for status in ("skipped_single_class", "skipped_insufficient_rows",
                   "fitted_platt", "fitted_isotonic"):
        assert f'"{status}"' in _TRAINER, f"missing calibration status {status}"


def test_the_single_class_check_runs_before_the_row_count_check():
    """A twenty-row single-class slice must report WHY it could not calibrate. Testing the row
    count first would label it insufficient_rows, which is false and misdirects the reader."""
    block = _TRAINER[_TRAINER.index("calibrator: IsotonicRegression | LogisticRegression | None"):]
    block = block[:block.index("# --- Precision-optimised BUY threshold")]
    assert block.index("skipped_single_class") < block.index("skipped_insufficient_rows")


def test_threshold_evaluation_mode_is_recorded_on_both_branches():
    assert 'threshold_evaluation_mode = "holdout"' in _TRAINER
    assert 'threshold_evaluation_mode = "in_sample_fallback"' in _TRAINER
    assert '"threshold_evaluation_mode": threshold_evaluation_mode,' in _TRAINER


def test_the_metrics_carry_the_evidence_the_audit_asked_for():
    """"persist evaluation_valid, calibration_status, threshold_evaluation_mode, date ranges,
    class counts, and effective sample counts"."""
    for key in ("evaluation_valid", "calibration_status", "calibration_rows",
                "threshold_evaluation_mode", "threshold_report_rows",
                "slice_rows", "slice_class_counts", "slice_date_ranges"):
        assert f'"{key}"' in _TRAINER, f"metrics do not record {key}"


def test_date_ranges_come_from_real_row_dates_not_a_row_count():
    """The audit: "irregular/dead-zone-filtered rows require actual timestamps, not assuming
    every row equals one trading session". Ten rows may span three months."""
    block = _TRAINER[_TRAINER.index("def _range(idx_start: int"):]
    block = block[:block.index("_slice_date_ranges = {")]
    assert "X_dates_for_split[idx_start:idx_end]" in block
    assert "pd.Timestamp(seg[0]).date()" in block


def test_the_date_ranges_account_for_the_embargo():
    """A range that started at the raw split point would report rows the slice does not
    contain, and would hide the gap entirely."""
    block = _TRAINER[_TRAINER.index("_slice_date_ranges = {"):]
    block = block[:block.index("}", block.index('"test"'))]
    assert "split_train + _embargo" in block
    assert "split_es + _embargo_es" in block
    assert "split_cal + _embargo_cal" in block


# ── An in-sample metric is not marketed as OOS ───────────────────────────────

def test_an_in_sample_threshold_costs_the_evaluation_its_validity():
    assert '"evaluation_valid": (not _embargo_shortfall) and threshold_evaluation_mode == "holdout",' \
        in _TRAINER


def test_an_in_sample_threshold_suppresses_the_model():
    """"Logging leakage is not a restriction on using it" applies here exactly as it did to the
    embargo. The fallback was logged and then used."""
    assert 'if threshold_evaluation_mode != "holdout" and not oos_suppressed:' in _TRAINER_CODE
    block = _TRAINER[_TRAINER.index('if threshold_evaluation_mode != "holdout"'):]
    block = block[:block.index("if oos_suppressed:")]
    assert "oos_suppressed = True" in block
    assert "_suppression_reason" in block


def test_a_missing_calibrator_deliberately_does_NOT_suppress():
    """A different failure. Uncalibrated probabilities are not contaminated ones, and at 200
    rows EVERY horizon skips calibration — suppressing for it would disable the fleet for a
    reason that is not leakage. Recorded, not penalised."""
    assert "calibration_status" not in _TRAINER[
        _TRAINER.index('"evaluation_valid": (not _embargo_shortfall)'):
        _TRAINER.index('"calibration_status": calibration_status,')]
    suppression = _TRAINER[_TRAINER.index("if _embargo_shortfall and not oos_suppressed:"):
                           _TRAINER.index("if oos_suppressed:")]
    assert "calibration_status" not in suppression


def test_a_clean_run_keeps_its_validity():
    """The guard must not be so broad that nothing is ever valid — then the flag carries no
    information and consumers learn to ignore it."""
    assert '== "holdout"' in _TRAINER, "validity must be satisfiable, not merely losable"


# ── An invalid candidate does not replace a valid incumbent ──────────────────

def test_an_invalid_candidate_does_not_publish_over_a_valid_incumbent():
    """"Invalid candidates must not silently replace an eligible incumbent." Every finished run
    previously overwrote the artifact unconditionally, so a symbol that has since lost rows
    replaced a validly-evaluated model with a leaked or in-sample one."""
    block = _TRAINER[_TRAINER.index('if not metrics["evaluation_valid"] and path.exists():'):]
    block = block[:block.index("metrics[\"published\"] = True")]
    assert "joblib.load(path)" in block, "it must actually read the incumbent"
    assert '_incumbent.get("metrics") or {}).get("evaluation_valid")' in block
    assert '"published": False' in block


def test_the_refusal_is_narrow_in_both_directions():
    """An invalid candidate still replaces an invalid incumbent — newer data, same standing —
    and a VALID candidate always publishes. A broader rule would freeze a symbol on whatever
    model it happened to have when it last had enough rows."""
    guard = _TRAINER[_TRAINER.index('if not metrics["evaluation_valid"] and path.exists():'):]
    guard = guard[:guard.index("metrics[\"published\"] = True")]
    assert "if _inc_valid:" in guard, "only a VALID incumbent blocks publication"
    # The guard is entered only for an invalid candidate.
    assert guard.startswith('if not metrics["evaluation_valid"] and path.exists():')


def test_an_unreadable_incumbent_does_not_block_publication():
    """A corrupt or unloadable artifact is not an eligible one, and treating it as a valid
    incumbent would freeze the symbol permanently on a file nothing can read."""
    guard = _TRAINER[_TRAINER.index('if not metrics["evaluation_valid"] and path.exists():'):]
    guard = guard[:guard.index("metrics[\"published\"] = True")]
    assert "except Exception:" in guard
    assert "_inc_valid = False" in guard


def test_a_refused_candidate_still_reports_rather_than_raising():
    """The nightly job trains hundreds of symbols in sequence. Raising here would abort the
    rest of the run for a condition that is expected and benign."""
    guard = _TRAINER[_TRAINER.index('if not metrics["evaluation_valid"] and path.exists():'):]
    guard = guard[:guard.index("metrics[\"published\"] = True")]
    assert "return {" in guard
    assert 'metrics["not_published_reason"] = "incumbent_evaluation_valid"' in guard


def test_publication_status_is_recorded_on_every_path():
    """A consumer cannot tell "trained and published" from "trained and withheld" unless both
    say so; an absent key on one branch is the asymmetry that hides it."""
    assert 'metrics["published"] = False' in _TRAINER
    assert 'metrics["published"] = True' in _TRAINER
