"""Single-leg flow research plan from archived provider quotes; never a live fill claim."""
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from .option_strategy_ledger import parse_occ_contract


def build(flow, chains: list, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    strategy = "long_call" if flow.direction == "bullish" else "long_put"
    wanted_type = "call" if strategy == "long_call" else "put"
    blockers = ["live_two_sided_quote_and_ask_size_unavailable",
                "deliverable_unverified", "options_permission_unverified",
                "account_risk_and_buying_power_unverified"]
    if flow.fired_at is None or now - flow.fired_at.replace(tzinfo=timezone.utc) > timedelta(minutes=15):
        blockers.append("flow_event_stale")
    chosen = None
    eligible = []
    for row in chains:
        if row.symbol != flow.symbol or row.option_type != wanted_type or row.expiry is None:
            continue
        dte = (row.expiry - now.date()).days
        if not 14 <= dte <= 60:
            continue
        try:
            identity = parse_occ_contract(row.option_symbol)
        except (TypeError, ValueError):
            continue
        if (identity["root"] != flow.symbol or identity["option_type"] != wanted_type
                or identity["expiry"] != row.expiry.isoformat()
                or row.strike is None or abs(identity["strike"] - row.strike) > 0.0001):
            continue
        bid, ask = row.nbbo_bid, row.nbbo_ask
        if (bid is None or ask is None or not isfinite(bid) or not isfinite(ask)
                or bid < 0 or ask <= 0 or bid > ask):
            continue
        eligible.append(row)
    if eligible:
        chosen = min(eligible, key=lambda row: (
            abs(row.strike - flow.alert_price), row.expiry, row.option_symbol))
    if chosen is None:
        blockers.append("no_verified_archived_contract_in_14_to_60_day_window")
    else:
        blockers.append("quote_is_settled_archive")
    leg = None
    if chosen is not None:
        ask = float(chosen.nbbo_ask)
        leg = {"contract_id": chosen.option_symbol, "strategy": strategy,
               "expiry": chosen.expiry.isoformat(), "strike": chosen.strike,
               "archived_bid": chosen.nbbo_bid, "archived_ask": ask,
               "quote_as_of": chosen.as_of.isoformat(),
               "breakeven_at_expiry_before_fees": round(
                   chosen.strike + ask if strategy == "long_call" else chosen.strike - ask, 4),
               "premium_per_standard_100_share_contract_before_fees": round(ask * 100, 2),
               "contract_identity": "verified_against_stored_provider_chain",
               "deliverable": "unverified"}
    direction_word = "above" if flow.direction == "bullish" else "below"
    reverse_word = "below" if flow.direction == "bullish" else "above"
    return {"status": "research_only", "symbol": flow.symbol,
            "source_flow_contract": flow.option_chain,
            "source_flow_at": flow.fired_at.isoformat() if flow.fired_at else None,
            "observed_direction": flow.direction,
            "strategy": strategy, "leg": leg,
            "confirmation_rule": f"Completed intraday bar {direction_word} observed flow price {flow.alert_price:g}; not yet verified",
            "invalidation_rule": f"Completed intraday bar {reverse_word} observed flow price {flow.alert_price:g}; exit before expiry",
            "exit_policy": "Review at next close or before expiry; no automated fill or exercise handling",
            "blockers": blockers,
            "note": "Archived bid/ask and a 100-share illustration are research evidence only. No quantity, live premium, or profitable outcome is established."}
