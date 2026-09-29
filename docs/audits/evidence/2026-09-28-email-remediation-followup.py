"""Offline follow-up of c2703484. No mail/network/production writes.

Whole scheduler jobs execute with controlled external dependencies. Assertions
marked as defect witnesses demonstrate current faults, not release acceptance.
"""
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import MagicMock, patch
from sqlalchemy import update

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('old_email_probe',Path(__file__).with_name('2026-09-28-email-alert-reproductions.py'))
a=importlib.util.module_from_spec(spec);sys.modules[spec.name]=a;spec.loader.exec_module(a)

def signal_batch(raises=True):
    env,_=a.environment()
    user=NS(id=1,email='audit@example.invalid',username='offline',notification_webhook=None,is_active=True)
    alerts=[NS(id=i,symbol=s,horizon='SWING',user_id=1,user=user,email=user.email,
               last_signal='BUY',last_sent_at=None,require_consensus=False,alert_mode='all')
            for i,s in enumerate(['FIRST','SECOND'],1)]
    session=MagicMock();session.__enter__.return_value=session
    def execute(stmt,*args,**kw):
        cols=stmt.column_descriptions
        if cols[0]['entity'] is a.models.SignalAlert:return a.Result(alerts)
        if any(c['name']=='delisted' for c in cols):return a.Result([])
        return a.Result([(r.symbol,datetime.now(timezone.utc)) for r in alerts])
    session.execute.side_effect=execute
    def get(url,**kw):
        if '/signals/outcomes/' in url:return a.response({'by_symbol':[]})
        if '/signals/' in url:return a.response({'signal':'SELL','reasons':{}})
        if '/rankings' in url:return a.response({'rankings':[]})
        return a.response({})
    send=(MagicMock(side_effect=RuntimeError('controlled rendering failure')) if raises
          else MagicMock(return_value=True))
    env.update(SessionLocal=lambda:session,httpx=NS(get=get),_may_send=lambda *args:True,
               _get_current_regime=lambda:'unknown',_rotate_for_fair_budget=lambda items,key:items,
               _alert_fail_counts={},_store_conviction=MagicMock(),
               _signal_cohort_stats=MagicMock(side_effect=RuntimeError('controlled optional badge failure')),
               send_signal_alert_email=send,is_quota_exceeded=lambda:False)
    a.load(a.SCHED,'check_signal_alerts',env)()
    errors=[c.kwargs.get('error') for c in env['log'].error.call_args_list
            if c.args and c.args[0]=='signal_alert.check_error']
    if not raises:
        assert send.call_count==2 and not errors,(send.call_count,errors)
        assert all(r.last_signal=='SELL' for r in alerts)
        return {'fix_verified':True,'sender_calls_despite_badge_failure':send.call_count,
                'transitions_advanced_after_success':True}
    assert send.call_count==1 and errors and '_send_exc' in errors[0],errors
    assert all(r.last_signal=='BUY' for r in alerts)
    return {'defect_reproduced':True,'sender_calls_for_two_eligible_alerts':send.call_count,
            'outer_job_error':errors,'transitions_not_consumed':True}

def retry_price():
    env,_=a.environment();session=MagicMock();session.__enter__.return_value=session
    row=NS(id=1,symbol='RETRY',condition=NS(value='above'),threshold=90.0,
           triggered=True,triggered_at=datetime.now(timezone.utc),last_sent_at=None,
           email='audit@example.invalid',note=None,recurring=False)
    def execute(stmt,*args,**kw):
        sql=str(stmt)
        if 'UPDATE' in sql:return a.Result([])
        if 'price_alerts.triggered IS true' in sql:return a.Result([row])
        return a.Result([])
    session.execute.side_effect=execute
    sent=[];requested=[]
    def tickers(symbols):
        requested.append(symbols)
        return NS(tickers={'RETRY':NS(fast_info=NS(last_price=123.0))})
    yf=ModuleType('yfinance');yf.Tickers=tickers
    env.update(SessionLocal=lambda:session,update=update,
               send_price_alert_email=lambda **kw:sent.append(kw) or True)
    a.load(a.SCHED,'_is_usable_price',env)
    job=a.load(a.SCHED,'check_price_alerts',env)
    with patch.dict(sys.modules,{'yfinance':yf}):job()
    assert len(sent)==1 and sent[0]['price']==90.0 and requested==[''],(sent,requested)
    return {'defect_reproduced':True,'requested_symbols':requested,
            'available_provider_price':123.0,'rendered_current_price':sent[0]['price'],
            'actual_source':'configured threshold; not observed price'}

def ratchet_false_positive():
    path=ROOT/'services/market-data/tests/test_ea05_every_alert_type_is_enforced.py'
    spec=importlib.util.spec_from_file_location('prefs_ratchet',path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    # Alter only this isolated test module's in-memory source strings.
    m._SCHED='# _may_send(session, user, "brand_new_type")\n'
    m._PTE=''
    accepted=m._is_enforced('brand_new_type')
    assert accepted
    return {'defect_reproduced':True,'comment_only_source_accepted_as_enforcement':accepted,
            'scope':'isolated in-memory test inputs; repository source unmodified'}

def flow_unknown_side():
    env,_=a.environment();fn=a.load(a.SCHED,'_classify_flow_side',env)
    result=fn(100.0,None)
    assert result==('ask',1.0),result
    return {'defect_reproduced':True,'missing_bid_side':result}

if __name__=='__main__':
    result={f.__name__:f() for f in [signal_batch,retry_price,ratchet_false_positive,flow_unknown_side]}
    result['signal_success_with_failed_badge']=signal_batch(raises=False)
    print(json.dumps(result,indent=2))
