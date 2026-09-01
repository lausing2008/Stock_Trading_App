"""Tests for AUD-CONFIGGAP-WEEKLYGAINLOCK — the T232-CONFIGGAP class recurring for
max_weekly_gain_pct (T191's weekly gain-lock threshold).

paper_trading_engine.py's weekly-P&L circuit-breaker block already reads BOTH
max_weekly_loss_pct and max_weekly_gain_pct from cfg identically (both default to a real
_DEFAULT_CONFIG/_STYLE_OVERRIDES value if absent, both gate the SAME weekly-P&L block) — but
paper_portfolio.py's configure_portfolio() allowlist only ever included max_weekly_loss_pct,
never its gain-lock sibling. Any attempt to tune the gain-lock threshold via the Config Panel
was silently dropped as an "unknown key" (allowed_keys' own filter-with-no-error convention),
while the loss-limit side worked — a real, confirmed asymmetry, not a math bug.

Tested via source-text extraction, matching test_risk_check_config_wiring.py's established
technique exactly (paper_portfolio.py can't be imported directly in this test environment).
"""
import pathlib

_pp_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "paper_portfolio.py"
_pp_source = _pp_path.read_text()


def _load_configure_portfolio_body():
    start = _pp_source.index("def configure_portfolio(")
    end = _pp_source.index("\n\n\n@router.post", start)
    return _pp_source[start:end]


_configure_body = _load_configure_portfolio_body()


def test_max_weekly_gain_pct_is_in_allowed_keys():
    """The exact regression this guards against: setting max_weekly_gain_pct via
    POST /configure would previously be silently dropped as an unrecognized key, while its
    sibling max_weekly_loss_pct was correctly accepted."""
    assert '"max_weekly_gain_pct"' in _configure_body


def test_max_weekly_gain_pct_has_a_range_check_matching_its_sibling():
    """Both weekly thresholds should go through the same PT-H1 decimal-fraction sanity
    validation _RANGE_CHECKS applies to every other pct-shaped config key."""
    range_start = _pp_source.index('_RANGE_CHECKS: dict[str, tuple[float, float, str]] = {')
    range_end = _pp_source.index("\n    }", range_start)
    range_block = _pp_source[range_start:range_end]
    assert '"max_weekly_gain_pct":' in range_block
    assert '"max_weekly_loss_pct":' in range_block  # confirm the sibling is still present too


def test_max_weekly_gain_pct_range_bounds_are_sane_decimal_fractions():
    """The new range entry must be expressed as a decimal fraction (matching every other
    pct-shaped _RANGE_CHECKS entry), with a low bound below T191's real 6% default and a high
    bound that still rejects an obviously-mistaken whole-percent value like 6 (meant as "6%")."""
    range_start = _pp_source.index('"max_weekly_gain_pct":')
    line_end = _pp_source.index("\n", range_start)
    line = _pp_source[range_start:line_end]
    assert "(0.02" in line or "(0.01" in line  # a real low bound, not 0
    assert "1.00" not in line  # must not accidentally reuse an unrelated 0-100% bound
