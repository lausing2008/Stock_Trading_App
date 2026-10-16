"""Plan mode (forecast + textbook playbook) — behavioural acceptance through the real code.

Scenarios live in `_copilot_plan_probe.py` and run against a real database (PostgreSQL when
STOCKAI_INTEL_DB names one, otherwise SQLite); only the model is scripted. The fixture's bars
make every level computable by hand: true range 2 on every bar, so ATR(14) = 2.00, the prior
20-bar low/high = 99/101 and the 50-bar averages = 100.
Design: docs/features/2026-10-14-copilot-plan-mode-design.md.
"""
import json
import pathlib
import subprocess
import sys

import pytest

_PROBE = pathlib.Path(__file__).resolve().parent / "_copilot_plan_probe.py"


@pytest.fixture(scope="module")
def r():
    proc = subprocess.run([sys.executable, str(_PROBE)], capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.fail(f"probe failed:\n{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


# ── Levels and entry rules, hand-computed ────────────────────────────────────

def test_levels_are_the_hand_computed_values_and_cite_every_input(r):
    p = r["pl1"]
    assert p["packet_version"] == 7
    assert p["atr"].startswith("$2.00 (14-bar ATR") and p["atr_calc"] == 2.0
    assert (p["low20"], p["high20"]) == ("$99.00", "$101.00")
    assert p["sma50"].startswith("$100.00, flat")
    assert p["every_ref_resolves"] and p["every_field_ref_resolves"]
    assert p["atr_cites_inputs"] == 101, "the ATR's calculation record plus all 100 input bars"


def test_entry_rules_state_their_conditions_and_code_calculated_stops(r):
    p = r["pl1"]
    assert p["kind"] == "entry" and p["holding_rules"] == []
    assert "20-bar low ($99.00)" in p["e1"] and "stop below that low at $98.00" in p["e1"]
    assert p["e1"].startswith("If ")
    assert "20-bar high ($101.00)" in p["e2"] and "stop at $99.00" in p["e2"]
    assert p["e2"].startswith("If ")


def test_reward_risk_rule_covers_every_branch(r):
    p = r["pl1"]
    # 100 -> stop 98, target 101: 1/2 = 0.5; the price that meets 2.0 is (101 + 2*98)/3 = 99.
    assert "reward:risk 0.5, below the textbook minimum of 2" in p["e3"]
    assert "at or below $99.00" in p["e3"]
    assert "reward:risk 1.5" in p["e3_low_close"]
    assert "breakout case" in p["e3_at_high"]
    # A close below the pullback stop (reachable only because the 20-bar window EXCLUDES the
    # latest bar — the first build included it, which made this branch dead code).
    assert "has broken" in p["e3_broken"] and "$97.93" in p["e3_broken"]   # ATR 30/14
    assert "has broken" in p["e3_broken_96"] and "$97.89" in p["e3_broken_96"]  # ATR 31/14


def test_trend_filter_says_at_or_above_when_equal(r):
    assert "at or above the 50-bar average ($100.00)" in r["pl1"]["e4"]


def test_sizing_uses_the_typed_budget_or_says_how_to_give_one(r):
    p = r["pl1"]
    assert "250 shares ($25,000.00)" in p["e6"]             # floor(500 / 2)
    assert "250 shares ($25,000.00)" in p["e6_pct"]         # 50,000 x 1% = 500
    assert "Each 100 shares" in p["e6_no_budget"] and "$200.00" in p["e6_no_budget"]
    assert "Each 100 shares" in p["e6_pct_no_account"], "a % with no account sizes nothing"
    assert "needs an account size" in p["pct_no_account_refusal"]


# ── Holding rules ───────────────────────────────────────────────────────────

def test_holding_rules_for_a_typed_position(r):
    p = r["pl2"]
    assert p["kind"] == "holding"
    assert p["position.entry"] == "$95.00" and p["position.shares"] == "100 shares"
    assert p["position_source"] == "stated by you in this question"
    assert "gain of $5.00 per share ($500.00 on 100 shares)" in p["rule.h1"]
    assert "+5.3%" in p["rule.h1"] and "+2.5 ATRs" in p["rule.h1"]
    assert "$91.00" in p["rule.h2"] and "$98.00" in p["rule.h2"] and "structure-based" in p["rule.h2"]
    assert "raises the stop to at least your entry ($95.00)" in p["rule.h3"]
    assert "today's stop candidate stays $98.00" in p["rule.h4"]
    assert "20-bar high ($101.00), above your entry" in p["rule.h5"] and "partial profit" in p["rule.h5"]
    assert "current stop is unknown" in p["rule.h6"] and "candidate is $98.00" in p["rule.h6"]
    assert "SWING BUY" in p["rule.h8"] and "LONG SELL" in p["rule.h8"]
    assert p["rule.e1"], "entry rules stay available beside the holding rules"


def test_a_losing_position_says_so_and_flags_a_reached_stop(r):
    p = r["pl2"]
    assert "loss of $4.00 per share ($40.00 on 10 shares)" in p["loss_h1"]
    assert "entry-based" in p["loss_h2"] and "$100.00" in p["loss_h2"]
    assert "once a close is above $106.00" in p["loss_h3"]


def test_trailing_stop_rises_in_a_trend_and_drives_the_exit_level(r):
    p = r["pl2"]
    assert p["trend_low20"] == "$219.00"
    assert "today's stop candidate is $226.00" in p["trail_h4"]
    assert "candidate is $226.00" in p["trail_h6"] and "unknown" in p["trail_h6"]
    assert "$230.50" in p["trend_h5"] and "$231.00" in p["trend_h5"]


def test_an_implausible_entry_or_none_builds_no_holding_rules(r):
    p = r["pl2"]
    assert "far from the latest close" in p["far_entry_refused"] and not p["far_entry_has_h"]
    assert p["no_entry_kind"] == "holding" and not p["no_entry_has_h"]
    assert "no entry price was found" in p["no_entry_refusal"]
    assert "rules for a held position were not built" in p["no_entry_gap"]


# ── Calls and track records ───────────────────────────────────────────────────

def test_calls_carry_their_own_time_and_staleness(r):
    p = r["pl3"]
    assert p["kind"] == "forecast" and p["no_rules_for_forecast"]
    assert p["call_swing"].startswith("BUY (SWING") and p["call_swing_state"] == "OK"
    assert p["call_long"].startswith("SELL (LONG") and p["call_long_state"] == "STALE"
    assert "no SHORT signal" in p["gaps"] and "no GROWTH signal" in p["gaps"]


def test_records_state_count_span_and_verification(r):
    p = r["pl3"]
    # 24 + 5 correct of 40 + 7 resolved, every one unverified.
    assert p["record"].startswith("all stocks, SWING BUY: ")
    assert "in 62% of 47 past BUY calls" in p["record"]
    assert "none verified" in p["record"] and "signals 2026-09-19 to 2026-09-19" in p["record"]
    assert "3 of 47 verified" in p["verified_3"], "the status is computed, not hard-coded"
    assert "too few" in p["record_10d"] and "at least 30" in p["record_10d"]
    assert "too few" in p["long_record"]
    assert "62% of 47 past SWING calls of either direction" in p["baseline"]


def test_floors_cohort_30_and_stock_8(r):
    p = r["pl3"]
    assert "too few" in p["cohort_29"] and "29 resolved" in p["cohort_29"]
    assert "of 30 past BUY calls" in p["cohort_30"]
    assert "too few" in p["own"] and "7 resolved" in p["own"]
    assert p["own_8"].startswith("TESTCO, SWING BUY: ") and "75% of 8 past BUY calls" in p["own_8"]


def test_no_look_ahead_and_lookback_is_bounded(r):
    assert r["pl3"]["with_late_and_old"] == r["pl3"]["record"]


# ── Staleness and thin data ───────────────────────────────────────────────────

def test_stale_prices_mark_levels_and_rules_but_not_fresh_calls(r):
    p = r["pl4"]
    assert p["price_stale_rule_state"] == "STALE" and p["price_stale_level_state"] == "STALE"
    assert p["call_swing_state_when_price_stale"] == "OK"
    assert p["rendered"].endswith("[stale]")


def test_a_split_sized_jump_marks_levels_stale_with_the_reason(r):
    p = r["pl4"]
    assert p["split_state"] == "STALE" and "split or other corporate action" in p["split_note"]


def test_thin_history_builds_no_rules_and_says_why(r):
    p = r["pl4"]
    assert not p["thin_has_rules"] and "no levels or rules can be computed" in p["thin_gap"]


def test_earnings_rule_by_distance(r):
    p = r["pl4"]
    assert "4 calendar days away" in p["earnings_near"] and "wait until after" in p["earnings_near"]
    assert "stop does not cap the loss" in p["earnings_near_holding"]
    assert "does not apply yet" in p["earnings_far"]


# ── Routing, refusals, turns ───────────────────────────────────────────────────

def test_ordinary_questions_get_no_plan_and_no_extra_limitation(r):
    p = r["pl5"]
    assert not p["plain_has_plan_keys"] and p["plain_plan_kind"] is None
    assert "not loaded for this question" in p["plain_playbook_note"]
    assert not p["plain_limitations_mention_playbook"]


def test_options_shorts_split_entries_and_incomplete_numbers_get_no_holding_rules(r):
    p = r["pl5"]
    for label in ("options", "short", "two_entries", "incomplete"):
        assert not p[label]["has_h"] and p[label]["refusal"], label
    assert "long stock positions only" in p["options"]["refusal"]
    assert "average cost" in p["two_entries"]["refusal"]


def test_a_plan_answer_cites_rules_and_records_and_renders_their_figures(r):
    p = r["pl6"]
    assert p["good_status"] == "grounded" and p["good_packet_version"] == 7
    assert p["model_saw_rules"]
    assert {"call.swing", "record.swing.5d", "rule.h1", "rule.h2"} <= set(p["good_refs"])
    assert "$98.00" in p["good_content"] and "{{" not in p["good_content"]
    assert "[stale]" not in p["good_content"].split("\n")[0]


def test_a_price_prediction_is_refused_and_the_plan_is_shown_without_the_model(r):
    p = r["pl6"]
    assert p["predict_status"] == "rejected"
    assert any("predicts a price move" in x for x in p["predict_reasons"])
    assert p["predict_fallback_has_plan"]
    assert "call.swing" in p["predict_fallback_refs"] and "record.swing.5d" in p["predict_fallback_refs"]


def test_calendar_day_windows_pass_only_in_their_exact_wording(r):
    p = r["pl6"]
    assert p["good_status"] == "grounded", "'5 calendar days' names a window the evidence names"
    # "5 days" beside a 5-BAR value is a mislabel and a figure; beside a 5-calendar-day RECORD
    # it is an accurate label (test_held_out_fixes_are_narrow).
    assert p["five_days_status"] == "rejected", "'5 days' beside a bar-based value is refused"


def test_plan_view_is_deterministic_free_and_validated(r):
    p = r["pl7"]
    assert p["kind"] == "holding"
    assert p["groups"] == ["calls", "levels", "moves", "position", "records", "rules", "setups"]
    assert "rule.h2" in p["rule_keys"] and "rule.e1" in p["rule_keys"]
    assert p["no_price_records_inline"] and p["price_bar_records"] > 200
    assert "NOT tuned" in p["parameters_note"]
    assert p["usage_rows"] == 0, "no model call, so nothing is charged to the daily ledger"
    assert p["errors"] == {"neg_entry": 400, "pct_no_account": 400, "bad_kind": 400,
                           "zero_shares": 400, "nan_risk": 400, "unknown_user": 403,
                           "unknown_symbol": 404}


# ── Grammar and forecast-language tables ──────────────────────────────────────

_G = {
    "I bought 100 shares at 182.50, what now?": ("holding", 182.5, 100, False),
    "bought 100 at $182.5": ("holding", 182.5, 100, False),
    "Bought 100 at 182.5. What next?": ("holding", 182.5, 100, False),
    "I own it, avg cost 1,250.75 and 30 shares": ("holding", 1250.75, 30, False),
    "I have 200 shares, cost basis 45.10": ("holding", 45.1, 200, False),
    "I bought at 180, stop at 170?": ("holding", 180.0, None, False),     # a stop is not an entry
    "I bought at 180 at 9:30 today": ("holding", 180.0, None, False),     # a time is not a price
    "I bought 50 at 10 and 50 at 12": ("holding", None, None, True),
    "I bought at 1,00": ("holding", None, None, True),
    "I bought at 182.5k": ("holding", None, None, True),
    "I bought calls at 5": ("holding", None, None, True),
    "I shorted it at 120": ("entry", None, None, True),
    "should I buy? risk $500": ("entry", None, None, False),
    "risk 1% should I buy": ("entry", None, None, True),
    "I don't own it, should I buy": ("entry", None, None, False),
    "what is the prediction for next week": ("forecast", None, None, False),
    "Explain this signal": (None, None, None, False),
    "Alert me if it falls below 170": (None, None, None, False),
    "What is the K-Score?": (None, None, None, False),
}


@pytest.mark.parametrize("q", list(_G))
def test_position_grammar(r, q):
    g = r["pl8"]["grammar"][q]
    kind, entry, shares, refused = _G[q]
    assert g["kind"] == kind
    assert g["entry"] == entry
    if shares is not None:
        assert g["shares"] == shares
    assert g["refused"] is refused


def test_risk_grammar(r):
    g = r["pl8"]["grammar"]
    assert g["should I buy? risk $500"]["risk_amount"] == 500.0
    assert g["account 50,000 risk 1% should I buy"]["account"] == 50000.0
    assert g["account 50,000 risk 1% should I buy"]["risk_pct"] == 1.0
    assert g["I can lose 300 dollars, where to buy"]["risk_amount"] == 300.0


@pytest.mark.parametrize("text,refused", [
    ("The stock will rise next week.", True),
    ("It should bounce from support.", True),
    ("It is expected to reach the high.", True),
    ("Shares are poised to rally.", True),
    ("The price will go up.", True),
    # Review C4: a condition word no longer exempts a model-authored prediction; conditions
    # come from the code-rendered rule keys.
    ("If a close is above the high, it will likely climb.", True),
    ("The data does not say the price will rise.", False),
    ("Nothing here shows it is going to fall.", False),
    ("The engine is set to recalculate tomorrow.", False),
    ("Once it reaches the 20-bar high, take partial profit.", False),
    ("The stop will trigger if a close is below it.", False),
    ("Past BUY calls rose in most cases.", False),
    ("Whether it will rise is not something the packet shows.", False),
])
def test_forecast_language(r, text, refused):
    assert bool(r["pl8"]["forecast"][text]) is refused


def test_held_out_fixes_are_narrow(r):
    """From the plan-mode held-out run (2026-10-14): accurate window labels beside a record,
    quoted examples, and citable refusals pass — and each neighbouring wrong case still fails."""
    e = r["pl6"]["exempt"]
    for k in ("label_beside_record", "words_beside_record", "calendar_beside_stock_record",
              "baseline_label", "quoted_example", "position_note_citable", "gaps_citable"):
        assert e[k], k
    for k in ("mismatched_window", "bar_return_called_days", "days_without_record",
              "other_figure_beside_record", "unquoted_first_person"):
        assert not e[k], k


def test_evidence_is_stored_once_per_message_and_served_unchanged(r):
    p = r["pl6"]
    assert p["refs_hold_ids"]
    assert p["stored_bytes"] < 150_000, p["stored_bytes"]
    assert p["api_preview"][0] == 5 and p["api_preview"][1] > 100
    assert p["detail_full"] == p["api_preview"][1] and p["detail_ids_unique"]
    assert tuple(p["legacy_preview"]) == (5, 7) and p["legacy_detail"] == 7


# ── Independent review C1–C5 (2026-10-15), reviewer reproductions ─────────────

def test_c1_a_calculated_stop_is_a_candidate_and_only_a_typed_stop_is_held(r):
    p = r["pl9"]
    # Two snapshots of the same position: the candidate may fall ($226 -> lower), and the
    # answer must not present it as a maintained stop that "never moves down".
    assert "candidate is $226.00" in p["c1_first_h6"] and "unknown" in p["c1_first_h6"]
    assert "unknown" in p["c1_second_h6"] and "$226.00" not in p["c1_second_h6"]
    assert not p["c1_any_maintains_claim"]
    # With the stop the user holds, a lower candidate never lowers it, and the breach is named.
    assert p["c1_typed_stop_shown"] == "$226.00"
    assert "stays at $226.00" in p["c1_typed_kept_h6"] and "exit rule has been met" in p["c1_typed_kept_h6"]
    assert "raises the stop to $226.00" in p["c1_typed_raised_h6"]


@pytest.mark.parametrize("label", ["c2_aapl_calls", "c2_calls", "c2_150c", "c2_covered"])
def test_c2_options_positions_get_no_stock_rules_anywhere(r, label):
    p = r["pl9"][label]
    assert p["unsupported"] and p["rules"] == [] and p["fallback_rules"] == []
    assert p["calls_kept"], "the platform's calls and records are still shown"


def test_c2_the_platforms_own_calls_are_not_options(r):
    assert r["pl9"]["c2_platform_calls_ok"] and r["pl9"]["c2_platform_calls_ok2"]


def test_c3_amounts_are_read_whole_through_sizing(r):
    p = r["pl9"]
    assert "5,000 shares" in p["c3_10k"]["e6"] and "$10,000.00" in p["c3_10k"]["e6"]
    assert "750 shares" in p["c3_1_5k"]["e6"]
    assert "1,000 shares" in p["c3_lose_2k"]["e6"]
    assert "250 shares" in p["c3_500"]["e6"]
    assert "not understood" in p["c3_bad"]["refusal"] and "Each 100 shares" in p["c3_bad"]["e6"]
    assert "not understood" in p["c3_glued"]["refusal"] and "Each 100 shares" in p["c3_glued"]["e6"]


def test_c4_a_condition_word_does_not_license_a_prediction(r):
    c = r["pl9"]["c4"]
    assert not c["If you are wondering about next week, the stock will rise."]
    assert not c["When considering the evidence, it will rally."]
    assert not c["If you ask me the stock will rise."], "condition in the same clause"
    assert not c["Whether or not you agree, it will rise."]
    assert not c["It will not fall."]
    assert c["I cannot say whether it will rise."]
    assert c["The data does not say the price will rise."]


def test_c5_a_level_below_cost_is_never_called_profit(r):
    p = r["pl9"]
    for k in ("c5_loss_h5", "c5_endpoint_h5"):
        assert "partial profit" not in p[k] and "not take a profit" in p[k], k
    assert "above your entry" in p["c5_gain_h5"] and "partial profit" in p["c5_gain_h5"]
    assert "at best get back to your cost" in p["c5_even_h5"]


def test_endpoint_accepts_a_typed_stop_and_requires_an_entry_for_it(r):
    p = r["pl9"]
    assert "Your stated stop is $99.50" in p["endpoint_stop_h6"]
    assert p["endpoint_stop_without_entry"] == 400


def test_residual_options_are_blocked_in_packets_and_fallbacks(r):
    for p in r["pl10"]["options"]:
        assert p["unsupported"] and p["rules"] == [] and p["fallback"] == []


def test_residual_money_units_conflicts_and_stops(r):
    a = r["pl10"]["amounts"]
    no_account = a["I want to buy. I can lose 10%"]
    assert no_account["refused"] and no_account["risk"] is None and "Each 100 shares" in no_account["sizing"]
    pct = a["I want to buy. I can lose 10%, account 50000"]
    assert pct["risk"] is None and pct["percent"] == 10 and "500 shares" in pct["sizing"]
    assert "$5,000.00" in pct["sizing"] and "capped" in pct["sizing"]
    for question in ("I want to buy, risk $1.2.3k", "I want to buy, risk $1...", "I want to buy, risk $10kfoo",
                     "I want to buy, risk $500, actually risk $1000",
                     "I want to buy, risk $500 and I can lose $1000"):
        assert a[question]["refused"] and a[question]["risk"] is None
        assert "Each 100 shares" in a[question]["sizing"]
    stop = a["I bought 10 shares at 95, my stop is 5%"]
    assert stop["refused"] and stop["stop"] is None and "unknown" in stop["exit"]
    valid = a["I bought 10 shares at 95, my stop is 99.5"]
    assert not valid["refused"] and valid["stop"] == 99.5 and "$99.50" in valid["exit"]
    repeated = a["I want to buy, account 50000, risk $500, risk $500"]
    assert not repeated["refused"] and repeated["risk"] == 500


def test_unrelated_negation_cannot_license_forecasts_even_after_retry(r):
    p = r["pl10"]
    assert not p["forecasts"]["There is no doubt the stock will rise."]
    assert not p["forecasts"]["I do not own shares and the stock will rise."]
    assert p["forecasts"]["I cannot say whether it will rise."]
    for i in range(2):
        assert p[f"fallback_{i}"] == {"status": "rejected", "calls": 2, "has_plan": True}


def test_plan_card_prices_and_expanded_evidence_are_from_the_engine(r):
    p = r["pl11"]
    assert p["numeric"]["level.low20"] == 99 and p["numeric"]["level.high20"] == 101
    assert p["numeric"]["level.pullback_stop"] == 98 and p["all_evidence"]


def test_plan_level_drafts_are_free_confirmed_scoped_and_replay_safe(r):
    p = r["pl11"]
    assert p["no_write_before_confirm"] and p["replayed"] and p["answer_quota"] == 0
    assert p["draft"]["arguments"]["threshold"] == 98
    assert p["draft"]["threshold_source"]["key"] == "level.pullback_stop"
    assert any("daily-close" in x for x in p["draft"]["side_effects"])
    assert p["saved_once"] and p["owner"] == 404 and p["expired"] == 410
    assert p["replay_conflict"] == 409 and p["changed_plan"] == 409
    assert p["wrong_key"] == 400 and p["stale"] == 400


def test_dual_plan_uses_typed_inputs_and_exact_snapshot_with_safe_replay(r):
    x = r["pl12"]
    assert x["status"] == "grounded" and x["same_snapshot"] and x["held_stop"]
    assert x["model"] == "scripted" and x["replay"] and x["calls"] == 1 and x["context_stored"]
    assert x["changed_replay"] == 409 and x["changed_snapshot"] == 409
    assert x["invalid_risk"] == 400 and x["quota_after_denials"] == 1
    assert x["options"] == 400 and x["stale"] == 400


def test_dual_plan_retains_rejection_status_and_cannot_propose_actions(r):
    x = r["pl12"]
    assert x["fallback_status"] == "rejected" and x["fallback_same_snapshot"]
    assert x["drafts"] == []


def test_trade_setup_entry_stop_targets_sizing_and_chart_prices_are_consistent(r):
    x=r["pl13"]; pull, breakout = x["entry"]
    assert (pull["entry"],pull["stop"],pull["shares"],pull["capital"],pull["cash_at_stop"]) == (99,98,400,39600,-400)
    assert [(t["price"],t["reward_risk"]) for t in pull["targets"]] == [(101,2),(102,3)]
    assert (breakout["entry"],breakout["stop"],breakout["shares"]) == (101,99,200)
    assert [(t["price"],t["reward_risk"]) for t in breakout["targets"]] == [(105,2),(107,3)]
    assert x["target_prices"]["level.breakout_target1"] == 105
    assert x["target_draft"]["arguments"]["threshold"] == 105 and x["target_draft"]["arguments"]["condition"] == "above"
    assert x["target_draft"]["threshold_source"]["key"] == "level.breakout_target1"
    assert x["precision_bound"] and all(s["state"] == "conditional" for s in x["entry"])
    assert all(s["state"] == "blocked" and s["shares"] == 0 for s in x["tiny_budget"])


def test_held_setup_uses_higher_stop_and_does_not_invent_original_risk_or_profit_below_cost(r):
    x=r["pl13"]; held=x["held"][0]; underwater=x["underwater"][0]
    assert held["entry"] == 95 and held["stop"] == 98 and held["shares_basis"] == "held"
    assert held["risk_per_share"] is None and all(t["reward_risk"] is None for t in held["targets"])
    assert held["cash_at_stop"] == 30
    assert all(t["price"] > underwater["entry"] for t in underwater["targets"])
    assert underwater["recovery_levels"] == [{"price":101,"label":"Resistance / loss reduction, not profit"}]


def test_llm_setup_choice_requires_matching_citation_and_cannot_override_blocked_entries(r):
    x=r["pl13"]
    assert x["selected"]["status"] == "grounded" and x["selected"]["id"] == "breakout" and x["selected"]["calls"] == 2
    assert x["selected"]["setups"] == x["entry"]
    assert x["missing_tag_status"] == "rejected"
    assert all(s["state"] == "blocked" for s in x["blocked"])
    assert x["wait"] == {"id":"wait","status":"grounded"}


def test_trade_setups_enforce_first_target_rr_and_handle_roundoff_and_nonpositive_stops(r):
    x = r["pl13"]
    assert x["low_rr"][0]["state"] == "blocked" and x["low_rr"][0]["targets"][0]["reward_risk"] < 2
    assert all(s["state"] == "conditional" for s in x["decimal"])
    assert all(s["state"] == "unavailable" and s["stop"] is None and s["shares"] is None for s in x["invalid_stop"])
    assert x["invalid_held_stop"]["setups"] == [] and x["invalid_held_stop"]["reason"]
