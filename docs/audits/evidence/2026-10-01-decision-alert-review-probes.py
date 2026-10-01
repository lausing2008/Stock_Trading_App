"""Offline defect witnesses executing current AST-extracted production functions/blocks.
Not release acceptance tests. No DB, Redis, network or email. Fake external dependencies.
"""
import ast, copy, datetime as dt, json, pathlib, sys, types, hashlib
ROOT=pathlib.Path(__file__).resolve().parents[3]
out={}
def tree(path): return ast.parse((ROOT/path).read_text())
def fn(path,name): return next(n for n in tree(path).body if isinstance(n,ast.FunctionDef) and n.name==name)
def run(nodes,env):
 exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),*copy.deepcopy(nodes)],type_ignores=[])),'<production AST>','exec'),env)
class Clock(dt.datetime):
 instant=dt.datetime(2026,10,1,15,tzinfo=dt.timezone.utc)
 @classmethod
 def now(cls,tz=None):return cls.instant.astimezone(tz) if tz else cls.instant.replace(tzinfo=None)
 @classmethod
 def utcnow(cls):return cls.instant.replace(tzinfo=None)
class Redis:
 def __init__(self):self.v={};self.sets={}
 def get(self,k):return self.v.get(k)
 def set(self,k,v,nx=False,ex=None):
  if nx and k in self.v:return False
  self.v[k]=v;return True
 def smembers(self,k):return self.sets.get(k,set())
 def delete(self,k):self.v.pop(k,None);self.sets.pop(k,None)
 def sadd(self,k,*v):self.sets.setdefault(k,set()).update(v)
 def expire(self,*a):pass
r=Redis(); mod=types.ModuleType('common.redis_client');mod.get_redis=lambda:r
sys.modules['common']=types.ModuleType('common');sys.modules['common.redis_client']=mod
p='services/decision-engine/src/api/core/hard_rejects.py'
env={'json':json,'datetime':Clock,'timezone':dt.timezone,'_NYSE_HOLIDAYS':set(),'_MAX_ROC10_FOR_ENTRY':10,'_bar_is_incomplete':lambda m:True}
run([fn(p,'check_hard_rejects')],env); check=env['check_hard_rejects']
args=dict(signal_direction='BUY',confidence=80,live_price=100,stop_price=95,take_profit=120,regime_state='neutral',days_to_earnings=None,open_positions=0,max_positions=10,daily_pnl_pct=0,cfg={},reasons={'macro_blackout':''},symbol='TEST',style='SWING')
assert check(**args) is None
r.v['conv_gate:TEST:SWING']=json.dumps({'signal':'BUY','sent':False,'gate_passed':False,'ts':'2026-09-30T10:00:00+00:00','failed':['old signal failed']})
out['stale_conviction']=check(**args,sig_ts='2026-10-01T14:55:00+00:00');assert 'Conviction gate failed' in out['stale_conviction']
r.v.clear()
out['naive_signal_age']=check(**args,sig_ts='2026-09-20T15:00:00')
out['aware_signal_age']=check(**args,sig_ts='2026-09-20T15:00:00+00:00')
assert out['naive_signal_age'] is None and 'stale' in out['aware_signal_age']
Clock.instant=dt.datetime(2026,10,1,3,tzinfo=dt.timezone.utc)
out['hk_oct1_entry_gate']=check(**{**args,'market':'HK'});assert out['hk_oct1_entry_gate'] is None
# Execute exact per-recipient loop with fake Redis/provider; no copies of its transition logic.
p='services/market-data/src/services/scheduler.py'; f=fn(p,'check_options_flow_alerts')
loop=next(n for n in ast.walk(f) if isinstance(n,ast.For) and isinstance(n.target,ast.Tuple) and ast.unparse(n.target)=='(uid, user)')
class Log:
 def __getattr__(self,n):return lambda *a,**k:None
def exercise(candidates,cap,cooldown=False):
 r=Redis(); sent=[]
 if cooldown:r.v['stockai:options_flow_alert_cooldown:1:TEST:bullish']='1'
 env={'recipients':{1:types.SimpleNamespace(email='fake@example.invalid')},'_rc':r,'candidates':candidates,'_OPTIONS_FLOW_ALERT_COOLDOWN_MINUTES':30,'_OPTIONS_FLOW_ALERT_EMAIL_CAP':cap,'sent':0,'log':Log(),'send_options_flow_alert_email':lambda email,payload,**kw:sent.append([x['option_chain'] for x in payload]) or True}
 run([loop],env)
 return {'sent':sent,'seen':sorted(r.sets.get('stockai:options_flow_alert_seen:1',set()))}
