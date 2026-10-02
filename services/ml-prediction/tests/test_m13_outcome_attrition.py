"""M13 — the outcome-row attrition ledger.

WHY A LEDGER AND NOT A LOG LINE. Before AUD-ML3-OUTCOMEDEDUP was found, **490 of 548 artifacts
had `n_outcome_rows == 0`** and the 58 non-zero ones had a MEDIAN OF 6 against 37-43 rows
actually loaded. The only number recorded was the final one, so a 43 -> 6 collapse and a symbol
that genuinely had 6 looked identical — and a bug that made an entire feature inert read as
"this symbol has little history".

A surviving count cannot distinguish data that never existed, data correctly excluded, and data
wrongly excluded. Those need different responses, so they need different records.
"""
import pathlib
import sys

import pytest

_SHARED = pathlib.Path(__file__).resolve().parents[3] / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from metrics.attrition import AttritionLedger  # noqa: E402


def test_a_stage_that_loses_rows_without_a_reason_records_them_as_unexplained():
    """The failure this class exists to prevent. A quiet discrepancy is unactionable; a named
    `unexplained` bucket points at the stage that lost them."""
    led = AttritionLedger("X")
    st = led.record("dedup", rows_in=40, rows_out=6)
    assert st.dropped == {"unexplained": 34}
    assert led.unexplained == 34


def test_an_unexplained_bucket_DEGRADES_reconciliation():
    """CORRECTED. `record()` closes the gap by naming it, which makes the arithmetic balance —
    so without this the ledger would reconcile cleanly while concealing rows nobody can account
    for. Balancing the books by inventing a line item is not reconciliation."""
    led = AttritionLedger("X")
    led.record("dedup", rows_in=40, rows_out=6)
    assert led.unexplained == 34
    assert led.reconciles is False, "an unexplained loss must not read as reconciled"


def test_the_module_states_which_exclusions_it_does_NOT_cover():
    """The register names label maturity, purge, calibration and promotion. Only the
    outcome-augmentation path is instrumented, and claiming otherwise would be the same kind of
    overreach the ledger exists to catch."""
    src = (pathlib.Path(__file__).resolve().parents[3] / "shared" / "metrics"
           / "attrition.py").read_text()
    # Whitespace-normalised: the sentence wraps, and a line break should not decide whether a
    # documented limitation counts as documented.
    flat = " ".join(src.split())
    assert "purge, calibration and promotion exclusions are NOT measured here" in flat


def test_a_reconciling_ledger_has_no_unexplained_rows():
    led = AttritionLedger("X")
    led.record("dedup", rows_in=40, rows_out=6, dropped={"overlaps_training_window": 34})
    assert led.unexplained == 0 and led.reconciles is True


def test_the_chain_must_compose_not_just_each_stage_balance():
    """Each stage can balance internally while the chain is broken — a stage receiving more
    than the previous one passed on means one was skipped or rows were double-counted."""
    led = AttritionLedger("X")
    led.record("loaded", rows_in=40, rows_out=40)
    led.record("dedup", rows_in=99, rows_out=99)          # internally fine, chain broken
    assert all(s.reconciles for s in led.stages)
    assert led.reconciles is False


def test_it_reproduces_the_historical_collapse_and_names_where_it_happened():
    """The measured case: 43 loaded, 6 survived. The ledger says WHICH stage and WHY, which is
    exactly what the single final count could not."""
    led = AttritionLedger("AAPL/SWING")
    led.record("loaded", rows_in=43, rows_out=43)
    led.record("min_sample", rows_in=43, rows_out=43)
    led.record("shared_features", rows_in=43, rows_out=43)
    led.record("dedup", rows_in=43, rows_out=6,
               dropped={"overlaps_training_window": 37})
    led.record("min_after_dedup", rows_in=6, rows_out=6)
    assert led.reconciles is True
    assert led.biggest_loss() == ("dedup", "overlaps_training_window", 37)
    assert led.survival_rate() == pytest.approx(6 / 43)


def test_an_empty_pipeline_has_no_survival_rate_rather_than_zero():
    """"Received nothing" and "discarded everything received" are different facts, and only one
    of them is evidence about the pipeline."""
    led = AttritionLedger("X")
    assert led.survival_rate() is None
    led.record("loaded", rows_in=0, rows_out=0)
    assert led.survival_rate() is None


def test_total_exclusion_is_recorded_with_its_reason_not_as_silence():
    """The 490-of-548 case. Zero survivors must still say WHY."""
    led = AttritionLedger("X")
    led.record("loaded", rows_in=12, rows_out=12)
    led.record("min_sample", rows_in=12, rows_out=0, dropped={"below_min_outcomes": 12})
    assert led.reconciles is True
    assert led.survival_rate() == 0.0
    assert led.biggest_loss() == ("min_sample", "below_min_outcomes", 12)


def test_negative_counts_are_rejected():
    led = AttritionLedger("X")
    with pytest.raises(ValueError):
        led.record("bad", rows_in=-1, rows_out=0)
    with pytest.raises(ValueError):
        led.record("bad", rows_in=5, rows_out=0, dropped={"r": -5})


# ── the trainer actually uses it ──────────────────────────────────────────────────────────────

_TRAINER = (pathlib.Path(__file__).resolve().parents[1] / "src" / "training"
            / "trainer.py").read_text()


def _recorded_stage_names() -> set[str]:
    """Stage names passed to `_attrition.record`, parsed rather than grepped.

    A contiguous-substring check fails on a call wrapped across lines — and would then report a
    recorded stage as missing, which is a false alarm about instrumentation that exists.
    """
    import ast
    tree = ast.parse(_TRAINER)
    names = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "record"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "_attrition"
                and node.args and isinstance(node.args[0], ast.Constant)):
            names.add(node.args[0].value)
    return names


def test_the_trainer_records_every_stage_that_can_drop_outcome_rows():
    """Each of these is a real exit in the augmentation path. One missing means rows can
    disappear with no record — the original defect."""
    recorded = _recorded_stage_names()
    for stage in ("loaded", "min_sample", "shared_features", "dedup", "min_after_dedup"):
        assert stage in recorded, f"{stage!r} is not recorded; found {sorted(recorded)}"


def test_the_trainer_emits_the_ledger_even_when_nothing_survived():
    """Emitting only on success would lose exactly the 490 artifacts worth explaining."""
    assert "if _attrition.stages:" in _TRAINER
    assert 'log.info("train.outcome_attrition"' in _TRAINER


def test_the_emitted_record_carries_the_reconciliation_flag():
    """An unreconciled ledger is a defect in the instrumentation, and must be visible as one
    rather than silently balanced."""
    start = _TRAINER.index('log.info("train.outcome_attrition"')
    block = _TRAINER[start:start + 500]
    for field in ("reconciles=", "unexplained=", "biggest_loss=", "survival_rate="):
        assert field in block, f"{field} missing from the emitted attrition record"
