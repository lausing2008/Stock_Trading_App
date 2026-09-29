"""Offline verification of fa6095b8, separate from email defect reproductions.

Executes actual settlement functions against temporary SQLite tables and actual
label-availability blocks against controlled frames. No production/network calls.
SQLite checks read ordering, not PostgreSQL locking or isolation guarantees.
"""
from __future__ import annotations
import ast
import copy
import importlib.util
import json
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import MagicMock, patch
import pandas as pd
from sqlalchemy import create_engine, select, func, update
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[3]
INCOME = ROOT / 'services/market-data/src/services/options_income_engine.py'
TRAINER = ROOT / 'services/ml-prediction/src/training/trainer.py'
META = ROOT / 'services/ml-prediction/src/training/meta_trainer.py'

def function(path, name):
    return copy.deepcopy(next(n for n in ast.parse(path.read_text()).body
                             if isinstance(n, ast.FunctionDef) and n.name == name))

def compile_fn(node, env):
    node.decorator_list = []
    mod = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), node], type_ignores=[])
    exec(compile(ast.fix_missing_locations(mod), '<actual-audited-body>', 'exec'), env)
    return env[node.name]

def assigned(n, name):
    return isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)

def settlement():
    spec = importlib.util.spec_from_file_location('email_review_models', ROOT/'shared/db/models.py')
    m = importlib.util.module_from_spec(spec); sys.modules[spec.name] = m; spec.loader.exec_module(m)
    expiry = date(2026, 9, 25)
    with tempfile.TemporaryDirectory(prefix='email-review-settlement-') as tmp:
        engine = create_engine('sqlite:///'+str(Path(tmp)/'test.db'))
        m.Stock.__table__.create(engine); m.Price.__table__.create(engine)
        with engine.begin() as c:
            c.execute(m.Stock.__table__.insert().values(id=1, symbol='TEST', market=m.Market.US, exchange=m.Exchange.NASDAQ, name='Test'))
            c.execute(m.Price.__table__.insert().values(id=1, stock_id=1, ts=datetime(2026,9,25), timeframe=m.TimeFrame.D1, open=99.9, high=100.1, low=99, close=99.9, volume=100))
        env = dict(__package__='email_review', Stock=m.Stock, Price=m.Price, TimeFrame=m.TimeFrame,
                   select=select, func=func, log=MagicMock(), expected_settlement_session=lambda d:d,
                   settlement_session_is_final=lambda d:True, _SETTLEMENT_CORROBORATION_TOL=.005)
        compile_fn(function(INCOME, '_corroborate_settlement_close'), env)
        settle = compile_fn(function(INCOME, '_settlement_close'), env)
        reads = []
        def corroboration_session():
            # Refresh exactly once; unlike the old defect fixture, allow a retry.
            if not reads:
                with engine.begin() as c:
                    c.execute(update(m.Price).where(m.Price.id==1).values(close=100.1))
                    c.execute(m.Price.__table__.insert().values(id=2, stock_id=1, ts=datetime(2026,9,28), timeframe=m.TimeFrame.D1, open=101, high=101, low=101, close=101, volume=100))
            reads.append(1)
            return Session(engine)
        env['SessionLocal'] = corroboration_session
        provider = ModuleType('email_review.paper_trading_engine')
        provider._fetch_live_prices = MagicMock(side_effect=AssertionError('No provider expected'))
        with patch.dict(sys.modules, {'email_review':ModuleType('email_review'), 'email_review.paper_trading_engine':provider}):
            with Session(engine) as s:
                result = settle(s, 1, expiry)
        assert result == (100.1, expiry) and len(reads) == 2, (result, reads)
        return {'refreshed_close_returned':result[0], 'corroboration_reads':len(reads), 'provider_calls':provider._fetch_live_prices.call_count}

def label_blocks():
    dates = pd.bdate_range('2026-09-14', periods=20)
    dates = dates[~dates.isin(pd.to_datetime(['2026-09-16','2026-09-17','2026-09-18']))]
    usable = [dates[0], dates[-1], pd.Timestamp('2026-09-16')]
    original = function(TRAINER, '_load_outcome_features')
    start = next(i for i,n in enumerate(original.body) if assigned(n, '_bar_dates'))
    block = ast.parse('def probe(X_out, y_out):\n pass').body[0]; block.body = original.body[start:]
    env = dict(pd=pd, df=pd.DataFrame({'ts':dates}), _usable=usable, _outcome_horizon=10,
               avail_map={}, X_out=pd.DataFrame({'value':[1,2,3]}, index=usable),
               y_out=pd.Series([1,0,1], index=usable), log=MagicMock(), symbol='TEST', style='SWING')
    probe = compile_fn(block, env)
    x,y,avail = probe(env['X_out'], env['y_out'])
    assert len(x)==len(y)==len(avail)==1 and avail.iloc[0]==date(2026,10,1)
    env['avail_map']={date(2026,9,14):date(2026,10,5)}
    _,_,later=probe(env['X_out'], env['y_out'])
    assert later.iloc[0]==date(2026,10,5)
    meta_tree = ast.parse(META.read_text())
    loop = next(n for n in ast.walk(meta_tree) if isinstance(n,ast.For)
                and isinstance(n.target,ast.Name) and n.target.id=='row'
                and any(assigned(s,'_h_bars') for s in n.body))
    start = next(i for i,n in enumerate(loop.body) if assigned(n,'_h_bars'))
    wrapper = ast.parse('def probe_meta():\n for row in rows:\n  pass\n return records').body[0]
    wrapper.body[0].body = copy.deepcopy(loop.body[start:])
    menv = dict(rows=[NS(horizon='SWING',is_correct=True,signal_date=date(2026,9,14))], records=[],
                vec=[1.0], feat_ts=pd.Series(dates), row_idx=0, _HORIZON_DAYS={'SWING':10})
    meta_probe=compile_fn(wrapper, menv)
    records=meta_probe(); assert records[0][-1]==date(2026,10,1)
    menv.update(records=[],row_idx=len(dates)-1)
    assert meta_probe()==[]
    return {'trainer_target':'2026-10-01','trainer_kept':len(x),'missing_or_unprinted_rows_dropped':2,
            'later_observed_exit_preserved':str(later.iloc[0]),'meta_target':'2026-10-01',
            'meta_unprinted_target_dropped':True, 'scope':'actual availability blocks; not whole model training'}

if __name__ == '__main__':
    print(json.dumps({'settlement':settlement(), 'label_blocks':label_blocks()}, indent=2))
