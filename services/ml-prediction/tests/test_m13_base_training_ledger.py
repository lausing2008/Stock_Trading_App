"""M13-BASE — the base training ledger: stored bars to reported rows.

The augmentation ledger (M13) measures one branch. It cannot say why a fit reported eleven
rows, because the rows it tracks were never in the main lineage. This ledger measures the
main lineage as a chain of reconciled stages:

    loaded bars -> completed/unique bars -> required features -> available labels
    -> dead-zone selection -> deduplication -> split allocation -> embargo
    -> threshold/report sets

Two acceptance requirements are tested here directly, because both are the kind of claim
that is easy to assert and hard to keep true:

1. **Instrumentation leaves training behaviour unchanged.** `build_features` is called
   twice on the same frame, once with a trace and once without, and the returned rows,
   labels, returns and index must be identical. A source-level check backs it up: nothing
   in `train_model` that selects rows, builds weights or sets a split boundary may read the
   ledger or the trace.
2. **The ledger explains empty and aborted fits, not only saved artifacts.** Every exit
   from `train_model` that produces no model is checked for an outcome, and the boundary
   decorator is exercised with a function that raises.

Two measurement rules are also pinned, because getting either wrong produces a ledger that
looks healthy and is wrong:

* `dropped` is a first-match attribution and reconciles exactly. `diagnostics` counts every
  reason that applied to a row and never reconciles against anything. A row can be missing
  a feature AND have no label AND sit in the dead zone.
* Weighted augmentation is reported beside the base counts, never inside them.
"""
import ast
import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
_TRAINER = (_SRC / "training" / "trainer.py").read_text()
_BUILDER = (_SRC / "features" / "builder.py").read_text()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "shared"))
from metrics.base_ledger import (  # noqa: E402
    STAGE_ORDER, BaseTrainingLedger, LedgerError, SafeLedger, Stage,
    eligible_cohort_for_window, run_with_ledger,
)

# The trainer itself cannot be imported here — it pulls xgboost, lightgbm and optuna, none of
# which are installed for unit tests. That is exactly why the ledger's mechanics live in
# `shared/metrics/base_ledger.py`: the behaviour is exercised directly, and the trainer's own
# wiring is checked against its parsed source.


def _fn_source(name: str, src: str = _TRAINER) -> str:
    """Source of one function, sliced on real `def` boundaries via the AST.

    Never by string search: an earlier version of this technique ran 1,300 lines past its
    target and would have reported that exits were gated by entry caps.
    """
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"{name} not found")


# ---------------------------------------------------------------------------------------
# Acceptance requirement 1: the instrumentation does not change what is trained on
# ---------------------------------------------------------------------------------------

def _price_frame(n: int = 420, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.02, n)))
    ts = pd.bdate_range("2023-01-02", periods=n)
    return pd.DataFrame({
        "ts": ts,
        "open": close * (1 + rng.normal(0, 0.002, n)),
        "high": close * (1 + abs(rng.normal(0, 0.006, n))),
        "low": close * (1 - abs(rng.normal(0, 0.006, n))),
        "close": close,
        "adj_close": close,
        "volume": rng.integers(1_000_000, 5_000_000, n).astype(float),
    })


def test_build_features_returns_identical_rows_with_and_without_a_trace():
    """THE ACCEPTANCE TEST. Same inputs, same selected rows, labels and returns.

    Run against the real `build_features`, not a stub: the point is that the instrumented
    code path produces the same training set, and a stub would only prove that a stub does.
    """
    from src.features.builder import build_features

    df = _price_frame()
    plain = build_features(df.copy(), horizon=5, label_threshold=0.01)
    trace: dict = {}
    traced = build_features(df.copy(), horizon=5, label_threshold=0.01, trace=trace)

    X0, y0, r0 = plain
    X1, y1, r1 = traced
    pd.testing.assert_frame_equal(X0, X1)
    pd.testing.assert_series_equal(y0, y1)
    pd.testing.assert_series_equal(r0, r1)
    assert list(X0.index) == list(X1.index), "the surviving row IDENTITIES must match, not just the count"
    assert trace, "the trace was requested and must have been filled"


