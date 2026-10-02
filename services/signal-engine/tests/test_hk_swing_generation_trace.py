"""HK-SWING-TRACE — make the generator's three explanations distinguishable.

THE MEASUREMENT THAT COULD NOT BE MADE. HK SWING converted ZERO of 211 signals to BUY over a
7-day window (2026-10-02 trace). Three explanations call for opposite responses:

    no_opportunity      the evidence was never near the bar — the generator is working
    suppression_bound   the score cleared the bar and a compression pushed it under
    stale_or_missing    a required input was absent, so the score was never a verdict

Stored `reasons` carried the FINAL fused score and the gate flags, but never the score the
compression chain STARTED from — so the first two are indistinguishable after the fact. The
cap's own arithmetic had the value all along (`fused_before_filters`); it was simply never
recorded.

These tests pin the instrumentation AND its observational character. Recording a score must
not change one.
"""
import ast
import pathlib
import re
import textwrap

import pytest

_SIG = (pathlib.Path(__file__).resolve().parents[1]
        / "src" / "generators" / "signals.py").read_text()


def _cap_block() -> str:
    """The compression-cap block, sliced on LINE boundaries so it can be parsed.

    Slicing from the marker character leaves the first line flush while every following line
    keeps its indent, which makes `textwrap.dedent` a silent no-op and `ast.parse` raise
    IndentationError on code that is perfectly valid in place. That is the third time this
    exact slip has appeared in this session's test harnesses; doing it once, here, is why the
    helper exists.
    """
    i = _SIG.index("# ── Compression cap ")
    i = _SIG.rindex("\n", 0, i) + 1
    j = _SIG.index("# ── Weekly BUY gate", i)
    j = _SIG.rindex("\n", 0, j) + 1
    block = textwrap.dedent(_SIG[i:j])
    assert not block.startswith(" "), "dedent did not take — the slice is still mid-line"
    return block


# ── the fields exist and mean what they say ─────────────────────────────────────────────

def test_the_pre_compression_score_is_recorded():
    """Without it, 'never near the bar' and 'compressed under the bar' look identical."""
    block = _cap_block()
    assert 'reasons["fused_pre_compression"]' in block
    assert "fused_before_filters" in block


def test_the_post_compression_score_is_recorded_beside_it():
    assert 'reasons["fused_post_compression"]' in _cap_block()


def test_the_cap_effect_is_separable_from_the_chain_effect():
    """The cap can RESTORE a score. If only the final value were stored, the chain's effect
    and the cap's undoing of it would be one unattributable number."""
    block = _cap_block()
    assert 'reasons["fused_post_cap"]' in block
    i = block.index('reasons["compression_cap_applied"] = True')
    j = block.index('reasons["fused_post_cap"]')
    assert i < j, "the restored value must be recorded in the arm that restores it"


def test_a_zero_distance_base_records_no_ratio_rather_than_dividing_by_zero():
    """`orig_dist == 0` is a real case — a perfectly neutral base signal. The ratio is then
    undefined, and `None` says so; 0.0 would claim total compression."""
    block = _cap_block()
    assert "if orig_dist else None" in block


# ── the instrumentation is observational ────────────────────────────────────────────────

def test_recording_the_score_does_not_change_the_score():
    """THE ACCEPTANCE PROPERTY. Every added line must be a write INTO `reasons`, never an
    assignment to `fused` or to anything the cap reads."""
    block = _cap_block()
    added = [ln for ln in block.splitlines()
             if "fused_pre_compression" in ln or "fused_post_compression" in ln
             or "compression_total_ratio" in ln or "fused_post_cap" in ln]
    assert added, "the instrumentation was not found"
    for ln in added:
        stripped = ln.strip()
        if stripped.startswith("#") or not stripped:
            continue
        assert stripped.startswith('reasons['), \
            f"instrumentation must only write to reasons, got: {stripped}"


