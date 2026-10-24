from datetime import date
import pytest
from src.services.option_search_preferences import normalize_preferences, delta_rejection
from src.services.options_strategies import _covered_call_candidates, covered_call_expiry_search

TODAY = date(2026, 1, 2)
EXP = '2026-02-06'
def row(strike, delta=None, **kw):
    return dict(strike=strike, bid=2, ask=2.2, delta=delta, option_symbol=f'EXAMPLE-{strike}',
                contract_terms={'automatic_payoff_supported': True, 'premium_multiplier': 100}, **kw)
def original(rows):
    return _covered_call_candidates(rows, 120, EXP, 35, TODAY, 100)
def search(rows, preferences=None, expiries=None):
    return covered_call_expiry_search(original=original([row(120)]), expiries=expiries or [EXP],
        today=TODAY, holding_days=14, current_price=100, read_rows=lambda _: rows, preferences=preferences)

@pytest.mark.parametrize('values', [
    {'min_dte': 0}, {'max_dte': 1096}, {'min_dte': 1.5}, {'min_dte': True},
    {'min_dte': 30, 'max_dte': 29}, {'min_abs_delta': -.1}, {'max_abs_delta': 1.1},
    {'min_abs_delta': float('nan')}, {'max_abs_delta': float('inf')},
    {'min_abs_delta': .6, 'max_abs_delta': .5},
])
def test_invalid_and_inverted_bounds_are_refused(values):
    with pytest.raises(ValueError): normalize_preferences(values)

@pytest.mark.parametrize('value,right,reason', [
    (None, 'call', 'delta_unavailable_or_invalid'), (True, 'call', 'delta_unavailable_or_invalid'),
    (float('nan'), 'call', 'delta_unavailable_or_invalid'), (1.1, 'call', 'delta_unavailable_or_invalid'),
    (-.3, 'call', 'delta_sign_mismatch'), (.3, 'put', 'delta_sign_mismatch'),
    (.29, 'call', 'outside_abs_delta_range'), (-.41, 'put', 'outside_abs_delta_range'),
    (.3, 'call', None), (-.4, 'put', None),
])
def test_absolute_delta_requires_known_consistent_values(value, right, reason):
    assert delta_rejection(value, right, normalize_preferences({'min_abs_delta': .3, 'max_abs_delta': .4})) == reason


def test_zero_is_a_valid_known_delta_and_blanks_do_not_exclude_missing_delta():
    assert delta_rejection(0, 'call', normalize_preferences({'min_abs_delta': 0, 'max_abs_delta': 0})) is None
    assert delta_rejection(None, 'call', normalize_preferences()) is None


def test_delta_preference_runs_before_the_top_three_cut_and_preserves_original():
    rows = [row(120, .9), row(119, .8), row(118, .7), row(117, .35), row(116, None), row(115, .3)]
    result = search(rows, {'min_abs_delta': .3, 'max_abs_delta': .4})
    assert [a['legs'][0]['strike'] for a in result['alternatives']] == [117, 115]
    assert result['original']['strike'] == 120
    assert result['rejected_counts'] == {'outside_abs_delta_range': 3, 'delta_unavailable_or_invalid': 1}
    assert result['search_preferences'] == normalize_preferences({'min_abs_delta': .3, 'max_abs_delta': .4})


def test_original_exclusion_runs_before_top_three_so_all_slots_can_be_used():
    result = search([row(120, .3), row(119, .3), row(118, .3), row(117, .3)])
    assert [a['legs'][0]['strike'] for a in result['alternatives']] == [119, 118, 117]


def test_dte_band_runs_before_four_expiry_cap_with_inclusive_boundaries():
    requested = []
    exps = ['2026-01-23', '2026-01-30', EXP, '2026-02-13', '2026-02-20', '2026-02-27', '2026-03-06']
    result = covered_call_expiry_search(original=original([]), expiries=exps, today=TODAY,
        holding_days=14, current_price=100, preferences={'min_dte': 35, 'max_dte': 63},
        read_rows=lambda e: requested.append(e) or [row(115, .3)])
    assert requested == [EXP, '2026-02-13', '2026-02-20', '2026-02-27']
    assert result['holding_eligible_expiry_count'] == 7 and result['eligible_expiry_count'] == 5
    assert result['failed_expiries'] == []


def test_no_expiry_in_the_band_causes_no_reads_not_a_fetch_error():
    result = covered_call_expiry_search(original=original([]), expiries=[EXP], today=TODAY,
        holding_days=14, current_price=100, preferences={'max_dte': 30},
        read_rows=lambda _: pytest.fail('must not read outside range'))
    assert result['holding_eligible_expiry_count'] == 1 and result['eligible_expiry_count'] == 0
    assert result['alternatives'] == result['failed_expiries'] == []