def test_the_trace_decomposition_describes_the_rows_that_actually_came_back():
    """A stage chain that does not end where the mask ended is a wrong ledger, not a near one."""
    from src.features.builder import build_features

    df = _price_frame()
    trace: dict = {}
    X, _, _ = build_features(df.copy(), horizon=5, label_threshold=0.01, trace=trace)

    assert trace["stage_chain_matches_mask"] is True
    assert trace["selected_rows"] == len(X)
    stages = trace["stages"]
    assert [s["stage"] for s in stages] == [
        "required_features", "available_labels", "dead_zone_selection"]
    # Chained: each stage starts where the previous one ended.
    assert stages[0]["rows_in"] == len(df)
    for prev, nxt in zip(stages, stages[1:]):
        assert prev["rows_out"] == nxt["rows_in"]
    assert stages[-1]["rows_out"] == len(X)
    # And each stage's single disjoint reason accounts for its whole loss.
    for st in stages:
        assert sum(st["dropped"].values()) == st["rows_in"] - st["rows_out"]


def test_dead_zone_exclusions_are_not_losses_of_unlabelled_rows():
    """A wider dead zone must move rows from `dead_zone_selection`, not from `available_labels`.

    This is the distinction the whole stage split exists for: "no label was computable" and
    "a label was computable and the move was too small to use" are different facts about
    the symbol, and only the second is a tuning decision.
    """
    from src.features.builder import build_features

    df = _price_frame()
    narrow: dict = {}
    wide: dict = {}
    build_features(df.copy(), horizon=5, label_threshold=0.005, trace=narrow)
    build_features(df.copy(), horizon=5, label_threshold=0.05, trace=wide)

    def _stage(tr, name):
        return next(s for s in tr["stages"] if s["stage"] == name)

    assert _stage(narrow, "available_labels")["rows_out"] == _stage(wide, "available_labels")["rows_out"]
    assert (_stage(wide, "dead_zone_selection")["dropped"]["inside_dead_zone"]
            > _stage(narrow, "dead_zone_selection")["dropped"]["inside_dead_zone"])


def test_the_ledger_is_never_read_by_anything_that_selects_rows_or_sets_a_boundary():
    """Source-level half of acceptance requirement 1.

    A test that runs one fit twice proves the fit it ran. This proves the property for every
    fit: no assignment to a training variable may have the ledger or the trace on its right
    hand side. `_bf_trace` is allowed to reach the cohort and the metrics dict, which are
    recorded, not trained on.
    """
    tree = ast.parse(_TRAINER)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "train_model")
    training_names = {
        "X", "y_dir", "y_ret", "X_train", "X_es", "X_cal", "X_test",
        "y_train", "y_es", "y_cal", "y_test", "y_test_thresh", "y_test_report",
        "train_weights", "_fit_X", "_fit_y", "_fit_w", "split_train", "split_es",
        "split_cal", "_embargo", "_embargo_es", "_embargo_cal", "label_threshold",
        "buy_threshold", "preds", "mask", "X_row_dates", "scaler", "model",
    }
    offenders = []
    for node in ast.walk(fn):
        if not isinstance(node, (ast.Assign, ast.AugAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = {t.id for t in targets if isinstance(t, ast.Name)}
        if not (names & training_names):
            continue
        read = {n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)}
        if read & {"_led", "_ledger", "_bf_trace"}:
            offenders.append(f"line {node.lineno}: {sorted(names)} reads the ledger")
    assert not offenders, offenders


def test_the_trace_is_an_out_parameter_and_cannot_fail_a_build():
    """build_features must return its rows even when the instrumentation raises."""
    body = _fn_source("build_features", _BUILDER)
    trace_call = body[body.index("if trace is not None:"):]
    assert "try:" in trace_call and "except Exception" in trace_call
    # The failure is recorded, not swallowed silently.
    assert 'trace["error"]' in trace_call
    # And the return is outside the guarded block, unchanged.
    assert body.rstrip().endswith("return X[mask], y_dir[mask], fwd_ret[mask]")


def test_a_ledger_step_that_raises_does_not_reach_the_caller():
    """The proxy, exercised — a strict ledger must not be able to cost a model."""
    reported = []
    led = BaseTrainingLedger(subject="T", style="SWING")
    safe = SafeLedger(led, on_error=lambda step, exc: reported.append(step))
    safe.record("loaded_bars", rows_in=10, rows_out=10)
    # Recording the same stage twice raises on the real ledger...
    with pytest.raises(LedgerError):
        led.record("loaded_bars", rows_in=10, rows_out=10)
    # ...and returns None through the proxy, leaving the ledger usable.
    assert safe.record("loaded_bars", rows_in=10, rows_out=10) is None
    assert reported == ["record"], "the failed step is reported, not silently dropped"
    assert safe.outcome == "in_progress", "attribute reads must pass straight through"
    # A reporter that itself fails must not become the second failure.
    noisy = SafeLedger(led, on_error=lambda step, exc: (_ for _ in ()).throw(RuntimeError("boom")))
    assert noisy.record("loaded_bars", rows_in=1, rows_out=1) is None


# ---------------------------------------------------------------------------------------
# Acceptance requirement 2: empty and aborted fits are explained
# ---------------------------------------------------------------------------------------

def test_every_no_model_exit_from_train_model_records_an_outcome():
    """Each `skipped` return must be preceded by a ledger outcome in the same block."""
    tree = ast.parse(_TRAINER)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "train_model")

    def _is_skip_return(node) -> bool:
        return (isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
                and any(isinstance(k, ast.Constant) and k.value == "skipped"
                        for k in node.value.keys))

    def _finishes(stmts) -> bool:
        for st in stmts:
            for sub in ast.walk(st):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "finish"):
                    return True
        return False

    checked = 0
    for node in ast.walk(fn):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if not isinstance(block, list):
                continue
            for i, st in enumerate(block):
                if _is_skip_return(st):
                    checked += 1
                    assert _finishes(block[:i]), (
                        f"the skipped return on line {st.lineno} records no ledger outcome; "
                        f"a fit that produces no model is exactly what this ledger is for")
    assert checked >= 4, f"expected every skip path to be checked, saw {checked}"


