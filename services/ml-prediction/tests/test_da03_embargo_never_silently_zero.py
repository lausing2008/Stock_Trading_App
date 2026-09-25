"""DA-03: the train/early-stop/calibration/test embargo collapsed silently to zero.

THE DEFECT.

    _embargo = horizon if (split_es - split_train) > horizon * 3 else 0

A slice narrower than 3x the horizon got NO gap at all, and that is reachable above the
function's own 200-row minimum. With 200 rows and LONG's 20-bar horizon, the calibration slice
contains a row whose forward label is computed from a price inside the TEST window — so the
evaluation scores partly on data the model was fitted through, and the reported AUC is
optimistic by an unknown amount. Silently: nothing in the bundle recorded that it happened.

WHY IT IS NOT SIMPLY ENFORCED. Measured across the live universe (180 symbols), the share that
can afford a full `horizon` gap at every boundary is:

    SHORT/5  172      SWING/10  164      LONG/20  159      GROWTH/28  0

Requiring the full embargo would disable GROWTH training for EVERY symbol — no stock on the
platform has enough history. Switching off a whole horizon is an operational decision, not a
bug fix, so the embargo is instead the LARGEST the slice can afford while leaving a usable
remainder, and never zero when a gap is affordable at all.

A partial gap still leaks. It is strictly better than none, and the honest move is to say by
how much — `embargo_shortfall` is recorded in the model's metrics and logged, so a compromised
split is identifiable and its evaluation discountable rather than quietly trusted.
"""
import ast
import re
from pathlib import Path

_TRAINER = (Path(__file__).resolve().parents[1] / "src" / "training" / "trainer.py").read_text()

# Comments stripped for the "the old rule is gone" checks. The fix's own comment QUOTES the
# defective line it replaced — which is worth keeping, and which made a raw-source search match
# the prose rather than the code. Same trap test_ml3_style_and_outcome_fixes.py's `_code_only`
# helper already exists for in this service.
_TRAINER_CODE = "\n".join(
    line.split("#", 1)[0] if not line.lstrip().startswith("#") else ""
    for line in _TRAINER.split("\n")
)


def _module_constant(name: str):
    """The VALUE of a module-level constant, without importing the module.

    trainer.py pulls in xgboost/lightgbm at import time — far too heavy for a unit test, which
    is why this service's tests read source (see test_ml3_style_and_outcome_fixes.py's header).
    But a SUBSTRING assertion on a number is exactly what AUD-T401-SOURCETEXTTESTS exists to
    stop: checking that the constant's declaration text appears in the source still passes when
    the code says `10 - 99999`, because the substring survives — and so does a regex capturing
    the digits. Parsing the assignment and EVALUATING it gives a real value: `10 - 99999`
    evaluates to -99989 and fails, as it should.
    """
    tree = ast.parse(_TRAINER)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    try:
                        return ast.literal_eval(node.value)
                    except ValueError:
                        # literal_eval refuses an arithmetic expression, so `10 - 99999` would
                        # raise here — at module import, surfacing as a collection ERROR rather
                        # than a readable assertion failure. Fold it in a namespace with no
                        # builtins and no names: a genuine constant expression evaluates, and
                        # anything referring to the outside world still fails loudly.
                        return eval(  # noqa: S307 - constant expression from our own source
                            compile(ast.Expression(node.value), "<trainer-const>", "eval"),
                            {"__builtins__": {}}, {},
                        )
    raise AssertionError(f"{name} is not a module-level constant in trainer.py")


MIN_SLICE_ROWS = _module_constant("_MIN_SLICE_ROWS")


def _afford(span: int, horizon: int, min_slice: int | None = None) -> int:
    """Mirrors the shipped rule, reading the REAL constant so the two cannot drift apart.

    Kept in step with the shipped expression by test_the_rule_matches_the_shipped_source."""
    return max(0, min(horizon, span - (MIN_SLICE_ROWS if min_slice is None else min_slice)))


# ── The rule itself ──────────────────────────────────────────────────────────

