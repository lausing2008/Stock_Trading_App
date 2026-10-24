"""Behavioural contract-search regressions; synthetic data, not production quote fixtures."""
from datetime import date
import random
import pytest
from src.services.options_strategies import build_strategy_matrix

TODAY = date(2026, 1, 2)
EXP = '2026-02-06'
TERMS = {'automatic_payoff_supported': True, 'premium_multiplier': 100}


def row(strike, bid=2, ask=2.2, **kw):
    return {'strike': strike, 'bid': bid, 'ask': ask, 'last_price': 0,
            'option_symbol': f'EXAMPLE-{strike}', 'contract_terms': TERMS,
            'oi': 100, 'delta': None, **kw}


def matrix(rows, **kw):
    return build_strategy_matrix(current_price=100, stop_loss=95, take_profit=120,
        signal='BUY', put_rows=[], put_expiry=None, call_rows=rows,
        call_expiry=kw.get('expiry', EXP), today=TODAY, shares=100)


def test_unpriceable_target_exposes_comparable_alternatives_without_auto_replacing_it():
    result = matrix([row(100), row(110, delta=.3, gamma=.02), row(120, 0, .1)])
    review = result['candidate_review']['covered_call']
    assert 'covered_call' not in result['singles']
    assert review['original']['strike'] == 120
    assert review['original']['reason'] == 'two_sided_quote_required'
    assert review['evaluated_count'] == 3 and review['screened_count'] == 2
    alt = review['alternatives'][0]
    assert alt['legs'][0]['strike'] == 110 and alt['legs'][0]['delta'] == .3
    assert alt['net_per_contract'] == 210 and alt['breakeven'] == 97.9
    assert alt['max_profit_per_contract'] == 1210 and alt['max_loss_per_contract'] == 9790
    assert result['recommendation']['primary'] != 'covered_call'
    assert review['anchor'] == 120 and review['actionability'] == 'research_only'


@pytest.mark.parametrize('bad,reason', [
    (row(115, 0, 2, last_price=2), 'two_sided_quote_required'),
    (row(115, 3, 2), 'crossed_quote'),
    (row(115, float('nan'), 2), 'two_sided_quote_required'),
    (row(115, True, 2), 'two_sided_quote_required'),
    (row(115, 1, 2), 'spread_exceeds_20_percent'),
    (row(115, contract_terms={}), 'unsupported_contract'),
    (row(80), 'non_positive_max_profit'),
    (row(115, 200, 210), 'invalid_payoff'),
])
def test_invalid_alternatives_are_explained_and_excluded(bad, reason):
    review = matrix([row(120, 0, .1), bad])['candidate_review']['covered_call']
    assert review['alternatives'] == []
    assert review['rejected_counts'][reason] >= 1


def test_expired_and_missing_expiry_never_offer_alternatives():
    for expiry in (None, 'nonsense', TODAY.isoformat(), '2025-12-31'):
        review = matrix([row(110), row(120)], expiry=expiry)['candidate_review']['covered_call']
        assert not review['alternatives']
        assert review['rejected_counts'] == {'invalid_or_expired_contract': 2}


def test_exact_spread_ceiling_and_deterministic_distinct_top_three():
    rows = [row(120, 0, .1), row(115, 1.8, 2.2), row(110), row(105), row(100)]
    expected = matrix(rows)['candidate_review']['covered_call']['alternatives']
    assert [s['legs'][0]['strike'] for s in expected] == [115, 110, 105]
    for seed in range(20):
        shuffled = list(rows) + [dict(rows[1])]
        random.Random(seed).shuffle(shuffled)
        assert matrix(shuffled)['candidate_review']['covered_call']['alternatives'] == expected
    too_wide = matrix([row(120, 0, .1), row(115, 1.7999, 2.2001)])
    assert not too_wide['candidate_review']['covered_call']['alternatives']


def test_missing_greeks_stay_unknown_and_no_alternative_is_a_global_best_claim():
    result = matrix([row(110, delta=float('nan'), gamma=float('inf')), row(120, 0, .1)])
    review = result['candidate_review']['covered_call']
    leg = review['alternatives'][0]['legs'][0]
    assert leg['delta'] is None and leg['gamma'] is None
    assert review['scope'] == 'all_rows_of_selected_call_expiry'
    assert 'not expected return' in review['ranking']


def test_empty_chain_has_an_explicit_no_rows_reason():
    review = matrix([])['candidate_review']['covered_call']
    assert review['original']['reason'] == 'no_chain_rows'
    assert review['evaluated_count'] == 0 and review['alternatives'] == []


