"""Tests for AUD-MINRR-STYLEBLIND-STUCKFILE's fix to calibrate_min_rr_ratio() (paper_portfolio.py):
once the pooled candidate threshold converges on the value already live, the "candidate must
beat baseline" check compares a number against itself and can never satisfy a strict ">",
rejecting forever. Before this fix that branch returned immediately with nothing written —
which meant by_market/by_style (AUD-MINRR-MARKETBLIND / AUD-MINRR-STYLEBLIND) could never get
a fresh run once the pooled number stabilized, confirmed live 2026-09-19 (the override file had
gone 19 days without a write despite the job running on schedule). Now it falls through and
sets `effective_threshold` to the existing baseline instead of aborting, so by_market/by_style
still refresh off the current full dataset.

paper_portfolio.py can't be imported directly in this test environment — same source-text-
extraction pattern as the sibling calibration tests in this directory.
"""
import pathlib

_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "paper_portfolio.py"
)
_SOURCE = _PATH.read_text()


class _NullLog:
    def info(self, *a, **kw):
        pass


def _extract_decision_block():
    start = _SOURCE.index("    pooled_updated = candidate_ev is not None")
    end = _SOURCE.index("\n\n    # AUD-MINRR-MARKETBLIND", start)
    func_source = _SOURCE[start:end]
    lines = func_source.splitlines()
    dedented = "\n".join(line[4:] if line.startswith("    ") else line for line in lines)
    return dedented


def _run(best_threshold, baseline_threshold, candidate_ev, baseline_ev, rows_n=123, val_n=37):
    namespace = {
        "best_threshold": best_threshold,
        "baseline_threshold": baseline_threshold,
        "candidate_ev": candidate_ev,
        "baseline_ev": baseline_ev,
        "rows": list(range(rows_n)),
        "val_rows": list(range(val_n)),
        "log": _NullLog(),
    }
    exec(_extract_decision_block(), namespace)  # noqa: S102 — isolated eval of real source
    return namespace["pooled_updated"], namespace["effective_threshold"]


def test_candidate_strictly_beating_baseline_updates_the_pooled_threshold():
    pooled_updated, effective = _run(best_threshold=2.5, baseline_threshold=2.25, candidate_ev=50.0, baseline_ev=10.0)
    assert pooled_updated is True
    assert effective == 2.5


def test_candidate_tied_with_baseline_the_exact_reported_stuck_file_case():
    """The exact live incident: candidate and baseline resolve to the SAME threshold (2.25),
    so their validation EV is identical (-27.39 == -27.39) — must NOT be treated as an update,
    but must also NOT raise or behave as an unhandled tie; effective_threshold must fall back
    to the existing baseline so downstream by_market/by_style still get a value to work with."""
    pooled_updated, effective = _run(best_threshold=2.25, baseline_threshold=2.25, candidate_ev=-27.39, baseline_ev=-27.39)
    assert pooled_updated is False
    assert effective == 2.25


def test_candidate_worse_than_baseline_keeps_the_baseline():
    pooled_updated, effective = _run(best_threshold=1.25, baseline_threshold=2.25, candidate_ev=-91.09, baseline_ev=10.0)
    assert pooled_updated is False
    assert effective == 2.25


def test_missing_candidate_ev_keeps_the_baseline_not_a_crash():
    """A None EV (too few qualifying trades for the CANDIDATE threshold specifically) must be
    treated as 'did not beat baseline', not compared numerically against a real float."""
    pooled_updated, effective = _run(best_threshold=4.0, baseline_threshold=2.25, candidate_ev=None, baseline_ev=10.0)
    assert pooled_updated is False
    assert effective == 2.25


def test_missing_baseline_ev_keeps_the_baseline_not_a_crash():
    pooled_updated, effective = _run(best_threshold=2.5, baseline_threshold=2.25, candidate_ev=50.0, baseline_ev=None)
    assert pooled_updated is False
    assert effective == 2.25