def test_a_comfortable_slice_gets_the_full_horizon_gap():
    assert _afford(span=200, horizon=20) == 20


def test_a_tight_slice_gets_a_partial_gap_rather_than_none():
    """The exact case the old rule zeroed: 20 rows of slice against a 20-bar horizon failed
    `> horizon * 3` and received no gap whatsoever."""
    assert _afford(span=20, horizon=20) == 10
    assert _afford(span=20, horizon=20) > 0


def test_a_slice_that_cannot_afford_any_gap_yields_zero_not_a_negative():
    """A negative embargo would slice backwards and silently OVERLAP the previous window —
    strictly worse than the bug being fixed."""
    assert _afford(span=10, horizon=20) == 0
    assert _afford(span=3, horizon=20) == 0


def test_the_gap_never_exceeds_the_label_horizon():
    """More gap than the label needs only throws away usable data."""
    assert _afford(span=10_000, horizon=5) == 5


def test_the_embargo_leaves_a_usable_remainder():
    """An embargo that consumed its whole slice trades leakage for an evaluation set too small
    to mean anything — one defect for a worse one."""
    for span in (11, 25, 60, 200):
        assert span - _afford(span, horizon=28) >= MIN_SLICE_ROWS


# ── The old rule is gone ─────────────────────────────────────────────────────

def test_no_embargo_is_assigned_from_a_conditional_that_can_yield_zero():
    """The defect's SHAPE, asserted structurally rather than by substring.

    The old code was `_embargo = horizon if (slice > horizon * 3) else 0` — a conditional
    expression whose else-branch is a constant zero. An `in`/`not in` check on that text would
    itself be a source-text assertion pinning numbers (AUD-T401-SOURCETEXTTESTS), and would miss
    any rewrite that kept the behaviour while changing the spelling. Walking the AST catches the
    shape regardless of how it is written.
    """
    tree = ast.parse(_TRAINER)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.IfExp):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if not any(n.startswith("_embargo") for n in names):
            continue
        orelse = node.value.orelse
        if isinstance(orelse, ast.Constant) and orelse.value == 0:
            offenders.append(names)
    assert not offenders, f"embargo still collapses to a constant zero: {offenders}"


def test_the_source_records_the_CORRECTED_measurement_not_the_false_one():
    """This test previously required the sentence "GROWTH training for EVERY symbol" — the
    claim that justified accepting a partial embargo. That claim was FALSE: it used horizon 28
    where the registry says 15, and the old gate's condition rather than the shipped rule.

    Pinning the false justification in a test is how a wrong number outlives the person who
    wrote it, so the assertion is inverted: the corrected measurement must be present and the
    discredited claim must be gone."""
    assert "GROWTH = 15, not 28" in _TRAINER
    assert "scarcity argument was false" in _TRAINER
    assert "GROWTH training for EVERY symbol" not in _TRAINER


def test_the_rule_matches_the_shipped_source():
    """Pins the helper above against the real implementation, so this file cannot drift into
    testing a rule the trainer does not use."""
    m = re.search(r"def _afford\(span: int\) -> int:\s*\n\s*return ([^\n]+)", _TRAINER)
    assert m, "the affordability helper is missing"
    assert m.group(1).strip() == "max(0, min(horizon, span - _MIN_SLICE_ROWS))"
    # AUD-T401-SOURCETEXTTESTS: the VALUE, parsed and evaluated — not the text.
    assert MIN_SLICE_ROWS == 10


def test_all_three_boundaries_use_the_same_rule():
    """The old code repeated the condition three times, which is how one of them drifts."""
    for name in ("_embargo     = _afford(", "_embargo_es  = _afford(", "_embargo_cal = _afford("):
        assert name in _TRAINER, name


# ── The shortfall must be visible ────────────────────────────────────────────

