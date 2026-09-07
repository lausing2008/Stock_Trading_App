"""Regression test for the false-premise confidence tier boundary found in the 2026-09-06
deep audit (priority item #15), in compute_position()'s confidence-sizing block.

The T232-DE2 comment asserted "the hard-reject floor in hard_rejects.py is
min_confidence(62) * 0.90 = 55.8, so every trade that reaches this function already has
confidence >= 55.8" and hardcoded the tier boundary as the literal 62. That premise is false
for 3 of 4 real styles — hard_rejects.py's actual floor is `cfg.get("min_confidence", 62.0) *
0.90`, and real per-style min_confidence is LONG=40.0, GROWTH=45.0, SWING=50.0 (only the
never-instantiated decision-engine default/fallback is 62.0, itself the separately-documented
T234-CONFIG-DECIDE-DEFAULT-MISMATCH).

Consequence: for LONG, the real floor is 40.0*0.90=36.0, not 55.8 — so confidence values in
36-61 DO reach compute_position(), and ALL of them landed in the `else: 0.85` branch,
systematically under-sizing every LONG (and most GROWTH) position by 15% with ZERO variation
by conviction — reintroducing exactly the "one branch always fires" defect T232-DE2 was
written to fix, just shifted to the other branch.

Fix: derive the tier boundary from cfg["min_confidence"] (the same source hard_rejects.py's
own floor reads), so the tiers are self-consistent with whichever style/market this position
actually belongs to.
"""
from src.api.core.sizer import compute_position


def _game_plan(live_price=100.0):
    return {"stop": live_price * 0.90, "take_profit": live_price * 1.30}


def _compute(confidence, cfg):
    _, mults = compute_position(
        equity=100_000.0, live_price=100.0, game_plan=_game_plan(),
        confidence=confidence, research_rec=None, research_score_val=None,
        regime_state="bull", cross_style_buys=0, days_to_earnings=None, cfg=cfg,
    )
    return mults.confidence


def test_long_style_confidence_of_45_now_gets_full_conviction_not_the_dead_zone():
    """THE CORE BUG: a LONG-style trade at confidence=45 (real floor 36.0, so 45 clears the
    LONG floor with room to spare) must get the neutral 1.00 multiplier, not the pre-fix 0.85
    dead-zone bucket that EVERY confidence in 36-61 fell into for LONG."""
    cfg = {"min_confidence": 40.0}  # real LONG style config
    assert _compute(confidence=45.0, cfg=cfg) == 1.00


def test_long_style_confidence_just_below_its_own_real_floor_still_gets_deweighted():
    """Confidence genuinely below LONG's real floor (36.0) must still land in the deweighted
    bucket — the fix must not simply always return 1.00."""
    cfg = {"min_confidence": 40.0}
    assert _compute(confidence=30.0, cfg=cfg) == 0.85


def test_growth_style_confidence_of_50_now_gets_full_conviction():
    """GROWTH's real floor is 45.0*0.90=40.5 — confidence=50 clears it comfortably and must
    not fall into the pre-fix dead zone (which required >=62 regardless of style)."""
    cfg = {"min_confidence": 45.0}
    assert _compute(confidence=50.0, cfg=cfg) == 1.00


def test_swing_style_confidence_of_46_now_gets_full_conviction():
    """SWING's real floor is 50.0*0.90=45.0 — confidence=46 clears it."""
    cfg = {"min_confidence": 50.0}
    assert _compute(confidence=46.0, cfg=cfg) == 1.00


def test_missing_min_confidence_in_cfg_falls_back_to_the_same_62_default_as_hard_rejects():
    """An empty/missing cfg (e.g. the never-instantiated decision-engine default path) must
    fall back to the SAME 62.0 default hard_rejects.py itself uses, not a different or missing
    value — this keeps the sizer and the hard-reject gate consistent even in that edge case."""
    assert _compute(confidence=60.0, cfg={}) == 1.00  # 60 >= 62*0.90=55.8
    assert _compute(confidence=50.0, cfg={}) == 0.85  # 50 < 55.8


def test_confidence_of_80_or_above_always_gets_the_boosted_tier_regardless_of_style():
    """The top tier (>=80, 1.25x) is NOT style-relative — this must be unaffected by the fix."""
    for cfg in ({"min_confidence": 40.0}, {"min_confidence": 45.0}, {"min_confidence": 65.0}):
        assert _compute(confidence=85.0, cfg=cfg) == 1.25


def test_source_no_longer_hardcodes_the_literal_62_as_the_tier_boundary():
    """Source-level guard against the exact regression shape: the tier comparison must read
    cfg's own min_confidence, not a hardcoded 62 that matches no real style."""
    import pathlib

    src_path = (
        pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "core" / "sizer.py"
    )
    source = src_path.read_text()
    anchor = source.index("if confidence >= 80:")
    block = source[anchor:anchor + 300]
    assert "elif confidence >= 62:" not in block, (
        "found the exact pre-fix hardcoded boundary — the tier must be derived from "
        "cfg.get('min_confidence', ...), matching hard_rejects.py's own real floor formula."
    )
    assert "_hard_floor_for_sizing" in block