def test_the_boundary_closes_the_ledger_when_the_fit_raises():
    """An exception mid-fit is the case a return-path-only ledger cannot see."""
    seen = {}
    closed = []

    def _explodes(symbol, model_name="xgboost", horizon=5, hyperparams=None,
                  style="SWING", *, _ledger=None):
        _ledger.record("loaded_bars", rows_in=755, rows_out=755)
        seen["ledger"] = _ledger
        raise RuntimeError("xgboost said no")

    with pytest.raises(RuntimeError):
        run_with_ledger(_explodes, ("MU",), {"style": "GROWTH"}, on_close=closed.append)

    assert len(closed) == 1, "the ledger must be emitted on the raising path too"
    led = seen["ledger"]
    assert led.subject == "MU" and led.style == "GROWTH"
    assert led.outcome == "aborted"
    assert led.abort_stage == "loaded_bars"
    assert "RuntimeError" in led.abort_reason and "xgboost said no" in led.abort_reason
    assert led.survivors == 755, "the stages reached before the abort are still readable"


def test_the_boundary_marks_a_skip_as_skipped_and_a_fit_as_saved():
    seen = {}

    def _run(symbol, model_name="xgboost", horizon=5, hyperparams=None,
             style="SWING", *, _ledger=None):
        seen["ledger"] = _ledger
        return seen["result"]

    seen["result"] = {"symbol": "X", "skipped": True, "reason": "only 12 clean samples"}
    run_with_ledger(_run, ("X",), {})
    assert seen["ledger"].outcome == "skipped"
    assert seen["ledger"].abort_reason == "only 12 clean samples"

    seen["result"] = {"symbol": "X", "metrics": {}}
    run_with_ledger(_run, ("X",), {})
    assert seen["ledger"].outcome == "saved"


def test_an_abort_must_name_a_reason():
    led = BaseTrainingLedger(subject="T")
    with pytest.raises(LedgerError):
        led.finish("aborted")
    with pytest.raises(LedgerError):
        led.finish("skipped", reason="")


def test_the_artifact_snapshot_does_not_claim_an_outcome_it_cannot_know():
    """The ledger stored in a bundle is taken before the save; it must say so."""
    led = BaseTrainingLedger(subject="T")
    led.record("loaded_bars", rows_in=755, rows_out=755)
    snap = led.snapshot()
    assert snap["outcome"] == "reached_artifact_write"
    assert "outcome_note" in snap
    assert led.to_dict()["outcome"] == "in_progress", "snapshot must not mutate the ledger"
    assert "_led.snapshot()" in _TRAINER and '"base_ledger": _led.to_dict()' not in _TRAINER


