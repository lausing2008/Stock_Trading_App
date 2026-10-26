#!/usr/bin/env bash
set -euo pipefail
docker exec -i stockai-market-data-1 python - <<'PY'
from datetime import date
from src.services.options_strategies import build_strategy_matrix

terms = {'automatic_payoff_supported': True, 'premium_multiplier': 100}
def row(strike, right):
    return {'strike': strike, 'bid': 2.0, 'ask': 2.2, 'oi': 100,
            'option_symbol': f'EXAMPLE-{right}-{strike}', 'contract_terms': terms,
            'delta': .3 if right == 'call' else -.3}

plan = build_strategy_matrix(current_price=100, stop_loss=95, take_profit=120, signal=None,
    call_rows=[row(k, 'call') for k in (100, 105, 110, 115)], call_expiry='2026-11-20',
    put_rows=[row(k, 'put') for k in (90, 95, 100, 105)], put_expiry='2026-11-20',
    today=date(2026, 10, 26))
reviews = plan['candidate_review']
assert set(reviews) == {'long_call', 'protective_put', 'covered_call', 'cash_secured_put'}
assert all(reviews[key]['alternatives'] for key in reviews)
assert all(len(reviews[key]['alternatives']) <= 3 for key in reviews)
assert plan['trade_eligibility']['status'] == 'research_only'
print('DEPLOYED_FOUR_PRESET_SHORTLISTS=' + ','.join(f'{key}:{len(review["alternatives"])}' for key, review in reviews.items()))
PY
