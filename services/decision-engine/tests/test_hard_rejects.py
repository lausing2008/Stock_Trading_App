"""Tests for check_hard_rejects()'s gate ordering and boundary math.

check_hard_rejects() runs a sequence of hard gates, each returning early with a reject reason
the moment one fires — a real trading decision (open a paper position) depends on every one of
these being both individually correct AND correctly ordered (an earlier gate masking a later
one, or vice versa, changes what a candidate is actually being evaluated against). This file
uses a fixed, real, non-holiday weekday/time inside normal market hours (Tuesday 2026-07-14,
11:00 ET) via monkeypatching datetime.now, so every test exercises exactly the layer under
test without the market-closed/time-of-day gates interfering.

The conviction-gate check (added for T232-DL-DUALSCORER-DEBT) needs `common.config` — stubbed
here via sys.modules.setdefault, matching test_risk_agent.py's established convention for this
exact Docker-only dependency (real `redis` is installed and used directly; only `common`/
`common.config` need stubbing).
"""
import sys
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("common", MagicMock())
sys.modules.setdefault("common.config", MagicMock())

from src.api.core import hard_rejects as hr  # noqa: E402


_INSIDE_MARKET_HOURS_UTC = datetime(2026, 7, 14, 15, 0, 0, tzinfo=timezone.utc)  # Tue 11:00 ET


class _FrozenDateTime(datetime):
    """A datetime subclass whose .now() always returns a fixed instant, but whose other
    classmethods/instance methods behave normally — hard_rejects.py calls datetime.now(tz)
    directly, so this needs to intercept exactly that call."""
    _frozen_now = _INSIDE_MARKET_HOURS_UTC

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls._frozen_now.replace(tzinfo=None)
        return cls._frozen_now.astimezone(tz)


@pytest.fixture(autouse=True)
def _frozen_market_hours(monkeypatch):
    """Every test defaults to a safely-inside-market-hours, non-holiday Tuesday, so tests
    for OTHER gates don't have to think about the market-closed/time-of-day gates at all
    unless that's the specific thing being tested."""
    monkeypatch.setattr(hr, "datetime", _FrozenDateTime)


def _base_kwargs(**overrides):
    """A full set of arguments that clears every gate — individual tests override just the
    one input relevant to what they're testing."""
    kwargs = dict(
        signal_direction="BUY",
        confidence=70.0,
        live_price=100.0,
        stop_price=95.0,       # stop_dist = 5.0, well above the 0.5%/$0.05 floor
        take_profit=115.0,     # rr = (115-100)/5 = 3.0
        regime_state="bull",
        days_to_earnings=None,
        open_positions=1,
        max_positions=6,
        daily_pnl_pct=0.0,
        cfg={},
        research_rec=None,
        game_plan=None,
        market="US",
        reasons={"macro_blackout": None, "last_price": 100.0},  # avoid the DB call entirely
    )
    kwargs.update(overrides)
    return kwargs


def test_all_gates_clear_returns_none():
    assert hr.check_hard_rejects(**_base_kwargs()) is None


# ── Signal direction ──────────────────────────────────────────────────────────

def test_sell_signal_rejected_before_any_other_check():
    result = hr.check_hard_rejects(**_base_kwargs(signal_direction="SELL"))
    assert result is not None and "SELL" in result


# ── Market-closed / time-of-day gates ─────────────────────────────────────────

def test_weekend_blocks_entry():
    saturday = datetime(2026, 7, 18, 15, 0, 0, tzinfo=timezone.utc)  # a real Saturday
    class _Sat(_FrozenDateTime):
        _frozen_now = saturday
    import src.api.core.hard_rejects as hr_mod
    orig = hr_mod.datetime
    hr_mod.datetime = _Sat
    try:
        result = hr.check_hard_rejects(**_base_kwargs())
    finally:
        hr_mod.datetime = orig
    assert result is not None and "weekend" in result.lower()


def test_nyse_holiday_blocks_entry():
    christmas_2026 = datetime(2026, 12, 25, 15, 0, 0, tzinfo=timezone.utc)  # in _NYSE_HOLIDAYS
    import src.api.core.hard_rejects as hr_mod
    class _Holiday(_FrozenDateTime):
        _frozen_now = christmas_2026
    orig = hr_mod.datetime
    hr_mod.datetime = _Holiday
    try:
        result = hr.check_hard_rejects(**_base_kwargs())
    finally:
        hr_mod.datetime = orig
    assert result is not None and "holiday" in result.lower()


