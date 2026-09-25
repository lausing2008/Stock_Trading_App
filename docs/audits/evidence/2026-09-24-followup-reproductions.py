"""Offline review probes: actual selected source, controlled infrastructure.

Run: python docs/audits/evidence/2026-09-24-followup-reproductions.py
Requires numpy, pandas, fastapi, python-jose. No network, DB, training, or writes.
Assertions describe observed behavior, including defects; not desired regression tests.
"""
from __future__ import annotations
import ast
import copy
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace, ModuleType
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
TRAINER = 'services/ml-prediction/src/training/trainer.py'
INCOME = 'services/market-data/src/services/options_income_engine.py'
NEWS = 'services/news-intelligence/src/services/storage.py'
REPORT = {}


def tree(path):
    return ast.parse((ROOT / path).read_text())


def function(path, name):
    return next(n for n in tree(path).body if isinstance(n, ast.FunctionDef) and n.name == name)


def run(nodes, ns):
    nodes = copy.deepcopy(nodes)
    for n in nodes:
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
            n.decorator_list = [] if isinstance(n, ast.FunctionDef) else n.decorator_list
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *nodes], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), '<audited-source>', 'exec'), ns)
    return ns


def load(path, names, ns):
    nodes = [n for n in tree(path).body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    assert len(nodes) == len(names)
    return run(nodes, ns)


def augmentation_after_holdout():
    fn = function(TRAINER, 'train_model')
    start = next(i for i,n in enumerate(fn.body) if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == '_fit_X')
    nodes = fn.body[start:start+4]  # three initial assignments and augmentation if
    assert isinstance(nodes[-1], ast.If)
    # Feature values encode original session ordinals. Future outcome dates need not
    # equal test dates to violate a historical evaluation's availability boundary.
    ns = run(nodes, {'np':np, 'pd':pd, 'log':MagicMock(),
        'X_train_s':np.arange(140).reshape(-1,1), 'X_train':pd.DataFrame({'session':range(140)}),
        'y_train':pd.Series([0,1]*70), 'train_weights':np.ones(140),
        '_X_out_for_fit':pd.DataFrame({'session':range(180,200)}, index=pd.date_range('2026-09-01', periods=20)),
        '_y_out_for_fit':pd.Series([0,1]*10), 'scaler':SimpleNamespace(transform=lambda x:x)})
    assert ns['_fit_X'][-1,0] == 199
    REPORT['future_outcome_augmentation'] = {'base_training_last_session':139,
        'evaluation_start_session':170, 'augmented_training_last_session':int(ns['_fit_X'][-1,0]),
        'future_rows_appended':20, 'outcome_sample_weight':float(ns['_fit_w'][-1])}


def partial_embargo_and_threshold_fallback():
    fn = function(TRAINER, 'train_model')
    names = {'split_es','split_cal','_embargo','_embargo_es','_embargo_cal','X_train','X_es','X_cal','X_test'}
    nodes = [n for n in fn.body if (isinstance(n, ast.FunctionDef) and n.name=='_afford') or
        (isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in names for t in n.targets))]
    cases = []
    horizon_node=next(n for n in tree(TRAINER).body if isinstance(n,ast.AnnAssign) and ast.unparse(n.target)=='_HORIZON_BY_STYLE')
    horizons=ast.literal_eval(horizon_node.value)
    for h in sorted(set(horizons.values())):
        ns = run(nodes, {'X':pd.DataFrame({'session':range(200)}), 'split_train':140, 'horizon':h,'_MIN_SLICE_ROWS':10})
        cases.append({'horizon':h, 'gap':ns['_embargo_cal'], 'calibration_rows':len(ns['X_cal']),
            'test_rows':len(ns['X_test']), 'last_calibration_label_end':int(ns['X_cal'].index[-1])+h,
            'test_start':int(ns['X_test'].index[0])})
    assert cases[-1]['last_calibration_label_end'] > cases[-1]['test_start']
    start = next(i for i,n in enumerate(fn.body) if isinstance(n,ast.Assign) and ast.unparse(n.targets[0])=='_thresh_split')
    stop = next(i for i in range(start,len(fn.body)) if isinstance(fn.body[i],ast.If))
    calls=[]
    def threshold(y,p,**kw):
        calls.append(len(y)); return 0.5
    ns=run(fn.body[start:stop+1], {'np':np,'X_test':pd.DataFrame({'x':range(10)}),
        'y_test':pd.Series([0,1]*5), 'preds':np.linspace(.1,.9,10), 'symbol':'TEST','style':'GROWTH',
        '_MIN_PRECISION':.55,'_PRECISION_BY_STYLE':{},'_precision_threshold':threshold,'log':MagicMock()})
    assert calls == [10] and len(ns['y_test']) == 10
    REPORT['partial_embargo'] = cases
    REPORT['actual_horizon_registry'] = horizons
    REPORT['threshold_fallback'] = {'threshold_fit_rows':calls[0], 'reported_rows':len(ns['y_test']),
        'same_observations':True,'calibrator_minimum_rows_in_source':20}


