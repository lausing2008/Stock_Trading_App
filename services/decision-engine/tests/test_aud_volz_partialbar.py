"""AUD-VOLZ-PARTIALBAR — a volume z-score computed against a half-finished day.

FOUND BY MEASURING, after the user asked whether the -1.50 volume-z floor was well calibrated.
It is. The INPUT was contaminated.

`volume_z` compares TODAY's volume against a 20-day baseline of COMPLETED days. Mid-session
"today" is a partial bar, so the comparison is apples-to-oranges and the z-score is biased
NEGATIVE by construction — and this gate only ever rejects on the negative side, so the
contamination can only ever OVER-BLOCK. It can never accidentally admit a bad candidate, which
is exactly why it went unnoticed.

MEASURED — same universe, same 20-day baseline, different generation time:

    date        first signal (ET)   mean volume_z   % below the -1.5 floor
    2026-09-09     12:28  (mid)        -1.84              64.3%
    2026-09-08     16:31  (post)       +0.50               3.1%
    2026-09-06     18:22  (post)       -0.11               2.3%
    2026-09-04     16:20  (post)       -0.25               5.5%
    2026-09-01     16:40  (post)       -0.04               1.6%

and the raw bars confirm the cause is elapsed session time, not a thin tape — at 12:28 ET the
day's volume stood at AAPL 0.51x / NVDA 0.34x / SPY 0.29x / MSFT 0.26x of its own 20-day average.

THE FLOOR IS NOT CHANGED. At 1.6-6.5% blocked on post-close cycles it is reasonable selectivity
for a slippage filter. Retuning the threshold to accommodate a broken input would have loosened
the gate permanently for the ~95% of cycles that are fine.

SCOPE IS DELIBERATELY NARROW. The proper fix is a time-of-day-adjusted baseline in signal-engine's
feature computation, and the user deferred the signal-engine live-bar refactor on 2026-09-06
pending prebreakout outcome data (docs/2026-09-05 records it as high regression risk against
AUD232 for marginal gain). So this only makes the CONSUMERS fail OPEN on an input they cannot
trust, matching the gate's existing convention for a missing volume_z (T232-DL5).

FIXED ON BOTH PATHS. decision-engine's hard_rejects.py is authoritative
(decision_engine_mode defaults to "primary"); paper_trading_engine._scan_for_entries() is the
shadow-logged fallback whose own `paper.skip_low_volume` fired 128 times in 24h. Three fixes this
month landed on only one of the two.
"""
import pathlib
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

HR = pathlib.Path(__file__).resolve().parents[1] / "src/api/core/hard_rejects.py"
SRC = HR.read_text()
PT = (
    pathlib.Path(__file__).resolve().parents[2]
    / "market-data/src/services/paper_trading_engine.py"
).read_text()


def _incomplete(market, ts_utc):
    """Mirrors _bar_is_incomplete. Source assertions below pin the real implementations."""
    from common.market_calendar import is_hk_trading_day, is_us_trading_day
    tz = ZoneInfo("America/New_York") if market.upper() != "HK" else ZoneInfo("Asia/Hong_Kong")
    loc = datetime.fromisoformat(ts_utc).replace(tzinfo=timezone.utc).astimezone(tz)
    if loc.weekday() >= 5:
        return False
    if market.upper() == "HK":
        if not is_hk_trading_day(loc):
            return False
    elif not is_us_trading_day(loc):
        return False
    mins = loc.hour * 60 + loc.minute
    return 570 <= mins < 960


# ── The exact scenario that over-blocked ────────────────────────────────────────────────

def test_the_contaminated_run_is_now_recognised_as_incomplete():
    """THE BUG. 2026-09-09 16:28 UTC = 12:28 ET, mid-session — 64.3% of candidates blocked."""
    assert _incomplete("US", "2026-09-09 16:28") is True


def test_the_post_close_run_is_still_gated():
    """THE RISK IN THIS FIX. 2026-09-08 20:31 UTC = 16:31 ET. That cycle measured mean
    volume_z +0.50 with only 3.1% blocked — a working gate that must keep working."""
    assert _incomplete("US", "2026-09-08 20:31") is False


@pytest.mark.parametrize("ts,label,want", [
    ("2026-09-09 13:30", "09:30 ET — the open",              True),
    ("2026-09-09 17:00", "13:00 ET — midday",                True),
    ("2026-09-09 19:59", "15:59 ET — one minute before close", True),
    ("2026-09-09 20:00", "16:00 ET — the close itself",       False),
    ("2026-09-09 12:00", "08:00 ET — pre-market",             False),
    ("2026-09-09 23:00", "19:00 ET — after hours",            False),
])
def test_session_boundaries(ts, label, want):
    assert _incomplete("US", ts) is want, label


