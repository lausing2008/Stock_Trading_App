"""DA-01 / DA-02: the meta model's validation was not chronological, and its features change
meaning between training and inference.

DA-01 — "CHRONOLOGICAL" SPLIT THAT WAS NOT. Records are built in a symbol-OUTER loop, so the
list arrives grouped by symbol and sorted only WITHIN each symbol. Dates were then DISCARDED
from the (vector, label) tuples, and an 80/20 array split carved off the last symbol(s) — whose
dates overlap the training slice completely. The audit's reproduction uses five symbols across
the same four dates: training contains observations through Sep 4 while "validation" begins
Sep 1. The reported AUC measured a partial symbol holdout against overlapping history and was
presented as out-of-time performance — and it gates promotion.

A leave-symbol-out study is a legitimate thing to want; it answers cold-start generalisation.
It is a DIFFERENT question from "does this work on the future", and only the second justifies
promoting a model.

DA-02 — THE FEATURE CONTRACT DOES NOT HOLD. Training appends the signal outcome's own
confidence, fused_prob and ta_score. Inference sends XGBoost's confidence, XGBoost's bullish
probability as `fused_prob`, and the weighted ML ensemble probability as `ta_score` — the source
says so itself ("best available proxy"). No TA measurement is supplied at all. The targets
differ too: the meta model learns SignalOutcome.is_correct, the base models learn their own
forward-return label, and the two are blended 85/15 as though they were the same quantity.
Sharing the range [0, 1] is not sharing a meaning.

That one is DISABLED, not patched: manufacturing the missing input from another probability is
what created the problem. Re-enabling is a Redis flag so it is a reviewable operational action
rather than a code edit buried in a release.
"""
import ast
import re
from datetime import date
from pathlib import Path

_SVC = Path(__file__).resolve().parents[1]
_META = (_SVC / "src" / "training" / "meta_trainer.py").read_text()
_TRAINER = (_SVC / "src" / "training" / "trainer.py").read_text()


# ── DA-01: the split must be ordered by time ─────────────────────────────────

def test_records_carry_their_signal_date():
    """Dates were dropped from the tuple, so no later code COULD order by time."""
    assert "records.append((vec, int(row.is_correct), row.signal_date))" in _META


def test_records_are_sorted_globally_before_the_split():
    """The sort must happen after every symbol has contributed — sorting within a symbol is
    what the code already did, and is exactly what produced the defect."""
    sort_at = _META.index("records.sort(key=lambda r: r[2])")
    split_at = _META.index("split = int(len(X_raw) * 0.8)")
    assert sort_at < split_at


def test_a_symbol_grouped_record_list_becomes_chronological_when_sorted():
    """Behavioural check of the ordering itself, on the audit's own shape: five symbols over
    the same four dates, appended symbol by symbol."""
    records = []
    for sym in ("A", "B", "C", "D", "E"):
        for d in (date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4)):
            records.append(([0.0], 1, d))

    # Before: the last 20% is entirely symbol E, spanning Sep 1-4 — overlapping the train slice.
    split = int(len(records) * 0.8)
    assert records[split - 1][2] > records[split][2], "precondition: unsorted split is out of order"

    records.sort(key=lambda r: r[2])
    assert records[split - 1][2] <= records[split][2]


def test_the_split_boundary_is_asserted_at_runtime():
    """A future refactor that reorders `records` would otherwise silently restore the defect
    while every metric kept rendering."""
    assert "split_not_chronological" in _META
    assert 'return {"trained": False, "reason": "split_not_chronological"}' in _META


def test_feature_selection_is_fitted_on_training_rows_only():
    """Choosing non-constant columns across the full dataset lets the validation slice decide
    which features the model may see — the validation AUC then is not a clean measurement."""
    m = re.search(r"non_const = np\.where\(np\.nanstd\(([^)]+)\)", _META)
    assert m, "the feature selector is missing"
    assert "X_raw[:split]" in m.group(1), f"selector still sees the full dataset: {m.group(1)}"


def test_the_split_boundary_is_computed_exactly_once():
    """Two boundaries computed separately drift, and the leak returns quietly when they do.

    Counted via the AST rather than by matching the assignment's TEXT: a source-text assertion
    containing the 0.8 would pin a number as a substring, which AUD-T401-SOURCETEXTTESTS exists
    to stop — and would also survive the fraction being changed to `0.8 - 99`.
    """
    tree = ast.parse(_META)
    assigns = [n for n in ast.walk(tree)
               if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "split" for t in n.targets)]
    assert len(assigns) == 1, f"`split` is assigned {len(assigns)} times; the two can drift apart"


# ── DA-02: the blend is off until the contract is validated ──────────────────

def test_the_meta_blend_is_gated():
    assert "if _meta_prob is not None and _meta_blend_enabled():" in _TRAINER


def test_the_gate_defaults_to_off_and_fails_closed():
    """An unvalidated feature contract is not something to fall back TO, so a Redis outage must
    leave the blend disabled rather than enabled."""
    tree = ast.parse(_TRAINER)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_meta_blend_enabled")
    src = ast.get_source_segment(_TRAINER, fn)
    assert "return False" in src, "the except path must disable, not enable"
    # The only True comes from an explicit, positive flag check.
    assert '== "1"' in src


def test_re_enabling_is_an_operational_action_not_a_code_edit():
    """Redis-flagged so switching it back on is reviewable and leaves a trail."""
    assert "stockai:ml:meta_blend_enabled" in _TRAINER


def test_the_reason_for_disabling_survives_in_the_source():
    """The next person to find a disabled feature will want to know whether it is safe to turn
    on. The mismatch, not just the fact of the gate, has to be written down."""
    assert "ta_score" in _TRAINER and "proxy" in _TRAINER
    # Whitespace-normalised: the sentence is line-wrapped across a comment continuation, so an
    # exact substring match fails on the "# " in the middle. Asserting on prose at all is a
    # narrow exception — the point here is that the REASON survives, and a reason that can be
    # deleted without a test noticing tends to be.
    import re as _re
    flat = _re.sub(r"\s*#\s*", " ", _re.sub(r"\s+", " ", _TRAINER))
    assert "not sharing a meaning" in flat
    assert "two different forecast events" in flat


def test_the_blend_weights_still_sum_to_one():
    """Gating is not the place to also change the mix — that would conflate two decisions.

    The weights are parsed out of the AST and checked as NUMBERS. Asserting the expression's
    text would pin them as a substring and still pass if one were changed to `0.15 - 99`, which
    is the failure mode AUD-T401-SOURCETEXTTESTS names.
    """
    tree = ast.parse(_TRAINER)
    weights = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "prob" for t in node.targets)
                and isinstance(node.value, ast.BinOp) and isinstance(node.value.op, ast.Add)):
            consts = [n.value for n in ast.walk(node.value)
                      if isinstance(n, ast.Constant) and isinstance(n.value, float)]
            if len(consts) == 2:
                weights = consts
                break
    assert weights, "the meta blend expression was not found"
    assert sum(weights) == 1.0, f"blend weights no longer sum to 1: {weights}"