# ---------------------------------------------------------------------------------------
# Disjoint vs overlapping, and the rules that keep a ledger honest
# ---------------------------------------------------------------------------------------

def test_dropped_reconciles_exactly_and_over_attribution_is_a_defect_too():
    s = Stage("loaded_bars", 100, 60, dropped={"a": 30, "b": 10})
    assert s.reconciles and s.unexplained == 0
    assert not Stage("loaded_bars", 100, 60, dropped={"a": 30}).reconciles
    over = Stage("loaded_bars", 100, 60, dropped={"a": 30, "b": 20})
    assert not over.reconciles and over.unexplained == -10, (
        "two buckets claiming the same row is as wrong as an unexplained loss")


def test_diagnostics_may_overlap_and_are_never_reconciled():
    """The whole reason the two books are separate."""
    s = Stage("required_features", 100, 60,
              dropped={"required_feature_missing": 40},
              diagnostics={"any_required_feature_missing": 40,
                           "any_forward_label_unavailable": 5,
                           "any_inside_dead_zone": 55})
    assert sum(s.diagnostics.values()) > s.lost
    assert s.reconciles, "diagnostics must not enter the reconciliation"
    assert s.to_dict()["diagnostics_overlap"] is True


def test_a_real_frame_produces_overlapping_diagnostics_that_would_not_reconcile():
    """Not a constructed example: the overlap is a property of real price data.

    The last `horizon` bars have no forward label, and the warm-up bars have no features;
    summing the three criteria over a real frame overcounts, which is why a ledger that
    reconciled against that sum would report a shortfall nobody lost.
    """
    from src.features.builder import build_features

    df = _price_frame()
    trace: dict = {}
    X, _, _ = build_features(df.copy(), horizon=5, label_threshold=0.02, trace=trace)
    d = trace["diagnostics"]
    naive_total = (d["any_required_feature_missing"]
                   + d["any_forward_label_unavailable"]
                   + d["any_inside_dead_zone"])
    real_loss = len(df) - len(X)
    assert naive_total > real_loss, (
        "if these ever stop overlapping on real data the test has stopped testing anything")
    assert sum(s["dropped"][next(iter(s["dropped"]))] for s in trace["stages"]) == real_loss


def test_rows_out_none_means_not_measured_and_zero_means_measured_zero():
    with pytest.raises(LedgerError):
        Stage("loaded_bars", 10, None)
    unmeasured = Stage("loaded_bars", 10, None, unmeasured_reason="probe failed")
    measured_zero = Stage("loaded_bars", 10, 0, dropped={"all_dropped": 10})
    assert unmeasured.lost is None and unmeasured.reconciles is False
    assert measured_zero.lost == 10 and measured_zero.reconciles

    led = BaseTrainingLedger(subject="T")
    led.record("loaded_bars", rows_in=10, rows_out=None, unmeasured_reason="probe failed")
    assert led.complete is False
    assert led.to_dict()["survivors"] is None


def test_a_stage_cannot_create_rows():
    """Augmentation adds rows to the FIT, not to this lineage — recording it as a stage
    would make the base population look larger than any set of observations ever was."""
    with pytest.raises(LedgerError):
        Stage("deduplication", 100, 140)


def test_stages_must_follow_the_documented_order_and_appear_once():
    led = BaseTrainingLedger(subject="T")
    led.record("completed_unique_bars", rows_in=10, rows_out=10)
    with pytest.raises(LedgerError):
        led.record("loaded_bars", rows_in=10, rows_out=10)
    with pytest.raises(LedgerError):
        led.record("completed_unique_bars", rows_in=10, rows_out=10)
    with pytest.raises(LedgerError):
        led.record("not_a_stage", rows_in=1, rows_out=1)


def test_a_chain_break_is_reported_separately_from_a_reconciliation_failure():
    """Both stages reconcile internally; the lineage between them does not."""
    led = BaseTrainingLedger(subject="T")
    led.record("loaded_bars", rows_in=755, rows_out=755)
    led.record("completed_unique_bars", rows_in=700, rows_out=700)
    assert all(s.reconciles for s in led.stages)
    assert led.chain_breaks and led.reconciles is False


def test_a_partition_must_sum_to_the_rows_it_divides():
    Stage("split_allocation", 100, 100, partition={"train": 70, "test": 30})
    with pytest.raises(LedgerError):
        Stage("split_allocation", 100, 100, partition={"train": 70, "test": 20})


