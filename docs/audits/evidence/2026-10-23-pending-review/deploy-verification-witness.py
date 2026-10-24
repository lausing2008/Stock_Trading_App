import importlib.util,json,tempfile
from pathlib import Path
repo=Path(__file__).resolve().parents[4]
spec=importlib.util.spec_from_file_location('host_tests',repo/'scripts/tests/test_host_lock.py')
t=importlib.util.module_from_spec(spec);spec.loader.exec_module(t)
results=[]
for case in ('unhealthy','missing'):
 with tempfile.TemporaryDirectory(prefix='pending-deploy-review-') as d:
  e=t.env.__wrapped__(Path(d))
  docker=Path(d)/'bin/docker'
  script=docker.read_text()
  if case=='unhealthy':
   script=script.replace('echo "Up 1 minute (healthy)"','echo "Up 1 minute (unhealthy)"')
  else:
   script=script.replace('echo "Up 1 minute (healthy)"',':').replace('echo "stockai-x-1"',':')
  docker.write_text(script)
  e['env']['FAKE_HEALTH_STATE']='running/unhealthy' if case=='unhealthy' else 'missing'
  r=t.run(['bash',str(t.REMOTE),'signal-engine'],e['env'])
  results.append({'case':case,'exit':r.returncode,'reported_ok': 'signal-engine: Up 1 minute (unhealthy)' in r.stdout,'verification_warning':('one or more services are not healthy' in r.stdout or 'deployment verification failed' in r.stdout),'status':e['status'].read_text().strip()})
print(json.dumps(results,indent=2))