@pytest.mark.parametrize('holding_days,mismatched_search', [(None, False), (14, False), (14, True)])
def test_real_route_keeps_a_response_visible_when_only_alternatives_can_be_built(monkeypatch, holding_days, mismatched_search):
    """Execute real composition and handler source; stub only DB/provider dependencies."""
    import ast
    from datetime import datetime, timezone
    from pathlib import Path
    from types import SimpleNamespace
    import importlib
    scope_module = importlib.import_module('src.services.options_scope')
    monkeypatch.setattr(scope_module, 'current_expiries', lambda expiries, *a, **k: expiries)
    source = (Path(__file__).parents[1] / 'src/api/routes.py').read_text()
    log = SimpleNamespace(warning=lambda *a, **k: None)
    namespace = {'date': date, 'datetime': datetime, 'timezone': timezone, 'log': log,
                 '__name__': 'src.api.routes', '__package__': 'src.api'}
    start = source.index('_OPTIONS_GAME_PLAN_MIN_PUT_DTE = ')
    end = source.index('\n@router.get("/{symbol}/options-game-plan")', start)
    exec(source[start:end], namespace)
    handler = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'get_options_game_plan')
    handler.decorator_list = []
    handler.returns = None
    handler.args.defaults = []
    for arg in handler.args.args:
        arg.annotation = None
    rows = [row(100, 0, .1), row(110), row(120, 0, .1)]
    reads = []
    def read_chain(*args, **kwargs):
        assert kwargs['as_of'] == TODAY
        reads.append(args[2])
        as_of = '2026-01-01' if mismatched_search and len(reads) > 2 else TODAY.isoformat()
        return SimpleNamespace(calls=rows, puts=[], as_of=as_of)
    namespace.update(_today_et=lambda: TODAY, _goal_current_price=lambda *args: 100,
                     _uw_session_expiries=lambda *args: (TODAY, [EXP]),
                     _uw_option_chain=read_chain,
                     _options_chain_rows=lambda values, symbol: values)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[handler], type_ignores=[])), '<real route>', 'exec'), namespace)
    result = namespace['get_options_game_plan']('EXAMPLE', 95, 120, None, object(), None, holding_days)
    review = result['strategy_matrix']['candidate_review']['covered_call']
    assert result['strategy_matrix']['singles'] == {}
    if mismatched_search:
        assert review['failed_expiries'] == [EXP]
        assert review['alternatives'] == []
    else:
        assert result['available'] is True
        assert review['alternatives'][0]['legs'][0]['strike'] == 110


def test_expiry_search_is_bounded_filters_holding_and_reports_failed_reads():
    from src.services.options_strategies import covered_call_expiry_search
    original = matrix([row(120, 0, .1), row(110)])['candidate_review']['covered_call']
    requested = []
    def read(expiry):
        requested.append(expiry)
        if expiry == '2026-03-06':
            raise RuntimeError('archive unavailable')
        return [row(120, option_symbol=expiry + '-120'), row(110, option_symbol=expiry + '-110')]
    expiries = ['2026-01-09', EXP, '2026-03-06', '2026-04-03', '2026-05-01', '2026-06-05']
    review = covered_call_expiry_search(original=original, expiries=list(reversed(expiries)),
        today=TODAY, holding_days=33, current_price=100, read_rows=read)
    assert requested == [EXP, '2026-03-06', '2026-04-03', '2026-05-01']
    assert review['eligible_expiry_count'] == 5
    assert review['failed_expiries'] == ['2026-03-06']
    assert len(review['completed_expiries']) == 3
    assert review['evaluated_count'] == review['screened_count'] == 6
    assert review['original'] == original['original']
    assert review['alternatives'][0]['legs'][0]['strike'] == 120
    assert all(s['legs'][0]['days_to_expiry'] >= 35 for s in review['alternatives'])
    assert review['method_version'] == 'covered_call_candidates_v2'


def test_expiry_search_no_coverage_is_distinct_from_read_failure():
    from src.services.options_strategies import covered_call_expiry_search
    original = matrix([])['candidate_review']['covered_call']
    review = covered_call_expiry_search(original=original, expiries=[EXP], today=TODAY,
        holding_days=365, current_price=100, read_rows=lambda _: pytest.fail('no read expected'))
    assert review['eligible_expiry_count'] == 0
    assert review['failed_expiries'] == review['alternatives'] == []
