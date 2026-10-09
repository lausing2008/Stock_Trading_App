from copy import deepcopy
from datetime import datetime, timezone
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from services.option_strategy_ledger import validate_capture, resolve, actionable_gate, fingerprint


def capture():
    return dict(strategy='long_call', origin='replay', symbol='TEST', contract_id='TEST-C100',
                quote_source='fixture', confirmation_rule='above 100', invalidation_rule='below 95',
                captured_at='2026-01-02T14:00:00Z', entry_at='2026-01-02T15:00:00Z',
                exit_at='2026-01-05T15:00:00Z', last_trade_at='2026-01-16T21:00:00Z',
                deliverable='standard_100_shares', multiplier=100, quantity=1,
                fee_per_contract_per_side=.65, slippage_per_share_per_side=.05,
                exit_policy='scheduled_close_before_expiry')


def evidence():
    return dict(entry=dict(contract_id='TEST-C100', source='fixture', quoted_at='2026-01-02T15:00:00Z', bid=4.8, ask=5, underlying=100),
                exit=dict(contract_id='TEST-C100', source='fixture', quoted_at='2026-01-05T15:00:00Z', bid=3, ask=3.2, underlying=102),
                deliverable_unchanged_verified=True)


NOW = datetime(2026, 2, 1, tzinfo=timezone.utc)


def test_correct_underlying_direction_can_lose_option_money():
    c, e = capture(), evidence()
    original = deepcopy((c, e))
    r = resolve(c, e, NOW)
    assert e['exit']['underlying'] > e['entry']['underlying']
    assert r['resolution_state'] == 'RESOLVED'
    assert r['net_pnl'] == -211.3
    assert r['fees'] == 1.3
    assert r['performance_eligibility'] == 'provisional'
    assert (c, e) == original
    assert resolve(c, e, NOW) == r


@pytest.mark.parametrize('mutation,state', [
    (lambda e: e.pop('exit'), 'UNRESOLVED_QUOTE_MISSING'),
    (lambda e: e['exit'].update(contract_id='OTHER'), 'UNRESOLVED_QUOTE_IDENTITY'),
    (lambda e: e['exit'].update(quoted_at='2026-01-06T15:00:00Z'), 'UNRESOLVED_QUOTE_TIME'),
    (lambda e: e['entry'].update(bid=6), 'UNRESOLVED_QUOTE_INVALID'),
    (lambda e: e['entry'].update(ask=float('nan')), 'UNRESOLVED_QUOTE_INVALID'),
    (lambda e: e.update(deliverable_unchanged_verified=False), 'UNRESOLVED_DELIVERABLE'),
])
def test_missing_or_bad_evidence_abstains(mutation, state):
    e = evidence(); mutation(e)
    r = resolve(capture(), e, NOW)
    assert r['resolution_state'] == state
    assert r['net_pnl'] is None


def test_no_early_resolution():
    assert resolve(capture(), evidence(), datetime(2026, 1, 4, tzinfo=timezone.utc))['resolution_state'] == 'UNRESOLVED_HORIZON'


@pytest.mark.parametrize('change', [dict(multiplier=150), dict(strategy='cash_secured_put'),
    dict(quantity=1.5), dict(origin='prospective', captured_at='2026-01-03T15:00:00Z'),
    dict(exit_at='2026-01-16T21:00:00Z'), dict(fee_per_contract_per_side=-1)])
def test_unsupported_contract_or_policy_is_rejected(change):
    c = capture(); c.update(change)
    with pytest.raises(ValueError): validate_capture(c)


def test_unknown_account_and_quotes_never_actionable():
    r = actionable_gate({})
    assert r['status'] == 'research_only'
    assert 'quotes_fresh' in r['blockers']
    assert 'capital_sufficient' in r['blockers']
    assert r['quantity'] is None
    assert len(fingerprint()) == 64
