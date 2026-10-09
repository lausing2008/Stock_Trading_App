"""Execute the shared-response snapshot path; never reproduce its aggregation in tests."""
from unittest.mock import patch
import pytest
from src.services.options_flow_snapshot import compute_options_flow


def response(**over):
    return dict(available=True, cp_ratio=10., call_volume=50000, put_volume=100,
                activity_composition='call_heavy', chain_as_of='2026-10-07',
                coverage_status='unverified', expiries_used=['2026-10-09'],
                expiries_attempted=['2026-10-09'], directional_intent='not_established',
                independence_group='option_chain', source='unusual_whales_settled_chain', **over)


def test_snapshot_uses_served_composition_and_preserves_unknown_premium():
    with patch('src.services.options_flow_snapshot._fetch_flow_response', return_value=response()) as fetch:
        r = compute_options_flow('MU')
    fetch.assert_called_once_with('MU')
    assert r.cp_ratio_uncapped == 500
    assert r.cp_ratio == 10
    assert r.sentiment == 'call_heavy'
    assert r.call_premium is None and r.put_premium is None
    assert r.whale_count is None and r.top_whale_premium is None
    assert r.evidence['chain_as_of'] == '2026-10-07'
    assert r.evidence['coverage_status'] == 'unverified'


def test_partial_coverage_is_not_promoted_to_complete():
    data = response(); data['coverage_status'] = 'partial'; data['expiries_attempted'].append('2026-10-16')
    with patch('src.services.options_flow_snapshot._fetch_flow_response', return_value=data):
        r = compute_options_flow('MU')
    assert r.evidence['coverage_status'] == 'partial'
    assert len(r.evidence['expiries_attempted']) == 2
    assert len(r.evidence['expiries_used']) == 1


def test_fetch_error_is_not_empty_history():
    with patch('src.services.options_flow_snapshot._fetch_flow_response', side_effect=RuntimeError('offline')):
        assert compute_options_flow('MU') is None


def test_unavailable_is_not_a_zero_snapshot():
    with patch('src.services.options_flow_snapshot._fetch_flow_response', return_value={'available': False}):
        assert compute_options_flow('MU') is None
