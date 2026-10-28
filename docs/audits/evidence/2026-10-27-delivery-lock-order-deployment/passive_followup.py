import datetime,json,pathlib,subprocess,re
cut=json.loads(pathlib.Path('/home/ec2-user/f03-observation/release-cutovers/market-data-8dc84150.json').read_text())
started=next(c['started_at'] for c in cut['containers'] if c['name']=='/stockai-market-data-1')
def instant(value):
    # Docker uses nanoseconds; the host Python accepts only microseconds.
    value=re.sub(r'(\.\d{6})\d+',r'\1',value.replace('Z','+00:00'))
    return datetime.datetime.fromisoformat(value)
raw=subprocess.check_output(['docker','exec','stockai-redis-1','redis-cli','get','scheduler:job:check_signal_alerts'],text=True).strip()
job=json.loads(raw) if raw else {}
probe="""import json
from datetime import datetime
from sqlalchemy import text
from db import SessionLocal
with SessionLocal() as s:
 s.execute(text('SET TRANSACTION READ ONLY'));s.execute(text(\"SET LOCAL statement_timeout='10000'\"))
 n=s.scalar(text('SELECT count(*) FROM signals WHERE alert_delivery_decided_at>=:start'),{'start':datetime.fromisoformat(START.replace('Z','+00:00')).replace(tzinfo=None)})
 s.rollback()
print(json.dumps({'delivery_rows_decided_since_restart':n}))
""".replace('START',repr(started))
counts=json.loads(subprocess.check_output(['docker','exec','stockai-market-data-1','python','-c',probe],text=True,timeout=20))
logs=subprocess.run(['docker','logs','--since',started,'stockai-postgres-1'],capture_output=True,text=True,check=True,timeout=10)
record={'status':job.get('status'),'last_run':job.get('last_run'),'duration_s':job.get('duration_s'),'has_error':bool(job.get('error'))}
current=bool(record['last_run'] and instant(record['last_run'])>=instant(started))
print(json.dumps({'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'cutover_started_at':started,'normal_alert_job':record,'job_completed_after_cutover':current,**counts,'retained_deadlocks_since_restart':(logs.stdout+logs.stderr).count('ERROR:  deadlock detected'),'f03_daily_schedule_still_present':'stockai-f03-observation-daily' in subprocess.check_output(['crontab','-l'],text=True),'note':'Passive observation of normal scheduler activity; no alert job, mail or provider call was manually triggered. Still not a complete active-session contention test.'},indent=2))
