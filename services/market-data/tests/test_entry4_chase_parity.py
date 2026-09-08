"""AUD-ENTRY4 — the three entry-timing fixes from Domain 4 of the 2026-09-07 six-part audit.

All three findings were gates that EXIST, are configured, and are threaded — but could not
block anything. Two were mathematically incapable of firing; one was on the wrong code path.
None of them would fail a code review, and none produced an error.

Measured production evidence
(docs/audits/2026-09-07-six-part-audit-4-entry-timing.md):

  F1 roc_10 ABSENT FROM DE — `grep -rn "roc_10" services/decision-engine/` returned ZERO
     matches, while decision_engine_mode defaults to "primary". Closed trades with
     roc_10 >= 10% returned -2.39% at a 16.7% win rate vs -0.32% / 34.8% for the rest.
     ANF entered at roc_10 = 33.0% with a measured T171 gap of only +0.37%.
  F2/F3 SELF-REFERENTIAL BREAKOUT — breakout was derived from live_price, so
     ext_pct = (1/breakout_pct - 1)*100, a CONSTANT: SHORT -0.99%, SWING -1.96%, LONG -2.91%,
     GROWTH -3.38%, against a +6% threshold. Zero "above breakout" rejections in 30 days; all
     111 fallback-path trades took the same price-zone branch.
"""
import pathlib

import pytest

import src.services.paper_trading_engine as pte

# pte.__file__ = <repo>/services/market-data/src/services/paper_trading_engine.py
# parents: [0]=services [1]=src [2]=market-data [3]=services [4]=<repo>
_DE_HARD_REJECTS = (pathlib.Path(pte.__file__).resolve().parents[4]
                    / "services" / "decision-engine" / "src" / "api" / "core" / "hard_rejects.py")
DE_SRC = _DE_HARD_REJECTS.read_text()


# ── F1: anti-chase parity between the fallback and the AUTHORITATIVE gate ────────────────

def test_roc10_gate_exists_in_decision_engine():
    """THE CORE F1 FIX. decision-engine is the authoritative gate; roc_10 appeared nowhere in
    it, so the validated anti-chase filter had never blocked a real entry."""
    assert "roc_10" in DE_SRC, "the anti-chase gate must exist on the path that decides entries"
    assert "_MAX_ROC10_FOR_ENTRY" in DE_SRC


def test_roc10_threshold_matches_the_fallback_exactly():
    """The two gates screen the same candidates on two paths. A divergence would mean the
    authoritative gate and the shadow-logged fallback disagree about the same trade."""
    import re
    m = re.search(r"_MAX_ROC10_FOR_ENTRY\s*=\s*([\d.]+)", DE_SRC)
    assert m, "decision-engine must declare the threshold explicitly"
    assert float(m.group(1)) == pte._MAX_ROC10_FOR_ENTRY_PAPER == 10.0


def test_roc10_rejection_precedes_scoring():
    """It is a HARD reject — it must block outright, not merely subtract points."""
    block = DE_SRC[DE_SRC.index("AUD-ENTRY4-CHASEPARITY: the 10-day"):][:2400]
    assert "return (" in block, "must return a rejection reason, not adjust a score"


def test_roc10_gate_tolerates_a_malformed_value():
    """reasons come from JSON; a non-numeric roc_10 must not 500 the whole decision."""
    block = DE_SRC[DE_SRC.index("AUD-ENTRY4-CHASEPARITY: the 10-day"):][:2400]
    assert "except (TypeError, ValueError)" in block


def test_roc10_gate_uses_is_not_none_not_truthiness():
    """FALSY-ZERO: roc_10 == 0.0 is a real reading (a flat stock) and must reach the comparison
    rather than being skipped by a truthiness check."""
    block = DE_SRC[DE_SRC.index("AUD-ENTRY4-CHASEPARITY: the 10-day"):][:2400]
    assert "_roc10 is not None" in block
    assert "if _roc10:" not in block


def test_elevated_volume_gap_condition_ported_too():
    """The AUD-GAPCHASE-EARNINGSVOL half also existed only in the fallback."""
    assert "elevated volume" in DE_SRC
    assert "volume_z" in DE_SRC


# ── F2/F3: the self-referential breakout ────────────────────────────────────────────────