def test_time_of_day_gate_blocks_first_30_min_of_session():
    open_930_et = datetime(2026, 7, 14, 13, 45, 0, tzinfo=timezone.utc)  # 09:45 ET
    import src.api.core.hard_rejects as hr_mod
    class _Open(_FrozenDateTime):
        _frozen_now = open_930_et
    orig = hr_mod.datetime
    hr_mod.datetime = _Open
    try:
        result = hr.check_hard_rejects(**_base_kwargs())
    finally:
        hr_mod.datetime = orig
    assert result is not None and "first 30 min" in result


def test_time_of_day_gate_blocks_last_15_min_of_session():
    close_1550_et = datetime(2026, 7, 14, 19, 50, 0, tzinfo=timezone.utc)  # 15:50 ET
    import src.api.core.hard_rejects as hr_mod
    class _Close(_FrozenDateTime):
        _frozen_now = close_1550_et
    orig = hr_mod.datetime
    hr_mod.datetime = _Close
    try:
        result = hr.check_hard_rejects(**_base_kwargs())
    finally:
        hr_mod.datetime = orig
    assert result is not None and "last 15 min" in result


# ── Research gating ────────────────────────────────────────────────────────────

def test_research_gating_blocks_avoid_when_enabled():
    result = hr.check_hard_rejects(**_base_kwargs(
        cfg={"research_gating_enabled": True}, research_rec="AVOID",
    ))
    assert result is not None and "AVOID" in result


def test_research_gating_does_not_block_when_disabled():
    result = hr.check_hard_rejects(**_base_kwargs(
        cfg={"research_gating_enabled": False}, research_rec="AVOID",
    ))
    assert result is None


# ── Regime / portfolio-state gates ────────────────────────────────────────────

def test_bear_regime_blocks_all_entries():
    result = hr.check_hard_rejects(**_base_kwargs(regime_state="bear"))
    assert result is not None and "Bear regime" in result


def test_portfolio_full_blocks_entry():
    result = hr.check_hard_rejects(**_base_kwargs(open_positions=6, max_positions=6))
    assert result is not None and "full" in result.lower()


def test_daily_loss_limit_boundary():
    """daily_pnl_pct <= -abs(max_daily_loss) blocks — exactly at the limit must block,
    one basis point better must not."""
    at_limit = hr.check_hard_rejects(**_base_kwargs(daily_pnl_pct=-0.04, cfg={"max_daily_loss_pct": 0.04}))
    just_inside = hr.check_hard_rejects(**_base_kwargs(daily_pnl_pct=-0.0399, cfg={"max_daily_loss_pct": 0.04}))
    assert at_limit is not None and "Daily loss limit" in at_limit
    assert just_inside is None


def test_consecutive_loss_cooldown_at_cap():
    result = hr.check_hard_rejects(**_base_kwargs(
        cfg={"consec_losses": 3, "max_consecutive_losses": 3},
    ))
    assert result is not None and "cooldown" in result.lower()


def test_consecutive_loss_cooldown_below_cap_does_not_block():
    result = hr.check_hard_rejects(**_base_kwargs(
        cfg={"consec_losses": 2, "max_consecutive_losses": 3},
    ))
    assert result is None


# ── Confidence hard floor (90% of min_confidence) ─────────────────────────────

def test_confidence_just_above_hard_floor_passes():
    """hard_floor = min_confidence * 0.90 = 62 * 0.90 = 55.8 — confidence must be STRICTLY
    below the floor to reject (< not <=). Uses 55.81 rather than the exact float boundary
    (62.0 * 0.90 evaluates to 55.800000000000004 in IEEE 754, so asserting the literal 55.8
    passes would be testing float-rounding luck, not the gate's actual logic)."""
    result = hr.check_hard_rejects(**_base_kwargs(confidence=55.81, cfg={"min_confidence": 62.0}))
    assert result is None


def test_confidence_just_below_hard_floor_rejects():
    result = hr.check_hard_rejects(**_base_kwargs(confidence=55.7, cfg={"min_confidence": 62.0}))
    assert result is not None and "Confidence" in result


