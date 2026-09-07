"""AUD-ML3 — the three ML-training fixes from Domain 2 of the 2026-09-07 six-part audit.

Source-text extraction rather than importing the modules, matching this service's established
convention (see test_feature_ablation.py / test_cv_single_class_fold.py): trainer.py and
routes.py pull in xgboost/lightgbm/torch at module level, far too heavy for a focused unit test.

Measured production evidence that motivated each fix
(docs/audits/2026-09-07-six-part-audit-2-ml-training.md):

  F1 STYLE STARVATION — zero `_long` artifacts existed anywhere; exactly ONE GROWTH artifact
     (66 days old); and over 7 days, 1,030 LONG + 1,030 GROWTH live signals carried ZERO
     ml_weight while SHORT/SWING averaged 0.139/0.088. Half the platform ran on TA alone.
  F2 OUTCOME DEDUP — 490 of 548 artifacts had n_outcome_rows == 0; the 58 nonzero ones had a
     median of 6, against 37-43 rows actually loaded per symbol.
  F3 STALE SUPPRESSION — 11 artifacts had test_auc of EXACTLY 0.0 or 1.0 with
     oos_suppressed=False, all of which the current rule says should suppress; 45 artifacts
     were >30 days old AND unsuppressed, the oldest 82 days.
"""
import pathlib
import re

_SVC = pathlib.Path(__file__).resolve().parents[1]
TRAINER = (_SVC / "src" / "training" / "trainer.py").read_text()
ROUTES = (_SVC / "src" / "api" / "routes.py").read_text()


def _func_src(src: str, name: str) -> str:
    """Body of a top-level def, up to the next top-level def/decorator."""
    start = src.index(f"def {name}(")
    m = re.search(r"\n(?=@|def )", src[start + 1:])
    return src[start:start + 1 + m.start()] if m else src[start:]


def _code_only(src: str, name: str) -> str:
    """Function source with its docstring stripped.

    Necessary because these functions carry long explanatory docstrings that legitimately
    mention thresholds ("0.52") and other functions ("train_model") — matching against the raw
    source made two of these tests fail on the PROSE rather than the code. Assert on executable
    lines only.
    """
    body = _func_src(src, name)
    for quote in ('"""', "'''"):
        first = body.find(quote)
        if first != -1:
            second = body.find(quote, first + 3)
            if second != -1:
                return body[:first] + body[second + 3:]
    return body


# ── F1: style starvation ─────────────────────────────────────────────────────────────────

def test_enqueue_is_symbol_outer_style_inner():
    """THE CORE F1 FIX.

    Style-outer/symbol-inner means a truncated queue loses a WHOLE STYLE — which is exactly
    how LONG and GROWTH ended up with no artifacts at all. Symbol-outer/style-inner means a
    cut at any point still covered all four styles for the symbols it reached.
    """
    body = _func_src(ROUTES, "train_all_horizons")
    sym_loop = body.index("for sym in ordered_symbols")
    style_loop = body.index("for style, horizon in _HORIZON_BY_STYLE.items()")
    assert sym_loop < style_loop, (
        "symbol loop must be OUTER — style-outer starves whole styles when the queue truncates"
    )


def test_symbol_order_is_rotated_so_the_same_tail_is_not_always_starved():
    """Interleaving alone still truncates the same alphabetical tail every run (the pre-fix job
    ended at 9961.HK every single time), so those symbols would never train. Rotation moves
    every symbol to the front within one cycle."""
    body = _func_src(ROUTES, "train_all_horizons")
    assert "ordered_symbols" in body
    assert "tm_yday" in body, "rotation must advance daily, not be a fixed permutation"
    assert re.search(r"symbols\[_rotate:\]\s*\+\s*symbols\[:_rotate\]", body), \
        "rotation must preserve every symbol exactly once (a slice-and-concat, not a sample)"


