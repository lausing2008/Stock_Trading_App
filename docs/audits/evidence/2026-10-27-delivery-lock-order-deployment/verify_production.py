import json,subprocess,pathlib,hashlib,datetime
services=['market-data','signal-engine','api-gateway','ml-prediction','research-engine','ranking-engine','strategy-engine','technical-analysis','portfolio-optimizer','event-intelligence','news-intelligence','decision-engine']
code="import pathlib,hashlib,json; out={};\nfor name in ('src','shared'):\n p=pathlib.Path('/app')/name; h=hashlib.sha256();\n for f in sorted(p.rglob('*.py')): h.update(f.read_bytes())\n out[name]=h.hexdigest()\nprint(json.dumps(out))"
names=['stockai-'+s+'-1' for s in services]+['stockai-frontend-1','stockai-postgres-1','stockai-redis-1']
out={'captured_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'hashes':{},'containers':[]}
for svc in services:out['hashes'][svc]=json.loads(subprocess.check_output(['docker','exec','stockai-'+svc+'-1','python','-c',code],text=True,timeout=15))
for c in json.loads(subprocess.check_output(['docker','inspect',*names],text=True)):
 out['containers'].append({'name':c['Name'],'image':c['Image'],'started_at':c['State']['StartedAt'],'health':c['State'].get('Health',{}).get('Status'),'status':c['State']['Status']})
out['repo_head']=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()

out['cutover']=json.loads(pathlib.Path('/home/ec2-user/f03-observation/release-cutovers/market-data-8dc84150.json').read_text())
started=next(c['started_at'] for c in out['cutover']['containers'] if c['name']=='/stockai-market-data-1')
probe="""import ast,json
from pathlib import Path
from datetime import datetime
from sqlalchemy import select,text
from db import SessionLocal,Signal
from src.services.ai_signal_delivery_write import canonical_write_order,persist_delivery_decisions,GENERATION_HORIZON_ORDER
started=datetime.fromisoformat(__STARTED__.replace('Z','+00:00')).replace(tzinfo=None)
with SessionLocal() as s:
 s.execute(text('SET TRANSACTION READ ONLY'));s.execute(text("SET LOCAL statement_timeout='10000'"))
 rows=s.execute(select(Signal.id,Signal.stock_id,Signal.horizon).order_by(Signal.id.desc()).limit(64)).all()
 ordered=canonical_write_order(rows);by_id={r.id:r for r in rows};rank={h:i for i,h in enumerate(GENERATION_HORIZON_ORDER)}
 keys=[(by_id[i].stock_id,rank[by_id[i].horizon.value]) for i in ordered]
 assert keys==sorted(keys)
 assert persist_delivery_decisions(s,Signal,{},decided_at=started,version='audit-noop')==0
 n=s.scalar(text('SELECT count(*) FROM signals WHERE alert_delivery_decided_at>=:start'),{'start':started})
 s.rollback()
source=Path('/app/src/services/scheduler.py').read_text();fn=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='check_signal_alerts');body=ast.unparse(fn)
assert '_persist_ai_delivery(session, Signal, _ai_delivery' in body
assert '_ai_delivery.items()' not in body
print(json.dumps({'loaded_generation_order':GENERATION_HORIZON_ORDER,'read_only_metadata_order_checked_rows':len(rows),'sender_calls_helper':True,'empty_decisions_noop':True,'delivery_metadata_rows_decided_since_restart':n}))
""".replace('__STARTED__',repr(started))
out['loaded_features']=json.loads(subprocess.check_output(['docker','exec','stockai-market-data-1','python','-c',probe],text=True,timeout=25))
record=subprocess.check_output(['docker','exec','stockai-redis-1','redis-cli','get','scheduler:job:check_signal_alerts'],text=True,timeout=10).strip()
out['alert_job_status']=json.loads(record) if record else None
logs=subprocess.run(['docker','logs','--since',started,'stockai-postgres-1'],capture_output=True,text=True,timeout=15,check=True)
out['deadlocks_in_retained_post_restart_logs']=(logs.stdout+logs.stderr).count('ERROR:  deadlock detected')
out['measurement_note']='Immediate quiet-window check, not a full-session contention or zero-deadlock guarantee. No alert job was manually invoked.'
print(json.dumps(out,indent=2))