# ── Stop-distance validity ─────────────────────────────────────────────────────

def test_stop_above_price_is_invalid_setup():
    result = hr.check_hard_rejects(**_base_kwargs(stop_price=105.0, live_price=100.0))
    assert result is not None and "above price" in result


def test_stop_too_close_to_price_rejected():
    """min_stop_dist = max(live_price * 0.005, 0.05) = max(0.5, 0.05) = 0.5 at live_price=100.
    A stop_dist of exactly 0.4 (< 0.5) must reject."""
    result = hr.check_hard_rejects(**_base_kwargs(live_price=100.0, stop_price=99.6, take_profit=110.0))
    assert result is not None and "too close" in result


def test_stop_distance_exactly_at_minimum_passes():
    result = hr.check_hard_rejects(**_base_kwargs(live_price=100.0, stop_price=99.5, take_profit=110.0))
    assert result is None or "too close" not in result


# ── R:R gate, including regime-tightened floor ────────────────────────────────

def test_rr_below_default_minimum_rejects():
    # stop_dist=5, take_profit=108 -> rr = 8/5 = 1.6, below default min_rr_ratio=2.0
    result = hr.check_hard_rejects(**_base_kwargs(take_profit=108.0))
    assert result is not None and "R:R" in result


def test_rr_at_default_minimum_passes():
    # stop_dist=5, take_profit=110 -> rr = 10/5 = 2.0, exactly at the default floor
    result = hr.check_hard_rejects(**_base_kwargs(take_profit=110.0))
    assert result is None


def test_choppy_regime_tightens_rr_floor_to_regime_min():
    """T190: in choppy/risk_off, min_rr is raised to regime_min_rr_ratio (default 3.0).
    An R:R of 2.5 clears the base 2.0 floor but must be rejected in choppy."""
    result = hr.check_hard_rejects(**_base_kwargs(
        regime_state="choppy", take_profit=112.5,  # rr = 12.5/5 = 2.5
    ))
    assert result is not None and "R:R" in result


def test_choppy_regime_passes_when_rr_clears_the_tightened_floor():
    result = hr.check_hard_rejects(**_base_kwargs(
        regime_state="choppy", take_profit=115.0,  # rr = 15/5 = 3.0, clears regime floor
    ))
    assert result is None


def test_custom_regime_min_rr_ratio_is_respected():
    result = hr.check_hard_rejects(**_base_kwargs(
        regime_state="risk_off", take_profit=115.0,  # rr = 3.0
        cfg={"regime_min_rr_ratio": 3.5},
    ))
    assert result is not None and "R:R" in result


# ── Earnings proximity ─────────────────────────────────────────────────────────

def test_earnings_within_5_days_blocks():
    result = hr.check_hard_rejects(**_base_kwargs(days_to_earnings=5))
    assert result is not None and "Earnings" in result


def test_earnings_6_days_out_does_not_block():
    result = hr.check_hard_rejects(**_base_kwargs(days_to_earnings=6))
    assert result is None


# ── Signal staleness hard reject (T222-C, T234-CONFIG-UNJUSTIFIED-THRESHOLDS) ─────────────
# Genuinely distinct from decision-engine's own soft SA-24 freshness SCORE (scorer.py's
# 4h/18h bands, -1/+1 points, never a hard reject) — this is a separate, earlier HARD cutoff
# ported from paper_trading_engine.py's _scan_for_entries(), default 72h.

def test_stale_signal_beyond_max_age_blocks():
    stale_ts = (_INSIDE_MARKET_HOURS_UTC.replace(tzinfo=None) - timedelta(hours=100)).isoformat() + "Z"
    result = hr.check_hard_rejects(**_base_kwargs(sig_ts=stale_ts))
    assert result is not None and "stale" in result.lower()


def test_signal_within_max_age_does_not_block():
    fresh_ts = (_INSIDE_MARKET_HOURS_UTC.replace(tzinfo=None) - timedelta(hours=10)).isoformat() + "Z"
    result = hr.check_hard_rejects(**_base_kwargs(sig_ts=fresh_ts))
    assert result is None


