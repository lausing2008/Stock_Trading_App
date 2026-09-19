"""Tests for AUD-MINRR-STYLEBLIND's per-style cap logic in calibrate_min_rr_ratio()
(paper_portfolio.py) — the style-axis sibling of test_min_rr_calibration_by_market.py's
AUD-MINRR-MARKETBLIND coverage.

Root cause this guards: calibrate_min_rr_ratio() pools every closed trade across ALL trading
styles. GROWTH's stop/target math structurally produces a much higher rr_ratio_at_entry than
SWING's, so a pooled EV-maximizing threshold learns mostly from whichever style trades more —
measured live 2026-09-19, a pooled floor of 2.25 cleared virtually every GROWTH trade but only
the top quartile of SWING's, and every US SWING portfolio went 16+ days without a single new
entry as a direct result. Same source-text-extraction pattern as the by_market test — pure
logic over already-fetched rows, no DB access of its own, so paper_portfolio.py's real block is
exec'd rather than reimplemented.
"""
import pathlib

_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "paper_portfolio.py"
)
_SOURCE = _PATH.read_text()


def _extract_by_style_block():
    start = _SOURCE.index("    _by_style_pairs: dict[str, list[tuple[float, float]]] = {}")
    end = _SOURCE.index("\n\n    result = {", start)
    func_source = _SOURCE[start:end]
    # Dedent (the real source sits inside calibrate_min_rr_ratio(), indented one level)
    lines = func_source.splitlines()
    dedented = "\n".join(line[4:] if line.startswith("    ") else line for line in lines)
    return dedented


def _run(market_rows, effective_threshold, pooled_regime_rr, min_rr_min_trades=100):
    namespace = {
        "market_rows": market_rows,
        "effective_threshold": effective_threshold,
        "_pooled_regime_rr": pooled_regime_rr,
        "_MIN_RR_MIN_TRADES": min_rr_min_trades,
    }
    exec(_extract_by_style_block(), namespace)  # noqa: S102 — isolated eval of real source
    return namespace["by_style"]


def _row(rr, style, pnl=1.0):
    return (rr, {"trading_style": style}, pnl)


def test_under_evidenced_style_below_pooled_floor_falls_back_to_original_literal():
    """The exact reported incident: SWING has fewer trades than _MIN_RR_MIN_TRADES and most of
    its own history sits below the pooled floor — its by_style entry must revert min_rr_ratio to
    the original pre-calibration literal (2.0), not the GROWTH-dominated pooled value."""
    swing_rows = [_row(r, "SWING") for r in
                  [1.8, 1.9, 2.0, 2.0, 2.1, 2.1, 2.15, 2.2, 2.2, 2.3, 2.4, 2.5]]  # p75 ~= 2.2
    growth_rows = [_row(3.0, "GROWTH") for _ in range(80)]
    by_style = _run(swing_rows + growth_rows, effective_threshold=2.25, pooled_regime_rr=3.38)
    assert by_style["SWING"]["n_trades"] == 12
    assert by_style["SWING"]["min_rr_ratio"] == 2.0
    assert by_style["SWING"]["regime_min_rr_ratio"] == 3.0


def test_style_with_enough_of_its_own_trades_is_left_uncapped():
    """A style that clears _MIN_RR_MIN_TRADES on its own is left exactly as before — no
    min_rr_ratio/regime_min_rr_ratio key at all in its by_style entry, so
    _default_min_rr_ratio() falls through to the pooled/global value unchanged."""
    swing_rows = [_row(2.0, "SWING") for _ in range(120)]  # thin R:R, but plenty of volume
    by_style = _run(swing_rows, effective_threshold=2.25, pooled_regime_rr=3.38, min_rr_min_trades=100)
    assert by_style["SWING"]["n_trades"] == 120
    assert "min_rr_ratio" not in by_style["SWING"]
    assert "regime_min_rr_ratio" not in by_style["SWING"]


def test_under_evidenced_style_whose_own_history_clears_the_pooled_floor_is_left_uncapped():
    """A thin-but-unaffected style: not enough trades for independent calibration, but its own
    75th percentile already clears the pooled floor — must NOT be capped (the pooled number
    isn't actually excluding most of its own history)."""
    long_rows = [_row(r, "LONG") for r in [3.0, 3.2, 3.4, 3.6, 3.8, 4.0, 4.2, 4.4, 4.6, 4.8]]
    by_style = _run(long_rows, effective_threshold=2.25, pooled_regime_rr=3.38)
    assert by_style["LONG"]["n_trades"] == 10
    assert "min_rr_ratio" not in by_style["LONG"]
    assert "regime_min_rr_ratio" not in by_style["LONG"]


def test_cap_only_fires_for_the_specific_key_the_style_actually_fails():
    """A style whose p75 sits between the neutral floor and the (larger) regime-stiffened
    floor should get regime_min_rr_ratio capped but NOT min_rr_ratio — the two keys are
    independent checks, not an all-or-nothing switch."""
    rows = [_row(r, "SHORT") for r in
            [2.6, 2.7, 2.8, 2.9, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0]]  # p75 == 3.0
    by_style = _run(rows, effective_threshold=2.25, pooled_regime_rr=3.38)
    assert "min_rr_ratio" not in by_style["SHORT"]  # p75 (3.0) >= effective_threshold (2.25)
    assert by_style["SHORT"]["regime_min_rr_ratio"] == 3.0  # p75 (3.0) < pooled_regime_rr (3.38)


def test_missing_trading_style_in_config_defaults_to_growth():
    """A portfolio config with no 'trading_style' key (or an empty config) buckets under
    GROWTH, matching _DEFAULT_CONFIG['trading_style'] == 'GROWTH' — never silently dropped."""
    rows = [(3.5, {}, 1.0), (3.6, None, 1.0)]
    by_style = _run(rows, effective_threshold=2.25, pooled_regime_rr=3.38)
    assert by_style["GROWTH"]["n_trades"] == 2


def test_style_missing_from_rows_entirely_produces_no_by_style_entry():
    growth_rows = [_row(3.5, "GROWTH") for _ in range(20)]
    by_style = _run(growth_rows, effective_threshold=2.25, pooled_regime_rr=3.38)
    assert "SWING" not in by_style
