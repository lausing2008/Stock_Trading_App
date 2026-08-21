"""Regression test for AUD292-SHARPE-VAREPS.

_portfolio_risk_metrics()'s Sharpe/Sortino computation used a bare `variance > 0` /
`annualised_vol > 0` / `downside_dev > 0` gate before dividing by each of those values. numpy-
free Python floating-point arithmetic on an all-identical (but nonzero) or all-nonnegative-but-
not-exactly-zero return series produces a variance/downside_dev that is pure float noise
(~1e-17, not exactly 0.0) rather than a genuine "no volatility" 0.0 — it still passes a bare
`> 0` check and produces a near-zero denominator, which explodes the resulting Sharpe/Sortino
ratio toward +-1e7-1e9. This is the EXACT bug class services/strategy-engine/src/backtest/
engine.py's own T237-SE1 fix already found and guarded against with a real epsilon threshold
(_VOL_EPS = 1e-9) in its own, INDEPENDENT Sharpe/Sortino implementation — that fix was never
ported back to this sibling implementation until now.

_portfolio_risk_metrics() itself is pure math (no DB/session dependency at all — takes a plain
list of row-like objects with .equity/.date attributes) but paper_portfolio.py as a WHOLE
module can't be imported directly in this test environment (its module-level `from db.models
import ...` fails against the wholesale-MagicMock `db` stub, which has no real `models`
submodule) — extract just this one function's source text and exec() it in isolation, then
test it BEHAVIORALLY with real numeric input, rather than only checking source text.
"""
import math
import pathlib

_PAPER_PORTFOLIO_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "paper_portfolio.py"
_PAPER_PORTFOLIO_SOURCE = _PAPER_PORTFOLIO_PATH.read_text()


class _Row:
    def __init__(self, equity: float, d):
        self.equity = equity
        self.date = d