def test_weighted_augmentation_is_reported_beside_the_base_counts_never_inside_them():
    led = BaseTrainingLedger(subject="T")
    led.record_augmentation(unique_base_observations=289, augmentation_rows=12,
                            weight_multiple=2.0)
    aug = led.to_dict()["augmentation"]
    assert aug["unique_base_observations"] == 289
    assert aug["augmentation_rows"] == 12
    assert aug["augmentation_weight_total"] == 24.0
    assert aug["augmentation_weight_total_is_not_a_sample_size"] is True
    # And no stage count was inflated by it.
    assert led.to_dict()["survivors"] is None


def test_the_trainer_reports_augmentation_against_the_base_training_rows():
    """The weight multiple is read as a NUMBER, per T401 — a source-text assertion on
    `weight_multiple=2.0` would survive the value being edited to anything else."""
    tree = ast.parse(_TRAINER)
    call = next(n for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "record_augmentation")
    kw = {k.arg: k.value for k in call.keywords}
    assert ast.literal_eval(kw["weight_multiple"]) == 2.0, (
        "the ledger must report the multiple the fit actually applies")
    # The base count is the TRAINING rows, not the augmented fit array.
    base = ast.get_source_segment(_TRAINER, kw["unique_base_observations"])
    assert "X_train" in base and "_fit_" not in base
    assert "n_train" not in ast.get_source_segment(_TRAINER, call), \
        "the base training count must not be redefined here"


# ---------------------------------------------------------------------------------------
# The eligible-opportunity cohort
# ---------------------------------------------------------------------------------------

def test_the_cohort_keeps_the_small_moves_training_dropped():
    """What training excludes as 'too small to label' is most of what serving is asked about."""
    from src.features.builder import build_features

    df = _price_frame()
    trace: dict = {}
    X, _, _ = build_features(df.copy(), horizon=5, label_threshold=0.03, trace=trace)
    cohort = trace["cohort"]
    assert cohort["n_selected_for_training"] == len(X)
    assert cohort["n_eligible"] > len(X), "a 3% dead zone must exclude real rows"
    assert cohort["n_eligible_excluded_from_training"] == cohort["n_eligible"] - len(X)
    assert sum(cohort["rows"]["selected_for_training"]) == len(X)
    assert len(cohort["rows"]["date"]) == cohort["n_eligible"]
    # Every excluded row has a resolved forward return; it was small, not missing.
    excluded = [r for r, sel in zip(cohort["rows"]["fwd_ret"],
                                    cohort["rows"]["selected_for_training"]) if not sel]
    assert excluded and all(abs(r) < 0.03 for r in excluded)
    assert cohort["excluded_abs_move_max"] < 0.03


