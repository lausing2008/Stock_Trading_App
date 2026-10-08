"""Read-only witness against the actual pure strategy module; no network or orders.
Run from any directory: python /path/to/strategy-witness.py
This records current behavior, not assertions that the behavior is desirable.
"""
import importlib.util
import json
from datetime import date
from pathlib import Path

root = Path(__file__).resolve().parents[4]
path = root / 'services/market-data/src/services/options_strategies.py'
spec = importlib.util.spec_from_file_location('audited_options_strategies', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

def contract(strike, bid, ask, right):
    return dict(strike=strike, bid=bid, ask=ask, right=right,
                last_price=None, iv=40, oi=500)

for signal in ('SELL', 'HOLD', 'BUY'):
    matrix = module.build_strategy_matrix(
        current_price=100, stop_loss=95, take_profit=105, signal=signal,
        put_rows=[contract(90, 1, 1.2, 'put'), contract(95, 2, 2.2, 'put')],
        call_rows=[contract(100, 3, 3.2, 'call'), contract(105, 1, 1.2, 'call')],
        put_expiry='2026-11-20', call_expiry='2026-11-20', shares=None,
        iv_rank=80, today=date(2026, 10, 8),
    )
    print(json.dumps(dict(signal=signal, primary=matrix['recommendation']['primary'],
                         available_combos=list(matrix['combos']))))
print(json.dumps(dict(undated_last_trade_leg=module._leg(
    {'strike': 100, 'last_price': 3, 'right': 'call'}, 'buy', '2026-11-20', 43))))