c={'c1':{'symbol':'TEST','direction':'bullish','total_premium':100,'option_chain':'c1'}}
out['cooldown_without_send']=exercise(c,5,True)
assert not out['cooldown_without_send']['sent'] and out['cooldown_without_send']['seen']==['c1']
c['c2']={'symbol':'OTHER','direction':'bullish','total_premium':50,'option_chain':'c2'}
out['cap_omitted_seen']=exercise(c,1)
assert out['cap_omitted_seen']['sent']==[['c1']] and out['cap_omitted_seen']['seen']==['c1','c2']
# Candidate-building loop: show same-contract selection changes with provider list ordering.
loop=next(n for n in ast.walk(f) if isinstance(n,ast.For) and isinstance(n.target,ast.Name) and n.target.id=='row' and ast.unparse(n.iter)=='rows')
side=fn(p,'_classify_flow_side');direction=fn(p,'_options_flow_alert_direction')
def choose(rows):
 env={'rows':rows,'symbol':'TEST','price':100,'candidates':{},'_cal_buckets':{},'log':Log(),'_FLOW_SIDE_MIN_IMBALANCE':.65}
 run([side,direction,loop],env);return env['candidates']['same']['direction']
base=dict(option_chain='same',option_type='call',strike=100,expiry='2026-10-16',total_premium=100000,volume_oi_ratio=3,has_sweep=True,alert_rule='fixture')
new=types.SimpleNamespace(**base,total_ask_side_prem=90000,total_bid_side_prem=10000,created_at='2026-10-01T14:59:00Z')
old=types.SimpleNamespace(**base,total_ask_side_prem=10000,total_bid_side_prem=90000,created_at='2026-09-30T15:00:00Z')
out['same_contract_order']={'newest_first':choose([new,old]),'oldest_first':choose([old,new])}
assert out['same_contract_order']=={'newest_first':'bearish','oldest_first':'bullish'}
# Pure option-playbook module: actual pricing/recommendation code, synthetic quotes.
import runpy
op=runpy.run_path(str(ROOT/'services/market-data/src/services/options_strategies.py'))
make=op['build_strategy_matrix']
row=lambda strike,bid,ask:dict(strike=strike,bid=bid,ask=ask,last_price=(bid+ask)/2)
x=make(current_price=100,stop_loss=95,take_profit=105,signal='BUY',put_rows=[],put_expiry=None,call_rows=[row(100,11,13),row(105,1,3)],call_expiry='2026-11-20',shares=0,iv_rank=50,today=dt.date(2026,10,1))
out['negative_payoff_spread']={'spread':x['combos']['bull_call_spread'],'recommendation':x['recommendation']['primary']}
assert x['combos']['bull_call_spread']['max_profit_per_contract']==-500 and x['recommendation']['primary']=='bull_call_spread'
x=make(current_price=100,stop_loss=95,take_profit=110,signal='SELL',put_rows=[row(95,2,2)],put_expiry='2026-11-20',call_rows=[row(110,1,1)],call_expiry='2026-10-16',shares=100,iv_rank=50,today=dt.date(2026,10,1))
c=x['combos']['collar'];out['different_expiry_collar']=c
assert c['legs'][0]['expiry']!=c['legs'][1]['expiry'] and c['max_profit_per_contract']==900
# Delayed old material story is stamped fresh at ingestion by actual _mark_hot.
pnews='services/news-intelligence/src/services/storage.py'
class NewsRedis(Redis):
 def eval(self,script,n,key,old,payload,ttl):self.v[key]=payload;return 1
nr=NewsRedis()
env={'json':json,'datetime':Clock,'timezone':dt.timezone,'get_redis':lambda:nr,'_parse_hot':lambda v:json.loads(v) if v else None,'_HOT_NEWS_KEY_PREFIX':'hot:','_HOT_NEWS_TTL_SECONDS':7200,'_HOT_SET_IF_UNCHANGED_LUA':'fake-CAS','log':Log()}
run([fn(pnews,'_mark_hot')],env)
env['_mark_hot']('TEST','Old adverse story','negative',dt.datetime(2026,9,1,tzinfo=dt.timezone.utc))
out['late_news_fresh_flag']=json.loads(nr.v['hot:TEST'])
assert out['late_news_fresh_flag']['ts'].startswith('2026-10-01') and out['late_news_fresh_flag']['published_at'].startswith('2026-09-01')
out['source_hashes']={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in ['services/decision-engine/src/api/core/hard_rejects.py','services/market-data/src/services/scheduler.py']}
print(json.dumps(out,indent=2))