def test_a_shortfall_is_recorded_in_the_model_metrics():
    """Without this, a compromised split is indistinguishable from a clean one in the bundle —
    which is what made the original defect silent rather than merely wrong."""
    assert '"embargo_bars"' in _TRAINER
    assert '"embargo_target_bars": horizon' in _TRAINER
    assert '"embargo_shortfall"' in _TRAINER


def test_a_shortfall_is_also_logged_with_its_cause():
    body = _TRAINER[_TRAINER.index("_embargo_shortfall = {"):]
    body = body[:body.index("X_train = X.iloc[")]
    assert "train.embargo_shortfall" in body
    for field in ("symbol=symbol", "horizon=horizon", "n_rows=len(X)"):
        assert field in body, field


def test_no_shortfall_is_recorded_as_None_rather_than_an_empty_dict():
    """`{}` and None both read as falsy in Python but not in stored JSON, where an empty object
    looks like a measurement that was taken and found nothing."""
    assert '"embargo_shortfall": _embargo_shortfall or None' in _TRAINER


# ── R02: the shortfall must have a CONSEQUENCE, not just a log line ──────────
#
# From the 2026-09-24 follow-up audit: "Logging leakage is not a restriction on using it."
# DA-03 recorded the shortfall in metrics and then let the artifact be used exactly as if the
# split had been clean. A model whose calibration slice contains a label built from a price
# inside its own test window has an optimistic evaluation by an unknown amount — that is not a
# number to size trades with, whatever else it scores.

def test_a_shortfall_suppresses_the_models_oos_output():
    """Suppression, not refusal to train: keeping a research candidate is useful, and deciding
    a symbol may never be modelled is a different decision from deciding its metrics are not
    evidence."""
    body = _TRAINER[_TRAINER.index("oos_suppressed, _suppression_reason = _compute_oos_suppression("):]
    body = body[:body.index("if oos_suppressed:")]
    assert "if _embargo_shortfall and not oos_suppressed:" in body
    assert "oos_suppressed = True" in body


def test_the_suppression_reason_names_the_cause():
    """A suppressed model with an opaque reason is a model somebody re-enables."""
    assert "train.suppressed_for_embargo_shortfall" in _TRAINER
    assert "embargo shortfall" in _TRAINER


def test_an_existing_suppression_reason_is_not_overwritten():
    """If the model was ALREADY suppressed for dead recall or an overfit gap, that reason is
    more specific and must survive — the `and not oos_suppressed` clause is what guards it.

    Asserted on the AST rather than by substring: the guard is a boolean operand, and a
    rewrite that dropped it while keeping the words nearby would pass a text match."""
    tree = ast.parse(_TRAINER)
    guards = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.BoolOp):
            continue
        names = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
        if "_embargo_shortfall" in names and "oos_suppressed" in names:
            has_not = any(isinstance(v, ast.UnaryOp) and isinstance(v.op, ast.Not)
                          for v in node.test.values)
            guards.append(has_not)
    assert guards, "the shortfall suppression branch was not found"
    assert all(guards), "the branch must be guarded by `not oos_suppressed`"


def test_evaluation_validity_is_a_first_class_field():
    """A consumer should not have to infer trustworthiness from the PRESENCE of another key."""
    assert '"evaluation_valid": not _embargo_shortfall,' in _TRAINER


def test_a_clean_split_is_not_suppressed_by_this_rule():
    """Guards the other direction — 165 of 180 symbols per style can afford a full gap, and
    suppressing them all would silently disable the fleet."""
    assert "if _embargo_shortfall and" in _TRAINER
    # The condition is on the shortfall dict being non-empty, which is {} for a full gap.
    assert "_embargo_shortfall = {" in _TRAINER


def test_the_corrected_measurement_is_recorded_at_the_source():
    """The original comment justified a partial embargo with 'GROWTH/28: 0 of 180'. The registry
    says GROWTH = 15, and 165 of 180 can afford the full gap. A wrong number that justified a
    decision has to be corrected where the decision lives, not only in a doc."""
    assert "GROWTH = 15, not 28" in _TRAINER
    assert "scarcity argument was false" in _TRAINER