def test_the_cohort_is_narrowed_to_the_evaluation_window_without_losing_the_exclusions():
    from src.features.builder import build_features

    df = _price_frame()
    trace: dict = {}
    build_features(df.copy(), horizon=5, label_threshold=0.03, trace=trace)
    dates = trace["cohort"]["rows"]["date"]
    start, end = dates[len(dates) // 2], dates[-1]

    out = eligible_cohort_for_window(trace, start, end)
    assert out["n_eligible_in_window"] == sum(1 for d in dates if start <= d <= end)
    assert out["n_excluded_small_moves_in_window"] > 0, (
        "a window with no excluded rows would not be an eligible-opportunity cohort")
    assert (out["n_selected_in_window"] + out["n_excluded_small_moves_in_window"]
            == out["n_eligible_in_window"])
    assert all(start <= d <= end for d in out["rows"]["date"])


def test_an_unavailable_cohort_says_so_rather_than_reporting_zero():
    out = eligible_cohort_for_window({}, None, None)
    assert out["rows"] is None
    assert out["unavailable_reason"]
    assert "n_eligible_in_window" not in out, "an unmeasured cohort must not render a count"


def test_the_cohort_is_recorded_not_trained_on():
    """The cohort must not reach the fit: it exists to make a later evaluation possible."""
    call = _fn_source("train_model")
    assert "record_cohort(" in call
    fit_region = call[call.index("model.fit("):call.index("record_cohort(")] \
        if call.index("model.fit(") < call.index("record_cohort(") else ""
    assert "record_cohort" not in fit_region
    for forbidden in ("_fit_X", "_fit_y", "_fit_w", "X_train ="):
        tail = call[call.index("record_cohort("):]
        assert forbidden not in tail, f"{forbidden} is assigned after the cohort is recorded"


# ---------------------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------------------

def test_the_trainer_records_every_stage_in_the_lineage():
    call = _fn_source("train_model")
    recorded = {s for s in STAGE_ORDER if f'"{s}"' in call}
    # The three selection stages are transcribed from build_features' trace, so they are
    # named in the builder and in the transcriber rather than inline in train_model.
    recorded |= {s for s in STAGE_ORDER if f'"{s}"' in _BUILDER}
    assert recorded == set(STAGE_ORDER), f"never recorded: {sorted(set(STAGE_ORDER) - recorded)}"


def test_the_embargo_stage_counts_the_gap_as_a_loss_and_the_shortfall_as_a_diagnostic():
    """A gap NOT taken is leakage, not a missing row. Mixing them would make an under-gapped
    fit reconcile against rows it still has."""
    call = _fn_source("train_model")
    block = call[call.index('"embargo", rows_in='):]
    block = block[:block.index("if len(np.unique(y_train))")]
    assert "embargo_gap_before_early_stop" in block and "dropped=" in block
    short = block[block.index("diagnostics="):]
    assert "_embargo_shortfall" in short
    assert "_embargo_shortfall" not in block[:block.index("diagnostics=")]


def test_the_ledger_travels_with_the_artifact():
    assert '"base_ledger": _led.snapshot(),' in _TRAINER
    tree = ast.parse(_TRAINER)
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict) and any(
                isinstance(k, ast.Constant) and k.value == "n_metric_rows" for k in node.keys):
            keys = {k.value for k in node.keys if isinstance(k, ast.Constant)}
            assert "base_ledger" in keys
            assert "n_test" in keys, "the legacy field keeps its place and its meaning"
            return
    raise AssertionError("the metrics dict was not found")


def test_no_suppression_threshold_or_split_proportion_was_changed_by_this_ledger():
    """The user's instruction: review the ledger BEFORE changing history windows or split
    proportions. This pins that the instrumentation did not quietly change either."""
    for literal in ("int(len(X) * 0.70)", "int(len(X) * 0.80)", "int(len(X) * 0.90)",
                    "if len(X) < 200:", "_MIN_REPORT_ROWS = 10",
                    "TimeSeriesSplit(n_splits=5, gap=horizon)"):
        assert literal in _TRAINER, f"{literal} was changed or removed"
    assert "_load_prices(symbol)" in _TRAINER


def test_the_trainer_records_the_stages_in_the_order_the_ledger_requires():
    """Statically, because at runtime the proxy would swallow the error.

    `SafeLedger` is what keeps an instrumentation bug from costing a model — but it also
    means a stage recorded out of order fails silently and leaves a ledger that is merely
    incomplete. The ordering is therefore checked here, where it can still fail loudly.
    """
    tree = ast.parse(_TRAINER)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "train_model")

    sequence: list[tuple[int, str]] = []
    for node in ast.walk(fn):
        # `_led` specifically: the same function also drives the AUGMENTATION ledger, whose
        # stage names belong to a different lineage and would make this comparison nonsense.
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "_led"):
            continue
        if node.func.attr == "record" and node.args and isinstance(node.args[0], ast.Constant):
            sequence.append((node.lineno, node.args[0].value))
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "_record_selection_stages":
            # Stands in for the three stages the builder's trace supplies.
            sequence.append((node.lineno, "required_features"))
    ordered = [name for _, name in sorted(sequence)]
    positions = [STAGE_ORDER.index(n) for n in ordered]
    assert positions == sorted(positions), f"stages are wired out of order: {ordered}"
    assert "deduplication" in ordered and ordered.index("required_features") < ordered.index("deduplication")


def test_inference_mode_reports_no_cohort_rather_than_an_empty_one():
    """There is no training selection to contrast against when labels are skipped, so the
    question is inapplicable — which is a different answer from "nothing was excluded"."""
    from src.features.builder import build_features

    trace: dict = {}
    build_features(_price_frame().copy(), horizon=5, inference_mode=True, trace=trace)
    cohort = trace["cohort"]
    assert cohort["rows"] is None
    assert cohort["not_applicable_reason"]
    assert "n_eligible" not in cohort
