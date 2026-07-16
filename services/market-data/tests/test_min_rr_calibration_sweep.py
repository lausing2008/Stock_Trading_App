"""Tests for calibrate_min_rr_ratio()'s _ev_at() sweep logic (paper_portfolio.py).

paper_portfolio.py can't be imported directly in this test environment — sqlalchemy/db are
stubbed as MagicMock() by conftest.py, and this module does real query construction at import
time via decorated route functions. _ev_at() is a small, pure nested function (no DB access
itself — takes already-fetched rows), so its source is extracted directly from the real file
and exec()'d, matching the source-text-extraction pattern already established elsewhere in
this session's test suite (test_backfill_realized_ev.py, test_scheduler_static_names.py) —
this exercises the ACTUAL function under test, not a hand-copied re-implementation that could
silently drift from it.
"""
import pathlib
from types import SimpleNamespace

_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "paper_portfolio.py"
)
_SOURCE = _PATH.read_text()


def _extract_ev_at():
    start = _SOURCE.index("    def _ev_at(threshold, rset):")
    end = _SOURCE.index("\n\n    curve = []", start)
    func_source = _SOURCE[start:end]
    # Dedent (the real source sits inside calibrate_min_rr_ratio(), indented one level)
    lines = func_source.splitlines()
    dedented = "\n".join(line[4:] if line.startswith("    ") else line for line in lines)
    namespace = {"_MIN_RR_MIN_CANDIDATE_N": 15}
    exec(dedented, namespace)  # noqa: S102 — isolated eval of one pure function's real source
    return namespace["_ev_at"]


_ev_at = _extract_ev_at()


def _trade(rr, pnl):
    return SimpleNamespace(rr_ratio_at_entry=rr, pnl=pnl)


def test_below_min_candidate_n_returns_none():
    rows = [_trade(3.0, 100) for _ in range(10)]  # only 10, floor is 15
    ev, n = _ev_at(2.0, rows)
    assert ev is None
    assert n == 10


def test_computes_mean_pnl_of_qualifying_trades_only():
    rows = (
        [_trade(1.0, -50) for _ in range(20)]   # below threshold — excluded
        + [_trade(3.0, 100) for _ in range(15)]  # at/above threshold — included
        + [_trade(2.5, 200) for _ in range(15)]  # at/above threshold — included
    )
    ev, n = _ev_at(2.0, rows)
    assert n == 30
    assert abs(ev - 150.0) < 0.01


def test_threshold_is_inclusive():
    rows = [_trade(2.0, 42) for _ in range(15)]
    ev, n = _ev_at(2.0, rows)
    assert n == 15
    assert ev == 42.0


def test_no_qualifying_trades_at_all_returns_none():
    rows = [_trade(1.0, 100) for _ in range(50)]
    ev, n = _ev_at(5.0, rows)
    assert ev is None
    assert n == 0