def test_signal_staleness_respects_custom_max_age():
    ts_50h_old = (_INSIDE_MARKET_HOURS_UTC.replace(tzinfo=None) - timedelta(hours=50)).isoformat() + "Z"
    # Default 72h: 50h old passes.
    assert hr.check_hard_rejects(**_base_kwargs(sig_ts=ts_50h_old)) is None
    # Tightened to 24h: the same 50h-old signal must now be rejected.
    result = hr.check_hard_rejects(**_base_kwargs(sig_ts=ts_50h_old, cfg={"max_signal_age_hours": 24}))
    assert result is not None and "stale" in result.lower()


def test_no_staleness_check_when_sig_ts_absent():
    result = hr.check_hard_rejects(**_base_kwargs(sig_ts=None))
    assert result is None


def test_malformed_sig_ts_fails_open():
    result = hr.check_hard_rejects(**_base_kwargs(sig_ts="not-a-real-timestamp"))
    assert result is None


def test_stale_signal_accepts_a_real_datetime_object_not_just_a_string():
    """sig_ts can arrive as a real datetime (e.g. a value read straight from the DB) as well
    as an ISO string (from a JSON round-trip) — both must be handled."""
    stale_dt = _INSIDE_MARKET_HOURS_UTC - timedelta(hours=100)
    result = hr.check_hard_rejects(**_base_kwargs(sig_ts=stale_dt))
    assert result is not None and "stale" in result.lower()


# ── K-Score floor hard reject (T232-DL-DUALSCORER-DEBT) ───────────────────────────────────
# Genuinely distinct from this file's own AUD232-042 soft K-Score SCORE layer in scorer.py
# (±1 for kscore >=/< 55) — min_kscore is a separate, earlier HARD pre-filter ported from
# paper_trading_engine.py's _scan_for_entries(), which discards a candidate entirely before
# it's ever scored (per-style default 48-52, distinct from scorer.py's fixed 55 boundary).

def test_kscore_below_min_kscore_blocks():
    result = hr.check_hard_rejects(**_base_kwargs(cfg={"kscore": 40.0, "min_kscore": 48.0}))
    assert result is not None and "K-Score" in result and "40" in result


def test_kscore_at_or_above_min_kscore_does_not_block():
    result = hr.check_hard_rejects(**_base_kwargs(cfg={"kscore": 48.0, "min_kscore": 48.0}))
    assert result is None
    result2 = hr.check_hard_rejects(**_base_kwargs(cfg={"kscore": 60.0, "min_kscore": 48.0}))
    assert result2 is None


def test_kscore_gate_skipped_when_min_kscore_absent():
    """An older caller not yet sending min_kscore (or kscore) must not be blocked — this gate
    is opt-in via cfg, matching every other optional gate in this file."""
    result = hr.check_hard_rejects(**_base_kwargs(cfg={"kscore": 10.0}))  # min_kscore absent
    assert result is None


def test_kscore_gate_skipped_when_kscore_itself_absent():
    """min_kscore present but no actual kscore value sent — must not crash or spuriously
    block; there's nothing to compare against."""
    result = hr.check_hard_rejects(**_base_kwargs(cfg={"min_kscore": 48.0}))  # kscore absent
    assert result is None


def test_kscore_gate_respects_per_style_thresholds():
    """paper_trading_engine.py's real per-style defaults (GROWTH=48, SWING=52, LONG=50) — a
    candidate that clears GROWTH's floor but not SWING's must be blocked under SWING's."""
    result = hr.check_hard_rejects(**_base_kwargs(cfg={"kscore": 50.0, "min_kscore": 52.0}))
    assert result is not None and "K-Score" in result


# ── Gap filter (T171) ──────────────────────────────────────────────────────────
# NOTE: the gap filter runs AFTER the R:R gate (see gate order at the top of this file), so
# every test here must also move stop_price/take_profit along with live_price to keep R:R
# clearing its 2.0 floor — otherwise the R:R gate fires first and masks the gap-filter result.

def test_gap_up_beyond_limit_rejects():
    live_price = 105.0  # 5% above signal close of 100 -> exceeds default 4% limit
    result = hr.check_hard_rejects(**_base_kwargs(
        live_price=live_price, stop_price=live_price - 5.0, take_profit=live_price + 15.0,
        reasons={"macro_blackout": None, "last_price": 100.0},
    ))
    assert result is not None and "Gap-up" in result


