"""Real detector, real owned watchlists and real SQLite; no network or mail."""
from pathlib import Path
import json
p=Path(__file__).with_name('_price_risk_probe.py')
exec(compile(p.read_text().split('# 1. A qualifying decline')[0],str(p),'exec'))
build({'SPY':[100,100,100,100,100,97.5]},opt_in=(ALERT_TYPE_EMAIL,ALERT_TYPE_IN_APP))
with Session(engine,autoflush=False) as s:
    # Membership attached to an owned list, without the legacy direct user_id.
    s.add(models.Watchlist(id=1,user_id=1,name='Owned benchmarks'));s.flush()
    member=s.get(models.WatchlistItem,1);member.user_id=None;member.watchlist_id=1;s.commit()
    stock_run=prw.detect(s,now=NOW,redis_client=Redis(live=True));s.commit()
    assert stock_run['events']==0,stock_run  # 2.5% does not hit a stock's 3% first rung.
    run=prw.detect(s,now=NOW,redis_client=Redis(live=True),scope='benchmark');s.commit()
    events=list(s.scalars(select(models.RiskEvent)))
    move=next(e for e in events if e.subject=='SPY' and e.rule=='session_decline')
    assert move.scope=='benchmark' and move.delivery_mode=='shadow'
    assert move.evidence['ladder']==[2.,3.,5.,8.] and 'ETF' in move.evidence['instrument']
    assert run['subjects']==3 and run['exclusions']=={'no_stock_record':2},run
    receipts=list(s.scalars(select(models.RiskEventReceipt)))
    assert len(receipts)==1 and receipts[0].user_id==1 and receipts[0].state=='shadow_suppressed'
    assert not list(s.scalars(select(models.NotificationOutbox)))
    assert not list(s.scalars(select(models.AppNotification)))
    count=len(events)
    repeat=prw.detect(s,now=NOW,redis_client=Redis(live=True),scope='benchmark');s.commit()
    assert repeat['events']==0 and len(list(s.scalars(select(models.RiskEvent))))==count
    # A recovery closes the BENCHMARK episode instead of looking up symbol scope.
    assert prw.close_recovered_episodes(s,'SPY',[(TODAY,100.)],'shadow',TODAY,scope='benchmark')==1
    s.commit();assert move.evidence['episode_closed']
build({'NVDA':[100,100,100,100,100,94]},opt_in=(ALERT_TYPE_EMAIL,ALERT_TYPE_IN_APP))
with Session(engine,autoflush=False) as s:
    member=s.get(models.WatchlistItem,1);member.user_id=None;member.watchlist_id=1;s.commit()
    result=prw.detect(s,now=NOW,redis_client=Redis(live=True));s.commit()
    assert result['emails']==1 and result['in_app']==1,result
    receipts=list(s.scalars(select(models.RiskEventReceipt)))
    assert receipts and {r.user_id for r in receipts}=={1}
print(json.dumps({'benchmark_shadow':True,'owned_list_audience':True,'recovery_scope':True,'repeat_dedup':True}))
