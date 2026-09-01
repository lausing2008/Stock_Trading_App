"""Regression test for AUD-ALPHABETA-VAREPS.

_compute_alpha_beta()'s information-ratio computation (`te`, tracking error) used a bare
`var_active > 0` gate before dividing by it — the EXACT float-noise-explosion bug class
AUD292-SHARPE-VAREPS already found and fixed in the sibling _portfolio_risk_metrics() function
a few dozen lines above it in the SAME file, never ported to this function. A portfolio that
consistently tracks SPY with a fixed daily offset (plausible for a highly-correlated,
low-turnover book) produces a var_active that is pure floating-point noise (~1e-39, not exactly
0.0) — a bare `> 0` check lets it through and explodes info_ratio toward an enormous,
meaningless value (reproduced directly against a real, non-degenerate 30-day fixture: a
fixed-offset-tracking portfolio's var_active computed to ~6e-39, exploding info_ratio to
~1.02e+17 before this fix).

beta's own separate (and already-present) 1e-10 epsilon is also raised to match _VAR_EPS for
internal consistency within this one function — not itself a reported bug (it already had SOME
guard), but the two thresholds silently diverging within the same function is worth locking
down too.

_compute_alpha_beta() itself is pure math (no DB/session dependency) but paper_portfolio.py as
a WHOLE module can't be imported directly in this test environment (same constraint documented
in test_sharpe_variance_epsilon.py) — extract just this one function's source text and exec()
it in isolation, then test it BEHAVIORALLY with real numeric input.
"""
import math
import pathlib

_PAPER_PORTFOLIO_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "paper_portfolio.py"
_PAPER_PORTFOLIO_SOURCE = _PAPER_PORTFOLIO_PATH.read_text()


class _Row:
    def __init__(self, equity: float, spy_close: float):
        self.equity = equity
        self.spy_close = spy_close


def _extract_compute_alpha_beta():
    func_start = _PAPER_PORTFOLIO_SOURCE.index("def _compute_alpha_beta(")
    func_end = _PAPER_PORTFOLIO_SOURCE.index("\ndef ", func_start + 1)
    func_body = _PAPER_PORTFOLIO_SOURCE[func_start:func_end]

    namespace: dict = {"math": math}
    exec(func_body, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_compute_alpha_beta"]


_compute_alpha_beta = _extract_compute_alpha_beta()


def _rows_from_paired_returns(p_rets: list, s_rets: list, start_equity: float = 100_000.0, start_spy: float = 5000.0):
    """Builds >=20 equity/spy_close-paired rows whose day-over-day returns reproduce the given
    portfolio/SPY daily fractional-return sequences exactly."""
    assert len(p_rets) == len(s_rets)
    equity, spy = start_equity, start_spy
    rows = [_Row(equity, spy)]
    for pr, sr in zip(p_rets, s_rets):
        equity = equity * (1 + pr)
        spy = spy * (1 + sr)
        rows.append(_Row(equity, spy))
    return rows


def test_a_portfolio_tracking_spy_with_a_fixed_daily_offset_does_not_explode_info_ratio():
    """The exact production failure mode: a portfolio whose daily active return (p_ret - s_ret)
    is the SAME fixed offset every single day. Confirmed via direct reproduction that this
    construction yields a real, nonzero-but-sub-epsilon var_active (~6e-39) — not an exact 0.0
    that would pass regardless of the fix — and that the pre-fix code explodes info_ratio to
    ~1.02e+17 for this exact fixture. After the fix, info_ratio must report None (undefined
    tracking error), not an exploded ratio."""
    n = 30
    s_rets = [0.001 * (1 + 0.3 * ((-1) ** i)) for i in range(n)]
    offset = 0.0005
    p_rets = [r + offset for r in s_rets]
    rows = _rows_from_paired_returns(p_rets, s_rets)
    result = _compute_alpha_beta(rows)
    assert result["beta"] is not None  # real, non-degenerate SPY variance — beta IS well-defined
    assert result["info_ratio"] is None, f"expected None (float-noise var_active), got {result['info_ratio']}"


def test_genuine_tracking_error_still_produces_a_real_finite_info_ratio():
    """The fix must not break the NORMAL case — a portfolio whose active return genuinely
    varies day to day (not a fixed offset) must still produce a real, finite info_ratio."""
    n = 30
    s_rets = [0.001 * (1 + 0.4 * ((-1) ** i)) for i in range(n)]
    p_rets = [s_rets[i] + 0.0003 * (1 if i % 3 == 0 else -1) for i in range(n)]
    rows = _rows_from_paired_returns(p_rets, s_rets)
    result = _compute_alpha_beta(rows)
    assert result["beta"] is not None
    assert result["info_ratio"] is not None
    assert abs(result["info_ratio"]) < 1000, f"info_ratio should be a normal-magnitude ratio, got {result['info_ratio']}"


def test_beta_and_alpha_are_unaffected_by_the_info_ratio_epsilon_fix():
    """beta/alpha are computed from a genuinely-varying SPY series in this fixture (var_s is
    real, not float noise) — confirm they still resolve to real, finite values regardless of
    what var_active does."""
    n = 30
    s_rets = [0.001 * (1 + 0.3 * ((-1) ** i)) for i in range(n)]
    offset = 0.0005
    p_rets = [r + offset for r in s_rets]
    rows = _rows_from_paired_returns(p_rets, s_rets)
    result = _compute_alpha_beta(rows)
    assert result["beta"] == 1.0  # p_rets is s_rets plus a CONSTANT offset -> beta of exactly 1
    assert result["alpha"] is not None
    assert result["alpha"] > 0  # a consistently-positive fixed offset above SPY is real positive alpha


def test_fewer_than_20_paired_points_returns_none_before_the_epsilon_path_ever_runs():
    """The pre-existing len(paired) < 20 sample floor is unaffected by, and unrelated to, this
    epsilon fix."""
    rows = _rows_from_paired_returns([0.01, 0.02, -0.01], [0.01, 0.015, -0.005])
    result = _compute_alpha_beta(rows)
    assert result == {"alpha": None, "beta": None, "info_ratio": None}


def test_degenerate_flat_spy_series_returns_none_beta_via_the_raised_epsilon():
    """A SPY series with zero real variance (var_s is exact 0.0 or float-noise-scale) must
    still correctly degrade to beta=None via the (now-1e-9, previously-1e-10) epsilon — proves
    the two thresholds staying in sync didn't accidentally loosen this pre-existing guard."""
    n = 25
    s_rets = [0.001] * n  # exactly flat SPY daily return -> var_s is exact 0.0
    p_rets = [0.001 + 0.0002 * (1 if i % 2 == 0 else -1) for i in range(n)]
    rows = _rows_from_paired_returns(p_rets, s_rets)
    result = _compute_alpha_beta(rows)
    assert result == {"alpha": None, "beta": None, "info_ratio": None}