def test_rotation_is_modulo_guarded_against_an_empty_universe():
    """`% len(symbols)` raises ZeroDivisionError if the universe query returns nothing —
    turning an empty-universe edge case into a 500 on the nightly job."""
    body = _func_src(ROUTES, "train_all_horizons")
    assert "max(len(symbols), 1)" in body


def test_rotation_is_a_permutation_not_a_filter():
    """Behavioural check of the rotation expression itself: every symbol must appear exactly
    once for any offset. A fix that silently dropped symbols would be worse than the bug."""
    symbols = [f"S{i}" for i in range(17)]
    for yday in (1, 5, 17, 200, 366):
        rot = yday % max(len(symbols), 1)
        ordered = symbols[rot:] + symbols[:rot]
        assert sorted(ordered) == sorted(symbols)
        assert len(ordered) == len(symbols)


# ── F2: outcome-augmentation dedup ───────────────────────────────────────────────────────

def test_dedup_drops_from_the_main_set_not_from_the_outcome_rows():
    """THE CORE F2 FIX.

    T232-ML3 correctly spotted the double-counting but resolved it on the wrong side. Closed
    signal_outcomes are BY CONSTRUCTION >=~2 weeks old, so their dates almost always fall
    inside the main training window — dropping from X_out therefore removed nearly all of them
    and made the whole Tier 87 feature inert. The real outcome label is the BETTER label, so
    on a collision the synthetic row in X is the one to drop.
    """
    body = TRAINER[TRAINER.index("# AUD-ML3-OUTCOMEDEDUP"):]
    body = body[:body.index("if len(X_out) >= 5")]
    assert "X = X[_keep]" in body, "must drop the colliding rows from X (the synthetic labels)"
    assert "_overlap_dates" in body


def test_dedup_reindexes_after_masking():
    """Everything downstream treats X positionally (TimeSeriesSplit, the 70/80/90 split points,
    X.iloc[...]). Leaving a gappy index after a boolean mask would silently misalign every
    split — a far worse bug than the one being fixed."""
    body = TRAINER[TRAINER.index("# AUD-ML3-OUTCOMEDEDUP"):]
    body = body[:body.index("if len(X_out) >= 5")]
    for target in ("X = X[_keep].reset_index(drop=True)",
                   "y_dir = y_dir[_keep].reset_index(drop=True)",
                   "y_ret = y_ret[_keep].reset_index(drop=True)"):
        assert target in body, f"missing reindex: {target}"


def test_dedup_keeps_x_y_aligned():
    """X, y_dir and y_ret must all be masked by the SAME boolean — masking only some would
    misalign features from labels, silently training on wrong answers."""
    body = TRAINER[TRAINER.index("# AUD-ML3-OUTCOMEDEDUP"):]
    body = body[:body.index("if len(X_out) >= 5")]
    assert body.count("[_keep]") == 3, "X, y_dir, y_ret must each be masked exactly once"


def test_dedup_falls_back_rather_than_shrinking_x_below_the_training_floor():
    """Fail-safe: if dropping from X would leave too few rows to train on, keep X intact and
    revert to the old (inert but safe) behaviour. Corrupting the main training set to enable a
    secondary feature would be a bad trade."""
    body = TRAINER[TRAINER.index("# AUD-ML3-OUTCOMEDEDUP"):]
    body = body[:body.index("if len(X_out) >= 5")]
    assert "_MIN_ROWS_AFTER_OUTCOME_DEDUP" in body
    assert "X_out.drop(index=overlap_idx" in body, "fallback path must still exist"
    assert "train.outcome_dedup_fallback" in body, "the fallback must be observable, not silent"


def test_dedup_floor_matches_the_functions_own_viability_threshold():
    """train_model() refuses to train at all below 200 rows; the dedup floor must not allow X
    to be shrunk past that same bar."""
    m = re.search(r"_MIN_ROWS_AFTER_OUTCOME_DEDUP\s*=\s*(\d+)", TRAINER)
    assert m and int(m.group(1)) == 200
    assert "if len(X) < 200:" in TRAINER, "the 200-row training floor this mirrors must still exist"


