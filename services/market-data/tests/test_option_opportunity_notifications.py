from datetime import datetime, timezone


def _opportunity(quantity=1):
    return {
        "opportunity_id": "opp-1", "symbol": "MU", "strategy": "long_call",
        "contract_id": "MU261218C00100000",
        "entry_deadline": "2026-10-09T19:00:00+00:00",
        "maximum_loss": 250.0, "quantity": quantity,
        "confirmation_rule": "completed close above 100",
        "invalidation_rule": "completed close below 95",
        "measurement_status": "prospective_unmeasured",
    }


def test_actionable_gate_sizes_from_both_risk_caps():
    from services.option_strategy_ledger import actionable_gate

    facts = {key: True for key in (
        "direction_compatible", "identity_verified", "deliverable_verified",
        "quotes_fresh", "two_sided_quotes", "spread_acceptable", "size_sufficient",
        "market_open", "entry_before_last_trade", "event_coverage_verified",
        "account_permissions_verified", "capital_sufficient", "portfolio_risk_checked",
    )}
    result = actionable_gate({**facts, "account_value": 100_000,
                              "open_option_risk": 700,
                              "max_loss_per_contract": 120})
    assert result["status"] == "actionable"
    assert result["risk_budget"] == 250.0
    assert result["quantity"] == 2


def test_actionable_gate_abstains_without_account_risk_inputs():
    from services.option_strategy_ledger import actionable_gate

    result = actionable_gate({})
    assert result["status"] == "research_only"
    assert result["quantity"] is None
    assert "account_permissions_verified" in result["blockers"]


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

    result = enqueue_if_eligible(object(), user=User(), opportunity=_opportunity(),
                                 gate={"status": "research_only", "quantity": None})
    assert result == {"status": "blocked", "reason": "not_actionable", "created": False}
