"""General company coverage through real models, producer and read endpoint; no provider calls."""
from pathlib import Path
import sys, types, json
p=Path(__file__).with_name('_price_risk_probe.py')
exec(compile(p.read_text().split('failures = []')[0], str(p), 'exec'))
for name in ('NewsImpactEvent','NewsImpactReceipt','RealtimeNewsItem'):
    setattr(db,name,getattr(models,name))
models.Base.metadata.create_all(engine, tables=[models.NewsImpactEvent.__table__,models.NewsImpactReceipt.__table__,models.RealtimeNewsItem.__table__])
from src.services.company_impact_watch import process_recent, impacts_for
from datetime import datetime,timedelta,timezone
now=datetime(2026,10,15,18,tzinfo=timezone.utc)
with Session(engine) as s:
    for i,(sym,name) in enumerate((('MSFT','Microsoft Corporation'),('NVDA','NVIDIA Corporation'),('INTC','Intel Corporation')),1):
        s.add(models.Stock(id=i,symbol=sym,name=name,active=True,market=models.Market.US,exchange=models.Exchange.NASDAQ))
    for i in (1,2,3): s.add(models.User(id=i,username=f'u{i}',password_hash='x',is_active=i!=2))
    s.flush()
    s.add(models.Watchlist(id=3,user_id=3,name='Owned list'));s.flush()
    for i in (1,2,3): s.add(models.WatchlistItem(id=i,user_id=1,stock_id=i))
    s.add(models.WatchlistItem(id=4,user_id=None,watchlist_id=3,stock_id=2))
    headline='Microsoft launches a device powered by NVIDIA and replaces Intel'
    for i,(sym,title,url,age) in enumerate([
        ('MSFT',headline,'https://source.example/a',0),('NVDA',headline,'https://other.example/b',0),
        ('MSFT','Microsoft cuts annual guidance','https://source.example/c',5),
        ('MSFT','Microsoft launches a chip','javascript:bad',0),
        ('MSFT','Microsoft launches a processor','https://[invalid',0),
        ('UNKNOWN','Unknown launches a device','https://source.example/e',0),
        ('MSFT','Microsoft weekly commentary','https://source.example/f',0)],1):
        s.add(models.RealtimeNewsItem(id=i,symbol=sym,source='test',headline=title,url=url,published_at=now.replace(tzinfo=None)-timedelta(hours=age)))
    s.commit()
    first=process_recent(s,now=now);s.commit()
    events=list(s.scalars(select(models.NewsImpactEvent)))
    product=next(e for e in events if e.title==headline)
    roles={i['symbol']:i['role'] for i in product.impacts}
    assert roles['NVDA']=='reported_supplier' and roles['INTC']=='reported_displaced_supplier'
    stocks=list(s.scalars(select(models.Stock)))
    for title in ('Microsoft launches a device not powered by NVIDIA', 'Microsoft may replace Intel'):
        inferred=impacts_for(types.SimpleNamespace(headline=title,symbol='MSFT'),stocks,'product')
        assert all(x['direction']=='unmeasured' for x in inferred), inferred
    assert all(e.evidence_status=='provider_headline_unverified' for e in events)
    assert first['events']==2 and first['unsafe_url']==2 and first['unbound']==1
    receipts=list(s.scalars(select(models.NewsImpactReceipt)))
    assert len(receipts)==3 and {r.user_id for r in receipts}=={1,3}
    assert any(r.state=='historical_no_alert' for r in receipts)
    assert not list(s.scalars(select(models.NotificationOutbox)))
    assert not list(s.scalars(select(models.AppNotification)))
    again=process_recent(s,now=now);s.commit()
    assert again['events']==0 and again['receipts']==0
    # New coverage persists the publisher's stage without promoting it to verification.
    for ident,title in ((100,'Microsoft may acquire a software company'),(101,'Microsoft announces a public offering')):
        s.add(models.RealtimeNewsItem(id=ident,symbol='MSFT',source='test',headline=title,url=f'https://source.example/{ident}',published_at=now.replace(tzinfo=None)))
    s.commit()
    expanded=process_recent(s,now=now);s.commit()
    assert expanded['events']==2 and expanded['sent']==0
    speculation=s.scalar(select(models.NewsImpactEvent).where(models.NewsImpactEvent.title=='Microsoft may acquire a software company'))
    assert speculation.evidence_status=='provider_headline_unverified'
    assert speculation.impacts[0]['statement_status']=='reported_speculation'
    assert speculation.impacts[0]['direction']=='unmeasured'
    assert 'reported speculation' in speculation.impacts[0]['reason']
    assert not list(s.scalars(select(models.NotificationOutbox)))
    assert not list(s.scalars(select(models.AppNotification)))
    # The user's relevant history must survive a wave of unrelated newer events.
    for i in range(40): s.add(models.NewsImpactEvent(event_key=f'unrelated-{i}',title='Other event',source_url='https://source.example/x',published_at=now.replace(tzinfo=None),impacts=[],evidence_status='unverified'))
    s.commit()
    db.get_session=lambda:None
    auth=types.ModuleType('src.api.auth');auth.get_current_user=lambda:None;sys.modules['src.api.auth']=auth
    from src.api.news_impacts import list_impacts
    mine=list_impacts(current=s.get(models.User,1),session=s,followed_only=True)
    assert len(mine)==4 and all(x['matched_symbols'] for x in mine)
print(json.dumps({'events':first['events'],'receipts':first['receipts'],'repeat_events':again['events'],'sent':0,'roles':roles,'scoped_history':len(mine)}))