# ── F3: suppression re-sweep ─────────────────────────────────────────────────────────────

def test_resweep_exists_and_defaults_to_dry_run():
    """It rewrites production model artifacts, so applying must be an explicit opt-in."""
    body = _func_src(TRAINER, "resweep_oos_suppression")
    assert re.search(r"def resweep_oos_suppression\(dry_run: bool = True", body)
    assert "if not dry_run:" in body, "must not write when dry_run"


def test_resweep_reuses_the_single_suppression_rule():
    """It must call _compute_oos_suppression(), not reimplement the conditions — a second copy
    of the rule is exactly the drift this codebase keeps getting bitten by."""
    body = _code_only(TRAINER, "resweep_oos_suppression")
    assert "_compute_oos_suppression(" in body
    for literal in ("0.52", "0.10"):
        assert literal not in body, f"suppression threshold {literal} must not be duplicated here"


def test_resweep_does_not_treat_a_missing_recall_as_zero():
    """FALSY-ZERO / MISSING-VALUE TRAP. _compute_oos_suppression() suppresses when
    recall == 0.0 AND precision == 0.0. Defaulting a MISSING metric to 0.0 would trip that
    condition and suppress models we simply have no metric for. The sentinel must be outside
    the valid [0,1] range."""
    body = _func_src(TRAINER, "resweep_oos_suppression")
    assert 'metrics.get("recall", -1.0)' in body
    assert 'metrics.get("precision", -1.0)' in body
    assert 'metrics.get("recall", 0' not in body
    assert 'metrics.get("recall")' not in body, "an implicit None would compare oddly; use -1.0"


def test_resweep_writes_atomically():
    """Same RACE-001 discipline as train_model()'s own save: a concurrent predict_latest() must
    never observe a torn bundle.

    An earlier version of this test only asserted that "os.replace" appeared SOMEWHERE in the
    function — a sabotage that swapped the real write for a direct `joblib.dump(bundle,
    artifact)` (i.e. non-atomic, torn-read-capable) passed all 15 tests. Assert on the actual
    write statements instead.
    """
    body = _code_only(TRAINER, "resweep_oos_suppression")
    assert "tempfile.mkstemp" in body
    assert "dir=os.path.dirname(artifact)" in body, "temp file must share the artifact's filesystem"
    assert "os.replace(tmp, artifact)" in body, "the publish step must be an atomic rename"
    # The bundle must be dumped to the TEMP path, never straight over the live artifact.
    assert "joblib.dump(bundle, tmp)" in body
    code_lines = [ln.split("#", 1)[0] for ln in body.splitlines()]
    assert not any("joblib.dump(bundle, artifact)" in ln for ln in code_lines), \
        "must never write directly over the live artifact — that is a torn read for any concurrent predict"


def test_resweep_recomputes_nothing_it_only_reapplies_the_rule():
    """It must read stored metrics, never retrain — that is what makes it cheap and safe to
    schedule. A call to train_model() here would turn a seconds-long sweep into hours."""
    body = _code_only(TRAINER, "resweep_oos_suppression")
    # Strip comments too: the atomic-write block legitimately REFERENCES train_model() in a
    # comment explaining where the discipline came from. The assertion is about CALLS.
    code_lines = [ln.split("#", 1)[0] for ln in body.splitlines()]
    assert not any("train_model(" in ln for ln in code_lines), \
        "must not retrain — this re-applies the rule to stored metrics only"
    assert 'bundle.get("metrics")' in body


def test_resweep_endpoint_is_exposed_and_dry_by_default():
    body = _func_src(ROUTES, "resweep_suppression")
    assert "dry_run: bool = True" in body
    assert "Depends(get_current_username)" in body, "must stay auth-protected"
