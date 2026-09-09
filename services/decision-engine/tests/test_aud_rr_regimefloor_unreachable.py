"""AUD-RR-REGIMEFLOOR-UNREACHABLE — a calibrated floor above what the geometry can deliver.

REPORTED BY THE USER: "why no trades on paper trading in US market".

MEASURED: 35 of 35 decision-engine verdicts in 24h blocked on R:R, ZERO approvals, and no US
paper entry since 2026-09-04. Blocked ratios clustered at 1.55-2.12 against a floor of 3.4:1.

THE CAUSE — two independently correct mechanisms:
  * `calibrate_min_rr_ratio` learned a choppy/risk_off floor of 3.4:1 from 103 real trades.
  * `_default_game_plan()` caps `take_profit` at `live_price * target_pct`, so a candidate's
    achievable R:R is bounded by its STYLE's geometry and its own ATR-derived stop.

Nothing ever compared them. Measured across 129 live US symbols with real ATR(14), the share of
the universe that can reach 3.4 at all: GROWTH 49%, LONG 53%, SWING 15%, SHORT 2%. For SWING and
SHORT that is not selectivity — it is an inability to ever pass.

SAME FAMILY AS `AUD-SIGALERT-RRUNREACHABLE`, which killed BUY alerts for four days: a floor and a
ceiling tuned by separate mechanisms, each defensible alone, jointly producing zero output.

THE FIX: cap the regime floor at the candidate's own geometric ceiling. A tight-stop candidate
still faces the full calibrated floor; only a candidate whose geometry cannot reach it is judged
against what it can actually deliver.

A CORRECTION TO MY OWN FIRST ATTEMPT, which was backwards and would have loosened every gate:
I first used a per-STYLE constant `(target_pct - 1) / (1 - stop_pct)`, believing it the achievable
maximum. It is the MINIMUM — `stop = max(atr_stop, fixed_stop)` means the fixed stop is the WIDEST
stop used, so it gives the largest risk and smallest ceiling. It coincided exactly with the
sweep's per-style WORST case (2.92/2.50/2.18/1.67), which should have flagged it immediately.
Capping at that constant would have dropped GROWTH to 2.77 and LONG to 2.38 for EVERY candidate —
including the ones already clearing 3.4.
"""
import pathlib

import pytest

# NO sys.modules STUBS HERE, DELIBERATELY. My first version added
# `sys.modules.setdefault("common", MagicMock())` (copied from test_hard_rejects.py's own
# preamble) and it broke FOUR sibling test files through module-cache pollution: a MagicMock
# makes `common` a non-package, so their real `from common.market_calendar import NYSE_HOLIDAYS`
# failed at import time. sys.modules stubs are PROCESS-WIDE — a stub added for one file's
# convenience can break a sibling's genuine import, and the failure surfaces in the OTHER file,
# which is far harder to attribute. This service's tests/conftest.py explicitly documents that
# no stubbing is needed: hard_rejects.py is dependency-light (pydantic + stdlib only).

HR = pathlib.Path(__file__).resolve().parents[1] / "src/api/core/hard_rejects.py"
SRC = HR.read_text()

HAIRCUT = 0.95
FLOOR = 3.4

# The real live style params.
STYLES = {
    "GROWTH": {"target_pct": 1.35, "stop_pct": 0.880, "atr_stop_mult": 3.0},
    "LONG":   {"target_pct": 1.25, "stop_pct": 0.900, "atr_stop_mult": 2.0},
    "SWING":  {"target_pct": 1.12, "stop_pct": 0.945, "atr_stop_mult": 2.0},
    "SHORT":  {"target_pct": 1.05, "stop_pct": 0.970, "atr_stop_mult": 2.0},
}


def _plan_stop(px, atr, style):
    p = STYLES[style]
    return max(px - p["atr_stop_mult"] * atr, px * p["stop_pct"])


def _ceiling(px, stop, style):
    """Mirrors _candidate_rr_ceiling."""
    if px <= 0 or stop <= 0 or stop >= px:
        return None
    reward = px * STYLES[style]["target_pct"] - px
    risk = px - stop
    if reward <= 0 or risk <= 0:
        return None
    return (reward / risk) * HAIRCUT


def _effective_floor(px, stop, style, regime="choppy", base=2.0, regime_floor=FLOOR):
    """Mirrors the fixed gate."""
    min_rr = base
    if regime in ("choppy", "risk_off"):
        c = _ceiling(px, stop, style)
        if c is not None:
            regime_floor = min(regime_floor, c)
        min_rr = max(min_rr, regime_floor)
    return min_rr


