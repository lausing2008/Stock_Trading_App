from copy import deepcopy


def _facts():
    booleans = {key: True for key in (
        "direction_compatible", "identity_verified", "deliverable_verified",
        "market_open", "entry_before_last_trade", "event_coverage_verified",
        "account_permissions_verified",
    )}
    return {**booleans,
        "opportunity_id": "opp-1", "symbol": "MU", "strategy": "long_call",
        "contract_id": "MU991218C00100000",
        "confirmation_rule": "completed close above 100",
        "invalidation_rule": "completed close below 95",
        "measurement_status": "prospective_unmeasured",
        "quote_source": "fixture", "decision_at": "2099-10-09T18:00:30+00:00",
        "quote_as_of": "2099-10-09T18:00:00+00:00", "event_at": "2099-10-09T17:55:00+00:00",
        "entry_deadline": "2099-10-09T19:00:00+00:00",
        "bid": 1.05, "ask": 1.15, "ask_size_contracts": 5,
        "fee_per_contract_per_side": .65, "slippage_per_share_per_side": .05,
        "account_value": 100_000, "open_option_risk": 700,
        "available_options_buying_power": 1_000,
    }


def _opportunity(gate):
    bound = gate["bound_decision"]
    return {
        "opportunity_id": "opp-1", "symbol": "MU", "strategy": "long_call",
        "contract_id": "MU991218C00100000",
        "entry_deadline": bound["entry_deadline"], "quote_source": "fixture",
        "quote_as_of": bound["quote_as_of"],
        "maximum_loss": bound["maximum_loss"], "quantity": bound["quantity"],
        "decision_fingerprint": bound["decision_fingerprint"],
        "confirmation_rule": "completed close above 100",
        "invalidation_rule": "completed close below 95",
        "measurement_status": "prospective_unmeasured",
    }


def test_actionable_gate_sizes_from_both_risk_caps():
    from services.option_strategy_ledger import actionable_gate

    result = actionable_gate(_facts())
    assert result["status"] == "actionable"
    assert result["risk_budget"] == 250.0
    assert result["quantity"] == 2
    assert result["bound_decision"]["max_loss_per_contract"] == 121.3
    assert result["bound_decision"]["maximum_loss"] == 242.6


def test_actionable_gate_derives_freshness_spread_size_and_loss():
    from services.option_strategy_ledger import actionable_gate

    cases = [
        ({"quote_as_of": "2099-10-09T17:00:00+00:00"}, "quote_stale"),
        ({"event_at": "2099-10-09T17:00:00+00:00"}, "event_stale"),
        ({"bid": .50}, "spread_too_wide"),
        ({"ask_size_contracts": 0}, "displayed_size_insufficient"),
        ({"max_loss_per_contract": 1}, "maximum_loss_mismatch"),
    ]
    for change, blocker in cases:
        result = actionable_gate({**_facts(), **change})
        assert result["status"] == "research_only"
        assert blocker in result["blockers"]


def test_actionable_gate_abstains_without_account_risk_inputs():
    from services.option_strategy_ledger import actionable_gate

    result = actionable_gate({})
    assert result["status"] == "research_only"
    assert result["quantity"] is None
    assert "account_permissions_verified" in result["blockers"]
    assert "numeric_identity_or_time_evidence_missing" in result["blockers"]


def test_new_channel_is_explicit_opt_in():
    from common.alert_prefs import ALERT_TYPES

    item = next(row for row in ALERT_TYPES if row["key"] == "option_opportunity")
    assert item["default_enabled"] is False


def test_notification_service_requires_actionable_gate_before_db_access():
    from services.option_opportunity_notifications import enqueue_if_eligible

    class User:
        id = 1
        is_active = True
        email = "user@example.com"

    result = enqueue_if_eligible(object(), user=User(), opportunity={}, decision_facts={})
    assert result == {"status": "blocked", "reason": "not_actionable", "created": False}


def test_notification_rejects_caller_tampering_before_db_access():
    from services.option_strategy_ledger import actionable_gate
    from services.option_opportunity_notifications import enqueue_if_eligible

    class User:
        id = 1
        is_active = True
        email = "user@example.com"

    gate = actionable_gate(_facts())
    valid = _opportunity(gate)
    for change, reason in [
        ({"quantity": 1}, "quantity_mismatch"),
        ({"maximum_loss": 1.0}, "maximum_loss_mismatch"),
        ({"contract_id": "OTHER"}, "bound_fields_mismatch"),
        ({"decision_fingerprint": "forged"}, "decision_fingerprint_mismatch"),
    ]:
        result = enqueue_if_eligible(object(), user=User(),
                                     opportunity={**deepcopy(valid), **change},
                                     decision_facts=_facts())
        assert result["reason"] == reason


def test_notification_recomputes_the_gate_instead_of_trusting_a_verdict():
    from services.option_strategy_ledger import actionable_gate
    from services.option_opportunity_notifications import enqueue_if_eligible

    class User:
        id = 1
        is_active = True
        email = "user@example.com"

    valid_gate = actionable_gate(_facts())
    result = enqueue_if_eligible(
        object(), user=User(), opportunity=_opportunity(valid_gate),
        decision_facts={**_facts(), "ask_size_contracts": 0},
    )
    assert result == {"status": "blocked", "reason": "not_actionable", "created": False}
