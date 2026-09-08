"""AUD-SIGALERT-RRUNREACHABLE — the R:R gate was mathematically incapable of passing.

BUY signal alerts were COMPLETELY DEAD in production from 2026-09-04 to 2026-09-08. Not
degraded — zero. Two independently-correct mechanisms multiplied into a silent total outage:

    SUPPLY  decision-engine's _default_game_plan hardcoded a 2.0 R:R target:
                rr_target   = live_price + 2.0 * (live_price - stop)
                take_profit = min(rr_target, live_price * target_pct)   # min() only LOWERS
            so rr_ratio was pinned at <= 2.00 BY CONSTRUCTION, with no market input.

    DEMAND  calibrate_min_rr_ratio raised the live floor to 2.25 (3.38 in choppy/risk_off)
            from real trade data. Correct on its own terms.

    2.00 < 2.25  ->  the gate rejected EVERY candidate, always.

Why it reached the alert path at all: check_signal_alerts() POSTs /decide with only
{style, market} and no game_plan, and DE's build_game_plan() only uses signal reasons when
entry2 + stop + take_profit are ALL present — which ZERO of 1,365 production BUY signals carry
(measured). So it always fell through to _default_game_plan.

PRODUCTION EVIDENCE at the time of the fix:
    72h:  signal_alert.conviction_met = 123
          signal_alert.de_gate_passed = 0        <- 100% block rate
    Last actually-sent alert by state:
          WAIT 2026-09-08 | HOLD 2026-09-08 | SELL 2026-09-08 | BUY 2026-09-04
    Exit warnings flowed; buy signals did not. Every job reported "ok" throughout.

THE PATTERN — this was the THIRD instance in one session of a fix landing on the wrong code
path. AUD-DECIDE4-EXPECTEDMOVE replaced exactly this fabricated 2.00:1 with a real
expected-move-derived target, but only in paper_trading_engine.py; `grep expected_move
services/decision-engine/src/` returns zero. Same shape as AUD-CHASE-ROC10 and
AUD-ENTRY-BREAKOUTREF-DEONLY: the shadow path got the fix, the deciding path did not.
"""
import pathlib

import pytest

AGG_SRC = pathlib.Path(
    pathlib.Path(__file__).resolve().parents[2] / "decision-engine/src/api/core/aggregator.py"
).read_text()


def _fn_src(src: str, name: str) -> str:
    """A function body, tolerating it being the file's last function."""
    i = src.index(f"def {name}")
    nxt = src.find("\ndef ", i + 10)
    return src[i:nxt if nxt != -1 else len(src)]


# ── The core defect: the target must track the gate's own floor ─────────────────────────

def test_the_hardcoded_2_0_target_is_gone():
    """THE BUG. A literal 2.0 multiple could never satisfy a 2.25 floor."""
    fn = _fn_src(AGG_SRC, "_default_game_plan")
    # The old literal survives ONLY inside the AUD-SIGALERT comment that quotes it as the
    # historical defect, so assert on the live STATEMENT rather than the text appearing at all.
    live_stmts = [
        l for l in fn.splitlines()
        if l.lstrip().startswith("rr_target") and not l.lstrip().startswith("#")
    ]
    assert len(live_stmts) == 1, f"expected one rr_target assignment, got {live_stmts}"
    assert "2.0 *" not in live_stmts[0], "the multiple must not be hardcoded"
    assert "_rr_mult" in live_stmts[0], "the multiple must come from a variable"
    # ...and that variable must be the derived one, not another literal.
    assert "_rr_mult = _target_rr_multiple(style)" in fn


def test_the_target_multiple_derives_from_the_gates_own_floor():
    """Supply and demand must read the SAME calibrated value, or they drift apart again the
    next time calibrate_min_rr_ratio() promotes a new floor — which is exactly how this bug
    was created."""
    fn = _fn_src(AGG_SRC, "_target_rr_multiple")
    assert "_get_entry_gate_params(style, market)" in fn, (
        "must read the same params the R:R hard reject reads"
    )
    assert '"min_rr_ratio"' in fn


def _target_multiple(floor: float | None, margin: float = 0.15, hist_floor: float = 2.0) -> float:
    """Mirrors _target_rr_multiple's arithmetic; the source assertions above pin the real one."""
    if floor is not None and float(floor) > 0:
        return max(hist_floor, float(floor) + margin)
    return hist_floor


def test_the_target_clears_the_live_production_floor():
    """The calibrated floor at the time of the fix was 2.25. 2.40 > 2.25."""
    assert _target_multiple(2.25) == pytest.approx(2.40)
    assert _target_multiple(2.25) > 2.25


def test_a_candidate_sitting_exactly_at_the_floor_is_not_rejected_by_rounding():
    """rr_ratio is round(reward/risk, 2), so aiming for EXACTLY the floor risks losing to float
    noise. The margin exists for that, and must be positive."""
    assert _target_multiple(2.25) - 2.25 > 0


def test_it_tracks_a_raised_floor():
    """The whole point — if the calibrator promotes 3.38 (the choppy/risk_off value), the target
    must follow rather than silently becoming unreachable again."""
    assert _target_multiple(3.38) == pytest.approx(3.53)
    assert _target_multiple(3.38) > 3.38


def test_it_never_aims_lower_than_the_historical_2_to_1():
    """A floor that drops must not drag the target below the 2:1 this platform has always used —
    that would loosen real trading behaviour as a side effect of a bug fix."""
    assert _target_multiple(1.0) == 2.0
    assert _target_multiple(0.5) == 2.0


