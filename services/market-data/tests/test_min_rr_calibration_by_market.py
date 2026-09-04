"""Tests for AUD-MINRR-MARKETBLIND's per-market cap logic in calibrate_min_rr_ratio()
(paper_portfolio.py).

paper_portfolio.py can't be imported directly in this test environment (see
test_min_rr_calibration_sweep.py's own docstring for why) — same source-text-extraction
pattern, since the _pct90()/by_market-building block is pure logic over already-fetched rows,
no DB access of its own.
"""
import pathlib

_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "paper_portfolio.py"
)
_SOURCE = _PATH.read_text()


def _extract_by_market_block():
    start = _SOURCE.index("    _by_market_rr: dict[str, list[float]] = {}")
    end = _SOURCE.index("\n\n    result = {", start)
    func_source = _SOURCE[start:end]
    # Dedent (the real source sits inside calibrate_min_rr_ratio(), indented one level)
    lines = func_source.splitlines()
    dedented = "\n".join(line[4:] if line.startswith("    ") else line for line in lines)
    return dedented


def _run(market_rows, best_threshold, baseline_threshold):
    namespace = {"market_rows": market_rows, "best_threshold": best_threshold, "baseline_threshold": baseline_threshold}
    exec(_extract_by_market_block(), namespace)  # noqa: S102 — isolated eval of real source
    return namespace["by_market"], namespace["_pooled_regime_rr"]


def _row(rr, market):
    return (rr, {"market": market})


def test_thin_market_below_pooled_floor_gets_capped_to_its_own_ceiling():
    """HK's own scenario: HK's observed R:R ceiling (~2.9) sits below the pooled
    regime_min_rr_ratio (3.38, from best_threshold=2.25 * 1.5) — HK's by_market entry must be
    capped at its own ceiling, not left at the pooled US-dominated value."""
    hk_rows = [_row(r, "HK") for r in [1.5, 2.0, 2.3, 2.5, 2.6, 2.7, 2.8, 2.85, 2.9, 2.95]]
    us_rows = [_row(3.5, "US") for _ in range(50)]
    by_market, pooled = _run(hk_rows + us_rows, best_threshold=2.25, baseline_threshold=2.25)
    assert pooled == 3.38
    assert by_market["HK"]["regime_min_rr_ratio"] < pooled
    assert by_market["HK"]["n_trades"] == 10


def test_market_whose_ceiling_exceeds_pooled_value_is_left_at_pooled_value():
    """US's own trades comfortably clear the pooled floor — no cap should apply, and its
    by_market entry should just equal the pooled value."""
    us_rows = [_row(r, "US") for r in [3.0, 3.2, 3.4, 3.6, 3.8, 4.0, 4.2, 4.4, 4.6, 4.8]]
    by_market, pooled = _run(us_rows, best_threshold=2.25, baseline_threshold=2.25)
    assert by_market["US"]["regime_min_rr_ratio"] == pooled


def test_capped_value_never_drops_below_the_baseline_threshold():
    """Even a market with a very low observed ceiling must not be capped below the current
    baseline (neutral-tier) threshold — regime_min_rr_ratio should always be at least as
    strict as the neutral floor, never looser."""
    hk_rows = [_row(r, "HK") for r in [0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.0, 1.0, 1.0, 1.0]]
    by_market, _ = _run(hk_rows, best_threshold=2.25, baseline_threshold=2.25)
    assert by_market["HK"]["regime_min_rr_ratio"] >= 2.25


def test_market_missing_from_rows_entirely_produces_no_by_market_entry():
    us_rows = [_row(3.5, "US") for _ in range(20)]
    by_market, _ = _run(us_rows, best_threshold=2.25, baseline_threshold=2.25)
    assert "HK" not in by_market


def test_null_market_in_config_defaults_to_us():
    """A portfolio config with no 'market' key at all (or market=None) must be bucketed under
    US, not silently dropped or crashing."""
    rows = [(3.5, {}), (3.6, None)]
    by_market, _ = _run(rows, best_threshold=2.25, baseline_threshold=2.25)
    assert by_market["US"]["n_trades"] == 2
