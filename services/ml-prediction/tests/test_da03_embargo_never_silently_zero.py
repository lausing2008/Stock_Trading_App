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


def test_the_reason_for_the_change_survives_in_the_source():
    """The comment explaining the old rule must stay, or the next reader re-derives why the
    obvious `enforce it fully` is wrong — and disables GROWTH training platform-wide."""
    assert "GROWTH training for EVERY symbol" in _TRAINER
    assert "silently" in _TRAINER


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
