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


def _code_only(name: str) -> str:
    """A function's EXECUTABLE lines — docstring and comments removed.

    Both have now caused false results in this file. The docstring R01 added QUOTES the
    defective call it replaced ("build_features(df, horizon=..., macro_df=None)") — worth
    keeping, and it made a plain substring search report the defect as still present. The same
    prose-collision trap has now cost a cycle on R04, R05, the T398 ordering test and here.
    Asserting on prose is the failure mode; stripping it is the fix.
    """
    body = _fn(name)
    for quote in ('"' * 3, "'" * 3):
        first = body.find(quote)
        if first != -1:
            second = body.find(quote, first + 3)
            if second != -1:
                body = body[:first] + body[second + 3:]
                break
    return "\n".join(ln.split("#", 1)[0] for ln in body.splitlines())


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


def test_availability_is_the_LATEST_of_the_OBSERVED_candidate_dates():
    """exit_date and ts_evaluated can disagree. Taking the earliest would admit a row before
    its label existed, which is the bug with extra steps.

    REWRITTEN 2026-09-28 (post-deployment audit). A third, ESTIMATED candidate used to sit in
    this list: first `signal_date + timedelta(days=horizon)` (the horizon is in BARS, so that
    landed ~4 sessions early), then a business-day helper that still under-counted whenever the
    frame had gaps. Both are gone. The label's resolution date is now READ OFF THE FRAME, and
    only genuinely observed dates remain here.
    """
    code = _code_only("_load_outcome_features")
    assert "avail_map[o.signal_date] = max(cands) if cands else None" in code
    for candidate in ("o.exit_date", "ts_evaluated"):
        assert candidate in code, candidate
    assert "_td(days=_horizon_days)" not in code, "the calendar/bar unit mismatch is back"
    assert "label_end_date(" not in code, "the estimate is back"


def test_the_label_date_is_read_off_the_frame_not_estimated():
    """THE CORE POST-DEPLOYMENT FIX. Counting business days equals counting BARS only while
    every business day has one — and `df` holds whatever the price table actually has. The
    audit's probe removes three sessions from a twenty-bar window: the estimate said the label
    resolved 2026-09-29, the tenth bar forward is 2026-10-01, and against a 2026-09-30 cutoff
    that row was admitted with its label built from a price past the boundary."""
    code = _code_only("_load_outcome_features")
    assert "_bar_dates.iloc[_pos + _outcome_horizon].date()" in code, \
        "the target bar must be located in the frame, not computed from a calendar"
    assert "_pos_by_date = {d.date(): i for i, d in enumerate(_bar_dates)}" in code


def test_an_unresolvable_label_date_drops_the_row_rather_than_guessing():
    """When the target bar has not printed, or the signal's own bar is not in the frame, the
    label is not yet available. The honest answer is to exclude the row — a guess is what this
    audit caught twice, in two different shapes."""
    code = _code_only("_load_outcome_features")
    assert "if _pos is None or _pos + _outcome_horizon >= len(_bar_dates):" in code
    frag = code[code.index("if _pos is None or _pos + _outcome_horizon"):][:200]
    assert "continue" in frag, "an unresolvable row must be dropped, not estimated"
    assert "trainer.outcome_rows_dropped_label_not_resolvable" in _SRC


def test_the_bar_walk_is_behaviourally_correct_over_a_gappy_frame():
    """The audit's own construction, run as arithmetic: a twenty-business-day window with three
    sessions missing. The tenth bar forward from 2026-09-14 is 2026-10-01, four days later than
    a business-day count would report."""
    import pandas as _pd

    bars = _pd.bdate_range(date(2026, 9, 14), periods=20)
    bars = bars[~bars.isin(_pd.to_datetime(["2026-09-16", "2026-09-17", "2026-09-18"]))]
    pos_by_date = {d.date(): i for i, d in enumerate(bars)}

    pos = pos_by_date[date(2026, 9, 14)]
    assert bars[pos + 10].date() == date(2026, 10, 1)
    # What a business-day count would have said, and why it is not safe:
    assert _pd.bdate_range(date(2026, 9, 14), periods=11)[-1].date() == date(2026, 9, 28)


