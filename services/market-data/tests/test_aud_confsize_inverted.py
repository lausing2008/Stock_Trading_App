"""AUD-CONFSIZE-INVERTED (2026-09-22) — confidence-weighted sizing is gated OFF by default.

Confidence is currently ANTI-predictive of return magnitude, so scaling position size by it
amplified the worst trades and shrank the least-bad ones.

Measured on 13,238 BUY outcomes, average forward return degrades monotonically across seven
consecutive confidence buckets — <=40 -1.67%, <=60 -2.73%, <=80 -3.65%, <=100 -6.39%,
>100 -11.43% — while hit rate stays flat at ~38-43%. Confidence therefore carries no
directional information at all, only a reliable NEGATIVE relationship with magnitude. The
inversion holds independently within each market and at every hold from 2 to 20 days.

The realised cost, on 123 closed paper trades: size followed confidence (Spearman +0.224,
p=0.013) and size was inversely related to outcome (Spearman -0.253, p=0.005). The two largest
size quartiles lost **$10,188 against an $8,222 NET loss** — the two smallest were profitable.

What this change does NOT do: it does not delete confidence-weighted sizing. The design is
sound; the input is currently pointed the wrong way. The band arithmetic is left intact and
still evaluated so that (a) the US-floor boundary behaviour and its existing regression test in
test_aud_sizing_multipliers_market_aware.py stay meaningful, and (b) re-enabling is a pure
config change rather than a code restore. Re-enable only once recalibration shows POSITIVE
out-of-sample correlation between confidence and forward alpha.

See docs/audits/2026-09-22-news-llm-hmm-prediction-audit.md.
"""
import pathlib
import textwrap

PT_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
PT_SRC = PT_PATH.read_text()


def _run_gate(sig_conf: float, hi: float, lo: float, cfg: dict) -> tuple[float, list[str]]:
    """exec the REAL gating branch, not a reimplementation of it."""
    start = PT_SRC.index('    if not cfg.get("confidence_sizing_enabled"')
    # End at the blank line before the next top-level block in this function, rather than at a
    # brittle literal from inside the branch.
    end = PT_SRC.index("\n\n", start) + 1
    block = textwrap.dedent(PT_SRC[start:end])
    ns = {
        "cfg": cfg,
        "sig_conf": sig_conf,
        "_hi_band": hi,
        "_lo_band": lo,
        "notes": [],
        "_DEFAULT_CONFIG": {"confidence_sizing_enabled": _default_flag()},
    }
    exec(block, ns)  # noqa: S102 — repo-own source, no external input
    return ns["confidence_size_mult"], ns["notes"]


def _default_flag() -> bool:
    """Read the shipped default straight out of _DEFAULT_CONFIG."""
    line = next(
        ln for ln in PT_SRC.split("\n")
        if ln.strip().startswith('"confidence_sizing_enabled"')
    )
    return "True" in line


_HI, _LO = 50.0, 30.0


# ── The shipped default ──────────────────────────────────────────────────────────────────────

def test_confidence_sizing_is_disabled_by_default():
    """The regression this file exists to prevent. If someone re-enables it without the
    recalibration evidence, this fails loudly."""
    assert _default_flag() is False, (
        "confidence_sizing_enabled must stay False until confidence is shown to correlate "
        "POSITIVELY with forward alpha out of sample"
    )


def test_default_config_key_exists_so_the_gate_cannot_keyerror():
    assert '"confidence_sizing_enabled"' in PT_SRC


# ── Disabled: size never varies with confidence ──────────────────────────────────────────────

def test_disabled_high_confidence_does_not_upsize():
    """The expensive half: 1.25x on high confidence was amplifying the worst cohort."""
    mult, _ = _run_gate(80.0, _HI, _LO, {})
    assert mult == 1.0


def test_disabled_low_confidence_does_not_downsize():
    """The other half: 0.75x was shrinking the LEAST-bad cohort, since the inversion means low
    confidence had the better (less negative) returns."""
    mult, _ = _run_gate(10.0, _HI, _LO, {})
    assert mult == 1.0


def test_disabled_multiplier_is_flat_across_the_whole_confidence_range():
    mults = {_run_gate(c, _HI, _LO, {})[0] for c in (0.0, 29.9, 30.0, 49.9, 50.0, 95.0, 150.0)}
    assert mults == {1.0}, f"expected a flat 1.0 multiplier, got {mults}"


def test_disabled_emits_a_note_only_when_size_would_have_moved():
    """The note is the audit trail of what was suppressed — but emitting it on every mid-band
    trade would be noise, since those were already 1.0x."""
    _, hi_notes = _run_gate(80.0, _HI, _LO, {})
    _, mid_notes = _run_gate(40.0, _HI, _LO, {})
    _, lo_notes = _run_gate(10.0, _HI, _LO, {})
    assert any("disabled" in n for n in hi_notes)
    assert any("disabled" in n for n in lo_notes)
    assert mid_notes == [], "mid-band was already 1.00x — no suppression to report"


# ── Enabled: the original behaviour is intact, so this is a pure config flip ──────────────────

def test_enabled_restores_the_original_three_band_behaviour():
    cfg = {"confidence_sizing_enabled": True}
    assert _run_gate(80.0, _HI, _LO, cfg)[0] == 1.25
    assert _run_gate(40.0, _HI, _LO, cfg)[0] == 1.0
    assert _run_gate(10.0, _HI, _LO, cfg)[0] == 0.75


def test_enabled_boundaries_are_inclusive_exactly_as_before():
    """Guards the documented US-floor boundary subtlety: a candidate at EXACTLY the band edge
    must still take the higher branch."""
    cfg = {"confidence_sizing_enabled": True}
    assert _run_gate(_HI, _HI, _LO, cfg)[0] == 1.25
    assert _run_gate(_LO, _HI, _LO, cfg)[0] == 1.0


def test_band_arithmetic_is_still_computed_so_re_enabling_needs_no_code_change():
    """_hi_band/_lo_band must remain derived above the gate — if they were deleted, the
    existing market-aware band tests would be testing dead code and re-enabling would be a
    code restore rather than a config flip."""
    assert "_hi_band = _conf_floor * _HI_BAND_RATIO" in PT_SRC
    assert "_lo_band = _conf_floor * _LO_BAND_RATIO" in PT_SRC


def test_the_multiplier_still_reaches_the_risk_calculation():
    """A neutralised multiplier that stopped being multiplied in would be a different (and
    silently larger) change than intended."""
    assert "confidence_size_mult" in PT_SRC.split("risk_dollar    =")[1].split("\n")[0]
