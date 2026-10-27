import json,subprocess,pathlib,hashlib,datetime
services=['market-data','signal-engine','api-gateway','ml-prediction','research-engine','ranking-engine','strategy-engine','technical-analysis','portfolio-optimizer','event-intelligence','news-intelligence','decision-engine']
code="import pathlib,hashlib,json; out={};\nfor name in ('src','shared'):\n p=pathlib.Path('/app')/name; h=hashlib.sha256();\n for f in sorted(p.rglob('*.py')): h.update(f.read_bytes())\n out[name]=h.hexdigest()\nprint(json.dumps(out))"
names=['stockai-'+s+'-1' for s in services]+['stockai-frontend-1','stockai-postgres-1','stockai-redis-1']
out={'captured_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'hashes':{},'containers':[]}
for svc in services:out['hashes'][svc]=json.loads(subprocess.check_output(['docker','exec','stockai-'+svc+'-1','python','-c',code],text=True,timeout=15))
for c in json.loads(subprocess.check_output(['docker','inspect',*names],text=True)):
 out['containers'].append({'name':c['Name'],'image':c['Image'],'started_at':c['State']['StartedAt'],'health':c['State'].get('Health',{}).get('Status'),'status':c['State']['Status']})
out['repo_head']=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()

probe="""import json
from datetime import datetime,timezone
import pandas as pd
from sqlalchemy import text
from db import SessionLocal
from src.api.signals_shared import _build_confidence_calibration
from src.api.calibration_safety import promotion_status
from src.generators.input_contract import daily_input_contract
with SessionLocal() as s:
 s.execute(text('SET TRANSACTION READ ONLY'));s.execute(text("SET LOCAL statement_timeout='15000'"))
 result=_build_confidence_calibration(s)
 assert result['__meta__']['evidence_counts']['verified']==0
 assert result['__meta__']['eligible_count']==0
 assert not any(k!='__meta__' for k in result)
 s.rollback()
frame=pd.DataFrame({'ts':['2026-10-27']})
mid=daily_input_contract(frame,'US',datetime(2026,10,27,14,tzinfo=timezone.utc))
closed=daily_input_contract(frame,'US',datetime(2026,10,27,22,tzinfo=timezone.utc))
hk=daily_input_contract(frame,'HK',datetime(2026,10,27,4,tzinfo=timezone.utc))
assert mid['basis']=='provisional_intraday' and closed['basis']=='session_closed'
assert hk['basis']=='provisional_intraday'
assert closed['close_ingest_verified'] is None
assert promotion_status()['status']=='research_only'
print(json.dumps({'calibration_meta':result['__meta__'],'published_bucket_count':len(result)-1,'fixed_clock_input_contract_checks':{'US_mid_session':mid['basis'],'US_after_close':closed['basis'],'HK_lunch':hk['basis'],'close_ingest_verified':closed['close_ingest_verified']},'promotion_policy':promotion_status()}))
"""
out['loaded_features']=json.loads(subprocess.check_output(['docker','exec','stockai-signal-engine-1','python','-c',probe],text=True,timeout=35))
print(json.dumps(out,indent=2))