def meta_same_session_boundary():
    fn=function('services/ml-prediction/src/training/meta_trainer.py','train_meta_model')
    sort_node=next(n for n in fn.body if isinstance(n,ast.Expr) and ast.unparse(n.value).startswith('records.sort('))
    split_node=next(n for n in fn.body if isinstance(n,ast.Assign) and ast.unparse(n.targets[0])=='split')
    records=[([0],0,date(2026,9,d)) for s in range(5) for d in range(1,5)]
    ns=run([sort_node,split_node], {'records':records,'X_raw':np.zeros((20,1))})
    last=ns['records'][ns['split']-1][2]; first=ns['records'][ns['split']][2]
    assert last==first
    REPORT['meta_boundary']={'last_train_signal':str(last),'first_validation_signal':str(first),
        'current_greater_than_guard_rejects':last>first}


def settlement_after_clock_cutoff():
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026,9,25,21,0,tzinfo=timezone.utc)
    db=MagicMock(); db.execute.return_value.first.return_value=SimpleNamespace(close=101.)
    ns=load(INCOME, {'settlement_session_is_final','_settlement_close','settle_position_economics'}, {
        'datetime':Clock,'timezone':timezone,'ZoneInfo':ZoneInfo,'_SETTLEMENT_FINALITY_ET':dt_time(16,15),
        'expected_settlement_session':lambda d:d,'select':MagicMock(),'Price':MagicMock(),
        'TimeFrame':MagicMock(),'func':MagicMock(),'log':MagicMock()})
    px,_=ns['_settlement_close'](db,1,date(2026,9,25))
    assert px==101.
    REPORT['clock_not_ingestion_finality']={'clock_et':'2026-09-25 17:00','stale_intraday_close_accepted':px,
        'bar_finality_metadata_required':False}


def news_supersession():
    payload={'headline':'Unresolved material adverse event','sentiment_label':'negative','ts':'2026-09-24T14:00:00+00:00'}
    ns=load(NEWS, {'_may_clear_negative_flag'}, {'_current_hot_payload':lambda s:payload,'timezone':timezone})
    clears=ns['_may_clear_negative_flag']('TEST',{'sentiment_label':'positive'},'2026-09-24T14:05:00+00:00')
    assert clears
    session=MagicMock(); session.execute.return_value.scalars.return_value.all.return_value=[]
    session.execute.return_value.rowcount=1
    factory=MagicMock(); factory.return_value.__enter__.return_value=session
    mark=MagicMock(); guard=MagicMock(return_value=False)
    ns=load(NEWS, {'persist_news_items'}, {'SessionLocal':factory,'select':MagicMock(),
        'RealtimeNewsItem':MagicMock(),'pg_insert':MagicMock(),'get_admin_ai_key':lambda p:'offline',
        'classify_in_batches':lambda *a:[{'sentiment_score':80,'sentiment_label':'positive','is_material':True,'category':'other'}],
        '_current_hot_sentiment':lambda s:'negative','_may_clear_negative_flag':guard,
        '_mark_hot':mark,'_clear_hot':MagicMock(),'log':MagicMock()})
    ns['persist_news_items']([{'url':'https://example.invalid/offline','symbols':['TEST'],
        'headline':'Unrelated older material positive event','published_at':'2026-09-23T10:00:00Z'}], 'offline',symbol_mode='tagged')
    assert mark.call_count==1 and guard.call_count==0
    REPORT['news_supersession']={'newer_unrelated_positive_may_clear':clears,
        'older_material_positive_overwrites':mark.call_args.args[2]=='positive','recency_guard_called':False}


def lease_expiry_overlap():
    class Redis:
        owner=None
        def set(self,k,v,**kw):
            if self.owner:return False
            self.owner=v; return True
        def eval(self,script,n,k,v):
            if self.owner==v:self.owner=None; return 1
            return 0
    redis=Redis(); active=[]; events=[]
    def work():
        if not active:
            active.append('A'); redis.owner=None  # TTL elapsed while A is still running
            nested=ns['run_options_income_step']()
            events.append('A writes after B'); active.pop(); return nested
        events.append('B writes while A active'); return {'ok':True}
    ns=load(INCOME, {'run_options_income_step','_release_income_lock'}, {'_get_income_redis':lambda:redis,
        '_INCOME_STEP_LOCK_KEY':'offline','_INCOME_STEP_LOCK_TTL':1800,'_INCOME_LOCK_RELEASE_LUA':'offline',
        '_run_options_income_step_locked':work,'log':MagicMock()})
    ns['run_options_income_step']()
    assert len(events)==2
    REPORT['lease_expiry_overlap']={'events':events,'real_redis_or_database_used':False}


