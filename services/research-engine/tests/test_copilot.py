"""Copilot — behavioural acceptance through the real packet, grounding, store and service.

The scenarios live in `_copilot_probe.py` and run against a real database (PostgreSQL when
STOCKAI_INTEL_DB names one, otherwise SQLite). Only the model is scripted. Each test asserts one
scenario's measurement, so a failure names the property that broke.
"""
import json
import pathlib
import subprocess
import sys

import pytest

_PROBE = pathlib.Path(__file__).resolve().parent / "_copilot_probe.py"


@pytest.fixture(scope="module")
def r():
    proc = subprocess.run([sys.executable, str(_PROBE)], capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.fail(f"probe failed:\n{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_packet_citations_resolve_and_gaps_carry_reasons(r):
    p = r["p1"]
    assert {"price.close", "return.5s", "signal.action", "kscore.score"} <= set(p["keys"])
    assert p["every_field_ref_resolves"], "a citation that resolves to nothing is not evidence"
    assert p["missing_have_reasons"]
    assert p["report_state"] == "UNAVAILABLE"
    assert "engine score" in p["strength_display"]
    assert p["every_quantity_has_meaning"]
    assert p["unknown_symbol_is_none"]


def test_headline_text_is_quoted_data_never_instructions(r):
    p = r["p1"]
    assert p["benign_headline_is_quoted_data"]
    assert p["injection_withheld_from_model"], "text addressed to AI must not reach the model"
    assert p["injection_not_rendered"], "the platform must not print the attacker's text"
    assert p["injection_still_cited"], "withholding is not deleting: the source stays linked"


def test_stale_price_marks_everything_derived_from_it(r):
    p = r["p2"]
    assert p["price_state"] == "STALE" and p["return_state"] == "STALE"
    assert p["rendered"].endswith("[stale]")


def test_model_written_figures_and_unknown_keys_are_refused(r):
    p = r["p3"]
    assert p["good_accepted"] and p["good_renders_packet_value"] and p["good_has_no_placeholders"]
    assert p["digits_rejected"], "a figure the model typed itself must never reach the user"
    assert p["digits_in_limitations_rejected"]
    assert p["unknown_key_rejected"]
    assert p["symbol_digits_allowed"], "0700.HK is an identifier, not a figure"
    assert p["probability_rejected"]
    assert p["non_json_rejected"] and p["fenced_json_parsed"]
    assert p["tagged_with_quotes"], "quotes inside an answer must not break parsing"
    assert p["history_format_round_trips"]


def test_grounded_answer_is_persisted_with_evidence_and_replayed_not_rebilled(r):
    p = r["p4"]
    assert p["status"] == "grounded"
    assert p["refs_have_evidence"] and p["has_limitations"] and p["data_as_of"]
    assert p["model_calls"] == 1, "a retried request must not pay for a second model call"
    assert p["replayed"] and p["same_message"] and p["rows"] == 2
    assert p["budget_reserved"] == 1 and p["budget_settled"] == [[1100, "ok"]]


def test_one_repair_then_deterministic_fallback(r):
    p = r["p5"]
    assert p["repaired_status"] == "grounded" and p["repair_calls"] == 2
    assert p["repair_names_reason"]
    assert p["rejected_status"] == "rejected" and p["rejected_calls"] == 2
    assert p["rejected_has_reasons"] and p["fallback_has_no_model_figures"]
    assert p["history_alternates"] == ["user", "assistant", "user"]


def test_conversations_are_owner_only(r):
    p = r["p6"]
    assert p["bob_read_none"] and p["bob_delete_false"] and p["bob_list_empty"]
    assert p["survives_bob"]
    assert p["bob_continue_status"] == 404
    assert p["bob_same_rid_not_replayed"], "another user's request id must not replay their answer"
    assert p["alice_delete"] and p["messages_left"] == 0


def test_admission_limits_and_failure_accounting(r):
    p = r["p7"]
    assert p["quota_status"] == 429
    assert p["budget_status"] == 429 and p["budget_refusal_skips_model"]
    assert p["own_key_bypasses_shared_budget"]
    assert p["timeout_status"] == 502
    assert p["timeout_settled"] == [[None, "ambiguous"]], "a timeout may have been billed"
    assert p["retry_after_timeout"] == "grounded" and p["retry_sends_question_once"]
    assert p["unknown_symbol"] == 404 and p["empty_question"] == 400


def test_retention_purge_removes_messages_too(r):
    p = r["p8"]
    assert p["purged"] == 1 and p["remaining"] == ["new"] and p["orphan_messages"] == 0


def test_report_changes_cite_both_versions_and_skip_private_reports(r):
    p = r["p9"]
    assert p["report_id"] == p["v2"] and p["changes"] > 0
    assert p["change_has_evidence_for_both"]
    assert p["private_report_ignored"], "a portfolio-bearing report is owner-only"


def test_a_retry_never_runs_while_a_call_may_still_be_running(r):
    p = r["p10"]
    assert p["inflight_status"] == 409 and p["inflight_model_calls"] == 0
    assert p["abandoned_taken_over"] == "grounded", "a dead process's claim must expire"
    assert p["cancelled_running_status"] == 409 and p["cancelled_running_calls"] == 0, \
        "a cancelled call may still be running; a same-id retry must not start a second one"
    assert p["failed_status"] == "failed"


def test_cancel_stops_the_repair_call_and_keeps_the_paid_outcome(r):
    p = r["p10"]
    assert p["cancel_mid_calls"] == 1
    assert p["cancel_mid_status"] == "rejected" and p["cancel_mid_reason"]
    assert p["statuses"]["req-cancel-mid"] == "answered"


def test_digit_names_pass_but_figures_beside_them_do_not(r):
    n = r["p3"]["names"]
    assert n["sp500_named"] and n["window_label"] and n["quoted_period"]
    assert n["negated_probability"] and n["data_as_of_key"]
    assert not n["sp500_with_figure"], "an index name must not carry an invented figure past"
    assert n["trading_sessions"] and n["report_named_window"]
    assert not n["unnamed_window"], "a window the evidence never names is an invented figure"
    assert n["sma_abbrev"] and not n["ema_abbrev"], "only averages the packet computes"
    assert not n["unknown_window"] and not n["day_window"] and not n["unknown_trading_window"]
    assert not n["invented_period"], "a period the model supplies itself is refused"
    assert not n["period_with_figure"]
    assert not n["asserted_probability"]


def test_figures_written_in_words_are_refused(r):
    w = r["p3"]["words"]
    assert not w["five_hundred_dollars"] and not w["twelve_percent"]
    assert w["small_count"], "small counts in words remain allowed"
