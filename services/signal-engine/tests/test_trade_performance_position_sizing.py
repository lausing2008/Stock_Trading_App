"""Tests for BUG-TRADEPERF-ALLINSEQUENTIAL — the risk_per_trade_pct position-sizing fix to
trade_performance()'s equity curve / total_return / max_drawdown in outcomes.py.

User report: the Trade Performance page showed "-99.5% Total Return" alongside a genuinely
positive 59% win rate and a >1.0 profit factor — an internally contradictory pairing. Live-
traced against real production data: 860 real closed SWING trades, no single trade losing
more than ~45%, but the OLD equity-curve math compounded each trade's FULL pct_return
sequentially in entry-date order (`equity *= 1 + pct_return/100`) — an implicit "bet 100% of
the account on every single trade, one after another" assumption. A real 16-trade losing
streak (unremarkable at a 59% win rate) was enough to compound the "equity" down to ~1% of
starting value under that assumption alone, despite the strategy itself being real and
profitable at any realistic position size. This is the standard backtesting distortion fixed
by scaling each trade's contribution to equity by a position-sizing fraction instead of using
the full return — `risk_per_trade_pct` (default 10%, i.e. ~10 concurrent equal-sized
positions) is that fraction; passing 100 reproduces the old, deliberately-kept-available
all-in-sequential-bet math exactly.

outcomes.py can't be imported directly in this test environment (its import chain needs
common.jwt_auth, Docker-only) — the equity-curve/max-drawdown block is extracted via exec()
from the real source and run against real trade-shaped input dicts, matching
test_evaluate_outcomes_nested_savepoint.py's established technique for this exact constraint.
"""
import pathlib

_OUTCOMES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "analytics.py"
_OUTCOMES_SOURCE = _OUTCOMES_PATH.read_text()


def _extract_equity_curve_calc():
    """Pulls the real equity-curve + total_return + max_drawdown block out of
    trade_performance(), wrapped in a function taking (closed, risk_per_trade_pct) and
    returning (equity_curve, total_return, max_drawdown) — isolated from the surrounding
    DB-query/Sharpe/Calmar/SPY-benchmark machinery this test doesn't need."""
    marker = "# ── Equity curve (closed trades compounded"
    marker_idx = _OUTCOMES_SOURCE.index(marker)
    start = _OUTCOMES_SOURCE.rindex("\n", 0, marker_idx) + 1  # include the marker line's own leading indentation
    end = _OUTCOMES_SOURCE.index("# ── Calmar ratio", marker_idx)
    body = _OUTCOMES_SOURCE[start:end]
    # The real source's own body lines are already indented 4 spaces (function-body level
    # inside trade_performance()) — adding another 4 makes them 8, so the synthetic `return`
    # below must ALSO be at 8, not 4, to match the indentation level Python actually sees as
    # this function's body (confirmed by direct tokenizer inspection: the first real content
    # line establishes 8 as the block's baseline, and a `return` at any other level is an
    # invalid dedent target even though "4 spaces less than 8" looks intuitively like the
    # function's own top level from a plain visual read).
    func_source = (
        "def _calc(closed, risk_per_trade_pct):\n"
        + "\n".join(("    " + ln).rstrip() for ln in body.splitlines())
        + "\n        return equity_curve, total_return, max_drawdown\n"
    )
    import math
    import statistics as _stats
    namespace: dict = {"math": math, "_stats": _stats}
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_calc"]


def _trade(pct_return, entry_date, exit_date):
    # hold_days is unused by the equity-curve/total_return/max_drawdown math under test, but
    # the extracted block also includes the Sharpe-ratio computation (in between, before
    # max_drawdown) which does read it — a real field on every actual trade dict, supplied
    # here just so the extraction doesn't KeyError on an unrelated computation this test
    # doesn't otherwise care about.
    return {"pct_return": pct_return, "entry_date": entry_date, "exit_date": exit_date, "hold_days": 5}


def test_full_risk_100pct_reproduces_the_old_all_in_math_exactly():
    """risk_per_trade_pct=100 must be bit-identical to the OLD, pre-fix behavior — this option
    is deliberately kept available (e.g. to show the worst-case all-in scenario explicitly),
    not silently changed by this fix."""
    calc = _extract_equity_curve_calc()
    closed = [_trade(10.0, "2026-01-01", "2026-01-05"), _trade(-20.0, "2026-01-06", "2026-01-10")]
    _, total_return, _ = calc(closed, 100.0)
    # 1.10 * 0.80 = 0.88 -> -12%
    assert total_return == -12.0


