"""Tests for IF-06: _size_aware_slippage_pct() and _avg_daily_volume_for()
(paper_trading_engine.py) — the flat 10bps entry/exit slippage constant now scales with
position size relative to the symbol's own liquidity.

_size_aware_slippage_pct() is a pure function with no DB/Redis dependency, imported directly
(module-level, matching test_paper_trading_engine.py's existing convention for its sibling pure
function _slipped_position_value()).

_avg_daily_volume_for() reads common.redis_client.get_redis() — conftest.py stubs
"common.redis_client" as a bare MagicMock() shared across the whole pytest session, so patching
must target sys.modules["common.redis_client"].get_redis directly (the one object every
`from common.redis_client import get_redis` statement in the same process actually shares) —
NOT a freshly re-imported name, which would silently miss the real call site (the exact gotcha
already documented in CLAUDE.md's Redis-connection-pooling audit history).
"""
import json
import pathlib
import sys
from unittest.mock import MagicMock

from src.services.paper_trading_engine import (
    _avg_daily_volume_for,
    _size_aware_slippage_pct,
    _SIZE_AWARE_SLIPPAGE_IMPACT_K,
)

_ENGINE_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_ENGINE_SOURCE = _ENGINE_PATH.read_text()


# ── _size_aware_slippage_pct() — pure function ──────────────────────────────────────────────

def test_returns_the_flat_base_when_avg_daily_volume_is_none():
    """Fail-open: no average-volume data for this symbol yet -> unmodified base, never lower."""
    assert _size_aware_slippage_pct(shares=1000, avg_daily_volume=None, base_slippage_pct=0.001) == 0.001


def test_returns_the_flat_base_when_avg_daily_volume_is_zero_or_negative():
    assert _size_aware_slippage_pct(1000, 0, 0.001) == 0.001
    assert _size_aware_slippage_pct(1000, -500, 0.001) == 0.001


def test_returns_the_flat_base_when_shares_is_zero_or_negative():
    assert _size_aware_slippage_pct(0, 100_000, 0.001) == 0.001
    assert _size_aware_slippage_pct(-10, 100_000, 0.001) == 0.001


def test_a_tiny_participation_rate_produces_a_slippage_close_to_the_base():
    """1 share against 10M average daily volume is an utterly negligible participation rate —
    the size-aware model must not meaningfully inflate slippage for a trivial order."""
    result = _size_aware_slippage_pct(shares=1, avg_daily_volume=10_000_000, base_slippage_pct=0.001)
    assert result == 0.001 or abs(result - 0.001) < 0.0001


def test_a_large_participation_rate_produces_materially_higher_slippage_than_the_base():
    """A position that is 100% of a stock's own average daily volume is enormous — the model
    must scale slippage meaningfully above the flat constant.
    Hand-verified: participation=1.0, sqrt(1.0)=1.0, result = 0.001*(1+2.0*1.0) = 0.003 (3x base)."""
    result = _size_aware_slippage_pct(shares=100_000, avg_daily_volume=100_000, base_slippage_pct=0.001)
    assert result == 0.003
    assert result > 0.001 * 2  # at least double the flat base


def test_matches_the_documented_sqrt_participation_formula_by_hand():
    """Verify the exact formula, not just directional behavior: participation = 4%,
    sqrt(0.04) = 0.2, so result = base * (1 + K * 0.2)."""
    shares, avg_vol, base = 4_000, 100_000, 0.001
    expected = round(base * (1 + _SIZE_AWARE_SLIPPAGE_IMPACT_K * (0.04 ** 0.5)), 6)
    assert _size_aware_slippage_pct(shares, avg_vol, base) == expected


def test_higher_participation_always_produces_monotonically_higher_slippage():
    """A bigger order relative to the SAME liquidity must never produce lower slippage than a
    smaller one — the model must be monotonic in participation rate."""
    avg_vol = 500_000
    small = _size_aware_slippage_pct(1_000, avg_vol, 0.001)
    medium = _size_aware_slippage_pct(10_000, avg_vol, 0.001)
    large = _size_aware_slippage_pct(50_000, avg_vol, 0.001)
    assert small < medium < large


def test_a_thinner_stock_produces_higher_slippage_for_the_same_share_count():
    """The SAME order size against a THINNER stock (lower avg_daily_volume) must produce higher
    slippage than against a more liquid one — this is the whole point of the feature."""
    shares = 5_000
    liquid = _size_aware_slippage_pct(shares, avg_daily_volume=5_000_000, base_slippage_pct=0.001)
    thin = _size_aware_slippage_pct(shares, avg_daily_volume=50_000, base_slippage_pct=0.001)
    assert thin > liquid


def test_never_produces_slippage_lower_than_the_flat_base():
    """The size-aware model must only ever be as-or-more conservative than the flat constant it
    replaces — it can raise the estimate, never lower it below the base."""
    for shares, avg_vol in [(1, 1_000_000_000), (100, 50_000), (0, 100_000)]:
        assert _size_aware_slippage_pct(shares, avg_vol, 0.001) >= 0.001


