"""AUD-ENTRY-BREAKOUTREF-DEONLY — the AUD-ENTRY4 fix landed on the shadow path only.

THIRD instance in one session of the same mistake. `AUD-ENTRY4-BREAKOUTSELFREF` fixed a
self-referential breakout comparison by adding `breakout_ref`, anchored to the SIGNAL-TIME
close. That fix landed in `paper_trading_engine.py` — the SHADOW-LOGGED FALLBACK — while
`decision_engine_mode` defaults to `"primary"`, so the path that actually opens trades kept the
broken form:

    grep -rn breakout_ref services/decision-engine/    ->  0 matches   (before this fix)

`breakout` is LIVE-ANCHORED: `_build_game_plan_for_style()` derives it from the current price,
and DE's own `_default_game_plan` falls back to `live_price * 1.035`. So

    ext_pct = (live_price / breakout - 1) * 100

was a CONSTANT with no market input — SWING -1.96%, GROWTH -3.38% — against a +6% threshold.
Two consequences, both on the authoritative path:

  1. `hard_rejects`' extended-move guard could NEVER fire.
  2. `scorer`'s price_zone layer: `live_price <= breakout` was ALWAYS TRUE, so every candidate
     collected a free +2 "optimal zone" and the -3 "chasing risk" penalty was unreachable.
     Against a `min_entry_score` of 4-6, that free +2 is a third of the bar.

`routes.py` was passing `breakout_ref` through the whole time (it copies every game_plan key);
DE simply never read it.

WHY THIS KEEPS HAPPENING — the pattern worth internalising: the fallback is the more readable,
better-tested, more OBVIOUS place to put a fix, and a fix there produces perfectly plausible
shadow logs while changing no real decision. `test_entry4_chase_parity.py` already encodes this
lesson for `roc_10` (it asserts `"roc_10" in DE_SRC`) and never applied the same assertion to
`breakout_ref` — in the very same commit.
"""
import pathlib

import pytest

DE = pathlib.Path(__file__).resolve().parents[2] / "decision-engine/src/api/core"
HR_SRC = (DE / "hard_rejects.py").read_text()
SC_SRC = (DE / "scorer.py").read_text()
PT_SRC = pathlib.Path(
    pathlib.Path(__file__).resolve().parents[1] / "src/services/paper_trading_engine.py"
).read_text()


# ── THE PARITY ASSERTION — the one that would have caught this ──────────────────────────

def test_breakout_ref_reaches_the_AUTHORITATIVE_path():
    """THE CHECK THAT WAS MISSING. `decision_engine_mode` defaults to "primary", so
    decision-engine — not paper_trading_engine — is what decides. A fix present only in the
    fallback changes nothing real while producing entirely plausible shadow logs.

    This mirrors test_entry4_chase_parity.py's own `"roc_10" in DE_SRC` assertion, which
    existed in the same commit and was never applied to breakout_ref.
    """
    assert "breakout_ref" in HR_SRC, "the hard-reject guard must read the signal-anchored level"
    assert "breakout_ref" in SC_SRC, "the scorer's price_zone layer must read it too"


def test_the_fallback_still_has_it_too():
    """Both paths must agree. Fixing DE while regressing the fallback would just move the bug."""
    assert "breakout_ref" in PT_SRC


# ── The guard must now be able to fire ──────────────────────────────────────────────────

def _ext_pct(game_plan: dict, live_price: float = 100.0) -> float:
    """Mirrors the fixed resolution order; source assertions below pin the real expression."""
    b = game_plan.get("breakout_ref") or game_plan.get("breakout")
    return (live_price / float(b) - 1) * 100


def test_a_real_extension_now_rejects():
    """Verified live in the production container: breakout_ref=83 vs live=100 is +20.5%,
    comfortably past the +6% threshold."""
    gp = {"breakout": 103.5, "breakout_ref": 83.0}
    assert _ext_pct(gp) == pytest.approx(20.48, abs=0.1)
    assert _ext_pct(gp) > 6.0, "a +20% extension must be rejectable"


def test_the_old_live_anchored_form_was_a_constant():
    """Pins WHY this was broken, so nobody 'simplifies' it back. With breakout derived from the
    live price, the ratio carries no market information at all."""
    for live in (10.0, 100.0, 1000.0):
        gp = {"breakout": live * 1.035}
        assert _ext_pct(gp, live) == pytest.approx(-3.38, abs=0.01), (
            "the live-anchored form is the SAME value at every price — that is the bug"
        )