def test_gap_up_within_limit_does_not_reject():
    live_price = 103.0  # 3% above signal close of 100 -> within default 4% limit
    result = hr.check_hard_rejects(**_base_kwargs(
        live_price=live_price, stop_price=live_price - 5.0, take_profit=live_price + 15.0,
        reasons={"macro_blackout": None, "last_price": 100.0},
    ))
    assert result is None


def test_no_gap_check_when_last_price_absent_from_reasons():
    live_price = 200.0  # would be a huge gap if last_price were known — but it's not provided
    result = hr.check_hard_rejects(**_base_kwargs(
        live_price=live_price, stop_price=live_price - 5.0, take_profit=live_price + 15.0,
        reasons={"macro_blackout": None},
    ))
    assert result is None


# ── Macro blackout (fast path via reasons, avoiding the DB call) ──────────────

def test_macro_blackout_from_reasons_fast_path_blocks():
    result = hr.check_hard_rejects(**_base_kwargs(
        reasons={"macro_blackout": "FOMC Rate Decision", "last_price": 100.0},
    ))
    assert result is not None and "FOMC" in result


# ── Sector concentration cap (T232-DL-DUALSCORER) ─────────────────────────────

def test_sector_position_count_cap_blocks():
    result = hr.check_hard_rejects(**_base_kwargs(
        cfg={"candidate_sector": "Technology", "open_sector_counts": {"Technology": 3}, "max_sector_positions": 3},
    ))
    assert result is not None and "Technology" in result


def test_sector_position_count_below_cap_does_not_block():
    result = hr.check_hard_rejects(**_base_kwargs(
        cfg={"candidate_sector": "Technology", "open_sector_counts": {"Technology": 2}, "max_sector_positions": 3},
    ))
    assert result is None


# ── Extended-move guard ────────────────────────────────────────────────────────
# NOTE: like the gap filter above, this gate runs after R:R (and after the gap filter, which
# also needs last_price omitted here so it doesn't fire first) — stop_price/take_profit move
# with live_price so only the extended-move gate is under test.

def test_extended_move_beyond_breakout_threshold_rejects():
    live_price = 110.0  # breakout=100, 10% extension, exceeds default 6% threshold
    result = hr.check_hard_rejects(**_base_kwargs(
        live_price=live_price, stop_price=live_price - 5.0, take_profit=live_price + 15.0,
        game_plan={"breakout": 100.0},
        reasons={"macro_blackout": None},
    ))
    assert result is not None and "breakout" in result.lower()


def test_extended_move_within_threshold_does_not_reject():
    live_price = 105.0  # breakout=100, 5% extension, within default 6% threshold
    result = hr.check_hard_rejects(**_base_kwargs(
        live_price=live_price, stop_price=live_price - 5.0, take_profit=live_price + 15.0,
        game_plan={"breakout": 100.0},
        reasons={"macro_blackout": None},
    ))
    assert result is None


# ── Gate ordering: earlier gates must fire before later ones ─────────────────

def test_bear_regime_fires_before_rr_check_even_when_rr_is_also_bad():
    """A candidate failing BOTH the bear-regime gate and the R:R gate should report the
    bear-regime reason (it comes first), not the R:R reason — confirms gate ORDER, not just
    that each gate works in isolation."""
    result = hr.check_hard_rejects(**_base_kwargs(regime_state="bear", take_profit=101.0))
    assert result is not None and "Bear regime" in result


def test_confidence_floor_fires_before_stop_distance_check():
    result = hr.check_hard_rejects(**_base_kwargs(
        confidence=10.0, stop_price=105.0,  # both confidence AND stop-distance are bad
    ))
    assert result is not None and "Confidence" in result


# ── Conviction gate (T232-DL-DUALSCORER-DEBT) ─────────────────────────────────
# Reads the same conv_gate:{symbol}:{style} Redis key the alert system writes. Mocks
# redis.Redis.from_url so no real Redis connection is needed — matches this file's own
# established pattern of mocking exactly the external dependency a gate reaches out to
# (datetime.now for the time gates, here redis.Redis.from_url for this one).

class _FakeRedisClient:
    def __init__(self, value: str | None):
        self._value = value

    def get(self, key):
        return self._value


