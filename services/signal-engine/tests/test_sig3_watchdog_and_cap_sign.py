"""AUD-SIG3 — the two AI-Signal-Engine fixes from Domain 3 of the 2026-09-07 six-part audit.

Measured production evidence that motivated each
(docs/audits/2026-09-07-six-part-audit-3-ai-signal.md):

  F1 WATCHDOG LOOSENING — the "self-healing" watchdog seeded a tighten from the CALIBRATED
     value while taking its ceiling from the BULL baseline. `floor_threshold` is named and
     documented as a floor but appeared only inside a min() as a ceiling — never applied as a
     floor. Live: SWING calibrated 0.56 vs bull base 0.72; three "tightenings" walked
     0.56 -> 0.59 -> 0.62 -> 0.65, all still BELOW 0.72. Since T232-CAL2 applies the value as a
     delta across every regime, the net effect was a 7-POINT LOOSENING everywhere. Then
     tighten_count hit _MAX_TIGHTEN=3 and the watchdog locked itself out.

  F2 CAP SIGN FLIP — the compression cap restored toward np.sign(orig_dist), a snapshot taken
     ~490 lines earlier, while the guard compared only MAGNITUDES. If intervening adjustments
     moved `fused` across 0.50, the cap reversed the signal's direction instead of restoring a
     compressed one. Cap fires on 1,129 of 4,120 signals (27%).
"""
import numpy as np
import pytest


# ── F1: watchdog tighten arithmetic ──────────────────────────────────────────────────────
#
# The tighten computation is extracted here rather than importing signal_watchdog(), which
# needs a live DB session and Redis. These mirror the real expressions at calibration.py:2631+
# and are guarded by the source-text checks further down so the copy cannot drift silently.

def _tighten(current_val: float, floor_threshold: float) -> float:
    """The FIXED tighten step (calibration.py, AUD-SIG3-WATCHDOGLOOSEN)."""
    base = max(current_val, floor_threshold)
    new_val = min(base + 0.03, floor_threshold + 0.12)
    return max(new_val, floor_threshold)


def _tighten_buggy(current_val: float, floor_threshold: float) -> float:
    """The ORIGINAL step, kept so the tests demonstrate the bug rather than assert the fix in
    a vacuum."""
    return min(current_val + 0.03, floor_threshold + 0.12)


def test_the_original_bug_reproduces_swings_live_production_values():
    """Anchor: replay SWING's three tightens from its real calibrated seed and confirm they
    reproduce the value observed live in Redis (0.65) — all of them BELOW the 0.72 bull base."""
    v = 0.56
    steps = []
    for _ in range(3):
        v = _tighten_buggy(v, 0.72)
        steps.append(round(v, 4))
    assert steps == [0.59, 0.62, 0.65]
    assert v < 0.72, "every step stayed below the bull baseline — i.e. a net LOOSENING"


def test_tighten_never_returns_a_value_below_the_bull_baseline():
    """THE CORE F1 FIX. A tighten must never emit something looser than the baseline it is
    tightening toward, whatever the calibrated seed happens to be."""
    for style_floor in (0.60, 0.63, 0.72):
        for seed in (0.30, 0.55, 0.56, style_floor - 0.01, style_floor, style_floor + 0.05):
            assert _tighten(seed, style_floor) >= style_floor


def test_tighten_is_monotonically_stricter_than_its_input():
    """A tighten must move the threshold UP (or hold at the ceiling), never down."""
    for style_floor in (0.60, 0.72):
        for seed in (0.40, 0.55, 0.70, 0.80, 0.90):
            assert _tighten(seed, style_floor) >= min(seed, style_floor)


def test_swing_now_tightens_above_the_baseline_instead_of_below_it():
    """The live SWING case: seed 0.56, bull base 0.72."""
    assert _tighten_buggy(0.56, 0.72) == pytest.approx(0.59)   # was: still a loosening
    assert _tighten(0.56, 0.72) == pytest.approx(0.75)         # now: a real tightening


