"""R01: final training could fit on resolved outcomes from AFTER its own evaluation boundary.

THE DEFECT. `_load_outcome_features()` loads closed BUY outcomes across a 365-day lookback and
returns them indexed by SIGNAL DATE. `train_model()` deduplicates overlapping dates, defines the
chronological splits, and then appends **all** surviving outcome rows to the fit set at DOUBLE
weight — with no check that those rows predate the training cutoff. A row signalled inside (or
after) the early-stop / calibration / test windows therefore carries information from the future
of every slice the model is then scored on.

DEDUPLICATING DATES DOES NOT ADDRESS IT. Removing an overlapping date from X says nothing about
whether a surviving outcome was knowable at training time; it is a different question with a
different answer.

AND THE SIGNAL DATE IS THE WRONG DATE TO TEST. A signal outcome resolves when its position
EXITS. One emitted before the cutoff whose trade closed after it was still unknowable then, so
admissibility is judged on the label's availability — the latest of exit_date, ts_evaluated, and
signal_date + horizon — not on when the signal fired.

The guard FAILS CLOSED: if admissibility cannot be established, nothing is augmented. An
unverifiable row is precisely the one this exists for.
"""
import ast
import re
from datetime import date, timedelta
from pathlib import Path

_SRC = (Path(__file__).resolve().parents[1] / "src" / "training" / "trainer.py").read_text()


def _fn(name: str) -> str:
    start = _SRC.index(f"def {name}(")
    m = re.search(r"\n(?=@|def )", _SRC[start + 1:])
    return _SRC[start:start + 1 + m.start()] if m else _SRC[start:]


# ── The availability date is produced and carried ────────────────────────────

def test_the_loader_returns_a_label_availability_series():
    """Without it, no later code COULD apply a cutoff — the same shape as DA-01, where the
    signal date was discarded before the split that claimed to be chronological."""
    sig = _SRC[_SRC.index("def _load_outcome_features("):]
    sig = sig[:sig.index(":\n")]
    assert "tuple[pd.DataFrame, pd.Series, pd.Series]" in sig
    body = _fn("_load_outcome_features")
    assert "return X_out, y_out, avail" in body


def test_availability_is_the_LATEST_of_the_candidate_dates():
    """exit_date, ts_evaluated and signal_date+horizon can disagree. Taking the earliest would
    admit a row before its label existed, which is the bug with extra steps."""
    body = _fn("_load_outcome_features")
    assert "avail_map[o.signal_date] = max(cands)" in body
    for candidate in ("o.exit_date", "ts_evaluated", "_td(days=_horizon_days)"):
        assert candidate in body, candidate


def test_every_early_return_keeps_the_three_value_shape():
    """A loader with eight early returns is a loader where one of them silently returns the old
    2-tuple and unpacks into a TypeError at the call site."""
    body = _fn("_load_outcome_features")
    returns = re.findall(r"return ([^\n]+)", body)
    for r in returns:
        assert r.count(",") >= 2, f"early return is not a 3-tuple: {r}"


def test_availability_survives_the_dedup_fallback():
    """The fallback path drops rows from X_out. If availability is not dropped alongside, the
    two series are misaligned and the cutoff filter compares the wrong dates."""
    body = _fn("train_model")
    frag = body[body.index("overlap_idx = X_out.index"):]
    frag = frag[:frag.index("if len(X_out) >= 5")]
    assert "avail_out = avail_out.drop(index=overlap_idx" in frag


# ── The cutoff itself ────────────────────────────────────────────────────────

def test_the_cutoff_is_taken_from_the_last_TRAINING_row():
    """Asserted on the AST. The expression contains an offset, so a substring check would pin a
    number as text (AUD-T401-SOURCETEXTTESTS) and would survive it being changed to
    `split_train - 1 + 99`, which is precisely the boundary this guard defends."""
    tree = ast.parse(_SRC)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "train_model")
    index_expr = None
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_cutoff" for t in node.targets)):
            continue
        for sub in ast.walk(node.value):
            if (isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Name)
                    and sub.value.id == "X_dates_for_split"):
                index_expr = sub.slice
    assert index_expr is not None, "the cutoff does not index X_dates_for_split"

    # EVALUATE the index with a known split point rather than inspecting its parts. Reading the
    # subtraction alone passed against `split_train - 1 + 99`, which parses as
    # `(split_train - 1) + 99` — the offset it reports is still 1 while the boundary has moved
    # 99 rows into the evaluation window.
    def _index_at(split_train: int) -> int:
        return eval(  # noqa: S307 - an expression from our own source, no builtins
            compile(ast.Expression(index_expr), "<cutoff-index>", "eval"),
            {"__builtins__": {}}, {"split_train": split_train},
        )

    for n in (50, 137, 1000):
        assert _index_at(n) == n - 1, (
            f"cutoff reads row {_index_at(n)} for split_train={n}; the last TRAINING row is {n - 1}"
        )


