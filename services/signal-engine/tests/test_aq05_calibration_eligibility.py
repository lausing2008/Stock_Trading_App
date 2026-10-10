"""AQ05 — the AI confidence calibration pooled ineligible and clustered outcomes.

MEASURED IN PRODUCTION (2026-10-10): of 21,520 resolved outcomes in the trailing 180 days,
**17 carry `entry_date = signal_date`** — entered on the signal's own day, under an evaluator
whose convention is entry at the NEXT session's close. They are few, and the rates barely move
(GROWTH/BUY 0.4004 -> 0.3998), but a calibration that silently includes them is not measuring
the convention it claims.

Three further problems, only the first of which is about size:
  * the only floor was 30 rows — thirty outcomes from two market days describe those two days;
  * the lookup drops from a market bucket to a pooled one and returns the identical shape;
  * outcomes from different model generations are pooled, which this CANNOT repair because no
    version is stored — so it is published as a standing limitation.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from api.signal_outcome_eligibility import (  # noqa: E402
    classify, partition, publication_status, ELIGIBLE, ENTERED_SAME_DAY,
    ENTERED_BEFORE_SIGNAL, NO_ENTRY_DATE, EXCLUDED, EXCLUSION_REASON,
    STANDING_LIMITATIONS, MIN_ELIGIBLE_OUTCOMES, MIN_DISTINCT_DATES)

SIG = date(2026, 1, 5)


# ---- timing ------------------------------------------------------------------------------------

def test_a_same_day_entry_is_not_eligible_under_a_next_session_convention():
    """THE MEASURED CASE: 17 production rows."""
    assert classify(SIG, SIG) == ENTERED_SAME_DAY
    assert "the evaluator's convention is entry at the next session" in \
        EXCLUSION_REASON[ENTERED_SAME_DAY]


def test_an_entry_before_the_signal_cannot_be_a_trade_taken_on_it():
    assert classify(SIG, SIG - timedelta(days=1)) == ENTERED_BEFORE_SIGNAL


def test_a_next_session_entry_is_eligible():
    assert classify(SIG, SIG + timedelta(days=1)) == ELIGIBLE


def test_a_missing_entry_date_cannot_establish_the_convention():
    assert classify(SIG, None) == NO_ENTRY_DATE


def test_every_exclusion_is_explained_in_words():
    for reason in EXCLUDED:
        assert reason in EXCLUSION_REASON and len(EXCLUSION_REASON[reason]) > 30


def test_the_partition_reports_what_it_removed():
    rows = [{"signal_date": SIG, "entry_date": SIG + timedelta(days=1)},
            {"signal_date": SIG, "entry_date": SIG},
            {"signal_date": SIG, "entry_date": None}]
    got = partition(rows)
    assert got["original_count"] == 3 and got["eligible_count"] == 1
    assert got["excluded_counts"] == {ENTERED_SAME_DAY: 1, NO_ENTRY_DATE: 1}
    assert set(got["exclusion_reasons"]) == {ENTERED_SAME_DAY, NO_ENTRY_DATE}


# ---- clustering --------------------------------------------------------------------------------

def _rows(n, dates):
    return [{"is_correct": True, "signal_date": dates[i % len(dates)],
             "entry_date": dates[i % len(dates)] + timedelta(days=1)} for i in range(n)]


def test_a_count_floor_alone_cannot_see_clustering():
    """Thirty outcomes from two market days describe those two days — the same correction the
    options flow calibration already carries."""
    got = publication_status(_rows(40, [SIG, SIG + timedelta(days=1)]))
    assert got["status"] == "clustered_dates"
    assert got["distinct_dates"] == 2
    assert "describes those days, not the signal" in got["missing_requirement"]


def test_too_few_outcomes_is_its_own_status():
    got = publication_status(_rows(5, [SIG + timedelta(days=i) for i in range(5)]))
    assert got["status"] == "insufficient_eligible_history"
    assert str(MIN_ELIGIBLE_OUTCOMES) in got["missing_requirement"]


def test_a_broad_cohort_is_measured():
    got = publication_status(_rows(60, [SIG + timedelta(days=i) for i in range(10)]))
    assert got["status"] == "measured" and got["missing_requirement"] is None
    assert got["distinct_dates"] == 10
    assert got["required_distinct_dates"] == MIN_DISTINCT_DATES


def test_the_standing_limitations_travel_with_every_status():
    got = publication_status(_rows(60, [SIG + timedelta(days=i) for i in range(10)]))
    joined = " ".join(got["limitations"])
    assert "no model or policy version" in joined, "version pooling must be disclosed"
    assert "not a calibrated probability" in joined
    assert len(STANDING_LIMITATIONS) >= 2


# ---- the calibration consumes it ---------------------------------------------------------------

def _shared_src():
    return (Path(__file__).resolve().parents[1] / "src/api/signals_shared.py").read_text()


def _fn(name):
    import ast
    src = _shared_src()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.unparse(fn)


def test_the_builder_filters_on_timing_eligibility():
    """STRUCTURAL, not a substring. The first version asserted `_elig_classify` appeared in the
    body — and it does, in the IMPORT line — so deleting the filtering comprehension left the
    test passing. A sabotage run caught that."""
    import ast
    src = _shared_src()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_build_confidence_calibration")
    # A comprehension that actually CALLS the classifier and rebinds `rows`.
    filters = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "rows" for t in n.targets)
        and isinstance(n.value, ast.ListComp)
        and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                and c.func.id == "_elig_classify" for c in ast.walk(n.value))
    ]
    assert filters, "the ineligible rows must actually be removed, not merely counted"
    assert "entry_date" in ast.unparse(fn), "the timing columns must be selected at all"


def test_the_builder_reports_its_exclusions():
    body = _fn("_build_confidence_calibration")
    for key in ("'original_count'", "'eligible_count'", "'excluded_total'",
                "'excluded_by_reason'"):
        assert key in body, f"{key} must travel with the calibration"
    assert "'limitations'" in body


def test_every_bucket_carries_its_scope_and_status():
    body = _fn("_bucket_entry")
    for key in ("'scope'", "'status'", "'distinct_dates'", "'missing_requirement'"):
        assert key in body
    assert "publication_status" in body, "the shared floors, not a local copy"


def test_an_unpublishable_bucket_returns_no_rate_but_keeps_its_counts():
    body = _fn("_bucket_entry")
    assert "if status['status'] == 'measured' else None" in body
    assert "'count': len(outcomes)" in body, "counts survive so the absence can be explained"


def test_the_lookup_skips_a_bucket_with_no_publishable_rate():
    body = _fn("_calibrated_win_rate")
    assert body.count("get('win_rate') is not None") == 2, \
        "both the market and the pooled path must skip an unpublishable bucket"


def test_a_detail_lookup_says_which_scope_answered():
    """The tuple form returns the same shape for a market and a pooled bucket, so a reader
    cannot tell which they were shown."""
    body = _fn("calibrated_win_rate_detail")
    assert "'scope': 'market'" in body and "'scope': 'pooled_markets'" in body
    assert "'fell_back'" in body and "fallback_note" in body


def test_the_historical_display_values_are_not_rewritten():
    """Preserve what each signal card actually showed; corrected eligibility is a new
    calculation, not a revision of the record."""
    body = _fn("_build_confidence_calibration")
    assert "update" not in body.lower().replace("updated", "")
    assert "SignalOutcome" in body and "session.execute" in body