def test_growth_the_already_correct_case_is_unchanged():
    """GROWTH's calibrated 0.65 sits ABOVE its 0.60 bull base, so it was always working. The
    fix must not perturb it — a regression here would mean over-tightening a healthy style."""
    assert _tighten_buggy(0.65, 0.60) == pytest.approx(0.68)
    assert _tighten(0.65, 0.60) == pytest.approx(0.68)


def test_ceiling_still_caps_a_runaway_tighten():
    """floor + 0.12 remains the ceiling. It was never the cause of the bug (0.84 for SWING,
    never binding) but it must still bound a legitimate escalation.

    Note the ceiling only BINDS when the seed is already within 0.03 of it: from 0.80 with a
    0.72 floor the step lands at 0.83, below the 0.84 cap. Asserting 0.84 there would have been
    testing my arithmetic rather than the code's.
    """
    assert _tighten(0.95, 0.60) == pytest.approx(0.72)   # 0.60 + 0.12, ceiling binds
    assert _tighten(0.82, 0.72) == pytest.approx(0.84)   # 0.72 + 0.12, ceiling binds
    assert _tighten(0.80, 0.72) == pytest.approx(0.83)   # a normal step, ceiling not reached


def test_short_and_long_the_next_victims_are_now_protected():
    """SHORT (0.55 vs 0.63) and LONG (0.55 vs 0.60) were both one bad week from the same bug."""
    assert _tighten_buggy(0.55, 0.63) < 0.63, "SHORT would have loosened"
    assert _tighten_buggy(0.55, 0.60) < 0.60, "LONG would have loosened"
    assert _tighten(0.55, 0.63) >= 0.63
    assert _tighten(0.55, 0.60) >= 0.60


# ── F1: source-text guards (the extracted copy above must match the real code) ────────────

def _calibration_src() -> str:
    import pathlib
    return (pathlib.Path(__file__).resolve().parents[1]
            / "src" / "api" / "calibration.py").read_text()


def test_tighten_seeds_from_the_stricter_of_current_and_baseline():
    src = _calibration_src()
    assert "_tighten_base = max(current_val, floor_threshold)" in src, \
        "the seed must not be the raw calibrated value — that is the loosening bug"


def test_floor_threshold_is_actually_applied_as_a_floor():
    """It is NAMED a floor and documented as one; before the fix it only ever appeared inside a
    min() as a ceiling."""
    src = _calibration_src()
    assert "new_val = max(new_val, floor_threshold)" in src


def test_self_heal_clears_a_looser_than_baseline_override():
    """SWING was already deadlocked at tighten_count=3 with a stale loosening value. Fixing the
    arithmetic alone would leave it stuck forever, since the relax branch requires
    signals_7d == 0 and SWING emits 262 BUYs/7d."""
    src = _calibration_src()
    assert "signal_watchdog.cleared_loosening_override" in src
    block = src[src.index("AUD-SIG3-WATCHDOGLOOSEN (self-heal)"):][:2000]
    assert "_adj_val < floor_threshold" in block, "must detect a looser-than-baseline override"
    assert "redis_client.delete(tighten_count_key)" in block, \
        "must also reset the counter or the deadlock survives"


def test_self_heal_tolerates_a_corrupt_redis_value():
    """A non-numeric Redis value must not take down the whole watchdog run."""
    src = _calibration_src()
    block = src[src.index("AUD-SIG3-WATCHDOGLOOSEN (self-heal)"):][:2000]
    assert "except (TypeError, ValueError)" in block


# ── F2: compression cap sign agreement ───────────────────────────────────────────────────

def _cap(fused: float, fused_before: float, max_ratio: float) -> tuple[float, bool, bool]:
    """The FIXED cap (signals.py, AUD-SIG3-CAPSIGNFLIP). Returns (fused, applied, blocked)."""
    orig_dist = fused_before - 0.5
    curr_dist = fused - 0.5
    sign_agrees = (curr_dist == 0) or (np.sign(curr_dist) == np.sign(orig_dist))
    over_compressed = orig_dist != 0 and abs(curr_dist) < abs(orig_dist) * max_ratio
    if over_compressed and sign_agrees:
        return float(np.clip(0.5 + float(np.sign(orig_dist)) * abs(orig_dist) * max_ratio, 0.0, 1.0)), True, False
    return fused, False, (over_compressed and not sign_agrees)


