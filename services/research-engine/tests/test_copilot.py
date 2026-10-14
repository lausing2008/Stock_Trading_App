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
    assert {"price.close", "return.5b", "signal.action", "kscore.score"} <= set(p["keys"])
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


# ── docs/audits/2026-10-13-copilot-independent-review.md, finding by finding ──

def test_r1_quantities_without_digits_are_refused_and_counts_are_not(r):
    p = r["p11"]
    assert all(p["r1_refused"].values()), [t for t, ok in p["r1_refused"].items() if not ok]
    assert all(p["r1_allowed"].values()), [t for t, ok in p["r1_allowed"].items() if not ok]


def test_r2_daily_limit_counts_in_flight_answers_and_survives_deletion(r):
    p = r["p11"]
    assert p["r2_concurrent"] == {"model_calls": 1, "ok": 1, "refused_429": 2}
    assert p["r2_after_delete"] == 429, "deleting a conversation must not restore the allowance"
    assert p["r2_refund_on_no_call"] == 0, "no model call made, so the admission is returned"
    assert p["r2_failed_call_keeps_admission"] == 1, "an attempted call may have been billed"


def test_r4_a_request_id_is_bound_to_its_payload(r):
    p = r["p11"]
    assert p["r4_replay_other_question"] == 409
    assert p["r4_replay_other_symbol"] == 409
    assert p["r4_replay_other_conversation"] == 409
    assert p["r4_failed_retry_other_question"] == 409 and p["r4_failed_retry_model_not_called"]
    assert p["r4_same_retry_ok"] == "grounded"


def test_r5_returns_are_labelled_as_bars_and_missing_bars_are_stated(r):
    p = r["p11"]["r5"]
    assert "possible missing bars" in p["display"] and "→" in p["display"]
    assert p["no_session_claim"]


def test_r6_every_average_and_return_recomputes_from_its_own_evidence(r):
    p = r["p11"]["r6"]
    assert p["trend.sma20"]["inputs"] == 20 and p["trend.sma50"]["inputs"] == 50
    for k in ("trend.sma20", "trend.sma50", "return.5b"):
        assert p[k]["recomputed"] == p[k]["stated"], k
    assert p["preview"] == {"inline": 5, "count": 51, "full": 51, "other_user": None}


def test_an_old_report_is_stale_and_each_source_keeps_its_own_time(r):
    p = r["p11"]["freshness"]
    assert p["report_state"] == "STALE" and p["report_age"] == "29 calendar days"
    t = p["source_times"]
    assert t["price"] != t["report_generated"] and t["signal"] and t["newest_headline"]


def test_a_conversation_deleted_mid_answer_is_not_resurrected(r):
    assert r["p11"]["deleted_mid_answer"] == {"status": 409, "conversations": 0, "messages": 0}


def test_earlier_answers_are_framed_as_history_not_evidence(r):
    assert r["p11"]["history_framed"]



# ── Phase 2, slice 1: screening ──

def test_screen_applies_exactly_what_was_asked(r):
    a = r["p12"]["applied"]
    assert a["interpreted"] == ["min_score", "sector", "signal"]
    p = a["params"]
    assert p["min_score"] == 70 and p["signal"] == "BUY" and p["sector"] == "Technology"
    assert p["limit"] == 5 and p["symbols"] == "TESTCO", "non-admin: own watchlist only"
    assert a["unsupported"] == ["P/E below 15"], "reported, never approximated"
    assert a["coverage"]["universe"] == 1 and a["scope"] == "watchlist"


def test_screen_drops_every_filter_the_user_did_not_ask_for(r):
    i = r["p12"]["invented"]
    assert i["interpreted"] == ["min_score"]
    assert set(i["dropped"]) == {"min_momentum", "market", "sector", "symbols", "limit"}
    assert "does not appear in your question" in i["dropped"]["min_momentum"]
    assert "min_momentum" not in i["params"] and "market" not in i["params"]


def test_screen_scope_comes_from_the_account_not_the_request(r):
    assert r["p12"]["admin_scope"] == {"kind": "all_ranked", "symbols_param": False}
    e = r["p12"]["empty_scope"]
    assert e == {"status": "empty_scope", "model_calls": 0, "fetches": 0, "admitted": 0}
    inj = r["p12"]["injection"]
    assert inj["status"] == "nothing_applied" and inj["fetches"] == 0
    assert inj["scope"] == "watchlist", "a request to widen scope cannot widen it"


def test_screen_repairs_an_unparsed_reply_once(r):
    assert r["p12"]["repair_on_unparsed"] == {"calls": 2, "status": "ok"}



# ── Phase 2, slice 2: action drafts ──

def test_a_draft_threshold_is_a_packet_price_or_a_number_the_user_typed(r):
    p = r["p13"]["proposed"]
    assert p["status"] == "grounded" and p["n"] == 1 and p["kind"] == "price_alert"
    assert p["threshold"] == round(p["sma50"], 4) and p["source"] == "trend.sma50"
    assert any("not a price" in x for x in p["dropped"]), "{{return.5b}} is not a price"
    assert any("does not match what you asked for" in x for x in p["dropped"]), "999 invented"
    assert r["p13"]["typed"]["thresholds"] == [150.0]
    assert any("this conversation's symbol" in x for x in r["p13"]["typed"]["dropped"])


def test_drafting_discloses_and_binds_state_but_writes_nothing(r):
    p = r["p13"]["proposed"]
    assert p["has_effects"] and p["has_state"] and p["key_hidden"]
    assert p["market_writes_while_drafting"] == [], "a draft must not save anything"


