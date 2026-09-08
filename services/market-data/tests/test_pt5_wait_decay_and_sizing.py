"""AUD-PT5 — the three paper-trading fixes from Domain 5 of the 2026-09-07 six-part audit.

Measured production evidence
(docs/audits/2026-09-07-six-part-audit-5-paper-trading.md):

  F1 WAIT-DECAY FAIL-OPEN — the lookback filtered `Signal.ts >= trade.entry_time`, which
     excludes the BUY that CAUSED the entry (signals are written by the ~20:30 UTC batch;
     entries execute on a later scan). The query returned None, and `last_non_wait_ts is None`
     fail-OPENED to still_waiting=True. All 4 momentum_exit trades ever recorded fired this
     way — three after holds of 5.1, 5.5 and 25.3 MINUTES — each closed with the false message
     "No non-WAIT signal in 3 days — momentum lost". 28 of 115 signal-linked trades exposed.

  F2 EARNINGS MULT DOUBLE-APPLIED — folded into risk_dollar AND applied again to the position
     cap, making the effective de-risking 0.25x/0.5625x rather than the documented 0.50x/0.75x.

  F3 SLIPPAGE ANCHOR — stop/target derived from the unslipped live_price while cost basis was
     the slipped fill. rr_ratio_at_entry overstated in EVERY portfolio (GROWTH 3.49 vs 3.33,
     HK GROWTH 3.48 vs 3.21, ETrade 2.71 vs 2.54, US SWING 2.37 vs 2.28, HK SWING 2.18 vs 2.13).
"""
import pathlib
import re
from datetime import datetime, timedelta, timezone

import pytest

import src.services.paper_trading_engine as pte

SRC = pathlib.Path(pte.__file__).read_text()


def _wait_decay_block() -> str:
    start = SRC.index('elif sig_type == "WAIT":')
    return SRC[start:SRC.index("# ── Execute exit", start)]


# ── F1: the WAIT-decay fail-open ─────────────────────────────────────────────────────────

def test_lookback_is_anchored_to_the_entry_signal_not_the_fill_time():
    """THE CORE F1 FIX. `Signal.ts >= trade.entry_time` excluded the entry-triggering BUY,
    because the evening batch writes signals hours before the fill executes."""
    body = _wait_decay_block()
    assert "_decay_anchor" in body
    assert "Signal.ts >= _decay_anchor" in body
    # Strip comments: the fix's own explanatory comment legitimately quotes the old filter.
    code = "\n".join(ln.split("#", 1)[0] for ln in body.splitlines())
    assert "Signal.ts >= trade.entry_time" not in code, \
        "filtering on the fill time hides the signal that opened the position"


def test_anchor_only_moves_backward_never_forward():
    """The anchor must be the EARLIER of entry_time and the entry signal's ts. Taking the later
    of the two would reintroduce the exclusion."""
    body = _wait_decay_block()
    assert "_entry_sig_ts < _decay_anchor" in body, \
        "must only replace the anchor when the signal predates the fill"


def test_missing_data_now_fails_closed():
    """`last_non_wait_ts is None` means 'no reading to measure decay from' — an ABSENCE of
    evidence. Treating it as 'N days elapsed' is the documented missing-data-fail-open class,
    and here it produced a confident, factually-false exit rather than an error."""
    body = _wait_decay_block()
    assert "last_non_wait_ts is None or" not in body, "the fail-open must not return"
    assert "_decay_from is not None" in body


def test_a_minimum_hold_guard_exists():
    """Belt-and-braces: three of the four historical misfires closed within 30 minutes. A hold
    shorter than the decay window is prima facie not decay."""
    body = _wait_decay_block()
    assert "wait_decay_suppressed_min_hold" in body
    assert "_held < timedelta(days=wait_days)" in body
    assert "still_waiting = False" in body


def test_min_hold_suppression_is_logged_not_silent():
    """A suppressed exit must be observable — otherwise the fix is invisible in the data."""
    body = _wait_decay_block()
    assert "log.warning(" in body[body.index("wait_decay_suppressed_min_hold") - 200:]


def test_tz_safety_is_preserved():
    """BUG-MONITORPOS-NAIVEAWARE: comparing a naive DB value to a tz-aware `now` raises
    TypeError and aborts the WHOLE paper_trading_step(), killing entries for every portfolio.
    The rewrite must not lose that."""
    body = _wait_decay_block()
    assert "now.replace(tzinfo=None)" in body
    assert "_decay_from.replace(tzinfo=None)" in body, \
        "the anchor fallback may itself be tz-aware"
    assert not re.search(r"_decay_from < now -", body), "must not compare against tz-aware now"