def test_cap_still_restores_a_genuinely_over_compressed_bullish_signal():
    """The cap's INTENDED behaviour must be preserved — this is not a disable."""
    out, applied, blocked = _cap(fused=0.51, fused_before=0.58, max_ratio=0.65)
    assert applied and not blocked
    assert out == pytest.approx(0.5 + 0.08 * 0.65)   # 0.552


def test_cap_does_not_flip_a_bearish_signal_back_to_bullish():
    """THE CORE F2 FIX — the documented failure scenario, with real adjustment magnitudes.

    LONG fuses to 0.58; analyst_momentum strong_downgrade (-0.08) plus kscore<35 (-0.06) take
    it to 0.48. Two independent bearish inputs. The cap must NOT resurrect the stale bullish
    direction.
    """
    out, applied, blocked = _cap(fused=0.48, fused_before=0.58, max_ratio=0.65)
    assert not applied, "must not fire when the filters reversed the signal's direction"
    assert blocked, "the suppression must be observable, not silent"
    assert out == pytest.approx(0.48), "the bearish verdict must stand unmodified"
    assert out < 0.5


def test_the_original_bug_would_have_flipped_it():
    """Demonstrate the bug rather than only asserting the fix: the magnitude-only guard turns a
    0.48 bearish reading into a 0.552 bullish one."""
    orig_dist, curr_dist = 0.58 - 0.5, 0.48 - 0.5
    assert abs(curr_dist) < abs(orig_dist) * 0.65          # old guard passed
    buggy = 0.5 + float(np.sign(orig_dist)) * abs(orig_dist) * 0.65
    assert buggy == pytest.approx(0.552) and buggy > 0.5   # bearish -> bullish


def test_cap_works_symmetrically_for_a_bearish_base_signal():
    """A genuinely bearish base signal that got over-compressed toward neutral must still be
    restored — the fix must not be bullish-only."""
    out, applied, blocked = _cap(fused=0.49, fused_before=0.42, max_ratio=0.65)
    assert applied and not blocked
    assert out == pytest.approx(0.5 - 0.08 * 0.65)   # 0.448
    assert out < 0.5


def test_cap_does_not_flip_a_bullish_signal_back_to_bearish():
    """Mirror of the core case: a bearish base that the filters turned bullish must also be
    left alone."""
    out, applied, blocked = _cap(fused=0.52, fused_before=0.42, max_ratio=0.65)
    assert not applied and blocked
    assert out == pytest.approx(0.52)


def test_exactly_neutral_current_is_treated_as_agreeing():
    """fused == 0.5 has no direction to disagree with; np.sign(0) == 0 would otherwise fail the
    equality check and silently disable the cap for a fully-compressed signal."""
    out, applied, blocked = _cap(fused=0.5, fused_before=0.58, max_ratio=0.65)
    assert applied and not blocked
    assert out == pytest.approx(0.552)


def test_cap_is_a_noop_when_compression_was_mild():
    out, applied, blocked = _cap(fused=0.56, fused_before=0.58, max_ratio=0.65)
    assert not applied and not blocked and out == pytest.approx(0.56)


def test_cap_is_a_noop_when_the_base_signal_was_neutral():
    """orig_dist == 0 means there was nothing to restore; guard against a divide-by-intent."""
    out, applied, blocked = _cap(fused=0.50, fused_before=0.50, max_ratio=0.65)
    assert not applied and not blocked


def test_signals_source_requires_sign_agreement():
    """Guard the real code, not just the local copy."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "src" / "generators" / "signals.py").read_text()
    assert "_sign_agrees" in src
    assert "np.sign(curr_dist) == np.sign(orig_dist)" in src
    assert "and _sign_agrees" in src, "the guard must be part of the cap's own condition"
    assert "compression_cap_sign_flip_blocked" in src, "suppression must be observable"
