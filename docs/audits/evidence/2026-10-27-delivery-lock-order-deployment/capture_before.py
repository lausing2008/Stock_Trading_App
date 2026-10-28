import json,subprocess,pathlib,hashlib,datetime
services=['market-data','signal-engine','api-gateway','ml-prediction','research-engine','ranking-engine','strategy-engine','technical-analysis','portfolio-optimizer','event-intelligence','news-intelligence','decision-engine']
code="import pathlib,hashlib,json; out={};\nfor name in ('src','shared'):\n p=pathlib.Path('/app')/name; h=hashlib.sha256();\n for f in sorted(p.rglob('*.py')): h.update(f.read_bytes())\n out[name]=h.hexdigest()\nprint(json.dumps(out))"
names=['stockai-'+s+'-1' for s in services]+['stockai-frontend-1','stockai-postgres-1','stockai-redis-1']
out={'captured_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'hashes':{},'containers':[]}
for svc in services:out['hashes'][svc]=json.loads(subprocess.check_output(['docker','exec','stockai-'+svc+'-1','python','-c',code],text=True,timeout=15))
for c in json.loads(subprocess.check_output(['docker','inspect',*names],text=True)):
 out['containers'].append({'name':c['Name'],'image':c['Image'],'started_at':c['State']['StartedAt'],'health':c['State'].get('Health',{}).get('Status'),'status':c['State']['Status']})
out['repo_head']=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip();print(json.dumps(out,indent=2))
