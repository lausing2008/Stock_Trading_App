from pathlib import Path
import importlib.util,sys,types,json
from datetime import datetime,timedelta
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
root=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(root/'services/signal-engine'),str(root/'shared')]
db=types.ModuleType('db');db.__path__=[str(root/'shared/db')];sys.modules['db']=db
spec=importlib.util.spec_from_file_location('db.models',root/'shared/db/models.py')
m=importlib.util.module_from_spec(spec);sys.modules['db.models']=m;spec.loader.exec_module(m)
from src.calibration_store import load_comparison
engine=create_engine('sqlite://')
m.Base.metadata.create_all(engine,tables=[m.Stock.__table__,m.Signal.__table__,m.SignalOutcomeHorizon.__table__])
now=datetime(2026,5,1);start=datetime(2026,1,1)
with Session(engine) as s:
    s.add(m.Stock(id=1,symbol='TEST',name='Test',market=m.Market.US,exchange=m.Exchange.NASDAQ));s.flush()
    for i in range(90):
        issued=start+timedelta(days=i//3)
        s.add(m.Signal(id=i+1,stock_id=1,ts=now,signal=m.SignalType.SELL,horizon=m.SignalHorizon.SWING,
                       confidence=99 if i%2 else 1,first_buy_sell_at=issued,
                       first_buy_sell_signal=m.SignalType.BUY,first_buy_sell_confidence=60));s.flush()
        s.add(m.SignalOutcomeHorizon(id=i+1,signal_id=i+1,stock_id=1,symbol='TEST',horizon=m.SignalHorizon.SWING,
            signal_direction='BUY',signal_date=issued.date(),window_days=5,horizon_unit='calendar_days',
            entry_date=(issued+timedelta(days=1)).date(),entry_price=100,target_date=(issued+timedelta(days=6)).date(),
            exit_date=(issued+timedelta(days=6)).date(),exit_price=102 if i%2 else 98,pct_return=.02 if i%2 else -.02,
            is_correct=bool(i%2),status='resolved',resolved_at=issued+timedelta(days=6),evidence_status='verified'))
    s.commit()
    first=load_comparison(s,now=now)
    assert first['eligible']==90 and first['status']=='research_only'
    assert all(x['paired_brier']>.2 for x in first['candidates'])
    for signal in s.scalars(select(m.Signal)):
        signal.confidence=1 if signal.confidence==99 else 99
    s.commit()
    second=load_comparison(s,now=now)
    assert first['candidates']==second['candidates']
    s.get(m.Signal,1).first_buy_sell_at=None;s.commit()
    missing=load_comparison(s,now=now)
    assert missing['eligible']==89 and missing['excluded']['missing_or_mismatched_prediction_snapshot']==1
print(json.dumps({'frozen_snapshot':True,'mutable_scores_ignored':True,'missing_snapshot_excluded':True,'candidates':first['candidates']}))