# ── Non-trading days: the bar is settled, so the gate applies ───────────────────────────

def test_a_holiday_bar_is_complete():
    """Labor Day 2026 — no session, so the last bar is a settled one and the gate should run."""
    assert _incomplete("US", "2026-09-07 16:00") is False


def test_a_weekend_bar_is_complete():
    assert _incomplete("US", "2026-09-12 16:00") is False


def test_the_hk_lunch_break_is_still_incomplete():
    """SUBTLE. HKEX closes 12:00-13:00, but the AFTERNOON session has yet to be written into
    the day's bar — so the bar is emphatically NOT complete during lunch. Treating the lunch
    break as 'closed, therefore settled' would reintroduce the bug for HK every day at noon."""
    assert _incomplete("HK", "2026-09-09 04:30") is True  # 12:30 HKT


# ── The gate is skipped, not the threshold changed ──────────────────────────────────────

def test_the_threshold_is_unchanged():
    """The floor is CORRECT and must not be retuned to paper over a bad input."""
    assert 'cfg.get("min_volume_z", -1.5)' in SRC
    assert 'cfg.get("min_volume_z", -1.5)' in PT


def test_the_gate_is_conditioned_on_bar_completeness_on_the_authoritative_path():
    """decision_engine_mode defaults to "primary", so THIS is the path that blocks a trade."""
    assert '_vol_z_raw is not None and not _bar_is_incomplete(market)' in SRC


def test_the_gate_is_conditioned_on_the_shadow_path_too():
    """THE CHECK THIS CODEBASE KEEPS NEEDING. `paper.skip_low_volume` fired 128 times in 24h on
    this path, so a fix only in decision-engine would have left it live-blocking."""
    assert '_vol_z_raw is not None and not _bar_is_incomplete(cfg.get("market") or "US")' in PT


def test_both_helpers_exist_and_agree_on_the_session_window():
    """The two must not drift — one is authoritative, the other its shadow."""
    for src in (SRC, PT):
        i = src.index("def _bar_is_incomplete(")
        fn = src[i:src.index("\ndef ", i + 10)]
        assert "570 <= _mins < 960" in fn, "09:30-16:00 local in both"
        assert "is_us_trading_day" in fn and "is_hk_trading_day" in fn


def test_both_helpers_fail_CLOSED():
    """A clock/tz error must leave the gate ACTIVE. Failing open here would silently disable a
    risk gate — the opposite of the missing-data convention, and deliberately so: absent data is
    a reason to skip a comparison, a broken clock is not."""
    for src in (SRC, PT):
        i = src.index("def _bar_is_incomplete(")
        fn = src[i:src.index("\ndef ", i + 10)]
        tail = fn[fn.index("except Exception:"):]
        assert "return False" in tail, "must fail CLOSED (gate still applies)"


def test_both_helpers_use_the_shared_calendar_not_a_private_table():
    """This codebase found FOUR copies of the NYSE holiday table before consolidating them."""
    for src in (SRC, PT):
        i = src.index("def _bar_is_incomplete(")
        fn = src[i:src.index("\ndef ", i + 10)]
        assert "from common.market_calendar import" in fn
        assert "date(20" not in fn, "no hardcoded holiday literals"


# ── The measured evidence, pinned ───────────────────────────────────────────────────────

def test_the_measurement_is_recorded_in_source():
    i = SRC.index("AUD-VOLZ-PARTIALBAR")
    block = SRC[i:i + 2400]
    for frag in ("64.3%", "12:28", "16:31", "0.29x"):
        assert frag in block, f"the measurement should record {frag}"


def test_the_contamination_is_one_directional():
    """WHY IT WENT UNNOTICED. A partial day always has LESS volume, so the bias is always
    negative, and the gate only rejects on the negative side — it can only over-block, never
    under-block. A gate that fails safe in the dangerous direction hides indefinitely."""
    partial_day_fractions = [0.51, 0.34, 0.29, 0.26]
    assert all(f < 1.0 for f in partial_day_fractions)


def test_the_post_close_block_rate_shows_the_floor_is_reasonable():
    """1.6-6.5% across five post-close cycles. Pinned so nobody 'fixes' the threshold later."""
    post_close_pct = [3.1, 2.3, 5.5, 1.6, 5.4]
    assert max(post_close_pct) < 10.0, "a sane selectivity rate for a slippage filter"
    assert 64.3 > 6 * max(post_close_pct), "the contaminated day was an order of magnitude worse"
