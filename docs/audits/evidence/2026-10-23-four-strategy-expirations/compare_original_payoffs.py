"""Offline equivalence witness; run from repository root. No network or DB."""
import random
import subprocess
from datetime import date
from pathlib import Path
baseline = '1b3aee919e36e383c1ebd9c27afc35e0e2700ddd'
path = 'services/market-data/src/services/options_strategies.py'
old, new = {}, {}
exec(subprocess.check_output(['git', 'show', f'{baseline}:{path}'], text=True), old)
exec(Path(path).read_text(), new)
rng = random.Random(468)
for case in range(200):
    spot = rng.uniform(25, 500)
    rows = [{'strike': round(spot * scale, 2), 'bid': premium, 'ask': premium * 1.1,
             'option_symbol': f'SYNTH-{i}', 'oi': i * 10,
             'contract_terms': {'automatic_payoff_supported': True, 'premium_multiplier': 100}}
            for i, (scale, premium) in enumerate((scale, rng.uniform(.1, 15)) for scale in (.8, .9, 1, 1.1, 1.2))]
    kwargs = dict(current_price=spot, stop_loss=spot * .9, take_profit=spot * 1.2,
                  signal='BUY', put_rows=rows, call_rows=rows,
                  put_expiry='2026-03-20', call_expiry='2026-02-20',
                  today=date(2026, 1, 2), iv_rank=rng.choice([None, 20, 50, 80]))
    assert old['build_strategy_matrix'](**kwargs) == new['build_strategy_matrix'](**kwargs), case
print('200 original plan matrices identical to deployed baseline: singles, combinations, recommendation and metadata')