def _best_achievable(px, stop, style):
    """The R:R a perfect setup of this style, at this stop, actually gets."""
    return (px * STYLES[style]["target_pct"] - px) / (px - stop)


# ── The bug: an unreachable floor ───────────────────────────────────────────────────────

@pytest.mark.parametrize("style,atr_pct", [
    ("SWING", 4.0), ("SWING", 6.0), ("GROWTH", 4.0), ("LONG", 6.0),
])
def test_a_wide_stop_candidate_is_no_longer_locked_out(style, atr_pct):
    """THE BUG. These candidates could NEVER reach 3.4 no matter how good the setup."""
    px = 100.0
    stop = _plan_stop(px, px * atr_pct / 100, style)
    assert _best_achievable(px, stop, style) < FLOOR, "precondition: unreachable before the fix"
    assert _best_achievable(px, stop, style) >= _effective_floor(px, stop, style), \
        "after the fix, a perfect setup must be able to pass"


@pytest.mark.parametrize("style", ["GROWTH", "LONG", "SWING"])
def test_every_style_has_a_reachable_floor_at_every_realistic_atr(style):
    """No style/ATR combination may be structurally incapable of passing.

    SHORT IS DELIBERATELY EXCLUDED, and that exclusion is the honest scope of this fix — see
    test_short_is_blocked_by_the_BASE_floor_which_is_out_of_scope below.
    """
    px = 100.0
    for atr_pct in (0.5, 1.0, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0):
        stop = _plan_stop(px, px * atr_pct / 100, style)
        assert _best_achievable(px, stop, style) >= _effective_floor(px, stop, style), \
            f"{style} at ATR {atr_pct}% still cannot pass"


# ── It must not LOOSEN a gate that was already working ──────────────────────────────────

@pytest.mark.parametrize("style,atr_pct", [
    ("GROWTH", 1.0), ("GROWTH", 2.5), ("LONG", 1.0), ("LONG", 2.5), ("SWING", 1.0),
])
def test_a_tight_stop_candidate_still_faces_the_full_calibrated_floor(style, atr_pct):
    """THE RISK IN THIS FIX. A candidate that CAN reach 3.4 must still be held to it — the
    calibrator learned that number from 103 real trades."""
    px = 100.0
    stop = _plan_stop(px, px * atr_pct / 100, style)
    assert _effective_floor(px, stop, style) == FLOOR


def test_short_is_blocked_by_the_BASE_floor_which_is_out_of_scope():
    """A LIMIT OF THIS FIX, found by my own tests failing and worth stating plainly.

    SHORT's style geometry (3% stop / 5% target) caps its R:R at 1.67 — BELOW the portfolio's
    BASE `min_rr_ratio` of 2.0. So SHORT is blocked by the base floor, not by the regime
    stiffening, and this cap neither can nor should lift it: the cap only ever reduces the REGIME
    component, never the operator's own base minimum.

    That matches the decision already recorded for AUD-SIGALERT-RRUNREACHABLE, where the user
    deliberately DEFERRED SHORT after being shown that its params cap R:R at 1.67 by design —
    forcing it through would fabricate an unsupported target. SHORT remaining blocked is
    therefore CORRECT, not a residual bug.
    """
    px = 100.0
    stop = _plan_stop(px, px * 2.5 / 100, "SHORT")
    assert _best_achievable(px, stop, "SHORT") < 2.0, "below the BASE floor, not just the regime one"
    assert _effective_floor(px, stop, "SHORT", base=2.0) == 2.0, "the base floor governs"


def test_the_first_attempt_would_have_loosened_growth_and_long():
    """Pins the error so it cannot be reintroduced as a 'simplification'. The per-style constant
    is the WORST case, not the best."""
    for style, expected in (("GROWTH", 2.92), ("LONG", 2.50), ("SWING", 2.18), ("SHORT", 1.67)):
        p = STYLES[style]
        const = (p["target_pct"] - 1.0) / (1.0 - p["stop_pct"])
        assert abs(const - expected) < 0.01, f"{style} per-style constant"
        assert const < FLOOR, "which is why capping at it would loosen a working gate"
    # A tight-stop GROWTH candidate can far exceed that constant.
    px, stop = 100.0, _plan_stop(100.0, 1.0, "GROWTH")
    assert _best_achievable(px, stop, "GROWTH") > 10.0