def _mock_conv_gate_redis(monkeypatch, value: str | None):
    import redis as _redis_lib
    monkeypatch.setattr(
        _redis_lib.Redis, "from_url",
        classmethod(lambda cls, *a, **kw: _FakeRedisClient(value)),
    )


def test_conviction_gate_failed_blocks_entry(monkeypatch):
    _mock_conv_gate_redis(monkeypatch, '{"signal": "BUY", "sent": false, "failed": ["K-Score", "RSI"]}')
    result = hr.check_hard_rejects(**_base_kwargs(symbol="AAPL", style="SWING"))
    assert result is not None and "Conviction gate failed" in result
    assert "K-Score" in result


def test_conviction_gate_passed_does_not_block(monkeypatch):
    _mock_conv_gate_redis(monkeypatch, '{"signal": "BUY", "sent": true, "failed": []}')
    result = hr.check_hard_rejects(**_base_kwargs(symbol="AAPL", style="SWING"))
    assert result is None


def test_conviction_gate_missing_key_fails_open(monkeypatch):
    """No gate key = gate not yet run — matches _should_enter()'s own fail-open-on-missing-
    data behavior; must NOT be treated as a failure."""
    _mock_conv_gate_redis(monkeypatch, None)
    result = hr.check_hard_rejects(**_base_kwargs(symbol="AAPL", style="SWING"))
    assert result is None


def test_conviction_gate_skipped_when_symbol_or_style_missing(monkeypatch):
    """Without symbol/style (e.g. an older caller not yet passing them), the gate must never
    even ATTEMPT the Redis lookup — not just "fail open eventually." Verified via a call-
    tracking mock rather than leaving Redis unmocked: an earlier version of this test relied
    on an unmocked `redis.Redis.from_url` to prove the guard worked (reasoning "if the code
    tried to reach Redis it would hit a real connection attempt and fail"), but with
    common.config stubbed as MagicMock, get_settings().redis_url is itself a MagicMock, and
    the resulting TypeError inside redis.Redis.from_url() is caught by the SAME outer
    except-Exception that handles genuine Redis failures — so removing the `if symbol and
    style:` guard entirely still produced result=None, just via the exception path instead of
    the intended skip path, and this test could not tell the difference. A call-counting mock
    makes the two paths distinguishable."""
    import redis as _redis_lib
    call_count = {"n": 0}

    class _TrackedRedis:
        def get(self, key):
            call_count["n"] += 1
            return None

    monkeypatch.setattr(_redis_lib.Redis, "from_url", classmethod(lambda cls, *a, **kw: _TrackedRedis()))
    result = hr.check_hard_rejects(**_base_kwargs(symbol=None, style=None))
    assert result is None
    assert call_count["n"] == 0, "conviction gate must not attempt a Redis lookup without symbol/style"


def test_conviction_gate_redis_error_fails_open(monkeypatch):
    """A Redis connection failure must allow entry, not block it — matches every other
    fail-open gate in this file (tz lookup failures, DB query failures for macro blackout)."""
    import redis as _redis_lib

    class _BrokenRedis:
        def get(self, key):
            raise ConnectionError("redis unavailable")

    monkeypatch.setattr(_redis_lib.Redis, "from_url", classmethod(lambda cls, *a, **kw: _BrokenRedis()))
    result = hr.check_hard_rejects(**_base_kwargs(symbol="AAPL", style="SWING"))
    assert result is None


def test_conviction_gate_ignores_non_buy_signal_in_cached_data():
    """A cached gate entry for a SELL (or any non-BUY) signal must never block a BUY
    evaluation — the gate only blocks when the CACHED signal itself was a failed BUY."""
    def _mock(monkeypatch, value):
        import redis as _redis_lib
        monkeypatch.setattr(_redis_lib.Redis, "from_url", classmethod(lambda cls, *a, **kw: _FakeRedisClient(value)))
    import pytest as _pytest
    mp = _pytest.MonkeyPatch()
    try:
        _mock(mp, '{"signal": "SELL", "sent": false, "failed": ["RSI"]}')
        result = hr.check_hard_rejects(**_base_kwargs(symbol="AAPL", style="SWING"))
        assert result is None
    finally:
        mp.undo()