def test_the_estimate_helper_is_gone_entirely():
    """An "errs late" estimator sitting unused in a file full of leakage guards is exactly what
    gets picked up later and used wrongly — and this one was wrong twice before it was removed.
    It is dominated by a measurement everywhere it was used, so it is deleted rather than kept
    as a fallback nobody audits."""
    assert "def label_end_date(" not in _SRC


def test_every_early_return_keeps_the_three_value_shape():
    """A loader with eight early returns is a loader where one of them silently returns the old
    2-tuple and unpacks into a TypeError at the call site."""
    import ast

    # Parsed, not regexed. `re.findall(r"return ...")` matched the words "forward return over
    # `horizon`" inside the docstring R01 added and tried to unpack an English sentence — the
    # same prose-collision trap that has now cost a cycle on R04, R05 and the T398 ordering
    # test. Return STATEMENTS are unambiguous in the AST.
    tree = ast.parse(_SRC)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
              and n.name == "_load_outcome_features")
    returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
    assert returns, "the loader has no return statements at all"
    for node in returns:
        assert isinstance(node.value, ast.Tuple) and len(node.value.elts) == 3, \
            f"return on line {node.lineno} is not a 3-tuple"


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


def test_the_row_dates_survive_the_outcome_dedup_aligned_with_X():
    """X loses rows during dedup, so the dates the split reads must lose the SAME rows.

    REWRITTEN 2026-09-28 (pre-deployment audit). This used to assert that the recompute came
    after the dedup and contained `df["ts"]` and `iloc[X.index]` — and it passed while that
    expression was wrong in exactly the way the test was meant to catch. The dedup ends with
    `X = X[_keep].reset_index(drop=True)`, after which `X.index` is 0..n-1, so
    `df["ts"].iloc[X.index]` returns the FIRST n rows of df rather than the surviving ones. The
    audit's probe reports 2026-09-14 for a row whose real date is 2026-09-18 — and every date
    downstream (the R01 training cutoff, every range in slice_date_ranges) inherited the error.

    The dates are now captured once, where X.index is still positional, and masked in lockstep.
    """
    body = _code_only("train_model")
    capture_at = body.index("X_row_dates = pd.to_datetime(df[\"ts\"])")
    dedup_at = body.index("X_row_dates = X_row_dates[_keep]")
    split_at = body.index("X_dates_for_split = pd.DatetimeIndex(X_row_dates.values)")
    assert capture_at < dedup_at < split_at, "capture, then mask with X, then read at the split"
    # The broken re-derivation must not come back.
    assert "iloc[X.index]" not in body[split_at:split_at + 400]


def test_a_length_mismatch_between_rows_and_dates_fails_closed():
    """An off-by-one here moves the training cutoff and every reported range silently. Refusing
    to supply dates at all is recoverable — R01's own filter then fails closed and declines the
    augmentation — whereas wrong dates are used with full confidence."""
    body = _code_only("train_model")
    assert "if len(X_dates_for_split) != len(X):" in body
    frag = body[body.index("if len(X_dates_for_split) != len(X):"):][:400]
    assert "train.row_dates_misaligned" in frag
    assert "pd.DatetimeIndex([])" in frag


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


# ── R01 remainder: the features and the target were both the wrong ones ──────
#
# The date filter above is the half of R01 that shares its name. These cover the half the
# audit lists last and which is the larger defect: "Also reconcile outcome `is_correct` with
# the base forward-return target and use the same feature inputs: this loader currently
# rebuilds with `macro_df=None` and without the richer inputs used by the main training path."

