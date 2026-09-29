"""Offline email audit. Executes current functions with controlled dependencies.

No email, HTTP, provider, Redis, or production database calls occur. Assertions
establish defects, not the desired regression behavior. Run from any directory.
"""
from __future__ import annotations
import ast
import copy
import importlib.util
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

ROOT=Path(__file__).resolve().parents[3]
SCHED='services/market-data/src/services/scheduler.py'
EMAIL='services/market-data/src/services/email_service.py'

def load(path,name,env):
    node=copy.deepcopy(next(n for n in ast.parse((ROOT/path).read_text()).body
                           if isinstance(n,ast.FunctionDef) and n.name==name))
    node.decorator_list=[]
    body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),node]
    exec(compile(ast.fix_missing_locations(ast.Module(body=body,type_ignores=[])),str(ROOT/path),'exec'),env)
    return env[name]

spec=importlib.util.spec_from_file_location('email_audit_models',ROOT/'shared/db/models.py')
models=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=models
spec.loader.exec_module(models)

class Result:
    def __init__(self,rows): self.rows=rows
    def scalars(self): return self
    def all(self): return self.rows

class Redis:
    def __init__(self): self.data={}
    def get(self,k): return self.data.get(k)
    def set(self,k,v,nx=False,ex=None):
        if nx and k in self.data: return False
        self.data[k]=v
        return True
    def setex(self,k,ttl,v): self.data[k]=v
    def delete(self,k): self.data.pop(k,None)
    def exists(self,k): return k in self.data

def environment():
    env={k:getattr(models,k) for k in dir(models) if not k.startswith('_')}
    env.update(__package__='audit_email',datetime=datetime,date=date,timedelta=timedelta,
               timezone=timezone,ZoneInfo=ZoneInfo,time=time,json=json,text=text,
               select=select,selectinload=selectinload,log=MagicMock())
    for n in ast.parse((ROOT/SCHED).read_text()).body:
        if isinstance(n,(ast.Assign,ast.AnnAssign)):
            target=n.targets[0] if isinstance(n,ast.Assign) else n.target
            if isinstance(target,ast.Name):
                try: env[target.id]=ast.literal_eval(n.value)
                except (ValueError,TypeError): pass
    redis=Redis()
    env.update(_get_redis=lambda:redis,_record_job_status=MagicMock(),
               _settings=NS(signal_engine_url='offline-signal',market_data_url='offline-market',
                            ranking_engine_url='offline-ranking',decision_engine_url='offline-decision'))
    return env,redis

def response(data): return NS(status_code=200,json=lambda:data)

def signal_retry_loss():
    env,redis=environment()
    user=NS(id=1,email='audit@example.invalid',username='offline',notification_webhook=None)
    alert=NS(id=1,symbol='TEST',horizon='SWING',user_id=1,user=user,email=user.email,
             last_signal='BUY',last_sent_at=None,require_consensus=False,alert_mode='all')
    s=MagicMock();s.__enter__.return_value=s
    def execute(stmt,*a,**k):
        cols=stmt.column_descriptions
        if cols[0]['entity'] is models.SignalAlert:return Result([alert])
        if any(c['name']=='delisted' for c in cols):return Result([])
        return Result([('TEST',datetime.now(timezone.utc))])
    s.execute.side_effect=execute
    def get(url,**kw):
        if '/signals/outcomes/' in url:return response({'by_symbol':[]})
        if '/signals/' in url:return response({'signal':'SELL','reasons':{}})
        if '/rankings' in url:return response({'rankings':[]})
        return response({})
    send=MagicMock(return_value=True)
    env.update(SessionLocal=lambda:s,httpx=NS(get=get),_get_current_regime=lambda:'unknown',
               _rotate_for_fair_budget=lambda items,key:items,_alert_fail_counts={},
               _store_conviction=MagicMock(),_signal_cohort_stats=lambda *a:None,
               send_signal_alert_email=send,is_quota_exceeded=lambda:False)
    job=load(SCHED,'check_signal_alerts',env)
    for _ in range(5): job()
    errs=[c.kwargs.get('error') for c in env['log'].warning.call_args_list
          if c.args and c.args[0]=='signal_alert.recipient_send_error']
    assert errs==["name 'new_signal' is not defined"]*5,errs
    assert send.call_count==0 and alert.last_signal=='SELL' and alert.last_sent_at is None
    return {'iterations':5,'sender_calls':0,'errors':errs,'last_signal_after_giveup':alert.last_signal,
            'last_sent_at':alert.last_sent_at}

def plan_defects():
    env,_=environment()
    yf=ModuleType('yfinance');yf.Ticker=lambda s:NS(history=lambda **kw:pd.DataFrame({'Close':[100.0]}))
    load(SCHED,'_round_step',env)
    plan=load(SCHED,'_build_game_plan',env)
    with patch.dict(sys.modules,{'yfinance':yf}):
        bad_target=plan('TEST',{'reasons':{}},{'target_price':90},style='SWING')
        same_day=plan('TEST',{'reasons':{}},{'next_earnings_date':'2026-09-28','days_to_earnings':0},style='SWING')
    assert bad_target['take_profit'] < bad_target['stop'] < bad_target['entry1'],bad_target
    assert any('clean runway' in c for c in same_day['catalysts']),same_day
    return {'bad_target':bad_target,'earnings_today':same_day}

def technical_render():
    sent=[]
    fn=load(EMAIL,'send_price_alert_email',{'send_email':lambda *a:sent.append(a) or True})
    fn('audit@example.invalid','TEST','MACD bullish crossover',0.0,100.0,None)
    assert 'fallen below 0.0' in sent[0][1]
    assert 'MACD' not in sent[0][2]
    return {'subject':sent[0][1],'text':sent[0][3],
            'recurring_footer_says_no_repeat':'will not fire again' in sent[0][2]}

