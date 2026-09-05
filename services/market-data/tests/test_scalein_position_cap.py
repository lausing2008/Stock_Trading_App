"""AUD-SCALEIN-BYPASSES-POSCAP: the scale-in path walked past max_position_pct.

_size_position() enforces the cap correctly on ENTRY (paper_trading_engine.py:
`max_pos = equity * cfg["max_position_pct"] * earnings_size_mult`). But the scale-in branch in
_scan_for_entries() gated ONLY on cash availability:

    if portfolio.current_cash >= _si_add_value * 1.1:

so a position already sitting at the cap could scale straight past it. Confirmed against 124
real production trades: JPM reached 13.0% of capital against a 10% cap, and HK GROWTH's 0005.HK
reached 12.7% — a ~30% overshoot on 6 of 124 trades. JPM's own entry_decision_notes show the
stack that produced it: "Size 1.25x (confidence 63%)", "Size 1.15x (multi-timeframe consensus)",
"SCALE_IN", "Scale-in: +3.7469sh @ $360.80 (+5.2%, conf 87%)".

Every component was individually intentional and the overshoot is bounded — but a hard cap a
code path can walk past is not a hard cap.

Fixed by TRUNCATING the add to remaining headroom rather than skipping it: a scale-in only fires
on an already-PROFITABLE position, so adding what the cap allows is better than forfeiting the
add entirely.

paper_trading_engine.py can't be imported here (heavy dependency chain), so this verifies the
real source text plus a behavioural model of the headroom arithmetic.
"""
import pathlib

_SOURCE = (pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py").read_text()


def _scalein_block() -> str:
    start = _SOURCE.index("_si_add_value = _si_live * _si_trade.shares * _si_size_pct")
    return _SOURCE[start:start + 2500]


# ── the cap is consulted at all ──────────────────────────────────────────────

def test_scalein_reads_max_position_pct():
    assert 'cfg.get("max_position_pct", 0.10)' in _scalein_block()


def test_scalein_computes_headroom_against_current_position_value():
    b = _scalein_block()
    assert "_si_current_value = _si_live * _si_trade.shares" in b
    assert "_si_headroom = max(0.0, _si_cap_value - _si_current_value)" in b


def test_headroom_uses_live_equity_not_initial_capital():
    """The entry-side cap is computed against live equity; the scale-in cap must match, or the
    two paths would disagree as the portfolio grows or shrinks."""
    assert "_compute_equity(session, portfolio, live_prices) * cfg.get(\"max_position_pct\"" in _scalein_block()


def test_cap_is_applied_before_the_cash_check():
    """If cash were checked first against the UNTRUNCATED value, a large add could be rejected
    for insufficient cash when its capped size would have fit comfortably."""
    b = _scalein_block()
    assert b.index("_si_headroom") < b.index("portfolio.current_cash >=")


# ── truncate, don't skip ─────────────────────────────────────────────────────

def test_add_is_truncated_to_headroom_not_abandoned():
    b = _scalein_block()
    assert "_si_add_value = _si_headroom" in b


def test_zero_headroom_skips_the_add_entirely():
    """At or over the cap, headroom is 0 and the `> 0` guard must prevent a no-op add."""
    assert "if _si_add_value > 0 and portfolio.current_cash >=" in _scalein_block()


def test_capping_is_logged():
    """A silently shrunk position is confusing; the log makes it explainable."""
    b = _scalein_block()
    assert 'log.info("paper.scale_in_capped"' in b
    assert "requested=" in b and "allowed=" in b


# ── behaviour of the headroom arithmetic ─────────────────────────────────────

def _final_add(equity, cap_pct, current_value, requested):
    """The exact expression the engine applies."""
    headroom = max(0.0, equity * cap_pct - current_value)
    return min(requested, headroom)


def test_add_below_cap_is_untouched():
    # 5% position, 10% cap, wants to add 2% — fully allowed.
    assert _final_add(50_000, 0.10, 2_500, 1_000) == 1_000


def test_add_that_would_breach_cap_is_truncated():
    """The JPM case: at 9% of a 10% cap, a 4% add is cut to the remaining 1%."""
    assert _final_add(50_000, 0.10, 4_500, 2_000) == 500


def test_position_already_at_cap_gets_nothing():
    assert _final_add(50_000, 0.10, 5_000, 1_000) == 0.0


def test_position_already_over_cap_gets_nothing_not_negative():
    """max(0.0, ...) matters — a negative headroom would flip min() and ADD the full request."""
    assert _final_add(50_000, 0.10, 7_000, 1_000) == 0.0


def test_final_position_never_exceeds_the_cap():
    for current in (0, 1_000, 4_900, 5_000, 9_000):
        for requested in (100, 1_000, 50_000):
            final = current + _final_add(50_000, 0.10, current, requested)
            assert final <= max(5_000, current) + 1e-9, (current, requested, final)
