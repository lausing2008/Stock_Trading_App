import json,shutil,subprocess,tempfile
from pathlib import Path
repo=Path(__file__).resolve().parents[4]
results=[]
for name in ('W01','W02','W04'):
 with tempfile.TemporaryDirectory(prefix='pending-fix-mutation-') as d:
  root=Path(d)
  if name in ('W01','W02'):
   for rel in ('scripts/deploy_remote.sh','scripts/deploy.sh','scripts/deploy_lock.sh','scripts/pg_backup.sh','scripts/tests/test_host_lock.py'):
    dest=root/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(repo/rel,dest)
   script=root/'scripts/deploy_remote.sh';s=script.read_text()
   if name=='W01': s=s.replace('elif [[ "$st" == "running/healthy" ]]','elif [[ "$st" == *healthy* ]]')
   else: s=s.replace('return "$result"','return 0')
   script.write_text(s)
   args=['scripts/tests/test_host_lock.py','-k','test_failed_health_fails_deployment_and_status_even_after_pruning']
  else:
   for rel in ('services/signal-engine/src/api/analytics.py','services/signal-engine/tests/test_filter_audit_empty_cohorts.py'):
    dest=root/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(repo/rel,dest)
   script=root/'services/signal-engine/src/api/analytics.py'
   script.write_text(script.read_text().replace('edge = round(act_wr - inact_wr, 1) if measured else None','edge = round((act_wr or 0) - (inact_wr or 0), 1)'))
   args=['services/signal-engine/tests/test_filter_audit_empty_cohorts.py']
  p=subprocess.run(['python3','-m','pytest','-q',*args],cwd=root,capture_output=True,text=True)
  assert p.returncode==1,(name,p.stdout,p.stderr)
  results.append({'finding':name,'removed_guard_detected':True,'exit':p.returncode,'summary':p.stdout.strip().splitlines()[-1]})
print(json.dumps(results,indent=2))
