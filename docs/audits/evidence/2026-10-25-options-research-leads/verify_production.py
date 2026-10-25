import json,subprocess,sys,urllib.request
from pathlib import Path
from datetime import datetime,timezone
release=sys.argv[1]
assert len(release)==40 and all(c in '0123456789abcdef' for c in release)
short=release[:8]
before=json.loads(Path('/home/ec2-user/deploy-evidence/2026-10-25-options-research-leads/before.json').read_text())
names=subprocess.check_output(['docker','ps','--format','{{.Names}}'],text=True).splitlines()
rows=json.loads(subprocess.check_output(['docker','inspect',*names],text=True))
containers=[{'name':r['Name'].lstrip('/'),'image':r['Image'],'started_at':r['State']['StartedAt'],'health':r['State'].get('Health',{}).get('Status')} for r in rows]
assert len(containers)==15 and all(r['health']=='healthy' for r in containers)
old={r['name']:r for r in before}
changed={'stockai-frontend-1'}
assert all(r['started_at']==old[r['name']]['started_at'] for r in containers if r['name'] not in changed)
assert all(r['started_at']!=old[r['name']]['started_at'] for r in containers if r['name'] in changed)
for svc in ('frontend',):
 expected=subprocess.check_output(['docker','image','inspect','-f','{{.Id}}',f'stockai-{svc}:release-{short}'],text=True).strip()
 assert next(r for r in containers if r['name']==f'stockai-{svc}-1')['image']==expected
 subprocess.run(['docker','image','inspect',f'stockai-{svc}:rollback-before-{short}'],check=True,stdout=subprocess.DEVNULL)
build=subprocess.check_output(['docker','exec','stockai-frontend-1','cat','/app/.next/BUILD_ID'],text=True).strip()
checks={}
for path in ('/','/stock/MU','/options-dark-pool-tutorial','/improvements'):
 with urllib.request.urlopen('https://lausing.com'+path,timeout=30) as r:checks[path]=r.status
assert all(v==200 for v in checks.values())
print('PRODUCTION_RELEASE_STATUS='+json.dumps({'verified_at_utc':datetime.now(timezone.utc).isoformat(),'application_release':release,'frontend_build_id':build,'containers':containers,'fourteen_other_containers_unchanged':True,'only_frontend_restarted':True,'prepared_images_match':True,'rollback_images_retained':True,'public_http':checks},sort_keys=True))
