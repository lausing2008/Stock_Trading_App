"""Fixed-contract, close-before-expiry simulation. No stock-return proxy or quote substitution.

Only long calls/puts are supported initially. Supplied quotes are evidence, not fills.
This module is ORM-free; the admin ledger persists inputs and every resolution attempt.
"""
from __future__ import annotations
import ast
import hashlib
import json
import math
from datetime import datetime, timedelta
from pathlib import Path


DEFAULT_MAX_LOSS_PER_TRADE_PCT = 0.0025
DEFAULT_MAX_OPEN_OPTIONS_RISK_PCT = 0.01
DEFAULT_MAX_QUOTE_AGE_SECONDS = 60
DEFAULT_MAX_EVENT_AGE_SECONDS = 15 * 60
DEFAULT_MAX_SPREAD_FRACTION = 0.15


def fingerprint() -> str:
    tree = ast.parse(Path(__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str):
                node.body.pop(0)
    return hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest()


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def instant(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.utcoffset() is None:
        raise ValueError('Timezone-aware timestamps are required')
    return dt


def number(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise ValueError(f'{name} must be a finite number >= {minimum}')
    return float(value)


def validate_capture(capture: dict) -> dict:
    if capture.get('strategy') not in ('long_call', 'long_put'):
        raise ValueError('Only long_call and long_put close-before-expiry simulations are supported')
    if capture.get('origin') not in ('replay', 'prospective'):
        raise ValueError('origin must be replay or prospective')
    for key in ('symbol', 'contract_id', 'quote_source', 'confirmation_rule', 'invalidation_rule'):
        if not isinstance(capture.get(key), str) or not capture[key].strip():
            raise ValueError(f'{key} is required')
    captured, entry, exit_at, last_trade = (instant(capture[k]) for k in ('captured_at', 'entry_at', 'exit_at', 'last_trade_at'))
    if not entry < exit_at < last_trade:
        raise ValueError('Require entry < exit < last trade; expiry/exercise handling is not implemented')
    if capture['origin'] == 'prospective' and not captured < entry:
        raise ValueError('Prospective capture must precede entry')
    if capture.get('deliverable') != 'standard_100_shares' or capture.get('multiplier') != 100:
        raise ValueError('Adjusted/unknown deliverables are unsupported; multiplier must be verified explicitly')
    quantity = number(capture.get('quantity'), 'quantity', 1)
    if int(quantity) != quantity:
        raise ValueError('quantity must be an integer')
    for key in ('fee_per_contract_per_side', 'slippage_per_share_per_side'):
        number(capture.get(key), key)
    if capture.get('exit_policy') != 'scheduled_close_before_expiry':
        raise ValueError('Only scheduled_close_before_expiry is measured; trigger execution is not measured')
    digest(capture)
    return capture


def resolve(capture: dict, evidence: dict, now: datetime) -> dict:
    validate_capture(capture)
    if now.utcoffset() is None:
        raise ValueError('now must be timezone aware')
    base = {'metric': 'simulated_fixed_contract_net_pnl', 'resolver_fingerprint': fingerprint(),
            'evidence_status': 'supplied_not_independently_verified',
            'performance_eligibility': 'provisional', 'net_pnl': None,
            'execution_assumptions': 'Buy at ask, sell at bid, plus frozen adverse slippage and fees. Quotes are not fills; size, impact and queue priority are unmodelled. No early exercise or assignment path.',
            'trigger_path_measured': False}
    if now < instant(capture['exit_at']):
        return {**base, 'resolution_state': 'UNRESOLVED_HORIZON'}
    # Do not reuse a neighbouring session, roll to another contract, or fabricate a zero.
    for phase in ('entry', 'exit'):
        quote = evidence.get(phase)
        if not isinstance(quote, dict):
            return {**base, 'resolution_state': 'UNRESOLVED_QUOTE_MISSING', 'missing': phase}
        if quote.get('contract_id') != capture['contract_id'] or quote.get('source') != capture['quote_source']:
            return {**base, 'resolution_state': 'UNRESOLVED_QUOTE_IDENTITY', 'missing': phase}
        try:
            if instant(quote['quoted_at']) != instant(capture[f'{phase}_at']):
                return {**base, 'resolution_state': 'UNRESOLVED_QUOTE_TIME', 'missing': phase}
            bid, ask = number(quote['bid'], 'bid'), number(quote['ask'], 'ask')
            if ask <= 0 or bid > ask:
                raise ValueError('crossed/empty quote')
        except (KeyError, ValueError, TypeError):
            return {**base, 'resolution_state': 'UNRESOLVED_QUOTE_INVALID', 'missing': phase}
    if evidence.get('deliverable_unchanged_verified') is not True:
        return {**base, 'resolution_state': 'UNRESOLVED_DELIVERABLE'}
    quantity = capture['quantity']
    slip = capture['slippage_per_share_per_side']
    entry = evidence['entry']['ask'] + slip
    exit_price = max(0, evidence['exit']['bid'] - slip)
    fees = 2 * quantity * capture['fee_per_contract_per_side']
    debit = entry * 100 * quantity
    gross = (exit_price - entry) * 100 * quantity
    return {**base, 'resolution_state': 'RESOLVED', 'entry_price': entry,
            'exit_price': exit_price, 'entry_debit': debit, 'fees': fees,
            'gross_pnl_after_slippage': round(gross, 4), 'net_pnl': round(gross - fees, 4),
            'maximum_loss_with_reserved_exit_fee': round(debit + fees, 4)}


def actionable_gate(facts: dict) -> dict:
    """Derive one numerically-bound single-leg decision. Missing facts abstain.

    Booleans may establish evidence/permission, but they never establish price, size,
    capital or risk. Those are recomputed here from the frozen quote and account facts.
    """
    required = ('direction_compatible', 'identity_verified', 'deliverable_verified',
                'market_open', 'entry_before_last_trade', 'event_coverage_verified',
                'account_permissions_verified')
    blockers = [key for key in required if facts.get(key) is not True]
    quantity = None
    risk_budget = None
    bound = None
    try:
        symbol = str(facts['symbol']).strip().upper()
        strategy = str(facts['strategy']).strip()
        contract_id = str(facts['contract_id']).strip()
        quote_source = str(facts['quote_source']).strip()
        opportunity_id = str(facts['opportunity_id']).strip()
        confirmation_rule = str(facts['confirmation_rule']).strip()
        invalidation_rule = str(facts['invalidation_rule']).strip()
        measurement_status = str(facts['measurement_status']).strip()
        if not all((symbol, strategy, contract_id, quote_source, opportunity_id,
                    confirmation_rule, invalidation_rule, measurement_status)):
            raise ValueError('identity fields are required')
        if strategy not in ('long_call', 'long_put'):
            blockers.append('strategy_not_supported')
        if measurement_status != 'prospective_unmeasured':
            blockers.append('measurement_not_prospective_unmeasured')

        decision_at = instant(facts['decision_at'])
        quote_as_of = instant(facts['quote_as_of'])
        event_at = instant(facts['event_at'])
        entry_deadline = instant(facts['entry_deadline'])
        if quote_as_of > decision_at:
            blockers.append('quote_time_future')
        elif decision_at - quote_as_of > timedelta(seconds=DEFAULT_MAX_QUOTE_AGE_SECONDS):
            blockers.append('quote_stale')
        if event_at > decision_at:
            blockers.append('event_time_future')
        elif decision_at - event_at > timedelta(seconds=DEFAULT_MAX_EVENT_AGE_SECONDS):
            blockers.append('event_stale')
        if entry_deadline <= decision_at:
            blockers.append('entry_window_expired')

        bid = number(facts['bid'], 'bid')
        ask = number(facts['ask'], 'ask', 0.000001)
        ask_size = number(facts['ask_size_contracts'], 'ask_size_contracts')
        fee = number(facts['fee_per_contract_per_side'], 'fee_per_contract_per_side')
        slippage = number(facts['slippage_per_share_per_side'], 'slippage_per_share_per_side')
        if bid > ask:
            blockers.append('quote_crossed')
        midpoint = (bid + ask) / 2
        spread_fraction = (ask - bid) / midpoint if midpoint > 0 else math.inf
        if spread_fraction > DEFAULT_MAX_SPREAD_FRACTION:
            blockers.append('spread_too_wide')

        account_value = number(facts['account_value'], 'account_value', 0.01)
        open_option_risk = number(facts['open_option_risk'], 'open_option_risk')
        buying_power = number(facts['available_options_buying_power'], 'available_options_buying_power')
        max_loss_per_contract = round((ask + slippage) * 100 + 2 * fee, 4)
        supplied_loss = facts.get('max_loss_per_contract')
        if supplied_loss is not None and abs(number(supplied_loss, 'max_loss_per_contract', 0.01) - max_loss_per_contract) > 0.005:
            blockers.append('maximum_loss_mismatch')

        risk_budget = min(
            account_value * DEFAULT_MAX_LOSS_PER_TRADE_PCT,
            max(0.0, account_value * DEFAULT_MAX_OPEN_OPTIONS_RISK_PCT - open_option_risk),
            buying_power,
        )
        risk_quantity = math.floor(risk_budget / max_loss_per_contract)
        displayed_quantity = math.floor(ask_size)
        quantity = min(risk_quantity, displayed_quantity)
        if risk_quantity < 1:
            blockers.append('risk_budget_too_small')
        if displayed_quantity < 1:
            blockers.append('displayed_size_insufficient')
        if quantity < 1:
            quantity = None

        if quantity is not None:
            maximum_loss = round(max_loss_per_contract * quantity, 4)
            policy = {
                'max_loss_per_trade_pct_of_account': DEFAULT_MAX_LOSS_PER_TRADE_PCT,
                'max_aggregate_open_option_risk_pct_of_account': DEFAULT_MAX_OPEN_OPTIONS_RISK_PCT,
                'max_quote_age_seconds': DEFAULT_MAX_QUOTE_AGE_SECONDS,
                'max_event_age_seconds': DEFAULT_MAX_EVENT_AGE_SECONDS,
                'max_spread_fraction': DEFAULT_MAX_SPREAD_FRACTION,
                'execution_basis': 'buy_ask_plus_frozen_slippage_and_two_sided_fees',
            }
            bound = {
                'opportunity_id': opportunity_id,
                'symbol': symbol, 'strategy': strategy, 'contract_id': contract_id,
                'confirmation_rule': confirmation_rule,
                'invalidation_rule': invalidation_rule,
                'measurement_status': measurement_status,
                'quote_source': quote_source, 'quote_as_of': quote_as_of.isoformat(),
                'event_at': event_at.isoformat(), 'decision_at': decision_at.isoformat(),
                'entry_deadline': entry_deadline.isoformat(), 'bid': bid, 'ask': ask,
                'ask_size_contracts': displayed_quantity, 'quantity': quantity,
                'fee_per_contract_per_side': fee,
                'slippage_per_share_per_side': slippage,
                'max_loss_per_contract': max_loss_per_contract,
                'maximum_loss': maximum_loss,
                'risk_budget': round(risk_budget, 4),
                'account_value': account_value, 'open_option_risk': open_option_risk,
                'available_options_buying_power': buying_power,
                'risk_policy': policy,
            }
            bound['decision_fingerprint'] = digest(bound)
    except (KeyError, TypeError, ValueError):
        blockers.append('numeric_identity_or_time_evidence_missing')
        quantity = None
        bound = None
    blockers = list(dict.fromkeys(blockers))
    return {'status': 'actionable' if not blockers else 'research_only',
            'blockers': blockers, 'quantity': quantity,
            'risk_budget': round(risk_budget, 2) if risk_budget is not None else None,
            'bound_decision': bound,
            'risk_policy': {
                'max_loss_per_trade_pct_of_account': DEFAULT_MAX_LOSS_PER_TRADE_PCT,
                'max_aggregate_open_option_risk_pct_of_account': DEFAULT_MAX_OPEN_OPTIONS_RISK_PCT,
                'max_quote_age_seconds': DEFAULT_MAX_QUOTE_AGE_SECONDS,
                'max_event_age_seconds': DEFAULT_MAX_EVENT_AGE_SECONDS,
                'max_spread_fraction': DEFAULT_MAX_SPREAD_FRACTION,
            },
            'note': ('Quantity is bounded by risk, buying power and displayed ask size. '
                     'Eligibility still does not establish predictive skill or an achievable fill.')}
