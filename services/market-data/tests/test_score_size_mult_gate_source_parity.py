"""Tests for AUD262-FALLBACK-SIZES-LARGER-THAN-DE.

The T188 score-to-size multiplier used to only derive from `score`/`min_entry_score` when
`gate_source == "de"` — on the fallback/legacy path (active during a Decision Engine outage)
it was unconditionally pinned to `score_size_mult = 1.0`, regardless of how marginal the
candidate's own score was. Since `score` on the fallback path is _should_enter()'s own
returned score, compared against the SAME `min_entry_score` cfg key DE uses (confirmed via
_record_de_shadow_comparison() passing the identical cfg value for both paths), there was no
real reason for the multiplier to be DE-only — pinning it to 1.0 on fallback meant a marginal
candidate (score exactly at min_entry_score) got FULL size (1.0x) on the fallback path but
REDUCED size (0.75x) under DE — 33% MORE capital on the weakest-conviction trades specifically
during an outage, when caution matters most.

Fixed: score_size_mult is now derived identically on every gate_source.

paper_trading_engine.py's _scan_for_entries() is a 1000+ line function with heavy
session/portfolio/live_prices state — far too large to import or drive end-to-end for this
narrow scoring-multiplier calculation. This extracts just the score_size_mult snippet's real
source text and exec()s it directly against synthetic score/min_entry_score/gate_source
inputs, matching the same narrow-closure-extraction technique already used for
_lookup_outcome_price() in signal-engine's own outcomes.py tests.
"""
import pathlib

_ENGINE_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
)
_ENGINE_SOURCE = _ENGINE_PATH.read_text()


def _compute_score_size_mult(score: float, min_entry_score: float, gate_source: str = "de") -> float:
    """Pulls the real score_size_mult computation out of _scan_for_entries() and exec()s it
    against synthetic inputs — the exact statements between the T188 comment and the
    risk_dollar computation that consumes score_size_mult, with `notes = notes + [...]`
    stripped since it's a side effect irrelevant to this calculation."""
    start = _ENGINE_SOURCE.index('_min_score_cfg = cfg.get("min_entry_score", 4)')
    end = _ENGINE_SOURCE.index("_risk_base     = equity", start)
    body = _ENGINE_SOURCE[start:end]
    # dedent by 8 (the real source's indentation inside _scan_for_entries()'s own body), then
    # re-indent by 4 to nest inside the wrapper function below. Replace the notes-append side
    # effect's body with a no-op `pass` (rather than dropping the line, which would leave the
    # enclosing `if` with no body at all) — the side effect itself is irrelevant to isolating
    # score_size_mult, but the `if` guard around it is real, load-bearing source we still want
    # exec()'d unmodified.
    dedented = [ln[8:] if ln.startswith(" " * 8) else ln for ln in body.splitlines()]
    reindented = [
        "        pass" if "notes = notes +" in ln else ("    " + ln if ln.strip() else ln)
        for ln in dedented
    ]
    func_source = (
        "def _compute(score, min_entry_score, gate_source):\n"
        "    cfg = {\"min_entry_score\": min_entry_score}\n"
        + "\n".join(reindented)
        + "\n    return score_size_mult\n"
    )
    namespace: dict = {}
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_compute"](score, min_entry_score, gate_source)


def test_de_path_score_at_minimum_gets_the_reduced_floor_multiplier():
    assert _compute_score_size_mult(score=4, min_entry_score=4, gate_source="de") == 0.75


def test_fallback_path_score_at_minimum_now_ALSO_gets_the_reduced_floor_multiplier():
    """The core regression this fix targets: before the fix, this returned 1.0 (pinned) — a
    marginal candidate got FULL size on the exact path active during a DE outage."""
    assert _compute_score_size_mult(score=4, min_entry_score=4, gate_source="fallback") == 0.75


def test_legacy_path_score_at_minimum_also_gets_the_reduced_floor_multiplier():
    assert _compute_score_size_mult(score=4, min_entry_score=4, gate_source="legacy") == 0.75


def test_de_and_fallback_agree_at_every_score_excess_level():
    """Regression guard: gate_source must no longer change the result at all — the two paths
    must be byte-identical for the same (score, min_entry_score) pair."""
    for score, min_entry_score in [(4, 4), (5, 4), (6, 4), (8, 4), (2, 4), (0, 4), (10, 6)]:
        de_val = _compute_score_size_mult(score, min_entry_score, gate_source="de")
        fallback_val = _compute_score_size_mult(score, min_entry_score, gate_source="fallback")
        assert de_val == fallback_val, f"diverged at score={score}, min={min_entry_score}"


def test_score_two_above_minimum_is_the_neutral_multiplier_on_every_path():
    for gs in ("de", "fallback", "legacy"):
        assert _compute_score_size_mult(score=6, min_entry_score=4, gate_source=gs) == 1.0


def test_score_far_above_minimum_caps_at_the_1_25_ceiling_on_every_path():
    for gs in ("de", "fallback", "legacy"):
        assert _compute_score_size_mult(score=12, min_entry_score=4, gate_source=gs) == 1.25


def test_score_far_below_minimum_floors_at_0_75_on_every_path():
    for gs in ("de", "fallback", "legacy"):
        assert _compute_score_size_mult(score=-5, min_entry_score=4, gate_source=gs) == 0.75


def test_the_write_side_no_longer_branches_on_gate_source_at_all():
    """Regression guard against the specific old code shape reappearing: the block must have
    no `if gate_source == "de"` conditional guarding score_size_mult's own computation."""
    start = _ENGINE_SOURCE.index('# T188: Score-to-size multiplier')
    end = _ENGINE_SOURCE.index("_risk_base     = equity", start)
    block = _ENGINE_SOURCE[start:end]
    assert 'if gate_source == "de"' not in block
    assert "score_size_mult = 1.0" not in block