def price_delivery_loss():
    env,_=environment()
    alert=NS(id=1,symbol='TEST',condition=NS(value='above'),threshold=90.,triggered=False,
             triggered_at=None,compound_conditions=None,note=None,email='audit@example.invalid',
             webhook_url=None,user_id=None)
    s=MagicMock();s.__enter__.return_value=s
    def execute(stmt,*a,**kw):
        if stmt.column_descriptions[0]['entity'] is models.PriceAlert:
            return Result([] if alert.triggered else [alert])
        return Result([])
    s.execute.side_effect=execute
    yf=ModuleType('yfinance');yf.Tickers=lambda s:NS(tickers={'TEST':NS(fast_info=NS(last_price=100.))})
    send=MagicMock(return_value=False)
    env.update(SessionLocal=lambda:s,send_price_alert_email=send,
               _evaluate_compound_conditions=lambda *a:True)
    load(SCHED,'_is_usable_price',env)
    job=load(SCHED,'check_price_alerts',env)
    with patch.dict(sys.modules,{'yfinance':yf}):job();job()
    assert alert.triggered and send.call_count==1
    return {'delivery_failed':True,'triggered_stays_true':alert.triggered,
            'sender_calls_across_two_runs':send.call_count,'commits':s.commit.call_count}

def top3_delivery_loss():
    env,redis=environment()
    user=NS(id=1,email='audit@example.invalid')
    s=MagicMock();s.__enter__.return_value=s
    s.execute.return_value=Result([NS(user_id=1,user=user)])
    def get(url,**kw):
        if 'confidence-calibration' in url:return response({'buckets':{'SWING|BUY|US|70-85':{'win_rate':.9,'count':100}}})
        if 'rankings' in url:return response({'rankings':[{'symbol':'TEST','score':90}]})
        return response([{'symbol':'TEST','signal':'BUY','confidence':80}] if kw.get('params',{}).get('style')=='SWING' else [])
    send=MagicMock(return_value=False)
    mail=ModuleType('audit_email.email_service');mail.send_top3_conviction_email=send
    paper=ModuleType('audit_email.paper_trading_engine');paper.get_last_hk_regime=lambda:{'state':'neutral'}
    env.update(SessionLocal=lambda:s,httpx=NS(get=get),_service_token=lambda:'',
               get_last_regime=lambda:{'state':'neutral'},_filter_by_alert_pref=lambda s,r,t:r)
    job=load(SCHED,'check_top3_conviction',env)
    with patch.dict(sys.modules,{'audit_email':ModuleType('audit_email'),
           'audit_email.email_service':mail,'audit_email.paper_trading_engine':paper}):job();job()
    assert send.call_count==1 and redis.get('stockai:top3_last_composition')=='TEST:BUY'
    return {'sender_calls_across_two_runs':send.call_count,'failed_send_consumes_composition':True}

def preference_inventory():
    sched=ast.parse((ROOT/SCHED).read_text());mail=ast.parse((ROOT/EMAIL).read_text())
    rows=[]
    for builder in mail.body:
        if not isinstance(builder,ast.FunctionDef):continue
        types=[c.args[1].value for c in ast.walk(builder) if isinstance(c,ast.Call)
               and isinstance(c.func,ast.Name) and c.func.id=='_with_unsub' and isinstance(c.args[1],ast.Constant)]
        if not types:continue
        jobs=[]
        for job in sched.body:
            if not isinstance(job,ast.FunctionDef):continue
            calls=[c.func.id for c in ast.walk(job) if isinstance(c,ast.Call) and isinstance(c.func,ast.Name)]
            if builder.name in calls:jobs.append({'job':job.name,'has_preference_filter': '_filter_by_alert_pref' in calls})
        rows.append({'type':types[0],'builder':builder.name,'scheduler_jobs':jobs})
    assert next(r for r in rows if r['type']=='signal')['scheduler_jobs'][0]['has_preference_filter'] is False
    return rows

def disabled_flow_recipient():
    env,_=environment();s=MagicMock();s.__enter__.return_value=s
    user=NS(id=7,is_active=False,email='disabled@example.invalid')
    def execute(stmt,*a,**kw):
        entity=stmt.column_descriptions[0]['entity']
        return Result([user] if entity is models.User else [NS()] if entity is models.DarkPoolAlertOutcome else [])
    s.execute.side_effect=execute
    send=MagicMock(return_value=True)
    mail=ModuleType('audit_email.email_service');mail.send_email=send
    env.update(SessionLocal=lambda:s,_is_us_trading_day=lambda d:True,
               _flow_digest_window=lambda l:datetime.now(timezone.utc),
               _flow_hit_rate=lambda *a:{'rate':None,'n':0},_render_flow_digest=lambda *a:('offline','offline'))
    job=load(SCHED,'send_flow_digest',env)
    with patch.dict(sys.modules,{'audit_email':ModuleType('audit_email'),'audit_email.email_service':mail}):job()
    assert send.call_count==1,env['log'].error.call_args_list
    return {'disabled_user_received_send_attempt':True,'scope':'controlled recipient; current production has one active emailed user'}

if __name__=='__main__':
    result={name:fn() for name,fn in [('signal_retry_loss',signal_retry_loss),('plan_defects',plan_defects),
        ('technical_render',technical_render),('price_delivery_loss',price_delivery_loss),
        ('top3_delivery_loss',top3_delivery_loss),('preference_inventory',preference_inventory),
        ('disabled_flow_recipient',disabled_flow_recipient)]}
    print(json.dumps(result,indent=2,default=str))