def _extract_portfolio_risk_metrics():
    """Extracts _portfolio_risk_metrics()'s real source (plus its two module-level constant
    dependencies, _MIN_SHARPE_DAYS/_MIN_CAGR_DAYS) and exec()s it into an isolated namespace —
    exercises the REAL function body, not a hand-copied reimplementation that could drift."""
    const_start = _PAPER_PORTFOLIO_SOURCE.index("_MIN_SHARPE_DAYS = ")
    const_end = _PAPER_PORTFOLIO_SOURCE.index("\n\n\ndef _portfolio_risk_metrics")
    consts = _PAPER_PORTFOLIO_SOURCE[const_start:const_end]

    func_start = _PAPER_PORTFOLIO_SOURCE.index("def _portfolio_risk_metrics(")
    func_end = _PAPER_PORTFOLIO_SOURCE.index("\ndef ", func_start + 1)
    func_body = _PAPER_PORTFOLIO_SOURCE[func_start:func_end]

    namespace: dict = {"math": math}
    exec(consts + "\n\n\n" + func_body, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_portfolio_risk_metrics"]


_portfolio_risk_metrics = _extract_portfolio_risk_metrics()


def _rows_from_daily_returns(returns: list, start_equity: float = 100_000.0):
    """Builds >= _MIN_SHARPE_DAYS+1 equity-curve rows from a list of daily fractional returns."""
    from datetime import date, timedelta
    rows = []
    equity = start_equity
    d0 = date(2026, 1, 1)
    rows.append(_Row(equity, d0))
    for i, r in enumerate(returns):
        equity = equity * (1 + r)
        rows.append(_Row(equity, d0 + timedelta(days=i + 1)))
    return rows


def test_all_identical_but_nonzero_daily_returns_does_not_explode_sharpe():
    """The exact production failure mode: a strategy whose per-day return is the SAME target
    rate every day but carries genuine sub-epsilon floating-point noise once round-tripped
    through equity construction/reconstruction (verified directly: recomputing
    equity[i]/equity[i-1]-1 from a deliberately-perturbed target-rate sequence still produces a
    real, nonzero variance of ~1e-33 — NOT an exact 0.0, and NOT a coincidence of this specific
    construction; this is exactly the class of value IEEE-754 arithmetic on real, non-uniform
    equity curves can produce). A bare `variance > 0` gate lets this through and explodes the
    resulting sharpe ratio; the epsilon fix must correctly treat it as "no real volatility."

    Confirmed this test actually distinguishes the fix from the bug (not just "passes either
    way" on a degenerate always-exactly-0.0 input): a naive `[0.001] * 24` fixture recomputes to
    an EXACT 0.0 variance regardless of the epsilon fix (both `> 0` and `> 1e-9` correctly
    reject it) — this fixture instead perturbs the target rate by 1e-17 per step specifically so
    the recomputed variance lands inside the (0, 1e-9) float-noise band the fix targets, not at
    exact zero."""
    base_rate = 0.001
    returns = [base_rate + i * 1e-17 for i in range(24)]
    rows = _rows_from_daily_returns(returns)
    result = _portfolio_risk_metrics(rows)
    assert result["insufficient_data"] is False
    # Before the fix: sharpe/sortino would be a wild outlier (order of 1e6+). After the fix,
    # a genuinely-negligible-real-variance series must report None (undefined), not an exploded
    # ratio.
    assert result["sharpe"] is None, f"expected None (float-noise variance), got {result['sharpe']}"
    assert result["sortino"] is None, f"expected None (float-noise variance), got {result['sortino']}"


def test_a_single_float_noise_negative_day_does_not_explode_sortino():
    """A strategy with meaningfully-positive returns on every real day except one, where that
    one day's return is a tiny NEGATIVE float-noise-scale value (not a real, meaningful loss) —
    downside_sq for that single day is nonzero-but-sub-epsilon, so downside_dev lands inside the
    (0, 1e-9) float-noise band the fix targets, while overall variance stays real/large (sharpe
    IS well-defined). Confirmed the fixture actually produces a real, nonzero-but-sub-epsilon
    downside_dev (~3.5e-16), not a degenerate exact 0.0 that would pass this test regardless of
    whether the epsilon fix exists at all — a naive all-nonnegative fixture (e.g. every return
    strictly >= 0) recomputes downside_sq as exactly {0.0} in every case, since `min(r, 0.0)` on
    a strictly-positive float involves no subtraction and produces no noise at all."""
    returns = [0.001, 0.003, 0.0005, 0.002, 0.0015] * 4 + [0.001, 0.003, -1e-16, 0.002, 0.0015]
    rows = _rows_from_daily_returns(returns)
    result = _portfolio_risk_metrics(rows)
    assert result["insufficient_data"] is False
    assert result["sharpe"] is not None  # real variance exists — sharpe IS well-defined here
    assert result["sortino"] is None, f"expected None (float-noise downside_dev), got {result['sortino']}"


def test_genuine_volatility_still_produces_a_real_finite_sharpe():
    """The fix must not break the NORMAL case — real, meaningfully-varying daily returns must
    still produce a real, finite (not None, not exploded) Sharpe/Sortino."""
    returns = [0.02, -0.01, 0.015, -0.008, 0.03, -0.02, 0.01, -0.005, 0.025, -0.015] * 3
    rows = _rows_from_daily_returns(returns)
    result = _portfolio_risk_metrics(rows)
    assert result["insufficient_data"] is False
    assert result["sharpe"] is not None
    assert result["sortino"] is not None
    assert abs(result["sharpe"]) < 100, f"sharpe should be a normal-magnitude ratio, got {result['sharpe']}"
    assert abs(result["sortino"]) < 100, f"sortino should be a normal-magnitude ratio, got {result['sortino']}"


def test_max_drawdown_is_unaffected_by_the_epsilon_fix():
    """Max drawdown is computed independently of the variance/std_r path this fix touches —
    confirm it's still correct and unaffected."""
    returns = [0.05, 0.05, -0.20, 0.05, 0.05, -0.20, 0.10] * 3
    rows = _rows_from_daily_returns(returns)
    result = _portfolio_risk_metrics(rows)
    assert result["max_drawdown_pct"] is not None
    assert result["max_drawdown_pct"] > 0


def test_insufficient_days_returns_none_for_sharpe_before_the_epsilon_path_ever_runs():
    """Fewer than _MIN_SHARPE_DAYS days must short-circuit to None — the pre-existing sample
    floor is unaffected by, and unrelated to, this epsilon fix."""
    rows = _rows_from_daily_returns([0.01, 0.02, -0.01])
    result = _portfolio_risk_metrics(rows)
    assert result["insufficient_data"] is True
    assert result["sharpe"] is None
    assert result["sortino"] is None