def test_the_row_dates_are_recomputed_AFTER_the_outcome_dedup():
    """X loses rows during dedup. Reusing the `X_dates` built before that would be misaligned
    with the split points — an off-by-N cutoff, which is worse than none because it looks
    deliberate."""
    body = _fn("train_model")
    dedup_at = body.index("X_dates = pd.DatetimeIndex(")
    recompute_at = body.index("X_dates_for_split = pd.DatetimeIndex(")
    assert recompute_at > dedup_at
    # ...and it must actually READ the current X, not merely exist. Replacing the recompute
    # with an empty index passed the ordering check alone.
    frag = body[recompute_at:recompute_at + 300]
    assert 'df["ts"]' in frag and "iloc[X.index]" in frag


def test_admissibility_is_judged_on_availability_not_the_signal_date():
    body = _fn("train_model")
    frag = body[body.index("_cutoff = pd.Timestamp"):]
    frag = frag[:frag.index("if len(_X_out_for_fit) < 5")]
    assert "_avail_out_for_fit" in frag
    assert "a <= _cutoff" in frag


def test_the_filter_applies_to_features_and_labels_together():
    """Masking one and not the other silently trains on wrong answers — the same alignment trap
    the outcome dedup already documents."""
    body = _fn("train_model")
    frag = body[body.index("_outcome_rows_after_cutoff = int("):]
    frag = frag[:frag.index("if len(_X_out_for_fit) < 5")]
    assert "_X_out_for_fit = _X_out_for_fit[_admissible]" in frag
    assert "_y_out_for_fit = _y_out_for_fit[_admissible]" in frag


def test_an_unverifiable_row_disables_augmentation_entirely():
    """Fails CLOSED. Falling back to 'augment anyway' on an exception would reinstate the exact
    leak whenever the date handling hit anything unexpected."""
    body = _fn("train_model")
    frag = body[body.index("except Exception as _cut_err:"):]
    frag = frag[:frag.index("if _X_out_for_fit is not None and _y_out_for_fit is not None:")]
    assert "_X_out_for_fit = None" in frag
    assert "_y_out_for_fit = None" in frag


def test_dropping_below_the_floor_disables_augmentation_rather_than_fitting_on_scraps():
    """The floor is read out of the AST and checked as a NUMBER — a text assertion would pass
    against `< 5 - 99999`, which disables the floor entirely."""
    tree = ast.parse(_SRC)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "train_model")
    floors = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        left = node.test.left
        if (isinstance(left, ast.Call) and isinstance(left.func, ast.Name)
                and left.func.id == "len"
                and any(isinstance(a, ast.Name) and a.id == "_X_out_for_fit" for a in left.args)
                and isinstance(node.test.ops[0], ast.Lt)
                and isinstance(node.test.comparators[0], ast.Constant)):
            floors.append(node.test.comparators[0].value)
    assert floors, "no augmentation floor found"
    assert all(f >= 1 for f in floors), f"floor is not positive: {floors}"


def test_the_refusal_count_is_recorded_in_the_artifact():
    """A non-zero value on a previously-clean symbol is the evidence that the augmentation WAS
    leaking — invisible if it is only logged."""
    assert '"outcome_rows_dropped_after_cutoff": _outcome_rows_after_cutoff,' in _SRC


# ── The admissibility rule, exercised ────────────────────────────────────────

def _admissible(avail: date, cutoff: date) -> bool:
    """The shipped comparison, mirrored. Kept in step by the source assertions above."""
    return avail <= cutoff


def test_a_label_resolved_before_the_cutoff_is_admitted():
    cutoff = date(2026, 6, 30)
    assert _admissible(date(2026, 6, 29), cutoff) is True
    assert _admissible(cutoff, cutoff) is True


def test_a_label_resolved_after_the_cutoff_is_refused():
    cutoff = date(2026, 6, 30)
    assert _admissible(date(2026, 7, 1), cutoff) is False


def test_a_signal_before_the_cutoff_whose_trade_closed_after_it_is_refused():
    """The case the signal-date index hides entirely: emitted 10 days before the boundary, on a
    20-day horizon, so it did not resolve until 10 days past it."""
    cutoff = date(2026, 6, 30)
    signal = cutoff - timedelta(days=10)
    resolved = signal + timedelta(days=20)
    assert signal < cutoff
    assert _admissible(resolved, cutoff) is False


def test_a_missing_cutoff_refuses_augmentation_rather_than_skipping_the_check():
    """Fail-closed on a missing PREREQUISITE, not only on an exception.

    An earlier draft guarded the filter with `and len(X_dates_for_split)`, so when the dates
    were unavailable the whole block was skipped and the unfiltered rows were fitted anyway —
    the opposite of the except branch directly below it, and the exact leak being closed. A
    cutoff that cannot be established is a reason to refuse the rows, never to trust them.
    """
    body = _fn("train_model")
    frag = body[body.index("if _X_out_for_fit is not None:"):]
    frag = frag[:frag.index("try:")]
    assert "not len(X_dates_for_split)" in frag
    assert "_X_out_for_fit = None" in frag
    assert "train.outcome_cutoff_unavailable" in frag
