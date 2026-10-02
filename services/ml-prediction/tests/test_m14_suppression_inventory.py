"""M14 — invalid models must not publish, and a resweep must not un-suppress them.

THE HISTORY. `oos_suppressed` is written once at training time and baked into each bundle.
Before AUD-ML3-STALESUPPRESSION there was no re-evaluation path, and the fleet drifted from its
own rule: 11 artifacts had `test_auc` of exactly 0.0 or 1.0 while unsuppressed, and 45 were over
30 days old and unsuppressed, the oldest 82 days. `test_auc == 0.0` is a perfectly inverted
ranking, so those models were contributing actively harmful signal at live fusion weight.

Then R02 found the sweep about to UNDO the leakage suppression it had just added, on its first
scheduled run. These tests pin that it cannot.

The inventory calls the REAL `_compute_oos_suppression`; a reimplemented rule is how an
inventory ends up disagreeing with the thing it inventories.
"""
import pathlib
import sys

import pytest

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
if str(_SRC.parent) not in sys.path:
    sys.path.insert(0, str(_SRC.parent))

import importlib.util as _ilu

_spec = _ilu.spec_from_file_location(
    "suppression_inventory", _SRC / "training" / "suppression_inventory.py")
si = _ilu.module_from_spec(_spec)
# Registered BEFORE exec: the module defines dataclasses with postponed annotations, and
# `dataclasses` resolves field types through `sys.modules[cls.__module__]`. Without this the
# import fails at class-creation time with a bare AttributeError on NoneType.
sys.modules["suppression_inventory"] = si
_spec.loader.exec_module(si)


def _real_decide():
    """The production rule, loaded without importing the whole trainer (which pulls in xgboost,
    sklearn and a settings object). Executed, not copied."""
    import ast
    src = (_SRC / "training" / "trainer.py").read_text()
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "_compute_oos_suppression")
    ns: dict = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "<rule>", "exec"), ns)  # noqa: S102
    return ns["_compute_oos_suppression"]


DECIDE = _real_decide()

_GOOD = {"cv_auc_mean": 0.61, "recall": 0.4, "precision": 0.5, "overfit_gap": 0.02,
         "evaluation_valid": True}


def _bundle(suppressed=False, **metric_overrides):
    m = dict(_GOOD)
    m.update(metric_overrides)
    return {"oos_suppressed": suppressed, "metrics": m}


# ── The two invariants ────────────────────────────────────────────────────────────────────────

def test_an_invalid_model_is_suppressed_however_good_its_other_metrics_look():
    """`evaluation_valid is False` means the recorded evaluation is not out of sample — the
    numbers describe rows the model was fitted on. Good-looking metrics are then evidence of
    nothing, so they must not rescue it."""
    should, reason = DECIDE(0.75, 0.6, 0.7, 0.01, False)
    assert should is True
    assert reason


def test_a_resweep_can_never_unsuppress_an_invalid_model():
    """THE R02 FAILURE, pinned as data. The sweep re-applies today's rule to yesterday's stored
    numbers; without validity in the rule it would have unsuppressed exactly the models R02 had
    just suppressed, on its first scheduled run."""
    inv = si.build_inventory(
        [("leaky", _bundle(suppressed=True, evaluation_valid=False))], decide=DECIDE)
    assert inv.resweep_would_unsuppress_invalid == []
    assert inv.would_unsuppress == []
    assert inv.holds is True


def test_the_detector_catches_a_RULE_REGRESSION_that_would_unsuppress_an_invalid_model():
    """THE TEST THAT WAS MISSING, found by a sabotage that survived.

    The assertion above (`resweep_would_unsuppress_invalid == []`) passes whether or not the
    detector exists, because today's rule already refuses to unsuppress an invalid model — so
    the list is empty either way. That makes it a check on the RULE, not on the safety net.

    The net exists for the case where the rule regresses: R02 found exactly that about to
    happen. Here the pre-R02 rule is supplied deliberately — one that ignores `evaluation_valid`
    — and the inventory must notice that applying it would unsuppress a model whose own
    evaluation is invalid."""
    def pre_r02_rule(cv_auc, recall, precision, gap, evaluation_valid=None):
        # The rule as it stood before R02: validity simply was not consulted.
        if cv_auc is not None and cv_auc < 0.52:
            return True, "cv_auc<0.52"
        if recall == 0.0 and precision == 0.0:
            return True, "dead recall"
        if gap is not None and abs(gap) > 0.10:
            return True, "overfit gap"
        return False, None

    inv = si.build_inventory(
        [("leaky", _bundle(suppressed=True, evaluation_valid=False))], decide=pre_r02_rule)
    assert inv.resweep_would_unsuppress_invalid == ["leaky"]
    assert inv.holds is False, "a rule that would unsuppress an invalid model must not read ok"


