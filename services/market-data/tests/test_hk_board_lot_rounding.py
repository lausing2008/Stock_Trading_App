"""Tests for AUD262-HK-NO-BOARD-LOTS.

HK positions were sized via `shares = round(shares, 4)` — a fractional/arbitrary quantity
that HKEX cannot actually fill, since HK equities trade only in fixed board lots (set
per-issuer at listing; real values are NOT available anywhere in this app's data pipeline —
confirmed absent from yfinance's Ticker.info, the Stock model, and any HKEX scrape). Fixed
with `_hk_board_lot_size(price)` — a documented, price-tier APPROXIMATION (mirroring HKEX's
own real practice of assigning smaller lots to higher-priced stocks), applied by rounding
`shares` DOWN to a whole multiple of the approximated lot size, at both the two places
_scan_for_entries() computes a fresh `shares` value for an HK candidate (the PT-C2 rounding,
and the max_position_pct cap's own re-derivation).

_hk_board_lot_size() is a small, pure, dependency-free function — imported directly, no
source-extraction workaround needed. The `market == "HK"` wiring inside _scan_for_entries()
itself (a 1000+ line function) is checked via source-text regression, matching this repo's
established technique for functions too large/stateful to drive end-to-end.
"""
import pathlib

from src.services.paper_trading_engine import _hk_board_lot_size

_ENGINE_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
)
_ENGINE_SOURCE = _ENGINE_PATH.read_text()


def test_high_priced_stock_gets_the_smallest_lot_size():
    assert _hk_board_lot_size(350.0) == 100


def test_low_priced_stock_gets_a_larger_lot_size():
    assert _hk_board_lot_size(0.05) == 5000


def test_lot_size_is_monotonically_non_increasing_as_price_rises():
    """Real HKEX practice: higher-priced stocks get smaller (or equal) lots, never larger —
    a lot's total cash value should stay in a broadly similar range across price tiers."""
    prices = [0.01, 0.05, 0.19, 0.20, 0.50, 0.99, 1.0, 4.99, 5.0, 19.99, 20.0, 99.99, 100.0, 500.0]
    lots = [_hk_board_lot_size(p) for p in prices]
    for earlier, later in zip(lots, lots[1:]):
        assert later <= earlier, f"lot size increased across the price tiers: {lots}"


def test_boundary_prices_resolve_to_the_higher_price_tiers_bucket():
    """>= comparisons: a price exactly at a tier boundary belongs to the CHEAPER-lot tier."""
    assert _hk_board_lot_size(100.0) == 100
    assert _hk_board_lot_size(99.99) == 200
    assert _hk_board_lot_size(20.0) == 200
    assert _hk_board_lot_size(19.99) == 500
    assert _hk_board_lot_size(5.0) == 500
    assert _hk_board_lot_size(4.99) == 1000
    assert _hk_board_lot_size(1.0) == 1000
    assert _hk_board_lot_size(0.99) == 2000
    assert _hk_board_lot_size(0.20) == 2000
    assert _hk_board_lot_size(0.19) == 5000


def test_rounding_down_to_a_lot_multiple_never_exceeds_the_original_risk_budget():
    """Direct property check on the rounding formula itself (not the full _scan_for_entries()
    flow): shares_after must never exceed shares_before — rounding down can only ever reduce
    risk relative to what the risk-budget math computed, never increase it."""
    for raw_shares, price in [(47.8, 350.0), (833.3, 12.0), (4999.0, 2.0), (12000.0, 0.50)]:
        lot = _hk_board_lot_size(price)
        rounded = float((int(raw_shares) // lot) * lot)
        assert rounded <= raw_shares
        assert rounded % lot == 0


def test_a_risk_budget_smaller_than_one_lot_rounds_to_zero_shares():
    """The exact 'skip the entry if one lot exceeds the risk budget' case from the tracker's
    own fix description: a $350 stock (100-share lot = $35,000/lot) with a risk budget that
    only supports 47.8 raw shares must round down to 0, not up to a fractional-lot fill."""
    raw_shares, price = 47.8, 350.0
    lot = _hk_board_lot_size(price)
    rounded = float((int(raw_shares) // lot) * lot)
    assert rounded == 0.0


def test_scan_for_entries_applies_hk_lot_rounding_right_after_pt_c2():
    """Source-text regression: the lot-rounding block must sit between PT-C2's own rounding
    and the FIN-07 skip check — after PT-C2 (so it rounds the FINAL shares value, not an
    intermediate one) and before FIN-07 (so a round-to-zero-lots candidate is caught by the
    existing `shares < 0.01` skip with no separate skip branch needed)."""
    pt_c2_idx = _ENGINE_SOURCE.index("# PT-C2: round shares first")
    lot_idx = _ENGINE_SOURCE.index("AUD262-HK-NO-BOARD-LOTS", pt_c2_idx)
    fin07_idx = _ENGINE_SOURCE.index("# FIN-07: skip near-zero share positions", pt_c2_idx)
    assert pt_c2_idx < lot_idx < fin07_idx


def test_scan_for_entries_re_applies_lot_rounding_after_the_max_position_cap():
    """The max_position_pct cap branch recomputes a fresh, fractional `shares` from
    max_pos/live_price — the lot-rounding must be re-applied inside that same branch, or a HK
    trade that hits the cap would exit with a fractional share count again."""
    cap_idx = _ENGINE_SOURCE.index('# Cap position at max_position_pct of equity')
    branch_end = _ENGINE_SOURCE.index("# PT-B5: Aggregate open-risk check", cap_idx)
    branch = _ENGINE_SOURCE[cap_idx:branch_end]
    assert "AUD262-HK-NO-BOARD-LOTS" in branch
    assert "_hk_board_lot_size" in branch


def test_lot_rounding_is_gated_on_market_hk_only():
    """A US candidate must never be lot-rounded — HKEX board lots are an HK-specific rule."""
    pt_c2_idx = _ENGINE_SOURCE.index("# PT-C2: round shares first")
    lot_idx = _ENGINE_SOURCE.index("AUD262-HK-NO-BOARD-LOTS", pt_c2_idx)
    fin07_idx = _ENGINE_SOURCE.index("# FIN-07: skip near-zero share positions", pt_c2_idx)
    block = _ENGINE_SOURCE[lot_idx:fin07_idx]
    assert 'cfg.get("market") == "HK"' in block
