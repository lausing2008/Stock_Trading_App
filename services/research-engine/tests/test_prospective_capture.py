"""Does the capture job select only what it can honestly record?

Storage immutability is proved against real PostgreSQL in
shared/tests/test_quality_value_persistence_integration.py. These cover the SELECTION rules,
which are pure and need no database — `estimates_to_capture` was split out of the write for
exactly that reason.
"""
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))

from intel_reports.prospective_capture import (  # noqa: E402
    estimates_to_capture, HORIZON_DAYS, METRIC_UNITS)

NOW = datetime(2026, 10, 6, 7, 30)


class _Ev:
    _n = 0

    def __init__(self, report_date, eps=None, rev=None):
        _Ev._n += 1
        self.id = _Ev._n
        self.report_date = report_date
        self.eps_estimate = eps
        self.revenue_estimate = rev


def _plan(*rows, now=NOW, horizon_days=HORIZON_DAYS):
    return estimates_to_capture(list(rows), now=now, horizon_days=horizon_days)


def test_a_scheduled_event_with_a_consensus_is_captured_for_both_metrics():
    p = _plan((_Ev(date(2026, 12, 17), eps=3.75, rev=1.2e10), "MU"))
    assert {w["metric"] for w in p["to_write"]} == {"eps", "revenue"}


def test_a_metric_with_no_estimate_is_skipped_not_written_as_zero():
    """A zero consensus is a real claim. An absent one is a different fact."""
    p = _plan((_Ev(date(2026, 12, 17), eps=3.75, rev=None), "MU"))
    assert [w["metric"] for w in p["to_write"]] == ["eps"]
    assert p["skipped_no_estimate"] == 1
    assert all(w["value"] is not None for w in p["to_write"])


def test_a_zero_estimate_is_captured_because_zero_is_a_real_consensus():
    p = _plan((_Ev(date(2026, 12, 17), eps=0.0), "MU"))
    assert [w["value"] for w in p["to_write"]] == [0.0], "falsy is not absent"


def test_an_already_released_event_is_not_captured():
    """A consensus read after the print is contaminated by the result."""
    p = _plan((_Ev(date(2026, 10, 6), eps=3.75), "MU"),
              (_Ev(date(2026, 9, 24), eps=3.75), "MU"))
    assert p["to_write"] == [] and p["out_of_horizon"] == 2


def test_an_event_beyond_the_horizon_is_not_captured():
    p = _plan((_Ev(date(2028, 1, 1), eps=3.75), "MU"))
    assert p["to_write"] == [] and p["out_of_horizon"] == 1


def test_the_accounting_basis_is_written_as_null_rather_than_assumed():
    p = _plan((_Ev(date(2026, 12, 17), eps=3.75), "MU"))
    w = p["to_write"][0]
    assert "accounting_basis" in w, "the field must be written, not omitted"
    assert w["accounting_basis"] is None


def test_the_period_is_the_providers_label_not_a_fiscal_quarter():
    """MU's stored fiscal_quarter is derived from the calendar month and is wrong."""
    w = _plan((_Ev(date(2026, 12, 17), eps=3.75), "MU"))["to_write"][0]
    assert w["target_period"] == "report_2026-12-17"
    assert "Q" not in w["target_period"]


def test_the_capture_time_is_the_jobs_now_never_the_events_date():
    w = _plan((_Ev(date(2026, 12, 17), eps=3.75), "MU"))["to_write"][0]
    assert w["captured_at"] == NOW


def test_units_are_recorded_per_metric_and_differ():
    p = _plan((_Ev(date(2026, 12, 17), eps=3.75, rev=1.2e10), "MU"))
    by = {w["metric"]: w["units"] for w in p["to_write"]}
    assert by == {"eps": METRIC_UNITS["eps"], "revenue": METRIC_UNITS["revenue"]}
    assert by["eps"] != by["revenue"]


def test_the_event_identity_travels_with_the_snapshot():
    w = _plan((_Ev(date(2026, 12, 17), eps=3.75), "MU"))["to_write"][0]
    assert w["raw"]["report_date"] == "2026-12-17" and w["raw"]["event_id"]


def test_nothing_is_captured_when_no_event_is_scheduled():
    assert _plan()["to_write"] == []


def test_the_result_states_why_these_cannot_be_compared_with_an_actual():
    assert "unverified basis boundary" in _plan()["note"]


def test_the_horizon_is_bounded_and_declared():
    assert _plan()["horizon_days"] == HORIZON_DAYS and HORIZON_DAYS > 0