def disabled_admin_token():
    from fastapi import HTTPException
    from jose import jwt, JWTError
    secret='offline-audit-only-never-production'
    ns=load('shared/common/jwt_auth.py', {'_decode_or_401','require_model_admin'}, {
        '_jwt':jwt,'JWTError':JWTError,'HTTPException':HTTPException,'Header':lambda **k:None,
        '_settings':SimpleNamespace(jwt_secret=secret),'_ALGORITHM':'HS256',
        '_check_blacklist':lambda jti:False,'_SERVICE_CLAIM':'svc'})
    token=jwt.encode({'sub':'offline-admin','role':'admin','jti':'offline','exp':int(time.time())+300},secret,algorithm='HS256')
    user=SimpleNamespace(username='offline-admin',is_active=True)
    db=MagicMock(); db.execute.return_value.scalar_one_or_none.return_value=user
    auth=load('services/market-data/src/api/auth.py', {'toggle_user'}, {'Depends':lambda x:None,
        'get_admin_user':lambda:None,'get_session':lambda:None,'select':MagicMock(),'User':MagicMock(),'HTTPException':HTTPException})
    auth['toggle_user'](user.username,SimpleNamespace(username='another-admin'),db)
    assert not user.is_active
    accepted=ns['require_model_admin']('Bearer '+token)
    assert accepted==user.username
    REPORT['disabled_admin_token']={'account_disabled_by_actual_toggle':True,'existing_admin_token_accepted':True}


def disabled_meta_reporting():
    stub=ModuleType('offline_review.meta_trainer'); calls=[]
    def meta(**kw):calls.append(kw); return .9
    stub.predict_meta=meta; sys.modules[stub.__name__]=stub
    try:
        def pred(s,m,h,style):
            p={'xgboost':.7,'lightgbm':.6,'random_forest':.5}[m]
            return {'bullish_probability':p,'confidence':40,'metrics':{'auc':.55,'buy_threshold':.5},'oos_suppressed':False}
        ns=load(TRAINER, {'predict_latest_ensemble_three'}, {'__package__':'offline_review',
            'predict_latest':pred,'_artifact_path':lambda *a:SimpleNamespace(exists=lambda:True),
            '_load_sector_and_market_cap':lambda s:('Technology',1e10),'_meta_blend_enabled':lambda:False,'log':MagicMock()})
        result=ns['predict_latest_ensemble_three']('TEST',10,'SWING')
        assert calls and result['model'].endswith('_meta') and result['model_probabilities']['meta']==.9
        assert abs(result['bullish_probability']-.59975)<1e-9
        REPORT['disabled_meta_reporting']={'model':result['model'],'meta_probability_reported':.9,
            'meta_called_despite_disabled':True,'actual_probability_without_meta':result['bullish_probability']}
    finally:del sys.modules[stub.__name__]


def bounded_stale_ask():
    db=MagicMock(); db.execute.return_value.first.return_value=SimpleNamespace(nbbo_ask=.01,as_of=date(2026,9,18))
    ns=load(INCOME, {'_latest_option_ask','short_option_liability'}, {'text':lambda q:q,
        '_MAX_ASK_AGE_DAYS':5,'timedelta':timedelta})
    ask=ns['_latest_option_ask'](db,'TEST_OPTION',as_of=date(2026,9,22))
    params=db.execute.call_args.args[1]
    assert params['floor']<=date(2026,9,18)<=params['ref']
    liability,source=ns['short_option_liability'](strategy='CASH_SECURED_PUT',strike=100,
        underlying_price=105,contracts=1,quote_ask=ask)
    assert liability==1 and source=='quote_ask'
    REPORT['bounded_stale_ask']={'quote_age_calendar_days':4,'liability':liability,'source':source,
        'timestamp_survives_query_return':False,'actual_current_time_value': 'not established by this probe'}


def cash_drawdown():
    ns=load('services/portfolio-optimizer/src/optimizers/methods.py', {'PortfolioWeights','_metrics','ai_allocation'}, {
        '__name__':__name__,'np':np,'pd':pd,'dataclass':dataclass,'field':field,
        'RISK_FREE':.04,'_CASH_RETURN':0.,'log':MagicMock(),
        '_prepare':lambda returns:(np.array([.10]),np.array([[.04]]))})
    returns=pd.DataFrame({'A':[.1,-.2,.05]})
    result=ns['ai_allocation'](returns, {'A':80}, cash_floor=.05)
    actual=ns['_metrics'](np.array([.95]),np.array([.08]),np.array([[.04]]),returns)
    assert result.expected_vol==.19  # the advertised risk scaling is fixed
    assert result.max_drawdown==-.2 and actual['max_drawdown']==-.19
    REPORT['cash_drawdown']={'cash':result.cash,'reported_drawdown':result.max_drawdown,
        'drawdown_of_returned_allocation':actual['max_drawdown'],'fixed_reported_volatility':result.expected_vol}


if __name__=='__main__':
    for probe in [augmentation_after_holdout,partial_embargo_and_threshold_fallback,
        meta_same_session_boundary,settlement_after_clock_cutoff,news_supersession,
        lease_expiry_overlap,disabled_admin_token,disabled_meta_reporting,bounded_stale_ask,cash_drawdown]:
        probe()
    print(json.dumps(REPORT,indent=2))