def test_the_loader_is_given_the_same_feature_inputs_as_the_main_path():
    """THE ZERO-FILL. The loader called build_features with macro_df=None and nothing else,
    while the main path passes seven inputs. Every column those produce was therefore ABSENT
    from the augmentation rows — and train_model's own
    `reindex(columns=X_train.columns, fill_value=0)` filled them with ZERO. The rows did not
    merely differ: they asserted, at double weight, that every macro, fundamental, sector and
    options feature was exactly zero on the days this platform actually traded."""
    call = _fn("train_model")
    call = call[call.index("_load_outcome_features("):]
    call = call[:call.index("\n\n")]
    for inp in ("macro_df", "label_threshold", "fund_data", "sector_df",
                "outcome_df", "fund_snapshots", "options_snapshots"):
        assert f'"{inp}":' in call, f"the loader is not given {inp}"


def test_the_loader_no_longer_hardcodes_macro_df_none():
    code = _code_only("_load_outcome_features")
    assert "macro_df=None" not in code
    assert "build_features(df, horizon=_outcome_horizon, **_fi)" in code


def test_the_seven_inputs_match_what_the_main_path_actually_passes():
    """Pinned against the main call rather than a hand-written list, so adding an eighth input
    to build_features cannot leave the augmentation silently behind again."""
    main = _SRC[_SRC.index("    X, y_dir, y_ret = build_features("):]
    main = main[:main.index(")\n")]
    # `trace` is an instrumentation OUT-parameter, not a feature input, and the augmentation
    # loader must not be handed this one: the two calls build different populations, and a
    # shared trace dict would have the second overwrite the first's stage counts. M13-BASE
    # records the augmentation lineage in its own ledger instead.
    passed = set(re.findall(r"(\w+)=", main)) - {"horizon", "trace"}
    call = _fn("train_model")
    call = call[call.index("feature_inputs={"):]
    call = call[:call.index("},")]
    given = set(re.findall(r'"(\w+)":', call))
    assert passed <= given, f"main path passes {sorted(passed - given)} that the loader is not given"


def test_the_label_is_the_base_forward_return_target_not_is_correct():
    """THE TARGET MISMATCH. `is_correct` is the signal engine's own verdict under its own exit
    rules; the base model learns "forward return over horizon exceeds label_threshold". Two
    different events, blended at double weight as if they were one probability."""
    code = _code_only("_load_outcome_features")
    assert "y_out = pd.Series([int(_base.loc[d]) for d in _usable]" in code
    assert "label_map[d.date()] for d in outcome_idx" not in code, \
        "the label is still is_correct"


def test_is_correct_is_still_measured_rather_than_discarded():
    """A low agreement rate between the signal engine's verdict and the forward-return label is
    a real finding about the signal engine. Dropping the field silently would lose it."""
    body = _fn("_load_outcome_features")
    assert "agreement_rate" in body
    assert "trainer.outcome_label_reconciliation" in body


def test_a_date_with_no_base_label_is_dropped_not_back_filled():
    """Back-filling from is_correct would reintroduce the mismatch for exactly the rows least
    able to support it — the ones at the end of the series, where the forward window runs off
    the data."""
    code = _code_only("_load_outcome_features")
    assert "pd.notna(_base.loc[d])" in code
    assert "if not _usable:" in code


def test_the_build_features_call_no_longer_discards_its_own_labels():
    """`X_full, y_dir, _ = build_features(...)` computed y_dir and threw it away while the
    function went on to use a different label entirely."""
    body = _fn("_load_outcome_features")
    assert "X_full, y_dir, _ = build_features" in body
    assert "_base = y_dir.copy()" in body


# ── The availability block, executed ──────────────────────────────────────────
#
# The two tests below run the REAL block rather than asserting on its shape. Both properties
# survived a sabotage pass when they were only structural: one assertion looked for `continue`
# within a fragment and passed when a row was appended immediately before it, the other did not
# exist at all.