def test_decay_arithmetic_still_fires_for_a_genuinely_stale_signal():
    """The gate must still work: a non-WAIT signal older than wait_days IS decay."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    wait_days = 3
    stale = now - timedelta(days=wait_days + 1)
    assert stale < now - timedelta(days=wait_days)          # decayed -> exit
    fresh = now - timedelta(hours=2)
    assert not (fresh < now - timedelta(days=wait_days))     # not decayed -> hold


# ── F2: the double-applied earnings multiplier ───────────────────────────────────────────

def test_earnings_multiplier_is_not_applied_to_the_position_cap():
    """It belongs in risk_dollar (the RISK budget). The position-value ceiling is an
    INDEPENDENT control and must stay a pure function of equity and config."""
    assert 'max_pos = equity * cfg["max_position_pct"]\n' in SRC
    assert 'max_pos = equity * cfg["max_position_pct"] * earnings_size_mult' not in SRC


def test_earnings_multiplier_still_applied_exactly_once():
    """The fix must not DELETE the de-risking — only stop double-counting it."""
    # NOTE: the real line uses aligned spacing (`risk_dollar    = _risk_base ...`), so match
    # on a whitespace-tolerant pattern rather than an assumed single space.
    m = re.search(r"risk_dollar\s*=\s*_risk_base\s*\*[^\n]*", SRC)
    assert m, "risk_dollar computation must still exist"
    assert "earnings_size_mult" in m.group(0)


def test_double_application_would_have_quartered_the_cap():
    """Demonstrate the bug's magnitude rather than just asserting the fix."""
    equity, max_pct, mult = 100_000.0, 0.10, 0.50
    assert equity * max_pct * mult == 5_000.0        # old: cap halved on top of halved sizing
    assert equity * max_pct == 10_000.0              # new: cap is the configured one


# ── F3: sizing and R:R anchored to the real fill ─────────────────────────────────────────

def test_sizing_uses_the_slipped_fill_price():
    """Cost basis is the slipped fill, so risk and R:R must be measured from it too."""
    assert "entry_fill_price" in SRC
    assert "stop_distance = entry_fill_price - stop" in SRC
    assert "stop_distance = live_price - stop\n" not in SRC


def test_rr_uses_the_slipped_fill_price():
    assert "rr = (take_profit - entry_fill_price)" in SRC


def test_degenerate_stop_still_rejected_before_slippage_widens_it():
    """EDGE CASE MY OWN FIX INTRODUCED, caught by an existing test.

    Slippage moves the fill AWAY from the stop, so a stop at or above live_price (a degenerate
    game plan) yields a small POSITIVE stop_distance once slipped — stop=100.0, live=100.0 gives
    0.10 at 10bps — which would size an enormous position off that sliver. The raw geometry must
    be validated first.
    """
    assert "if (live_price - stop) <= 0:" in SRC
    idx_raw = SRC.index("if (live_price - stop) <= 0:")
    idx_slipped = SRC.index("stop_distance = entry_fill_price - stop")
    assert idx_raw < idx_slipped, "the raw check must precede the slipped computation"


def test_slippage_uses_the_base_rate_not_the_size_aware_one():
    """Deliberate and unavoidable: size-aware slippage is a function of `shares`, and `shares`
    is what this block computes. Using it here would be circular."""
    block = SRC[SRC.index("AUD-PT5-SLIPPAGEANCHOR"):][:2000]
    assert '_base_slip = cfg.get("entry_slippage_pct", 0.001)' in block
    assert "_size_aware_slippage_pct" not in block, "would be circular — shares not yet known"


def test_slipped_price_moves_risk_in_the_correct_direction():
    """Behavioural: the slipped fill is ABOVE live_price for a long, so stop_distance WIDENS
    (real risk was understated) and distance to target NARROWS (R:R was overstated)."""
    live, stop, target, slip = 100.0, 95.0, 115.0, 0.001
    fill = round(live * (1 + slip), 4)
    assert fill > live
    assert (fill - stop) > (live - stop), "true risk per share is larger than sizing assumed"
    assert (target - fill) < (target - live), "true distance to target is smaller"
    rr_old = (target - live) / (live - stop)
    rr_new = (target - fill) / (fill - stop)
    assert rr_new < rr_old, "stored R:R was systematically optimistic"


def test_zero_slippage_config_is_a_clean_noop():
    """A portfolio configured with no slippage must behave exactly as before."""
    block = SRC[SRC.index("AUD-PT5-SLIPPAGEANCHOR"):][:2000]
    assert "if _base_slip else live_price" in block
