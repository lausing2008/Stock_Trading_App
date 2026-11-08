"""Run inside market-data: synthetic positions/write capture, real optimizer HTTP; no DB writes."""
import json, math
from types import SimpleNamespace
from sqlalchemy.sql.selectable import Select
from fastapi import HTTPException
from src.api import risk_snapshots as r
# Synthetic position books and a write-capturing session: no production snapshots are saved.
# The unmodified deployed route code makes REAL HTTP calls to portfolio-optimizer.
class Session:
    def __init__(self, rows): self.rows=rows; self.writes=0; self.commits=0; self.data=None
    def execute(self, stmt):
        if isinstance(stmt, Select): return SimpleNamespace(all=lambda: self.rows)
        self.writes += 1
        self.data=stmt.compile().params
    def commit(self): self.commits += 1
user=SimpleNamespace(id=-1)
names=['AAPL','MSFT','GOOGL','AMZN','META','NVDA','TSLA','SPY','QQQ','AMD','AVGO','NFLX']
rows=[(symbol, 1, i) for i,symbol in enumerate(names, 1)]
for route, kwargs in [(r.save_var_snapshot,{}),(r.save_stress_test,{'scenario':'covid_2020'})]:
    s=Session(rows); result=route(session=s,user=user,**kwargs); c=result['coverage']
    assert c['selected_symbols']==list(reversed(names[2:])), c
    assert [p['symbol'] for p in c['excluded_positions']]==['MSFT','AAPL'], c
    assert math.isclose(c['selected_weight_pct'],75/78*100), c
    assert math.isclose(c['covered_weight_pct'],75/78*c['optimizer_coverage']['covered_weight_pct']), c
    assert s.writes==s.commits==1 and s.data['coverage']==c
    print(json.dumps({'check':route.__name__, 'selected_weight_pct':c['selected_weight_pct'], 'covered_weight_pct':c['covered_weight_pct'], 'excluded':c['excluded_positions'], 'observations':c['optimizer_coverage']['observations']}))
for route in (r.save_var_snapshot,r.save_stress_test):
    s=Session([('I06_DOES_NOT_EXIST_A',1,1),('I06_DOES_NOT_EXIST_B',1,1)])
    try: route(session=s,user=user,**({'scenario':'covid_2020'} if route is r.save_stress_test else {}))
    except HTTPException as e:
        assert e.status_code==422 and 'Insufficient price history' in e.detail, (e.status_code,e.detail)
        assert s.writes==s.commits==0
        print(json.dumps({'check':route.__name__,'status':e.status_code,'detail':e.detail}))
    else: raise AssertionError('Expected 422')
s=Session(rows)
try: r.save_stress_test(scenario='i06_invalid_scenario',session=s,user=user)
except HTTPException as e:
    assert e.status_code==400 and 'i06_invalid_scenario' in e.detail
    assert s.writes==s.commits==0
    print(json.dumps({'check':'invalid scenario','status':e.status_code,'detail':e.detail}))
else: raise AssertionError('Expected 400')