def _run_availability_block(bar_dates, usable, horizon, avail_map):
    """Execute the shipped label-resolution block with controlled inputs."""
    import ast

    import pandas as _pd

    src = _SRC
    start = src.index('    _bar_dates = df["ts"].dt.normalize()')
    end = src.index('    avail = pd.Series(_avail_vals, index=X_out.index, dtype="object")')
    block = src[start:end]
    # Drop the two lines that re-index the caller's frames; this harness supplies them.
    block = "\n".join(ln for ln in block.splitlines()
                      if "X_out = X_out.loc[_kept]" not in ln
                      and "y_out = y_out.loc[_kept]" not in ln
                      and "return pd.DataFrame()" not in ln
                      and "if not _kept:" not in ln)
    import textwrap
    ns = {
        "df": _pd.DataFrame({"ts": _pd.to_datetime(bar_dates)}),
        "_usable": [_pd.Timestamp(d) for d in usable],
        "_outcome_horizon": horizon,
        "avail_map": avail_map,
        "pd": _pd,
        "log": type("L", (), {"info": lambda *a, **k: None})(),
        "symbol": "T", "style": "SWING",
    }
    exec(compile(ast.parse(textwrap.dedent(block)), "<trainer-block>", "exec"), ns)
    return ns["_kept"], ns["_avail_vals"], ns["_unresolvable"]


def test_a_row_whose_target_bar_has_not_printed_is_actually_dropped():
    """BEHAVIOURAL. A structural check for `continue` passed when a sabotage appended the row
    just before it — the row was kept AND counted as unresolvable."""
    bars = [date(2026, 9, 1) + timedelta(days=i) for i in range(12)]
    # The last signal's target bar (10 ahead) runs off the end of the frame.
    kept, avail, unresolvable = _run_availability_block(
        bars, usable=[bars[0], bars[5]], horizon=10, avail_map={})
    assert unresolvable == 1
    assert [k.date() for k in kept] == [bars[0]], "the unresolvable row was not dropped"
    assert len(avail) == len(kept), "availability must stay aligned with the kept rows"


def test_the_target_bar_is_the_nth_BAR_not_the_nth_day():
    """The gappy frame from the audit's probe: three sessions missing, so the tenth bar forward
    is four days later than any day-count would report."""
    import pandas as _pd

    bars = _pd.bdate_range(date(2026, 9, 14), periods=20)
    bars = [b.date() for b in bars
            if b not in _pd.to_datetime(["2026-09-16", "2026-09-17", "2026-09-18"])]
    kept, avail, _ = _run_availability_block(
        bars, usable=[date(2026, 9, 14)], horizon=10, avail_map={})
    assert kept and avail[0] == date(2026, 10, 1), f"got {avail[0]}"


def test_an_observed_exit_date_still_widens_availability():
    """A trade that closed AFTER its horizon elapsed resolved when it CLOSED. Dropping the
    observed dates would report the label knowable earlier than it was — and that sabotage
    passed until this test existed."""
    bars = [date(2026, 9, 1) + timedelta(days=i) for i in range(40)]
    late = date(2026, 10, 5)
    kept, avail, _ = _run_availability_block(
        bars, usable=[bars[0]], horizon=10, avail_map={bars[0]: late})
    assert kept and avail[0] == late, "the later observed resolution date was discarded"


def test_the_target_bar_wins_when_it_is_later_than_the_observed_dates():
    """The other direction: an exit recorded before the label's own bar printed must not make
    the label look available early."""
    bars = [date(2026, 9, 1) + timedelta(days=i) for i in range(40)]
    kept, avail, _ = _run_availability_block(
        bars, usable=[bars[0]], horizon=10, avail_map={bars[0]: date(2026, 9, 2)})
    assert kept and avail[0] == bars[10]


def test_a_row_with_no_observed_dates_uses_the_target_bar_alone():
    """avail_map is None for a row predating exit_date/ts_evaluated; that must not crash or
    fall back to the signal date."""
    bars = [date(2026, 9, 1) + timedelta(days=i) for i in range(40)]
    kept, avail, _ = _run_availability_block(
        bars, usable=[bars[0]], horizon=10, avail_map={bars[0]: None})
    assert kept and avail[0] == bars[10]
