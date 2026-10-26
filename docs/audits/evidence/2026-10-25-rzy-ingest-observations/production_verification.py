import subprocess,json,pathlib,urllib.request,urllib.error
services=['market-data','signal-engine','api-gateway','ml-prediction','research-engine','ranking-engine','strategy-engine','technical-analysis','portfolio-optimizer','event-intelligence','news-intelligence','decision-engine']
code="import pathlib,hashlib,json; out={};\nfor name in ('src','shared'):\n p=pathlib.Path('/app')/name; h=hashlib.sha256();\n for f in sorted(p.rglob('*.py')): h.update(f.read_bytes())\n out[name]=h.hexdigest()\nprint(json.dumps(out))"
out={'hashes':{},'containers':[]}
for svc in services:out['hashes'][svc]=json.loads(subprocess.check_output(['docker','exec','stockai-'+svc+'-1','python','-c',code],text=True))
names=['stockai-'+s+'-1' for s in services]+['stockai-frontend-1','stockai-postgres-1','stockai-redis-1']
for c in json.loads(subprocess.check_output(['docker','inspect',*names],text=True)):
 out['containers'].append({'name':c['Name'],'image':c['Image'],'status':c['State']['Status'],'health':c['State'].get('Health',{}).get('Status'),'started_at':c['State']['StartedAt']})
before=json.loads(pathlib.Path('/home/ec2-user/deploy-evidence/2026-10-25-rzy-ingest-observations/before.json').read_text())
old={c['name']:c for c in before};out['restarted']=[c['name'] for c in out['containers'] if c['started_at']!=old[c['name']]['started_at']]
out['build_id']=subprocess.check_output(['docker','exec','stockai-frontend-1','cat','/app/.next/BUILD_ID'],text=True).strip()
out['localhost_leak']=subprocess.run(['docker','exec','stockai-frontend-1','sh','-c',"grep -rl 'localhost:8000' /app/.next/static"],capture_output=True,text=True).returncode==0
probe="""import json
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from sqlalchemy import select,text,func
from db import SessionLocal,User
from src.services import rzy_workflow as w
from src.services.rzy_models import RzyPaperJournal
from src.services.bar_ingest_observations import BarIngestObservation,BarPostCloseWrite
from src.services.rzy_monitoring import monitoring_report
from src.services.rzy_data import prepare_bars
from common.market_calendar import session_bounds
from common.price_basis import resolve_ohlc
from src.services.rzy_email import event_label
from src.api.little_rzy import admin_overview,paper
now=datetime(2026,10,26,13,42,tzinfo=timezone.utc)
row=SimpleNamespace(ts=now-timedelta(minutes=12),open=100.,high=101.,low=99.,close=100.,adj_close=None,volume=100.,session='REGULAR')
_,ev=prepare_bars([row],'5m',now,session_bounds=session_bounds,resolve_ohlc=resolve_ohlc)
assert ev['status']=='stale'
assert event_label({'event_type':'PAPER_FILL'})=='Simulated entry filled'
assert w.current_config().hash=='8ceb8cf780c3eb981983'
with SessionLocal() as s:
 s.execute(text('SET TRANSACTION READ ONLY'));s.execute(text("SET LOCAL statement_timeout='10000'"))
 n=s.scalar(select(func.count()).select_from(RzyPaperJournal))
 batches=s.scalar(select(func.count()).select_from(BarIngestObservation))
 writes=s.scalar(select(func.count()).select_from(BarPostCloseWrite))
 admin=admin_overview(_admin=None,session=s)
 user=s.scalar(select(User).where(User.username=='lausing'))
 result=paper(user=user,session=s)
 assert result['performance']['summary']['conclusion'].startswith('No demonstrated edge')
 assert not admin['monitoring']['execution_ready']
 summary=result['performance']['summary']
 assert 'ingest_observations' in admin
 missing=SimpleNamespace(symbol='PROBE',timeframe='5m',event_type='TRIGGERED',observed_in='incremental',signal_time=now-timedelta(minutes=3),first_seen_at=now,queued_at=None,occurrence_id='probe')
 check=monitoring_report([missing],[],[],now,now)['by_timeframe']['5m']
 assert check['post_close_write_missing']==1 and check['seconds_from_close']['observed_post_close_write']['median'] is None
 s.rollback()
print(json.dumps({'config_hash':w.current_config().hash,'missing_latest_bin_refused':True,'paper_fill_label':True,'journal_table_rows':n,'ingest_observation_rows':batches,'post_close_observation_rows':writes,'missing_observation_not_zero':True,'read_only_paper_api_summary':summary,'monitoring_execution_ready':False,'monitoring_streams':len(admin['monitoring']['streams'])}))
"""
out['loaded_features']=json.loads(subprocess.check_output(['docker','exec','stockai-market-data-1','python','-c',probe],text=True))
out['pages']=[]
for path in ('/','/little-rzy','/stock/MU','/improvements'):
 with urllib.request.urlopen('http://127.0.0.1:3000'+path,timeout=30) as r:
  body=r.read().decode();out['pages'].append({'path':path,'status':r.status,'build_matches':out['build_id'] in body})
out['auth_guards']={}
for path in ('/stocks/rzy/paper','/stocks/rzy/admin/overview'):
 try:
  with urllib.request.urlopen('http://127.0.0.1:8000'+path,timeout=20) as r:status=r.status
 except urllib.error.HTTPError as e:status=e.code
 out['auth_guards'][path]=status
out['compiled_ui']=json.loads(subprocess.check_output(['docker','exec','stockai-frontend-1','node','-e',"const fs=require('fs'),p='/app/.next/static/chunks/pages/'; const text=fs.readdirSync(p).filter(f=>f.startsWith('little-rzy-')&&f.endsWith('.js')).map(f=>fs.readFileSync(p+f,'utf8')).join(''); console.log(JSON.stringify({forward_performance:text.includes('Forward simulator performance'),alert_readiness:text.includes('Alert timing and readiness'),ingest_accounting:text.includes('Received, rejected and written bars')}));"],text=True))
out['repo_head']=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
print(json.dumps(out,indent=2))
