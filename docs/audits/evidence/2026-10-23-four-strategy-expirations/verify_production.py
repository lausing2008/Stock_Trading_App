import json,subprocess,urllib.request
from datetime import datetime,timezone
names=subprocess.check_output(['docker','ps','--format','{{.Names}}'],text=True).splitlines()
containers=[]
for name in sorted(names):
    data=json.loads(subprocess.check_output(['docker','inspect',name],text=True))[0]
    containers.append({'name':name,'status':data['State']['Status'],'health':data['State'].get('Health',{}).get('Status'),'image':data['Image'],'started_at':data['State']['StartedAt']})
assert len(containers)==15 and all(c['health']=='healthy' for c in containers)
expected_starts={'stockai-api-gateway-1': '2026-10-22T23:47:20.048017661Z', 'stockai-decision-engine-1': '2026-10-22T23:47:17.622979137Z', 'stockai-event-intelligence-1': '2026-10-22T23:47:16.733739888Z', 'stockai-ml-prediction-1': '2026-10-22T23:47:19.009616969Z', 'stockai-news-intelligence-1': '2026-10-22T23:47:19.9974542Z', 'stockai-portfolio-optimizer-1': '2026-10-22T23:47:19.161390494Z', 'stockai-postgres-1': '2026-10-22T23:47:16.284451594Z', 'stockai-ranking-engine-1': '2026-10-22T23:47:14.512887134Z', 'stockai-redis-1': '2026-10-22T23:47:15.037867617Z', 'stockai-research-engine-1': '2026-10-22T23:47:19.964688592Z', 'stockai-signal-engine-1': '2026-10-24T01:13:24.095207013Z', 'stockai-strategy-engine-1': '2026-10-22T23:47:19.437830208Z', 'stockai-technical-analysis-1': '2026-10-22T23:47:14.056239696Z'}
assert all(c['started_at']==expected_starts[c['name']] for c in containers if c['name'] in expected_starts)

actual=subprocess.check_output(['docker','exec','stockai-frontend-1','cat','/app/.next/BUILD_ID'],text=True).strip()
expected=subprocess.check_output(['docker','run','--rm','--entrypoint','cat','stockai-frontend:release-dc5a058d','/app/.next/BUILD_ID'],text=True).strip()
assert actual==expected
checks={}
for path in ('/','/stock/TSM','/signal-tuning','/improvements','/signal-accuracy'):
    with urllib.request.urlopen('https://lausing.com'+path,timeout=30) as response:
        checks[path]=response.status
assert all(v==200 for v in checks.values())
logs=subprocess.run(['docker','logs','--since','15m','stockai-market-data-1'],capture_output=True,text=True)
ledger_errors=sum(line.count('dark_pool_ledger.capture_failed')+line.count('dark_pool_ledger.persist_failed') for line in (logs.stdout+logs.stderr).splitlines())
assert ledger_errors==0
print('PRODUCTION_RELEASE_STATUS='+json.dumps({'verified_at':datetime.now(timezone.utc).isoformat(),'application_release':'dc5a058db7ce11d016dfd1b85704966eb9ea8136','validation_commit':'dc5a058db7ce11d016dfd1b85704966eb9ea8136','containers':containers,'frontend_build_id':actual,'other_thirteen_containers_unchanged':True,'matches_prepared_frontend':True,'public_http':checks,'ledger_errors_since_restart':ledger_errors},sort_keys=True))