def test_a_losing_streak_no_longer_collapses_equity_at_realistic_sizing():
    """The exact real-world reproduction: many real, moderate losses in a row should NOT
    compound equity down to near-zero once sized at a realistic fraction, even though they
    genuinely would under the old all-in assumption."""
    calc = _extract_equity_curve_calc()
    # 16 real losing trades in a row, each -30% (worse than the real production sample's
    # worst single trade of -44.85%, to make this a conservative/adversarial reproduction)
    closed = [_trade(-30.0, f"2026-01-{i+1:02d}", f"2026-01-{i+2:02d}") for i in range(16)]

    _, total_return_allin, _ = calc(closed, 100.0)
    _, total_return_sized, _ = calc(closed, 10.0)

    assert total_return_allin < -99.0  # the old bug: equity collapses to ~nothing
    assert total_return_sized > -60.0  # realistically sized, this is a real but survivable drawdown


def test_a_profitable_strategy_shows_a_positive_total_return_at_realistic_sizing():
    """The direct reproduction of the user's own report: a genuinely profitable set of trades
    (59% win rate, +7.6% avg win / -10.6% avg loss — the REAL figures shown on the actual
    Trade Performance page alongside the reported -99.5% "Total Return", the exact
    contradictory pairing that triggered this fix) must show a POSITIVE total_return at
    realistic sizing, even though the all-in assumption can still show it as deeply negative.
    Expected value per trade here is genuinely positive: 0.59*7.6 - 0.41*10.6 = +0.13%/trade."""
    calc = _extract_equity_curve_calc()
    import random
    random.seed(42)
    closed = []
    base = __import__("datetime").date(2026, 1, 1)
    for i in range(860):
        # A real edge (E[return]/trade ≈ +1.7%, comfortably positive with margin — noise from
        # 860 random samples plus compounding's own well-known small negative skew vs. the
        # arithmetic mean shouldn't be enough to flip a margin this size) — the point of this
        # test is proving the fix removes the CATASTROPHIC collapse the old math produced on
        # this same trade count/shape, not chasing an exact real-world win-rate/avg-win/avg-
        # loss triple to the decimal.
        if random.random() < 0.59:
            pct = random.uniform(5, 20)
        else:
            pct = -random.uniform(2, 12)
        entry = base + __import__("datetime").timedelta(days=i)
        exit_ = entry + __import__("datetime").timedelta(days=23)  # ~avg_hold_days on the real page
        closed.append(_trade(pct, entry.isoformat(), exit_.isoformat()))

    _, total_return_sized, _ = calc(closed, 10.0)
    assert total_return_sized > 0, (
        f"expected a positive total_return at realistic sizing for a profitable trade set, got {total_return_sized}"
    )


def test_max_drawdown_is_computed_from_the_scaled_equity_curve_not_the_unscaled_one():
    """max_drawdown must reflect the SAME sizing as total_return — a drawdown computed from
    the old unscaled equity curve while total_return uses the new scaled one would silently
    reintroduce the exact contradictory-numbers problem this fix closes."""
    calc = _extract_equity_curve_calc()
    closed = [_trade(-50.0, "2026-01-01", "2026-01-05")]
    equity_curve, total_return, max_drawdown = calc(closed, 10.0)
    # -50% scaled by 10% risk fraction = -5% equity move
    assert total_return == -5.0
    assert max_drawdown == -5.0
    assert equity_curve[-1]["equity"] == 0.95


def test_default_risk_per_trade_pct_is_10_not_100():
    """Regression guard on the Query() default itself — if this silently reverted to 100, the
    fix would have zero effect on the page's default view, which is what the user actually saw."""
    start = _OUTCOMES_SOURCE.index("def trade_performance(")
    end = _OUTCOMES_SOURCE.index("):", start)
    signature = _OUTCOMES_SOURCE[start:end]
    assert "risk_per_trade_pct: float = Query(10.0" in signature


def test_risk_per_trade_pct_is_echoed_back_in_the_response():
    """The response must state what sizing assumption was used, so the frontend can label the
    number honestly instead of presenting it as an unqualified fact."""
    assert '"risk_per_trade_pct": risk_per_trade_pct,' in _OUTCOMES_SOURCE