# ── _avg_daily_volume_for() — Redis lookup, fail-open ───────────────────────────────────────

def _set_avg_volume_cache(value_or_raiser):
    """Patch the SHARED common.redis_client module's get_redis so _avg_daily_volume_for()'s own
    module-level `from common.redis_client import get_redis` call resolves to this fake."""
    if callable(value_or_raiser) and not isinstance(value_or_raiser, dict):
        fake_redis = MagicMock()
        fake_redis.get.side_effect = value_or_raiser
    else:
        fake_redis = MagicMock()
        fake_redis.get.return_value = json.dumps(value_or_raiser)
    sys.modules["common.redis_client"].get_redis = MagicMock(return_value=fake_redis)


def test_returns_the_cached_value_for_a_known_symbol():
    _set_avg_volume_cache({"AAPL": 55_000_000, "TSLA": 90_000_000})
    assert _avg_daily_volume_for("AAPL") == 55_000_000.0


def test_returns_none_for_a_symbol_not_in_the_cache():
    _set_avg_volume_cache({"AAPL": 55_000_000})
    assert _avg_daily_volume_for("ZZZZ") is None


def test_returns_none_when_the_cache_is_entirely_empty():
    _set_avg_volume_cache({})
    assert _avg_daily_volume_for("AAPL") is None


def test_fails_open_to_none_on_a_redis_connection_error():
    def _raise(*a, **kw):
        raise ConnectionError("redis unreachable")
    _set_avg_volume_cache(_raise)
    assert _avg_daily_volume_for("AAPL") is None


def test_fails_open_to_none_on_malformed_json():
    fake_redis = MagicMock()
    fake_redis.get.return_value = "not valid json {{{"
    sys.modules["common.redis_client"].get_redis = MagicMock(return_value=fake_redis)
    assert _avg_daily_volume_for("AAPL") is None


# ── Wiring — source-text regression checks across all 5 real call sites ────────────────────
# _monitor_positions()/_open_paper_trade()/_scan_for_entries() have heavy DB/session/live-
# price/live-regime dependencies disproportionate to a full behavioral exercise of the wiring
# alone — matching test_drawdown_alert.py's own established pattern for this exact constraint.

def _function_body(name: str, next_def: str) -> str:
    start = _ENGINE_SOURCE.index(f"def {name}(")
    end = _ENGINE_SOURCE.index(f"\n\ndef {next_def}(", start)
    return _ENGINE_SOURCE[start:end]


def test_final_exit_site_is_gated_and_size_aware():
    body = _function_body("_monitor_positions", "_open_paper_trade")
    assert "_size_aware_slippage_pct(trade.shares, _avg_daily_volume_for(trade.symbol), _base_slippage)" in body
    assert 'cfg.get("size_aware_slippage_enabled", True)' in body


def test_both_partial_scale_out_levels_use_their_own_tranche_share_count_not_the_full_position():
    """Each scale-out level's slippage lookup must use that level's OWN partial_shares (the
    tranche actually being sold), not the full remaining trade.shares — a 33%-of-position
    tranche should never be size-adjusted as if the WHOLE position were being sold at once."""
    body = _function_body("_monitor_positions", "_open_paper_trade")
    occurrences = body.count("_size_aware_slippage_pct(partial_shares, _avg_daily_volume_for(trade.symbol), _base_slippage)")
    assert occurrences == 2  # once for level 1, once for level 2 — each computed AFTER partial_shares


def test_entry_site_is_gated_and_size_aware():
    body = _function_body("_open_paper_trade", "_scan_for_entries")
    assert "_size_aware_slippage_pct(shares, _avg_daily_volume_for(stock.symbol), _base_slippage)" in body
    assert 'cfg.get("size_aware_slippage_enabled", True)' in body


def test_scale_in_add_site_is_gated_and_size_aware():
    idx = _ENGINE_SOURCE.index("def _scan_for_entries(")
    body = _ENGINE_SOURCE[idx:]
    assert "_size_aware_slippage_pct(_si_approx_shares, _avg_daily_volume_for(_si_trade.symbol), _si_base_slippage)" in body
    assert 'cfg.get("size_aware_slippage_enabled", True)' in body


def test_the_toggle_disabled_case_falls_back_to_the_unmodified_flat_base_at_every_site():
    """When size_aware_slippage_enabled is explicitly False, every site must use the plain
    _base_slippage value with no size-aware adjustment at all — confirming the toggle is a real
    escape hatch, not just present in the config lookup."""
    exit_body = _function_body("_monitor_positions", "_open_paper_trade")
    entry_body = _function_body("_open_paper_trade", "_scan_for_entries")
    scale_in_body = _ENGINE_SOURCE[_ENGINE_SOURCE.index("def _scan_for_entries("):]
    assert "else _base_slippage" in exit_body
    assert "else _base_slippage" in entry_body
    assert "else _si_base_slippage" in scale_in_body