def test_an_invalid_model_left_unsuppressed_is_reported_as_a_defect():
    inv = si.build_inventory(
        [("stale", _bundle(suppressed=False, evaluation_valid=False))], decide=DECIDE)
    assert inv.invalid_unsuppressed == ["stale"]
    assert inv.holds is False, "an invalid, unsuppressed model must not read as healthy"


# ── Valid incumbents are preserved ────────────────────────────────────────────────────────────

def test_a_valid_incumbent_is_not_suppressed_by_the_sweep():
    """The sweep must not churn models that legitimately pass — suppressing a good incumbent
    costs real signal."""
    inv = si.build_inventory([("good", _bundle(suppressed=False))], decide=DECIDE)
    assert inv.would_suppress == []
    assert inv.suppressed == 0 and inv.unsuppressed == 1


def test_a_model_whose_metrics_improved_can_be_unsuppressed_when_it_is_valid():
    """The sweep is two-directional by design: it re-applies the rule, not a ratchet."""
    inv = si.build_inventory([("recovered", _bundle(suppressed=True))], decide=DECIDE)
    assert inv.would_unsuppress == ["recovered"]
    assert inv.resweep_would_unsuppress_invalid == []


# ── Unknown is not valid ──────────────────────────────────────────────────────────────────────

def test_unknown_validity_is_counted_separately_from_valid():
    """Pre-R02 artifacts carry no such key. "Never measured" and "measured and fine" are
    different facts, and folding them together would report a coverage gap as a clean result."""
    inv = si.build_inventory([
        ("old", _bundle(evaluation_valid=None)),
        ("new", _bundle(evaluation_valid=True)),
    ], decide=DECIDE)
    assert inv.unknown_validity == 1
    assert inv.invalid == 0
    assert inv.coverage == pytest.approx(0.5)


def test_unknown_validity_does_not_by_itself_suppress():
    """Treating absence as failure would mass-suppress the fleet for a property never measured."""
    should, _ = DECIDE(0.61, 0.4, 0.5, 0.02, None)
    assert should is False


def test_an_empty_fleet_has_no_coverage_rather_than_zero():
    assert si.build_inventory([], decide=DECIDE).coverage is None


# ── Operational detail ────────────────────────────────────────────────────────────────────────

def test_the_dead_recall_population_is_caught_and_named():
    """A tiny test split lets headline `auc` reach 1.0 by chance while the model has never
    correctly predicted a single positive. Confirmed live on 9961.HK."""
    inv = si.build_inventory(
        [("dead", _bundle(recall=0.0, precision=0.0, cv_auc_mean=0.716))], decide=DECIDE)
    assert inv.would_suppress == ["dead"]
    assert any("recall" in r for r in inv.by_reason), inv.by_reason


def test_a_missing_recall_is_not_read_as_zero():
    """-1.0 is outside [0, 1] so it cannot trip the dead-recall condition. Reading a missing
    metric as 0.0 would suppress models we simply have no number for."""
    m = dict(_GOOD)
    del m["recall"]
    del m["precision"]
    inv = si.build_inventory([("nometric", {"oos_suppressed": False, "metrics": m})],
                             decide=DECIDE)
    assert inv.would_suppress == []


def test_an_unreadable_artifact_is_recorded_not_skipped():
    """A bundle that cannot be loaded is not a healthy one; counting it as absent would shrink
    the denominator and flatter every rate computed from it."""
    inv = si.build_inventory([("corrupt", None), ("ok", _bundle())], decide=DECIDE)
    assert inv.failed_to_read == ["corrupt"]
    assert inv.total == 2


def test_the_rule_version_travels_with_the_inventory():
    """A reason recorded under an older rule was produced by a different rule and is not
    comparable with a newer one — the same discipline the metric registry applies to formulas."""
    assert si.build_inventory([], decide=DECIDE).to_dict()["rule_version"]


# ── The consumer chain ────────────────────────────────────────────────────────────────────────

def test_a_suppressed_model_is_neutralised_at_inference_not_merely_dampened():
    """WHAT I ALMOST REPORTED AS A LEAK. signal-engine shrinks the FUSED score toward neutral by
    40% for a suppressed model, which looks like a suppressed model still contributing 60%.

    It is not: `predict_latest` already returns a flat 0.5 for a suppressed bundle, and the
    ensemble zeroes its weight, so the ML contribution is neutral BEFORE signal-engine sees it.
    The 40% shrink is a second, additional penalty on the combined signal. Pinned here so the
    next reader does not re-derive the same false alarm."""
    trainer = (_SRC / "training" / "trainer.py").read_text()
    i = trainer.index('if bundle.get("oos_suppressed"):')
    block = trainer[i:i + 700]
    assert '"bullish_probability": 0.5' in block
    assert '"confidence": 0.0' in block
    # And the ensemble zeroes the weight rather than blending a suppressed model at full AUC.
    assert 'if xgb.get("oos_suppressed"):' in trainer
    assert 'if rf.get("oos_suppressed"):' in trainer
