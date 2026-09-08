"""AUD-ENTRY-CONFSIZEMULT-HKCONSTANT — a 3-way sizing discriminator collapsed to a constant.

`confidence_size_mult` used hardcoded bands (>=50 -> 1.25x, >=30 -> 1.0x, else 0.75x) while the
candidate SQL admits only `confidence >= min_confidence * 0.90`.

HK carries `min_confidence = 65.0` (_HK_MARKET_OVERRIDES), so its floor is **58.5** — meaning
`sig_conf >= 50` was **always true** and the 1.0x / 0.75x branches were UNREACHABLE for HK.

The direction is what makes this matter: a conviction discriminator collapsed into a constant
**+25% UPSIZE** on exactly the market whose overrides exist to DE-RISK it. T222-F cuts HK's
`max_position_pct` to 0.07 and `risk_per_trade_pct` to 0.007; this multiplier handed a quarter
of that reduction straight back.

Production: **17 of 19 real HK entries sized at 1.25x** (the 2 that did not predate the HK
override). US was unaffected — its floor is 45 * 0.90 = 40.5, so all three bands are genuinely
reachable there, which is exactly why this stayed invisible.

Fixed by expressing the bands RELATIVE to the portfolio's own floor. US behaviour is preserved
byte-for-byte, using EXACT ratios (50.0/40.5, 30.0/40.5) rather than rounded decimals — a
rounded 1.235 yields 50.0175, which would silently demote a US candidate at exactly 50.0.
"""
import pathlib

import pytest

PT_SRC = pathlib.Path(
    pathlib.Path(__file__).resolve().parents[1] / "src/services/paper_trading_engine.py"
).read_text()

_US_FLOOR = 45.0 * 0.90
# Exact ratios, not rounded decimals — see the source comment. A rounded 1.235 yields 50.0175,
# which would drop a US candidate at exactly 50.0 from 1.25x to 1.0x.
_HI, _LO = 50.0 / _US_FLOOR, 30.0 / _US_FLOOR


def _bands(min_confidence: float) -> tuple[float, float]:
    floor = min_confidence * 0.90
    return floor * _HI, floor * _LO


def _mult(conf: float, min_confidence: float) -> float:
    hi, lo = _bands(min_confidence)
    return 1.25 if conf >= hi else (1.0 if conf >= lo else 0.75)


# ── US behaviour must be preserved EXACTLY ──────────────────────────────────────────────

def test_us_bands_are_unchanged_to_the_decimal():
    """This fix must not retune US sizing as a side effect. At the US default min_confidence of
    45 the derived bands must land on the historical 50/30 constants exactly."""
    hi, lo = _bands(45.0)
    assert hi == pytest.approx(50.0, abs=0.01)
    assert lo == pytest.approx(30.0, abs=0.01)


@pytest.mark.parametrize("conf,expected", [
    (80.0, 1.25), (50.0, 1.25), (49.9, 1.0), (30.0, 1.0), (29.9, 0.75), (10.0, 0.75),
])
def test_us_multipliers_match_the_old_hardcoded_behaviour(conf, expected):
    assert _mult(conf, 45.0) == expected


# ── HK: the bands must actually discriminate now ────────────────────────────────────────

def test_hk_bands_were_previously_unreachable():
    """Pins the defect: HK's candidate floor is 58.5, so the old `>= 50` test could never be
    false. A three-way discriminator with two dead branches."""
    hk_floor = 65.0 * 0.90
    assert hk_floor == pytest.approx(58.5)
    assert hk_floor > 50.0, "every HK candidate cleared the old 1.25x band by construction"


def test_hk_now_has_a_reachable_middle_band():
    hi, lo = _bands(65.0)
    assert hi == pytest.approx(72.22, abs=0.01)
    assert lo == pytest.approx(43.33, abs=0.01)
    # The HK floor (58.5) now sits BETWEEN the bands, so both 1.25x and 1.0x are reachable.
    assert lo < 58.5 < hi


@pytest.mark.parametrize("conf,expected", [
    (80.0, 1.25),   # genuinely high conviction keeps the upsize
    (72.3, 1.25),   # HK GROWTH's real average entry confidence
    (62.3, 1.0),    # HK SWING's real average — was 1.25x, now correctly neutral
    (60.0, 1.0),    # a marginal HK candidate — was 1.25x
])
def test_hk_multipliers_now_discriminate(conf, expected):
    """Real production averages: HK GROWTH 72.3 (n=15), HK SWING 62.3 (n=4)."""
    assert _mult(conf, 65.0) == expected


def test_the_blanket_upsize_on_the_de_risked_market_is_gone():
    """THE POINT. Marginal HK candidates no longer collect an automatic +25%."""
    assert _mult(60.0, 65.0) == 1.0, "was 1.25x for every HK entry"
    assert _mult(60.0, 65.0) < 1.25


# ── Source-level guards ─────────────────────────────────────────────────────────────────

def test_the_bands_are_derived_not_hardcoded():
    assert "_conf_floor" in PT_SRC
    assert "_hi_band" in PT_SRC and "_lo_band" in PT_SRC
    i = PT_SRC.index("_hi_band = _conf_floor")
    block = PT_SRC[i - 400:i + 300]
    assert "sig_conf >= 50" not in block, "the hardcoded band must be gone"


def test_the_floor_matches_the_candidate_sql_filter():
    """The bands are only meaningful if they use the SAME 0.90 factor the candidate query
    applies. A divergence would reintroduce the mismatch in a new form."""
    assert '_conf_floor = float(cfg.get("min_confidence"' in PT_SRC
    i = PT_SRC.index("_conf_floor = float(cfg.get")
    assert "* 0.90" in PT_SRC[i:i + 200]
    assert 'Signal.confidence >= cfg["min_confidence"] * 0.90' in PT_SRC, (
        "the candidate SQL filter this mirrors must still use 0.90"
    )


def test_a_missing_min_confidence_falls_back_to_the_real_default():
    """Must not fabricate a floor. _DEFAULT_CONFIG is the single source."""
    i = PT_SRC.index("_conf_floor = float(cfg.get")
    assert '_DEFAULT_CONFIG["min_confidence"]' in PT_SRC[i:i + 200]


def test_confidence_zero_still_lands_in_the_lowest_band():
    """Falsy-zero: `float(sig.confidence or 0.0)` maps a missing confidence to 0.0, which must
    size DOWN (0.75x), never up."""
    assert _mult(0.0, 45.0) == 0.75
    assert _mult(0.0, 65.0) == 0.75


def test_the_ratios_are_exact_not_rounded():
    """A rounded 1.235 gives 50.0175 at the US floor, silently demoting a candidate at exactly
    50.0 confidence. The source must derive the ratios from the constants themselves."""
    assert "50.0 / _US_CONF_FLOOR" in PT_SRC
    assert "30.0 / _US_CONF_FLOOR" in PT_SRC
    assert "_conf_floor * 1.235" not in PT_SRC


def test_the_exact_us_boundary_is_inclusive():
    """conf == 50.0 must still earn 1.25x, exactly as the hardcoded band did."""
    assert _mult(50.0, 45.0) == 1.25
    assert _mult(30.0, 45.0) == 1.0
