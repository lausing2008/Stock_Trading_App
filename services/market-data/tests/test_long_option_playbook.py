from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from services.long_option_playbook import build


def _flow(now, direction='bullish'):
    return SimpleNamespace(symbol='NVDA', direction=direction,
        option_chain='NVDA261120C00100000', alert_price=100,
        fired_at=now.replace(tzinfo=None) - timedelta(minutes=2))


def _chain(now, option_type='call', bid=4.8, ask=5.0):
    expiry = now.date() + timedelta(days=30)
    return SimpleNamespace(symbol='NVDA', option_type=option_type, expiry=expiry,
        option_symbol=f'NVDA{expiry:%y%m%d}{"C" if option_type == "call" else "P"}00100000',
        strike=100.0, nbbo_bid=bid, nbbo_ask=ask, as_of=now.date())


def test_long_call_uses_verified_contract_identity_and_archived_payoff():
    now = datetime(2026, 10, 12, 15, tzinfo=timezone.utc)
    result = build(_flow(now), [_chain(now)], now=now)
    assert result['strategy'] == 'long_call'
    assert result['leg']['breakeven_at_expiry_before_fees'] == 105
    assert result['leg']['premium_per_standard_100_share_contract_before_fees'] == 500
    assert result['status'] == 'research_only'
    assert 'live_two_sided_quote_and_ask_size_unavailable' in result['blockers']
    assert 'deliverable_unverified' in result['blockers']


def test_bearish_flow_uses_put_and_rejects_crossed_quote():
    now = datetime(2026, 10, 12, 15, tzinfo=timezone.utc)
    result = build(_flow(now, 'bearish'), [_chain(now, 'call'), _chain(now, 'put')], now=now)
    assert result['strategy'] == 'long_put'
    assert result['leg']['breakeven_at_expiry_before_fees'] == 95
    crossed = build(_flow(now, 'bearish'), [_chain(now, 'put', bid=6, ask=5)], now=now)
    assert crossed['leg'] is None
    assert 'no_verified_archived_contract_in_14_to_60_day_window' in crossed['blockers']