def test_the_cap_arithmetic_is_untouched():
    """The restore formula and its sign guard are the live behaviour here; the trace sits
    beside them and must not have edited them.

    Asserted on the PARSED expression, not its text. A source-text assertion carrying the
    literal 0.5 both trips the T401 ratchet and keeps passing if the arithmetic around it is
    edited — which is the ratchet's whole point. The restore is executed below against the
    real constants, so a changed formula fails on its value rather than on its spelling.
    """
    import numpy as np

    block = _cap_block()
    tree = ast.parse(block)
    restores = [n for n in ast.walk(tree)
                if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "fused" for t in n.targets)
                and "max_ratio" in ast.dump(n)]
    assert len(restores) == 1, "the cap must restore in exactly one place"
    expr = ast.unparse(restores[0].value)

    # Execute the real expression against known inputs and check the VALUE it produces.
    def restore(orig_dist, max_ratio):
        return eval(expr, {"np": np, "float": float, "abs": abs},  # noqa: S307 — repo-own source
                    {"orig_dist": orig_dist, "max_ratio": max_ratio})

    assert restore(0.24, 0.55) == pytest.approx(0.632), "a bar-clearing signal's cap floor"
    assert restore(-0.24, 0.55) == pytest.approx(0.368), "the restore must preserve direction"
    assert restore(0.0, 0.55) == pytest.approx(0.5)

    # The sign guard is a control-flow property, so its STRUCTURE is what matters.
    guards = [n for n in ast.walk(tree)
              if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == "_sign_agrees" for t in n.targets)]
    assert len(guards) == 1, "the sign guard must still exist and be computed once"
    assert "np.sign(curr_dist)" in ast.unparse(guards[0].value)
    assert "np.sign(orig_dist)" in ast.unparse(guards[0].value)
    ifs = [n for n in ast.walk(tree) if isinstance(n, ast.If)
           and "_sign_agrees" in ast.dump(n.test) and "max_ratio" in ast.dump(n.test)]
    assert ifs, "the cap must remain gated on BOTH over-compression and sign agreement"


def test_the_snapshot_point_was_not_moved():
    """`fused_before_filters` is taken at a specific point in the pipeline. Moving it would
    silently change what every recorded ratio means — and the cap's behaviour with it."""
    assert _SIG.count("fused_before_filters = fused") == 1


def test_no_threshold_was_changed_by_this_trace():
    """The trace exists to ANSWER whether the bar is binding, not to move it. Values parsed,
    not matched as text, per T401."""
    tree = ast.parse(_SIG)
    profiles = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.AnnAssign)
                    and isinstance(n.target, ast.Name)
                    and n.target.id == "_STYLE_PROFILES")
    prof = ast.literal_eval(profiles.value)
    swing = prof["SWING"]
    assert swing["buy_threshold"]["choppy"] == 0.74, "the measured HK regime's bar"
    assert swing["buy_threshold"]["bull"] == 0.72
    assert swing["max_compress_ratio"] == 0.55
    assert prof["SHORT"]["buy_threshold"]["bull"] == 0.63


# ── the recorded fields support the classification they exist for ───────────────────────

def _classify(pre, post, bar, missing_inputs):
    """The classification these fields make possible, stated once so the test asserts a rule
    rather than a sentence in a document."""
    if missing_inputs:
        return "stale_or_missing"
    if pre is None:
        return "unknown"
    if pre >= bar > post:
        return "suppression_bound"
    if pre < bar:
        return "no_opportunity"
    return "passed"


def test_a_signal_that_cleared_the_bar_and_was_compressed_under_it_is_separable():
    assert _classify(0.78, 0.70, 0.74, []) == "suppression_bound"


def test_a_signal_that_was_never_near_the_bar_is_separable():
    assert _classify(0.61, 0.58, 0.74, []) == "no_opportunity"


def test_a_missing_input_outranks_both():
    """A score built on absent inputs is not a verdict about the market, whatever its value."""
    assert _classify(0.80, 0.60, 0.74, ["breadth_pct"]) == "stale_or_missing"


def test_an_unrecorded_pre_score_is_unknown_not_no_opportunity():
    """Signals written before this instrumentation carry no pre-score. Treating their absence
    as 'no opportunity' would manufacture the very conclusion the trace is testing."""
    assert _classify(None, 0.58, 0.74, []) == "unknown"