def _gp(current_price: float, style: str, signal_close: float | None):
    reasons = {} if signal_close is None else {"last_price": signal_close}
    return pte._build_game_plan_for_style("TEST", style, current_price, reasons, atr=None)


def test_the_original_bug_reproduces_a_constant_extension():
    """Demonstrate the bug, not just the fix: with a live-anchored breakout, ext_pct is a
    constant regardless of price, and always negative — so a +6% threshold is unreachable."""
    for style, expected in (("SHORT", -0.99), ("SWING", -1.96), ("LONG", -2.91), ("GROWTH", -3.38)):
        vals = []
        for price in (12.34, 87.50, 145.54, 903.21):
            gp = _gp(price, style, signal_close=None)   # no signal anchor -> old behaviour
            vals.append((price / gp["breakout"] - 1) * 100)
        assert all(v < 0 for v in vals), f"{style}: every value negative -> guard can never fire"
        assert vals[0] == pytest.approx(expected, abs=0.3)


def test_breakout_ref_is_anchored_to_the_signal_close_not_the_live_price():
    """THE CORE F2/F3 FIX — the level must not move with the price it is being compared to."""
    gp_a = _gp(100.0, "SWING", signal_close=100.0)
    gp_b = _gp(130.0, "SWING", signal_close=100.0)   # price ran 30% since the signal
    assert gp_a["breakout_ref"] == pytest.approx(gp_b["breakout_ref"]), \
        "breakout_ref must depend on the signal close alone"


def test_a_real_run_up_now_produces_a_large_positive_extension():
    """The case the guard exists for: signal calibrated at 100, price now 130."""
    gp = _gp(130.0, "SWING", signal_close=100.0)
    ext = (130.0 / gp["breakout_ref"] - 1) * 100
    assert ext > 6.0, f"a 30% run-up must exceed the +6% threshold (got {ext:.1f}%)"


def test_breakout_itself_stays_live_anchored_for_sizing():
    """Only the EXTENSION judgement is signal-anchored. stop/target/sizing must still track the
    price we will actually fill at — moving those would be a far worse bug."""
    gp = _gp(130.0, "SWING", signal_close=100.0)
    assert gp["breakout"] == pytest.approx(130.0 * 1.020, abs=0.3)
    assert gp["breakout"] != pytest.approx(gp["breakout_ref"])


def test_missing_signal_close_falls_back_to_the_old_inert_behaviour():
    """FAIL-OPEN, deliberately. A missing anchor must not start rejecting every candidate —
    that would turn a dead gate into an outage."""
    gp = _gp(100.0, "SWING", signal_close=None)
    assert gp["breakout_ref"] == pytest.approx(gp["breakout"])


def test_malformed_signal_close_falls_back_rather_than_raising():
    for bad in ("not-a-number", "", 0, -5.0):
        gp = pte._build_game_plan_for_style("TEST", "SWING", 100.0, {"last_price": bad}, atr=None)
        assert gp["breakout_ref"] == pytest.approx(gp["breakout"]), f"bad anchor {bad!r}"


def test_price_zone_penalty_is_now_reachable():
    """F3: with a live-anchored breakout the -3 'chasing risk' branch was unreachable because
    the first branch was always true. Verify the ordering can now select it."""
    gp = _gp(130.0, "SWING", signal_close=100.0)
    entry2, bo = gp["entry2"], gp["breakout_ref"]
    live = 130.0
    assert not (entry2 <= live <= bo), "must NOT fall in the optimal-zone branch"
    assert not (live < entry2)
    assert not (bo < live <= bo * 1.03), "beyond the 'just above breakout' band too"
    # ...therefore the else branch (-3 chasing risk) is selected.


def test_a_normal_entry_still_scores_the_optimal_zone():
    """The fix must not turn every entry into a -3. A price near its signal close is still
    'optimal zone'."""
    gp = _gp(100.5, "SWING", signal_close=100.0)
    assert gp["entry2"] <= 100.5 <= gp["breakout_ref"]


def test_consumers_use_breakout_ref():
    src = pathlib.Path(pte.__file__).read_text()
    assert "_breakout_for_ext = game_plan.get(\"breakout_ref\") or breakout" in src
    assert "live_price / float(_breakout_for_ext)" in src, "extension guard must use the anchor"
    assert "if entry2 <= live_price <= _bo:" in src, "price-zone must use the anchor"