def test_a_bigger_atr_lowers_the_ceiling_not_raises_it():
    """The sanity check that caught my inverted formula."""
    px = 100.0
    ceilings = [
        _best_achievable(px, _plan_stop(px, px * a / 100, "GROWTH"), "GROWTH")
        for a in (0.5, 2.0, 5.0)
    ]
    assert ceilings == sorted(ceilings, reverse=True), "wider stop must mean LOWER ceiling"
    assert ceilings[0] > 20 and ceilings[-1] < 3


# ── Non-choppy regimes are untouched ────────────────────────────────────────────────────

@pytest.mark.parametrize("regime", ["bull", "neutral", None])
def test_the_cap_only_applies_in_choppy_or_risk_off(regime):
    px, stop = 100.0, _plan_stop(100.0, 6.0, "SWING")
    assert _effective_floor(px, stop, "SWING", regime=regime) == 2.0, \
        "outside choppy/risk_off the base min_rr_ratio governs, unchanged"


def test_the_base_floor_is_never_lowered_below_min_rr_ratio():
    """The cap may only reduce the REGIME stiffening, never the portfolio's own base floor."""
    px, stop = 100.0, _plan_stop(100.0, 8.0, "SHORT")
    assert _effective_floor(px, stop, "SHORT", base=2.0) == 2.0


# ── Fail-open behaviour ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("px,stop", [(0.0, 90.0), (100.0, 0.0), (100.0, 100.0), (100.0, 110.0)])
def test_degenerate_geometry_leaves_the_floor_untouched(px, stop):
    """An invalid stop must not silently loosen the gate."""
    assert _ceiling(px, stop, "SWING") is None


def test_an_unknown_style_leaves_the_floor_untouched():
    i = SRC.index("def _candidate_rr_ceiling(")
    fn = SRC[i:SRC.index("\ndef ", i + 10)]
    assert "if not style" in fn
    assert "return None" in fn


def test_the_helper_fails_open_on_any_exception():
    """A market-data outage must not disable the R:R gate."""
    i = SRC.index("def _candidate_rr_ceiling(")
    fn = SRC[i:SRC.index("\ndef ", i + 10)]
    assert "except Exception:" in fn
    tail = fn[fn.index("except Exception:"):]
    assert "return None" in tail


# ── Wiring: the fix must be in the DECIDING path ────────────────────────────────────────

def test_the_cap_is_applied_in_the_authoritative_hard_reject():
    """THE CHECK THIS CODEBASE KEEPS NEEDING. decision_engine_mode defaults to "primary", so
    hard_rejects.py is the path that actually blocks a trade — paper_trading_engine's own
    _should_enter() is the shadow-logged fallback. Three fixes have landed on the wrong one."""
    i = SRC.index('if regime_state in ("choppy", "risk_off"):')
    block = SRC[i:i + 2600]
    assert "_candidate_rr_ceiling(live_price, stop_price, style, cfg)" in block
    assert "_regime_floor = min(_regime_floor, _ceiling)" in block
    assert "min_rr = max(min_rr, _regime_floor)" in block


def test_the_ceiling_reads_the_canonical_style_params():
    """A hardcoded copy of target_pct would drift from the geometry it describes — this codebase
    has found FOUR copies of one constant before."""
    i = SRC.index("def _candidate_rr_ceiling(")
    fn = SRC[i:SRC.index("\ndef ", i + 10)]
    assert "from .aggregator import _get_style_params" in fn
    assert "1.35" not in fn and "1.12" not in fn, "must not hardcode style percentages"


def test_the_haircut_is_a_named_constant_and_small():
    assert "_RR_CEILING_HAIRCUT = 0.95" in SRC
    i = SRC.index("_RR_CEILING_HAIRCUT = ")
    val = float(SRC[i:SRC.index("\n", i)].split("=")[1].strip())
    assert 0.90 <= val < 1.0, "a haircut, not a second tuning knob"


# ── The measured evidence, pinned ───────────────────────────────────────────────────────

def test_the_measured_reachability_is_recorded():
    i = SRC.index("AUD-RR-REGIMEFLOOR-UNREACHABLE")
    block = SRC[i:i + 3000]
    for frag in ("GROWTH", "SWING", "129"):
        assert frag in block, f"the measurement should record {frag}"
    assert "35 of 35" in SRC, "the live block/approval count belongs in the source"


def test_swing_and_short_were_the_outage_not_growth_and_long():
    """The asymmetry is the whole finding: two styles were tight, two were dead."""
    reach = {"GROWTH": 0.49, "LONG": 0.53, "SWING": 0.15, "SHORT": 0.02}
    assert reach["SWING"] < 0.20 and reach["SHORT"] < 0.05
    assert reach["GROWTH"] > 0.40 and reach["LONG"] > 0.40


