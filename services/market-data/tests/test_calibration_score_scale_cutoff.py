"""Regression test for AUD-CALIBRATION-SCORESCALE.

T232-DL-DUALSCORER-DEBT's 2026-07-17 fix added 3 new scoring layers to _should_enter()
(pre-regime warning, regime-as-score, K-Score +/-1), shifting entry_score's scale for every
trade scored from that date forward. calibrate_entry_weights() persists entry_score verbatim
and previously fit w_score across the FULL closed-trade history with no distinction between
pre- and post-change trades — mixing two incompatible score scales under one coefficient.

paper_portfolio.py can't be imported directly in this test environment (heavy sqlalchemy/
sklearn/DB dependencies not stubbed for a fit this involved) — this is a source-text check
confirming the cutoff filter exists and is wired into the query, matching the same pattern
used elsewhere in this repo's test suite for similarly hard-to-isolate functions (e.g.
test_scheduler_static_names.py, test_fundamentals_empty_fetch_guard.py).
"""
import pathlib

_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "paper_portfolio.py"
)
_SOURCE = _PATH.read_text()


def _calibrate_entry_weights_body() -> str:
    start = _SOURCE.index("def calibrate_entry_weights(")
    end = _SOURCE.index("\ndef ", start + 1)
    return _SOURCE[start:end]


def test_score_scale_cutoff_constant_exists():
    body = _calibrate_entry_weights_body()
    assert "_SCORE_SCALE_CUTOFF" in body
    assert "date(2026, 7, 17)" in body


def test_query_filters_by_the_score_scale_cutoff():
    """The cutoff must actually be applied to the query's WHERE clause, not just declared
    and left unused — a regression here would silently re-mix old and new score scales."""
    body = _calibrate_entry_weights_body()
    where_start = body.index(".where(")
    where_end = body.index(").order_by(", where_start)
    where_clause = body[where_start:where_end]
    assert "PaperTrade.entry_date >= _SCORE_SCALE_CUTOFF" in where_clause
