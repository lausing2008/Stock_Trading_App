"""Fixed-contract, close-before-expiry simulation. No stock-return proxy or quote substitution.

Only long calls/puts are supported initially. Supplied quotes are evidence, not fills.
This module is ORM-free; the admin ledger persists inputs and every resolution attempt.
"""
from __future__ import annotations
import ast
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path


DEFAULT_MAX_LOSS_PER_TRADE_PCT = 0.0025
DEFAULT_MAX_OPEN_OPTIONS_RISK_PCT = 0.01


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
    """Promotion is explicit and independent of a research score. Missing facts abstain."""
    required = ('direction_compatible', 'identity_verified', 'deliverable_verified',
                'quotes_fresh', 'two_sided_quotes', 'spread_acceptable', 'size_sufficient',
                'market_open', 'entry_before_last_trade', 'event_coverage_verified',
                'account_permissions_verified', 'capital_sufficient', 'portfolio_risk_checked')
    blockers = [key for key in required if facts.get(key) is not True]
    account_value = facts.get('account_value')
    open_option_risk = facts.get('open_option_risk')
    max_loss_per_contract = facts.get('max_loss_per_contract')
    quantity = None
    risk_budget = None
    if not blockers:
        try:
            account_value = number(account_value, 'account_value', 0.01)
            open_option_risk = number(open_option_risk, 'open_option_risk')
            max_loss_per_contract = number(max_loss_per_contract, 'max_loss_per_contract', 0.01)
            risk_budget = min(
                account_value * DEFAULT_MAX_LOSS_PER_TRADE_PCT,
                max(0.0, account_value * DEFAULT_MAX_OPEN_OPTIONS_RISK_PCT - open_option_risk),
            )
            quantity = math.floor(risk_budget / max_loss_per_contract)
            if quantity < 1:
                blockers.append('risk_budget_too_small')
                quantity = None
        except (TypeError, ValueError):
            blockers.append('risk_inputs_verified')
    return {'status': 'actionable' if not blockers else 'research_only',
            'blockers': blockers, 'quantity': quantity,
            'risk_budget': round(risk_budget, 2) if risk_budget is not None else None,
            'risk_policy': {
                'max_loss_per_trade_pct_of_account': DEFAULT_MAX_LOSS_PER_TRADE_PCT,
                'max_aggregate_open_option_risk_pct_of_account': DEFAULT_MAX_OPEN_OPTIONS_RISK_PCT,
            },
            'note': 'Eligibility checks do not establish predictive skill or an achievable fill.'}
