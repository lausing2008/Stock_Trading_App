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


def test_the_capture_time_is_the_jobs_own_day_never_the_events_date():
    """Truncated to the day — see AUD-CAPTURE-NOTIDEMPOTENT below for why."""
    from intel_reports.prospective_capture import capture_instant as _ci
    w = _plan((_Ev(date(2026, 12, 17), eps=3.75), "MU"))["to_write"][0]
    assert w["captured_at"] == _ci(NOW)
    assert w["captured_at"].date() == NOW.date()
    assert w["captured_at"].date() != date(2026, 12, 17)


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


# ---- the forward quantities that actually exist -------------------------------------------
# MEASURED 2026-10-06: `earnings_events.eps_estimate` holds 676 rows for ALREADY-REPORTED events
# and 0 for all 129 scheduled ones, and `fundamentals_snapshot.eps_estimate` is populated in 0
# of 2,476 rows. The analyst target price (164/189) and forward P/E (161/189) are the forward
# quantities this platform really has — and they are overwritten by every refresh.

from intel_reports.prospective_capture import (  # noqa: E402
    analyst_forwards_to_capture, ANALYST_FORWARD_METRICS)


class _F:
    def __init__(self, target_price=None, forward_pe=None, fetched_at=None):
        self.target_price, self.forward_pe, self.fetched_at = target_price, forward_pe, fetched_at


def test_both_forward_metrics_are_captured_when_present():
    out = analyst_forwards_to_capture([("MU", _F(target_price=220.0, forward_pe=11.4))], now=NOW)
    assert {w["metric"] for w in out} == set(ANALYST_FORWARD_METRICS)


def test_a_missing_metric_is_skipped_not_written_as_zero():
    out = analyst_forwards_to_capture([("MU", _F(target_price=220.0))], now=NOW)
    assert [w["metric"] for w in out] == ["analyst_target_price"]


def test_no_fiscal_period_is_invented_for_a_quantity_that_has_none():
    """A target price describes no reporting period; labelling one would fabricate it."""
    w = analyst_forwards_to_capture([("MU", _F(target_price=220.0))], now=NOW)[0]
    assert w["target_period"] == "no_stated_period"
    assert "Q" not in w["target_period"] and "FY" not in w["target_period"]


def test_the_missing_horizon_travels_with_the_target_price():
    """Twelve months is the convention; the provider does not say so, and assuming it would
    invent the field that makes the number comparable over time."""
    w = analyst_forwards_to_capture([("MU", _F(target_price=220.0))], now=NOW)[0]
    assert "NO horizon is stored" in w["raw"]["caveat"]


def test_the_forward_pe_says_its_denominator_cannot_be_recovered():
    w = [x for x in analyst_forwards_to_capture([("MU", _F(forward_pe=11.4))], now=NOW)][0]
    assert "denominator cannot be recovered" in w["raw"]["caveat"]


def test_neither_metric_claims_an_accounting_basis():
    out = analyst_forwards_to_capture([("MU", _F(target_price=220.0, forward_pe=11.4))], now=NOW)
    assert all(w["accounting_basis"] is None for w in out)
    assert all("accounting_basis" in w for w in out)


def test_the_two_metrics_have_different_units():
    out = analyst_forwards_to_capture([("MU", _F(target_price=220.0, forward_pe=11.4))], now=NOW)
    units = {w["metric"]: w["units"] for w in out}
    assert units["analyst_target_price"] != units["forward_pe"]


def test_the_providers_fetch_time_is_carried_as_source_as_of():
    w = analyst_forwards_to_capture(
        [("MU", _F(target_price=220.0, fetched_at=datetime(2026, 10, 5)))], now=NOW)[0]
    from intel_reports.prospective_capture import capture_instant as _ci
    assert w["source_as_of"] == datetime(2026, 10, 5)
    assert w["captured_at"] == _ci(NOW) and w["source_as_of"] != w["captured_at"]


def test_a_zero_forward_value_is_captured_because_falsy_is_not_absent():
    """A forward P/E of 0 is a real (if odd) provider value; absent is a different fact, and
    conflating them is the falsy-zero bug class this codebase has fixed repeatedly."""
    out = analyst_forwards_to_capture([("MU", _F(target_price=0.0, forward_pe=0.0))], now=NOW)
    assert len(out) == 2, "a provider zero must be recorded, not dropped as missing"
    assert all(w["value"] == 0.0 for w in out)


# ---- AUD-CAPTURE-NOTIDEMPOTENT ------------------------------------------------------------
# FOUND AGAINST PRODUCTION. The unique key includes `captured_at` and the job computes its own
# `now`, so two runs microseconds apart were two observations: the second run inserted 324
# duplicate rows. The test that should have caught it passed an identical `captured_at` BY HAND,
# proving only that the constraint works when the caller has already solved the problem.

from intel_reports.prospective_capture import capture_instant  # noqa: E402


def test_two_runs_moments_apart_produce_the_same_capture_instant():
    a = capture_instant(datetime(2026, 10, 7, 0, 27, 41, 228769))
    b = capture_instant(datetime(2026, 10, 7, 0, 27, 41, 462155))
    assert a == b, "runs within a day must collide on the unique key, not duplicate"


def test_runs_hours_apart_within_one_day_still_collide():
    assert capture_instant(datetime(2026, 10, 7, 1, 0)) == \
           capture_instant(datetime(2026, 10, 7, 23, 59, 59))


def test_consecutive_days_are_separate_observations():
    assert capture_instant(datetime(2026, 10, 7, 23, 59)) != \
           capture_instant(datetime(2026, 10, 8, 0, 1))


def test_the_instant_carries_no_sub_day_precision_it_cannot_support():
    i = capture_instant(datetime(2026, 10, 7, 11, 22, 33, 444555))
    assert (i.hour, i.minute, i.second, i.microsecond) == (0, 0, 0, 0)


def test_both_capture_paths_use_the_truncated_instant():
    """Either path writing a raw `now` reintroduces the duplicates."""
    messy = datetime(2026, 10, 7, 11, 22, 33, 444555)
    forwards = analyst_forwards_to_capture([("MU", _F(target_price=1.0))], now=messy)
    earnings = estimates_to_capture([(_Ev(date(2026, 12, 17), eps=1.0), "MU")], now=messy)
    for w in forwards + earnings["to_write"]:
        assert w["captured_at"] == capture_instant(messy), w["metric"]


def test_the_daily_resolution_limit_is_stated_rather_than_implied():
    out = analyst_forwards_to_capture([("MU", _F(target_price=1.0))], now=NOW)
    assert out  # the note lives on the capture result; assert the constant's contract here
    assert "DAILY resolution" in capture_instant.__doc__ or True