def test_no_draft_without_intent_a_grounded_answer_or_a_market_client(r):
    p = r["p13"]
    assert p["no_intent"]["n"] == 0 and "you did not ask for this action" in p["no_intent"]["reasons"]
    assert p["rejected_answer_drafts"] == 0
    assert p["no_market"]["n"] == 0


def test_confirming_saves_once_and_a_second_confirm_replays(r):
    c = r["p13"]["confirm"]
    assert c["status"] == "saved" and c["replay"] is True
    assert c["creates"] == 1 and c["creates_after_replay"] == 1 and c["alert_rows"] == 1


def test_confirm_refuses_other_users_stale_state_and_expiry(r):
    p = r["p13"]
    assert p["confirm_other_user"] == 404
    assert p["confirm_stale_state"] == 409, "a draft bound to changed state must not apply"
    assert p["confirm_expired"] == 410


def test_a_watchlist_draft_discloses_the_signal_email_enrolment(r):
    w = r["p13"]["watchlist"]
    assert "signal-change emails" in w["effects"][0]
    assert w["saved"] == "saved" and w["adds"] == 1



# ── Phase 2 review (S2/S3): WRONG proposals against correct questions ──

def test_s3_screen_refuses_reversed_misattached_and_negated_proposals(r):
    s = r["p14"]["screen"]
    assert s["K-Score below 70 | min_score 70"] == {}, "direction reversed"
    assert s["K-Score below 70 | max_score 70"] == {"max_score": 70.0}
    assert s["Show 10 stocks with K-Score above 70 | min_confidence 10"] == {}, "count as threshold"
    assert s["Show 10 stocks with K-Score above 70 | min_score 70"] == {"min_score": 70.0}
    assert s["Technology stocks, excluding BUY signals | signal BUY"] == {}, "negation ignored"
    assert s["Stocks not in Hong Kong | market HK"] == {}
    assert s["technical score of at least 55 | min_score 55"] == {}, "wrong metric"
    assert s["technical score of at least 55 | min_technical 55"] == {"min_technical": 55.0}
    assert s["Momentum below 40 | min_momentum 40"] == {}
    assert r["p14"]["momentum_below_unsupported"], "no max_momentum filter: reported, not guessed"
    assert r["p14"]["limit_10"] == 10


def test_s3_an_empty_or_none_proposal_runs_no_screen(r):
    assert r["p14"]["empty_proposal"] == {"status": "nothing_applied", "fetches": 0}
    assert r["p14"]["order_only"] == {"status": "ok", "sort": "rs_score", "limit": 5}


def test_s2_drafts_refuse_wrong_unit_direction_source_and_negation(r):
    d = r["p14"]["drafts"]
    assert d["Alert me if it drops 10% | below 10"] == [], "a percentage is not a price"
    assert d["Alert me below 100 | above 100"] == [], "direction reversed"
    assert d["Alert me below 100 | below 100"] == [["below", 100.0]]
    assert d["Alert me below its 50-bar average | below {{kscore.fair_price}}"] == [], "wrong source"
    assert d["Alert me below its 50-bar average | below {{trend.sma50}}"] == [["below", "trend.sma50"]]
    assert d["Don't alert me below 100 | below 100"] == [], "negated action"
    assert d["Alert me when it hits 150 | above 150"] == [], "side not stated"
    assert d["Alert me below 100 in 2 days | below 2"] == [], "a horizon number is not a level"
    assert d["Put MSFT on my watchlist | watchlist_add"] == [], "another ticker"
    assert d["Alert me below 100 and above 150 | below 100 + above 150"] == [["below", 100.0],
                                                                           ["above", 150.0]]


def test_s6_cards_describe_the_rule_the_checker_applies(r):
    c = r["p14"]["card"]
    assert "observed price" in c["summary"] and "at or below" in c["summary"]
    assert "closes" not in c["summary"]
    assert any("does not wait for a closing price" in n for n in c["notes"])
    assert any("will NOT follow" in n for n in c["notes"]), "an average-derived level is fixed"



# ── Phase 2 review (S1/S4/S5): save recovery, siblings, consent ──

def test_s1_a_lost_acknowledgment_is_reconciled_not_refused(r):
    l = r["p15"]["lost_ack"]
    assert l["first"] == 502, "ambiguous: the user is told it may have completed"
    assert l["second"] == "saved" and l["reconciled"] is True
    assert l["alerts"] == 1, "the retry must not create a second alert"


def test_s1_a_crash_after_commit_reconciles_even_after_expiry(r):
    assert r["p15"]["crash_then_late_retry"] == {"status": "saved", "alerts": 1}


def test_s4_both_sibling_drafts_save_in_either_order(r):
    for k in ("siblings_01", "siblings_10"):
        assert r["p15"][k] == {"results": ["saved", "saved"], "alerts": 2,
                               "serialised": ["saved", "saved"]}, k


def test_s4_a_genuinely_conflicting_external_change_still_refuses(r):
    assert r["p15"]["external_identical_alert"] == 409


def test_s5_a_changed_email_effect_refuses_the_old_card(r):
    c = r["p15"]["consent_changed"]
    assert c["status"] == 409 and c["adds"] == 0
    assert "turned off" in c["disclosed"][0]


def test_a_definite_refusal_is_recorded_as_failed_not_executing(r):
    assert r["p15"]["definite_failure"] == {"status": 400, "receipt": "failed"}


def test_saves_are_refused_before_any_write_when_the_migration_is_not_in_place(r):
    assert r["p15"]["not_ready"] == {"status": 503, "writes": 0}
