"""Tests for AUD261-WALKFORWARD-COMPOUNDS-CROSSSECTIONAL (Deep Audit #1, Tier 261).

walkforward_backtest() compounds each window's MEAN return across all concurrent BUY signals
in that window as though it were a single sequential position's return, then derives
sharpe/max_drawdown/total_return_pct from the resulting synthetic curve — cross-sectional
averaging structurally suppresses variance relative to any real tradeable path, which
overstates Sharpe and understates drawdown. The frontend surfaced this as "Sharpe >= 1.0 —
signals generating real out-of-sample alpha", a claim the underlying math does not support.

Fix: kept the compounding math (still a genuine, internally-consistent summary statistic of
the per-window mean-return series) but added an explicit, structured "cross_sectional_caveat"
field to the response so no consumer can present it as an executable-strategy backtest without
also seeing the disclosure. Separately, n_correct's bare `> 0` win test (no cost hurdle) is
fixed to use the canonical _OUTCOME_WIN_HURDLE_PCT convention, matching AUD261-BARE-GT-ZERO-
NO-HURDLE's sibling fix for factor_exposure()/filter_audit() in the same file.

outcomes.py can't be imported directly (conftest.py stubs the `common` package wholesale) —
source-text regression checks, matching test_bare_gt_zero_hurdle_fix.py's established
convention for functions of this shape in this same file.
"""
import pathlib

_OUTCOMES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "analytics.py"
_OUTCOMES_SOURCE = _OUTCOMES_PATH.read_text()


def _walkforward_backtest_body() -> str:
    start = _OUTCOMES_SOURCE.index("def walkforward_backtest(")
    end = _OUTCOMES_SOURCE.index("\ndef _wf_empty(", start)
    return _OUTCOMES_SOURCE[start:end]


def _wf_empty_body() -> str:
    start = _OUTCOMES_SOURCE.index("def _wf_empty(")
    end = _OUTCOMES_SOURCE.index("\ndef _wf_benchmark(", start)
    return _OUTCOMES_SOURCE[start:end]


def test_n_correct_uses_the_cost_hurdle_not_a_bare_gt_zero():
    body = _walkforward_backtest_body()
    assert "r > _OUTCOME_WIN_HURDLE_PCT * 100" in body
    # Regression guard against the exact pre-fix literal reappearing.
    assert "r > 0" not in body.split("n_correct = sum")[1].split("\n")[0]


def test_response_includes_the_cross_sectional_caveat_field():
    body = _walkforward_backtest_body()
    assert '"cross_sectional_caveat": (' in body


def test_caveat_text_names_the_specific_fields_it_qualifies():
    """The disclosure must be concrete enough to actually inform a reader — not a vague
    generic warning — naming the exact fields it caveats and the mechanism (compounding a
    cross-sectional mean) that causes the overstatement."""
    body = _walkforward_backtest_body()
    caveat_start = body.index('"cross_sectional_caveat": (')
    caveat_end = body.index(")", caveat_start)
    caveat_text = body[caveat_start:caveat_end]
    assert "sharpe" in caveat_text
    assert "total_return_pct" in caveat_text
    assert "max_drawdown" in caveat_text
    assert "MEAN return" in caveat_text


def test_wf_empty_also_returns_the_caveat_field_for_response_shape_consistency():
    """A caller that always reads report['cross_sectional_caveat'] must not KeyError on the
    empty-result path (no windows/no evaluated signals)."""
    body = _wf_empty_body()
    assert '"cross_sectional_caveat": None' in body


def test_equity_compounding_logic_is_still_present_not_deleted():
    """This fix is a disclosure fix, not a removal — the underlying compounding math (still a
    genuine, internally-consistent summary statistic) must remain; only the CLAIM about what
    it represents changes (enforced on the frontend side, see signal-accuracy.tsx)."""
    body = _walkforward_backtest_body()
    assert 'equity *= (1 + w["avg_return_pct"] / 100)' in body
    assert "sharpe = float(rets.mean() / rets.std()" in body
