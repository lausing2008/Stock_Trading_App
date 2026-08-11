"""Tests for AUD261-IC-QUALITY-NO-NEGATIVE-TIER.

information_coefficient()'s "quality" label was `"excellent" if ic_mean > 0.05 else "good" if
ic_mean > 0.02 else "poor"` — an actively anti-predictive IC (ranking by fused_prob produces
WORSE returns than random) rendered under the exact same "poor" label as a merely weak,
near-zero-but-positive IC. Those are qualitatively different findings: one says "barely any
signal", the other says "the ranking is inverted" — a much more actionable, urgent finding.

Fixed: added an explicit "inverted" tier for ic_mean < 0, so a genuine sign flip in the
model's ranking power is visible rather than blending into "poor".

outcomes.py can't be imported directly in this test environment (its import chain pulls in
common.jwt_auth) — the quality-tier expression itself is small, pure, and self-contained
enough to extract via exec() and run directly against synthetic ic_mean values, without
needing a real DB session at all.
"""
import pathlib

_OUTCOMES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "outcomes.py"
_OUTCOMES_SOURCE = _OUTCOMES_PATH.read_text()


def _quality_for(ic_mean: float) -> str:
    """Pulls the real quality-tier expression out of information_coefficient() and exec()s
    it against a synthetic ic_mean, isolated from the surrounding Spearman-IC computation and
    DB session this test doesn't need."""
    start = _OUTCOMES_SOURCE.index('"quality": (', _OUTCOMES_SOURCE.index("def information_coefficient("))
    end = _OUTCOMES_SOURCE.index("),", start) + 1
    expr = _OUTCOMES_SOURCE[start:end].split(":", 1)[1].strip().rstrip(",")
    namespace = {"ic_mean": ic_mean}
    return eval(expr, namespace)  # noqa: S307 — isolated eval of one real expression


def test_a_strong_negative_ic_is_labelled_inverted_not_poor():
    assert _quality_for(-0.15) == "inverted"


def test_a_mildly_negative_ic_is_also_labelled_inverted():
    assert _quality_for(-0.001) == "inverted"


def test_exactly_zero_ic_is_poor_not_inverted():
    """0.0 is the boundary: no measured predictive power in either direction is a genuinely
    different (weaker, not inverted) finding than a negative IC — it must not falsely read as
    "the ranking is inverted" when there's no evidence of inversion at all."""
    assert _quality_for(0.0) == "poor"


def test_a_weak_positive_ic_is_still_poor():
    assert _quality_for(0.019) == "poor"


def test_a_good_ic_is_unaffected_by_this_fix():
    assert _quality_for(0.03) == "good"


def test_an_excellent_ic_is_unaffected_by_this_fix():
    assert _quality_for(0.06) == "excellent"


def test_boundary_at_0_02_resolves_to_good_not_poor():
    assert _quality_for(0.0200001) == "good"


def test_boundary_at_0_05_resolves_to_excellent_not_good():
    assert _quality_for(0.0500001) == "excellent"