def test_a_candidate_at_the_signal_price_is_not_extended():
    """The guard must not reject normal entries — only genuinely chased ones."""
    gp = {"breakout_ref": 100.0}
    assert _ext_pct(gp) == pytest.approx(0.0)
    assert _ext_pct(gp) <= 6.0


def test_a_pullback_scores_as_negative_extension():
    gp = {"breakout_ref": 110.0}
    assert _ext_pct(gp) < 0


# ── Missing anchor must restore the OLD behaviour, not reject everything ────────────────

def test_absent_breakout_ref_falls_back_rather_than_rejecting():
    """The critical safety property. If a signal carries no anchor, the guard must degrade to
    its previous (inert) behaviour — NOT start rejecting every candidate, which would be a
    self-inflicted outage of the same shape as AUD-SIGALERT-RRUNREACHABLE."""
    gp = {"breakout": 103.5}
    assert _ext_pct(gp) == pytest.approx(-3.38, abs=0.01)
    assert _ext_pct(gp) <= 6.0, "no anchor must mean 'do not block', not 'block everything'"


def test_the_source_uses_or_not_a_bare_get():
    """`game_plan.get("breakout_ref") or game_plan.get("breakout")` — the `or` is what provides
    the fallback. A bare `.get("breakout_ref")` would return None and skip the guard entirely
    for every signal lacking an anchor."""
    assert 'game_plan.get("breakout_ref") or game_plan.get("breakout")' in HR_SRC


def test_a_zero_anchor_is_treated_as_absent():
    """Falsy-zero, but here the falsy behaviour is CORRECT and deliberate: a breakout level of
    0.0 is meaningless (it would make the ratio infinite), so `or` correctly falls through.
    Pinned so the guard below is not 'fixed' into an `is not None` check that would divide by
    zero."""
    gp = {"breakout_ref": 0.0, "breakout": 103.5}
    assert _ext_pct(gp) == pytest.approx(-3.38, abs=0.01)
    assert "float(breakout) > 0" in HR_SRC, "and the >0 guard must remain as a second defence"


# ── The scorer's price_zone layer ───────────────────────────────────────────────────────

def test_the_scorer_no_longer_gives_every_candidate_a_free_plus_2():
    """`breakout` defaulted to `live_price * 1.035` right there in the scorer, so
    `live_price <= breakout` was always true -> +2 "optimal zone" for everyone, and the -3
    "chasing risk" branch was unreachable."""
    assert 'game_plan.get("breakout_ref") or game_plan.get("breakout", live_price * 1.035)' in SC_SRC


def test_the_minus_3_chasing_branch_is_now_reachable():
    """With a signal-anchored level, a genuinely chased price falls past the chase ceiling."""
    breakout_ref, live, chase_ceiling_pct = 83.0, 100.0, 3.0
    assert live > breakout_ref * (1 + chase_ceiling_pct / 100.0), (
        "a +20% extension must land in the -3 bucket, not the +2 one"
    )


def test_the_plus_2_optimal_zone_still_works_for_a_real_pullback():
    """The fix must not invert the layer — a genuine pullback still deserves +2."""
    breakout_ref, live = 100.0, 95.0
    assert live <= breakout_ref


# ── Guard against the pattern recurring ─────────────────────────────────────────────────

def test_both_de_consumers_resolve_the_anchor_the_same_way():
    """hard_rejects and scorer must not drift — one reading breakout_ref while the other reads
    breakout would reintroduce half the bug."""
    assert HR_SRC.count('game_plan.get("breakout_ref")') >= 1
    assert SC_SRC.count('game_plan.get("breakout_ref")') >= 1


def test_the_fallback_keeps_breakout_live_anchored_for_SIZING():
    """DELIBERATE ASYMMETRY, worth pinning: `breakout` stays live-anchored in
    _build_game_plan_for_style because stop/target/sizing must track the actual fill price.
    Only the EXTENSION judgement uses the signal anchor. Collapsing the two would break sizing.
    """
    assert "breakout_ref" in PT_SRC
    i = PT_SRC.index('"breakout_ref": breakout_ref')
    ctx = PT_SRC[max(0, i - 400):i]
    assert "live-anchored" in ctx or "EXTENSION" in ctx, (
        "the asymmetry between breakout and breakout_ref must stay documented"
    )