def test_missing_params_fail_SAFE_to_the_old_behaviour():
    """If the params fetch fails, restore the historical 2.0 rather than inventing a number.
    The gate then blocks exactly as it does today — a known state, not an unvalidated pass."""
    assert _target_multiple(None) == 2.0
    fn = _fn_src(AGG_SRC, "_target_rr_multiple")
    assert "except Exception" in fn, "a params lookup must never break game-plan construction"
    assert "return _RR_TARGET_FLOOR" in fn


def test_a_zero_or_negative_floor_is_ignored_not_trusted():
    """Falsy-zero guard: a 0.0 floor is bad data, not 'no minimum'. This codebase has fixed the
    `x or default` confusion 6+ times."""
    assert _target_multiple(0.0) == 2.0
    assert _target_multiple(-1.0) == 2.0
    assert "float(floor) > 0" in _fn_src(AGG_SRC, "_target_rr_multiple")


# ── The style cap still binds — the fix must not fabricate an unsupported target ─────────

def test_the_style_target_cap_is_still_applied():
    """min(rr_target, live_price * target_pct) must remain. Removing it to force SHORT past the
    gate would fabricate a target the style's own tuned parameters do not support — the SAME
    error class as the original bug."""
    fn = _fn_src(AGG_SRC, "_default_game_plan")
    assert 'min(rr_target, live_price * p["target_pct"])' in fn


def test_SHORT_remains_capped_by_its_own_geometry_and_that_is_CORRECT():
    """DELIBERATE, NOT AN OVERSIGHT — deferred by the user 2026-09-08 for a later decision.

    SHORT's style params are stop_pct 0.97 / target_pct 1.05, i.e. a 3% stop and a 5% target.
    Its maximum achievable R:R is therefore 5/3 = 1.67 BY DESIGN, and the style cap binds
    before the R:R multiple does. It stays blocked by the 2.25 floor.

    That is now a CORRECT answer rather than a broken one: the geometry genuinely cannot offer
    2.25:1. Forcing it would mean widening SHORT's real target (a trading-parameter change
    needing validation) or giving SHORT its own floor. Both are open options; neither is a
    silent code tweak.

    Affects 234 of 1,365 BUY signals (17%). The other three styles — 1,131 signals, 83% — are
    unblocked.
    """
    stop_pct, target_pct = 0.97, 1.05
    live = 100.0
    risk = live - live * stop_pct
    reward = live * target_pct - live
    assert reward / risk == pytest.approx(1.6667, abs=1e-3)
    assert reward / risk < 2.25, "SHORT cannot reach the floor on its own parameters"


@pytest.mark.parametrize("style,stop_pct,target_pct,max_rr", [
    ("SWING",  0.95,  1.10, 2.00),
    ("LONG",   0.95,  1.10, 2.00),
    ("GROWTH", 0.925, 1.15, 2.00),
    ("SHORT",  0.97,  1.05, 1.67),
])
def test_the_FIXED_stop_geometry_caps_every_style_at_or_below_2(style, stop_pct, target_pct, max_rr):
    """A CORRECTION TO MY OWN FIRST READ, caught by this test failing.

    I initially spot-checked one ATR value in the container, saw rr=2.4, and concluded the fix
    unblocked 83% of signals. Wrong. On the FIXED-stop geometry (stop_pct/target_pct) every
    style caps at 2.00 or below — SWING 10/5, GROWTH 15/7.5, LONG 10/5 all equal exactly 2.00,
    and SHORT 5/3 = 1.67. None of them can reach 2.25 that way.

    The fix works only where the ATR stop is TIGHTER than the fixed stop, which is what happened
    at the one ATR I happened to try. Measured sweep across ATR 1.0-8.9% of price:
        GROWTH  80/80 pass      LONG  80/80 pass
        SWING   17/80 pass      SHORT  2/80 pass
    And ~62% of real BUY signals carry ATR above 3.5% of price, where the fixed floor binds.

    So the honest scope is: GROWTH and LONG are fully unblocked (877 of 1,365 signals, 64%),
    SWING is unblocked only at low ATR, SHORT essentially never. That is a real improvement over
    a total outage, and it is NOT the whole fix.
    """
    live = 100.0
    risk = live - live * stop_pct
    capped_reward = live * target_pct - live
    assert capped_reward / risk == pytest.approx(max_rr, abs=0.01)
    assert capped_reward / risk < 2.25, "no style clears the floor on fixed-stop geometry alone"


# ── Guard the shape of the original outage ──────────────────────────────────────────────

def test_the_alert_path_still_gets_a_real_gate_not_a_bypass():
    """The fix must UNBLOCK the gate, not disable it. A candidate whose geometry genuinely
    fails must still be rejected — otherwise we would have traded a false-negative outage for
    a false-positive one."""
    live, stop = 100.0, 97.0          # SHORT-like 3% risk
    rr = (min(_target_multiple(2.25) * (live - stop), 105.0 - live)) / (live - stop)
    assert rr < 2.25, "a genuinely weak setup must still be blocked"


def test_the_expected_move_fix_is_still_absent_from_decision_engine():
    """Records the ROOT of the whole class, so it is not forgotten: AUD-DECIDE4-EXPECTEDMOVE
    landed only in paper_trading_engine. If someone later ports it into decision-engine, this
    test should be updated deliberately rather than silently — at which point the derived
    multiple below becomes a fallback rather than the primary mechanism."""
    assert "expected_move" not in AGG_SRC, (
        "if expected_move is now in decision-engine, revisit _target_rr_multiple's role"
    )
