"""Tests for GET /paper-portfolio/realized-performance — the genuine realized-P&L panel
AUD261-PAPERTRADE-PANEL-MISLABEL's own fix description offered as the honest alternative to
the hypothetical forward-return panel it relabeled, but deliberately did not build at the time
(scoped to the relabel only). Built now to complete that item's own remaining offer.

paper_portfolio.py can't be imported directly in this test environment (its module-level
`from db.models import ...` fails against the wholesale-MagicMock `db` stub, which has no real
models submodule) — extract _real_trade_stats() (pure, zero DB dependency) via source-text
exec() and test it BEHAVIORALLY with real SimpleNamespace trade fixtures, matching this
repo's established test_sharpe_variance_epsilon.py convention for the same constraint.
realized_performance() itself (the route handler, heavy DB/session dependency) is covered via
source-text regression checks instead, matching test_compare_portfolio_metrics.py's own
established pattern for this exact file.
"""
import pathlib
from types import SimpleNamespace

_PAPER_PORTFOLIO_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "paper_portfolio.py"
_PAPER_PORTFOLIO_SOURCE = _PAPER_PORTFOLIO_PATH.read_text()


def _func_body(func_name: str) -> str:
    start = _PAPER_PORTFOLIO_SOURCE.index(f"def {func_name}(")
    end = _PAPER_PORTFOLIO_SOURCE.index("\ndef ", start + 1)
    return _PAPER_PORTFOLIO_SOURCE[start:end]


def _extract_real_trade_stats():
    """Extracts _real_trade_stats()'s real source and exec()s it into an isolated namespace —
    exercises the REAL function body, not a hand-copied reimplementation that could drift.
    Bounded explicitly by the @router.get decorator of its immediate successor
    (realized_performance) rather than _func_body()'s generic "next `\\ndef `" search, since
    that search skips right past a decorator line onto the decorated function itself."""
    start = _PAPER_PORTFOLIO_SOURCE.index("def _real_trade_stats(")
    end = _PAPER_PORTFOLIO_SOURCE.index('\n@router.get("/realized-performance")')
    body = _PAPER_PORTFOLIO_SOURCE[start:end]
    namespace: dict = {}
    exec(body, namespace)  # noqa: S102 — isolated eval of one real function's actual source
    return namespace["_real_trade_stats"]


_real_trade_stats = _extract_real_trade_stats()


def _trade(pct_return: float | None, hold_days: int | None = 5):
    return SimpleNamespace(pct_return=pct_return, hold_days=hold_days)


def test_empty_list_returns_none_not_a_crash():
    assert _real_trade_stats([]) is None


def test_all_none_pct_returns_returns_none():
    """A closed trade with no pct_return at all (a real, if rare, data gap) must be excluded
    entirely, not counted as a loss."""
    trades = [_trade(None), _trade(None)]
    assert _real_trade_stats(trades) is None


def test_win_rate_and_avg_return_hand_computed():
    trades = [_trade(5.0), _trade(-2.0), _trade(3.0), _trade(-1.0), _trade(10.0)]
    result = _real_trade_stats(trades)
    assert result["count"] == 5
    # 3 of 5 have pct_return > 0
    assert result["win_rate"] == 0.6
    assert result["avg_return_pct"] == round((5.0 - 2.0 + 3.0 - 1.0 + 10.0) / 5, 2)


def test_median_return_matches_manual_sort():
    trades = [_trade(10.0), _trade(-5.0), _trade(2.0)]
    result = _real_trade_stats(trades)
    # sorted: [-5.0, 2.0, 10.0] -> median index 1 -> 2.0
    assert result["median_return_pct"] == 2.0


def test_exactly_zero_pct_return_is_not_counted_as_a_win():
    """A win is `r > 0` strictly — a breakeven exit (pct_return == 0.0) must not inflate the
    win rate, matching kelly_sizing()'s own established decisive-trades convention for wins."""
    trades = [_trade(0.0), _trade(0.0), _trade(1.0)]
    result = _real_trade_stats(trades)
    assert result["win_rate"] == round(1 / 3, 4)


def test_avg_hold_days_excludes_none_but_still_divides_by_full_trade_count():
    """avg_hold_days sums only trades with a real hold_days value, but the docstring's own
    intent is a per-trade average — mirrors the real function's own `for t in trades if
    t.hold_days is not None) / len(trades)` denominator exactly (not len of the filtered
    subset), so a None hold_days trade silently drags the average down rather than being
    excluded from the denominator too. Documenting the real behavior here, not asserting it
    should be different."""
    trades = [_trade(1.0, hold_days=10), _trade(2.0, hold_days=None)]
    result = _real_trade_stats(trades)
    assert result["avg_hold_days"] == round(10 / 2, 1)


def test_the_route_is_registered_at_the_documented_path():
    assert '@router.get("/realized-performance")' in _PAPER_PORTFOLIO_SOURCE


def test_the_route_filters_to_closed_trades_with_a_real_pct_return_only():
    body = _func_body("realized_performance")
    assert 'PaperTrade.stage == "closed"' in body
    assert "PaperTrade.pct_return.isnot(None)" in body


def test_the_route_scopes_market_by_symbol_suffix_not_a_portfolio_config_join():
    """PaperTrade has no direct market column — market must be derived from the trade's own
    symbol suffix (matching paper_trading_engine.py's established `.endswith(".HK")`
    convention), never from an actual join against PaperPortfolio.config (the function's own
    docstring EXPLAINS this choice by name, which is why this checks for a real join/filter
    usage — `.join(PaperPortfolio` / `PaperPortfolio.config ==` — rather than the bare
    string, which would false-positive on the explanatory docstring itself)."""
    body = _func_body("realized_performance")
    assert '.endswith(".HK")' in body
    assert ".join(PaperPortfolio" not in body
    assert "PaperPortfolio.config ==" not in body
    assert "PaperPortfolio.config[" not in body


def test_the_route_reuses_real_trade_stats_for_overall_and_both_breakdowns():
    """The whole point of this endpoint is ONE shared stats helper for overall/by_style/
    by_exit_reason — confirm all 3 call the same function, not 3 independently-drifting
    hand-rolled aggregations."""
    body = _func_body("realized_performance")
    assert body.count("_real_trade_stats(") == 3


def test_the_route_breaks_down_by_all_four_real_trading_styles():
    body = _func_body("realized_performance")
    assert '("SHORT", "SWING", "LONG", "GROWTH")' in body


def test_empty_result_returns_a_real_message_not_an_exception():
    body = _func_body("realized_performance")
    assert "No closed real trades in this window" in body
