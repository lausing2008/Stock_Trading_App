"""Local synthetic SQLite microbenchmark; never connects to production.
Run from any directory with the repository's Python dependencies installed.
The real workflow probe creates a disposable database; benchmark uses that database only.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
from statistics import median
from time import perf_counter
from datetime import date, timedelta, timezone

if os.environ.get('RZY_PG_URL'):
    raise SystemExit('Unset RZY_PG_URL: this benchmark is local SQLite only')
root=next(p for p in Path(__file__).resolve().parents if (p/'services/market-data/tests/_little_rzy_probe.py').exists())
with contextlib.redirect_stdout(io.StringIO()):
    g=runpy.run_path(str(root/'services/market-data/tests/_little_rzy_probe.py'))
Session,models,W=g['Session'],g['models'],g['W']
select,prepare,bounds,resolve=g['select'],g['prepare_bars'],g['session_bounds'],g['resolve_ohlc']
rows=[]; day=date(2026,2,2)
while len(rows)<6000:
    span=bounds('US',day)
    if span:
        stamp,end=(t.astimezone(timezone.utc) for t in span)
        while stamp<end and len(rows)<6000:
            i=len(rows); close=100+i/1000
            rows.append(dict(id=1000000+i,stock_id=9000,timeframe=models.TimeFrame.M5,ts=stamp.replace(tzinfo=None),
                open=close-.1,high=close+.5,low=close-.5,close=close,volume=10000.,session='REGULAR'))
            stamp+=timedelta(minutes=5)
    day+=timedelta(days=1)
now=stamp+timedelta(minutes=7)
with Session() as s:
    s.add(models.Stock(id=9000,symbol='PROFILE',market=models.Market.US,exchange=models.Exchange.NASDAQ,name='Synthetic benchmark',active=True,delisted=False));s.commit()
    s.execute(models.Price.__table__.insert(),rows);s.commit()

def original(s):
    stock=s.scalar(select(models.Stock).where(models.Stock.symbol=='PROFILE'))
    raw=s.scalars(select(models.Price).where(models.Price.stock_id==stock.id,models.Price.timeframe==models.TimeFrame.M5).order_by(models.Price.ts.desc()).limit(6000)).all()
    return prepare(list(reversed(raw)),'5m',now,session_bounds=bounds,resolve_ohlc=resolve)

timings={'orm':[],'projected':[]}; identity={}
for i in range(6):
    for name in (('orm','projected') if i%2==0 else ('projected','orm')):
        with Session() as s:
            started=perf_counter()
            value=original(s) if name=='orm' else W.load_bars(s,'PROFILE','5m',now)
            elapsed=perf_counter()-started
            # Warm-up is excluded; alternating order reduces warmed-page-cache bias.
            if i: timings[name].append(elapsed)
            if name=='orm': expected=value
            else: assert value==expected
            identity[name]=sum(isinstance(x,models.Price) for x in s.identity_map.values())
a,b=(median(timings[k]) for k in ('orm','projected'))
print(json.dumps(dict(dataset='6000 synthetic regular-session 5m bars, disposable SQLite',
    adapter_outputs_equal=True,samples_per_arm=5,median_seconds=dict(orm=a,projected=b),
    projected_change_percent=(b/a-1)*100,
    limitation='Local query+adapter microbenchmark only; no full-scan, journal overhead, production-I/O or delivery-latency claim.'),indent=2))