# ── Exercising the REAL function, not a Python mirror ───────────────────────────────────
#
# A GAP MY OWN SABOTAGE TESTING EXPOSED. Every test above mirrors the geometry in Python, so
# reverting the helper to the BACKWARDS per-style constant — the exact error caught during
# development — left all 30 passing. A formula pinned only by a copy of itself is not pinned.
# These call the real `_candidate_rr_ceiling`.

# A SEPARATE, PRE-EXISTING BREAKAGE, recorded here because I caused it yesterday and only found
# it today: commit 5796ebf (AUD-ENTRY-NYSEHOLIDAY-FOURTHCOPY) added
# `from common.market_calendar import NYSE_HOLIDAYS` to hard_rejects.py. FOUR sibling test files
# in this directory (test_hard_rejects, test_score_replay, test_entry_gate_params,
# test_entry_weights) carry their own `sys.modules.setdefault("common", MagicMock())` preamble,
# which makes `common` a non-PACKAGE — so that new real submodule import fails at collection.
# They error out with or without this file present, and did so before this change. NOT fixed
# here (it needs those four preambles updated to stub the submodule too, or to drop the stub
# now that conftest documents none is needed) — tracked so it is not mistaken for fallout of
# this change.
#
# `hard_rejects.py` imports `common.market_calendar` (AUD-ENTRY-NYSEHOLIDAY-FOURTHCOPY), which
# resolves in the container and in CI but NOT in a bare local run of this service's tests — a
# PRE-EXISTING gap that already breaks four sibling files here at collection time, unrelated to
# this change. Skip rather than fail, so these still execute where the import works. Do NOT
# "fix" it with a sys.modules MagicMock: that makes `common` a non-package and breaks the
# siblings' real imports process-wide (tried, and it did exactly that).
_real_import_error = None
try:  # noqa: SIM105
    from src.api.core import aggregator as _agg_mod  # noqa: F401
    from src.api.core import hard_rejects as _hr_mod  # noqa: F401
except Exception as _exc:  # pragma: no cover - environment-dependent
    _real_import_error = _exc

_needs_real_module = pytest.mark.skipif(
    _real_import_error is not None,
    reason=f"hard_rejects not importable in this environment: {_real_import_error}",
)


def _real_ceiling(px, stop, style, monkeypatch):
    """Call the REAL helper, with the canonical style-params lookup stubbed to the real values."""
    monkeypatch.setattr(_agg_mod, "_get_style_params", lambda: STYLES, raising=False)
    return _hr_mod._candidate_rr_ceiling(px, stop, style, {})


@_needs_real_module
def test_the_real_helper_scales_with_PRICE_not_just_percentages(monkeypatch):
    """THE DISCRIMINATOR. The correct formula uses live_price and stop_price, so the SAME
    percentage stop at a different price gives the same ceiling — but a DIFFERENT stop at the
    same price must give a different one. The backwards per-style constant ignores stop_price
    entirely and returns one number for every candidate of a style."""
    tight = _real_ceiling(100.0, 98.0, "GROWTH", monkeypatch)
    wide = _real_ceiling(100.0, 88.0, "GROWTH", monkeypatch)
    assert tight is not None and wide is not None
    assert tight > wide, "a tighter stop must yield a HIGHER ceiling"
    assert tight > 3 * wide, "and dramatically so — 17.5 vs 2.92 on the real params"


@_needs_real_module
def test_the_real_helper_is_price_scale_invariant(monkeypatch):
    """Same proportional stop at $10 and $1000 -> same ceiling."""
    a = _real_ceiling(10.0, 9.8, "GROWTH", monkeypatch)
    b = _real_ceiling(1000.0, 980.0, "GROWTH", monkeypatch)
    assert a is not None and abs(a - b) < 1e-6


@_needs_real_module
def test_the_real_helper_matches_the_expected_arithmetic(monkeypatch):
    """GROWTH, px=100, stop=88 (the fixed-stop floor): (135-100)/(100-88) * 0.95 = 2.77."""
    got = _real_ceiling(100.0, 88.0, "GROWTH", monkeypatch)
    assert abs(got - 2.77) < 0.01


@_needs_real_module
def test_the_real_helper_rejects_degenerate_input(monkeypatch):
    for px, stop in ((0.0, 90.0), (100.0, 0.0), (100.0, 100.0), (100.0, 110.0)):
        assert _real_ceiling(px, stop, "GROWTH", monkeypatch) is None
    assert _real_ceiling(100.0, 90.0, None, monkeypatch) is None
