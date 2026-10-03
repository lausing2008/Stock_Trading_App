"""Intelligence reports — the behavioural acceptance, driven through the real generators.

The scenarios live in `_intelligence_pg_probe.py` and run against a real database (PostgreSQL
when STOCKAI_INTEL_DB names one, otherwise a throwaway SQLite file so this suite needs no
server). Each test below asserts one scenario's verdict, so a failure names the property that
broke rather than "the probe failed".

NO SOURCE-TEXT ASSERTIONS. Every property here is established by generating a report, storing
it, and reading what came back.
"""
import json
import pathlib
import subprocess
import sys

import pytest

_PROBE = pathlib.Path(__file__).resolve().parent / "_intelligence_pg_probe.py"


@pytest.fixture(scope="module")
def results():
    proc = subprocess.run([sys.executable, str(_PROBE)], capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.fail(f"probe failed:\n{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}")
    return json.loads(proc.stdout)


def _scenario(results, name):
    assert name in results, f"{name} did not run"
    return results[name]


def test_all_four_report_types_generate_and_render(results):
    r = _scenario(results, "t1_all_four_types_generate")
    for kind in ("market_outlook", "stock_outlook", "pre_earnings", "post_earnings"):
        assert r[kind]["id"], f"{kind} produced no stored report"
        assert r[kind]["fields"] > 5
        assert r[kind]["markdown_lines"] > 10
    assert r["passes"]


def test_thin_data_yields_a_partial_report_not_an_error(results):
    r = _scenario(results, "t7_partial_inputs_still_produce_a_useful_report")
    assert r["status"] == "partial"
    assert r["missing_fields"] > 0
    assert r["every_missing_field_has_a_reason"], "a missing field with no reason is a blank cell"
    assert r["markdown_has_why_section"]


def test_identical_inputs_reuse_the_report_rather_than_duplicating(results):
    r = _scenario(results, "t2_identical_inputs_do_not_duplicate")
    assert r["same_fingerprint"]
    assert r["first_id"] == r["second_id"]
    assert r["rows"] == 1
    assert r["created"] == [True, False]


def test_changed_inputs_create_a_linked_version_and_leave_the_original_intact(results):
    r = _scenario(results, "t3_changed_inputs_create_a_linked_version")
    assert r["v2_version"] == 2
    assert r["supersedes"] == r["v1"]
    assert r["v1_status"] == "superseded"
    assert r["changed_fields"] > 0
    assert r["v1_payload_unchanged"], "a superseded report must keep the payload it was issued with"


def test_a_missing_pre_earnings_baseline_is_recorded_not_reconstructed(results):
    r = _scenario(results, "t4_post_report_without_a_pre_report_says_so")
    assert r["pre_report_id"] is None
    assert r["link_state"] == "UNAVAILABLE"
    assert "not reconstructed" in r["link_reason"].lower()
    assert r["verdict_state"] == "NOT_APPLICABLE"


def test_a_frozen_pre_report_survives_a_later_consensus_revision(results):
    """The accountability guarantee: the post-release report scores against what was written
    BEFORE the release, not against a consensus revised afterwards."""
    r = _scenario(results, "t5_a_frozen_pre_report_survives_and_scores")
    assert r["frozen_payload_unchanged"]
    assert r["post_pre_link"] == r["pre_id"]
    assert r["verdict"]["frozen_consensus_eps"] == 1.50, \
        "scored against the revised 1.95 instead of the frozen 1.50"


def test_a_report_written_after_the_release_cannot_become_the_baseline(results):
    r = _scenario(results, "t6_a_report_written_after_the_release_is_not_a_pre_report")
    assert r["found"] is None


def test_fiscal_period_is_never_asserted_from_the_release_month(results):
    """EarningsEvent.fiscal_quarter is derived from the period-end calendar month and is wrong
    for every non-calendar fiscal year. The report must say UNKNOWN rather than repeat it."""
    r = _scenario(results, "t8_fiscal_period_is_never_asserted_from_the_release_month")
    assert r["state"] == "UNKNOWN"
    assert r["value_is_absent"]


def test_a_zero_estimate_produces_no_percentage_surprise(results):
    r = _scenario(results, "t9_zero_estimate_gives_no_percentage_surprise")
    assert r["normal"]["state"] == "OK"
    assert r["zero_estimate"]["state"] == "NOT_APPLICABLE"
    assert "absolute difference" in r["zero_estimate"]["value"]
    assert r["missing_actual"]["state"] == "UNAVAILABLE"


def test_a_negative_estimate_does_not_invert_the_surprise_sign(results):
    """abs() in the denominator: beating a -0.50 estimate with -0.20 is a POSITIVE surprise."""
    r = _scenario(results, "t9_zero_estimate_gives_no_percentage_surprise")
    assert r["negative_estimate"]["value"]["pct"] > 0


def test_proxies_are_labelled_as_proxies(results):
    r = _scenario(results, "t10_market_report_labels_its_proxies")
    assert "not index constituent breadth" in r["breadth_basis"]
    assert "proxy" in r["benchmark_basis"]
    assert r["volatility_state"] == "UNAVAILABLE", \
        "an unsourced dimension must say so rather than be omitted"


def test_no_forecast_probability_is_invented(results):
    r = _scenario(results, "t11_no_forecast_probability_is_invented")
    assert r["horizons"] == 3
    assert all(p is None for p in r["probabilities"])
    assert r["signal_is_model_forecast_class"] == "model_forecast"
