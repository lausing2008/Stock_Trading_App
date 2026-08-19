"""Tests for IF-11: GET /paper-portfolio/compare-metrics — side-by-side risk/return metrics
across every active portfolio, genuinely distinct from the pre-existing GET /paper-portfolio/
compare (raw/indexed equity CURVES for an overlay chart, zero comparative metrics).

paper_portfolio.py can't be imported directly in this test environment (its import chain needs
the real conftest.py stub setup only pytest's own collection provides for db/db.models) —
covered by source-text regression checks, matching this repo's established pattern for this
exact file (e.g. the IF-07/IF-10 Brinson tests in this same directory).
"""
import pathlib as _pathlib

_PAPER_PORTFOLIO_PATH = _pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "paper_portfolio.py"
_PAPER_PORTFOLIO_SOURCE = _PAPER_PORTFOLIO_PATH.read_text()


def _func_body(func_name: str) -> str:
    start = _PAPER_PORTFOLIO_SOURCE.index(f"def {func_name}(")
    end = _PAPER_PORTFOLIO_SOURCE.index("\ndef ", start + 1)
    return _PAPER_PORTFOLIO_SOURCE[start:end]


def test_the_route_is_registered_at_the_documented_path():
    assert '@router.get("/compare-metrics")' in _PAPER_PORTFOLIO_SOURCE


def test_the_route_only_iterates_active_portfolios_not_every_portfolio_ever_created():
    body = _func_body("compare_portfolio_metrics")
    assert "PaperPortfolio.is_active.is_(True)" in body


def test_the_route_reuses_build_portfolio_summary_not_a_second_derivation():
    """The whole point of this endpoint is to avoid a second, independently-drifting
    reimplementation of get_summary()'s own win-rate/Sharpe/alpha-beta/benchmark-outperformance
    math — confirm it genuinely calls the shared helper, not a hand-rolled duplicate."""
    body = _func_body("compare_portfolio_metrics")
    assert "_build_portfolio_summary(session, p)" in body
    # And confirm it's a list comprehension over ALL fetched portfolios, not just the first one.
    assert "for p in portfolios" in body


def test_get_summary_itself_delegates_to_the_shared_helper_not_a_duplicate_computation():
    """A regression guard against the extraction silently reverting to two independent
    computations (get_summary()'s own inline body PLUS the new shared helper) — get_summary()
    must be a thin wrapper, not a parallel reimplementation that could drift from the one
    compare-metrics also calls. Isolates just the function's own real body (up to the blank
    line before the next section's comment block), since _func_body()'s "next \\ndef " search
    would otherwise also capture the trailing module-level comment block that precedes
    compare_portfolio_metrics()'s own def line."""
    start = _PAPER_PORTFOLIO_SOURCE.index("def get_summary(")
    end = _PAPER_PORTFOLIO_SOURCE.index("\n\n\n", start)
    own_body = _PAPER_PORTFOLIO_SOURCE[start:end]
    assert "_get_portfolio(session, portfolio_id)" in own_body
    assert "_build_portfolio_summary(session, p)" in own_body
    # A thin wrapper should be short — if this balloons back up, the extraction was undone.
    assert len(own_body.splitlines()) < 10


def test_build_portfolio_summary_reports_every_metric_a_real_comparison_dashboard_needs():
    """Confirm the shared helper's response actually carries the full set of comparative
    metrics (not just equity/return) — win rate, Sharpe/Sortino/CAGR/max-drawdown/Calmar,
    alpha/beta, and benchmark outperformance — since these are exactly what IF-11's own
    tracker recommendation named as the "step 1" comparison dashboard's required content."""
    body = _func_body("_build_portfolio_summary")
    for key in (
        '"win_rate_pct"', '"profit_factor"', '"sharpe"', '"sortino"', '"cagr_pct"',
        '"max_drawdown_pct"', '"calmar"', '"alpha"', '"beta"',
        '"outperformance_vs_spy"', '"outperformance_vs_qqq"', '"outperformance_vs_hsi"',
    ):
        assert key in body, f"missing {key} from the shared portfolio-summary computation"


def test_build_portfolio_summary_never_reads_portfolio_id_from_a_query_param():
    """_build_portfolio_summary() takes an already-resolved PaperPortfolio object (p), not a
    portfolio_id to look up itself — confirming it stays a pure per-portfolio computation
    reusable across a whole list, rather than re-deriving its own portfolio from a request.
    (PaperTrade.portfolio_id, the FK column filter, is expected and legitimate inside the body —
    this checks the function's own SIGNATURE never accepts a portfolio_id parameter, and never
    calls _get_portfolio(), which is what would look one up from a request.)"""
    assert "def _build_portfolio_summary(session: Session, p: PaperPortfolio) -> dict:" in _PAPER_PORTFOLIO_SOURCE
    body = _func_body("_build_portfolio_summary")
    assert "_get_portfolio(" not in body


def test_no_automatic_reallocation_logic_exists_anywhere_in_this_route():
    """IF-11's own tracker recommendation explicitly warns against automatic capital
    reallocation without a real, validated, opt-in decision (chasing recent outperformance is a
    well-known way to buy high and sell low) — confirm this endpoint is read-only and never
    writes to PaperPortfolio.initial_capital or any allocation field."""
    body = _func_body("compare_portfolio_metrics")
    assert "session.commit()" not in body
    assert "initial_capital =" not in body
    assert ".add(" not in body
