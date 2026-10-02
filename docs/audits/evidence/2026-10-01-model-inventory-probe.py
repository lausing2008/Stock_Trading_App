"""Trusted production artifacts only. No trainer import, retrain or resweep mutation."""
import ast,sys,pathlib,hashlib,json
from datetime import datetime,timezone
sys.path[:0]=['/app/shared','/app']
from common.config import get_settings
import joblib
from src.training.suppression_inventory import build_inventory
p=pathlib.Path('/app/src/training/trainer.py');t=ast.parse(p.read_text());f=next(n for n in t.body if isinstance(n,ast.FunctionDef) and n.name=='_compute_oos_suppression')
env={};exec(compile(ast.Module(body=[f],type_ignores=[]),str(p),'exec'),env)
root=pathlib.Path(get_settings().model_dir); rows=[]; details=[]
for p in sorted(root.glob('*/*.joblib')):
 name=str(p.relative_to(root));before=p.stat(); digest=hashlib.sha256(p.read_bytes()).hexdigest()
 try:
  b=joblib.load(p);m=b.get('metrics') or {}; rows.append((name,b));after=p.stat()
  details.append({'name':name,'sha256':digest,'changed_during_read':(before.st_mtime_ns,before.st_size)!=(after.st_mtime_ns,after.st_size),'evaluation_valid':m.get('evaluation_valid'),'suppressed':b.get('oos_suppressed',False)})
 except Exception as e:rows.append((name,None));details.append({'name':name,'error_type':type(e).__name__})
inv=build_inventory(rows,decide=env['_compute_oos_suppression'])
print('INVENTORY_JSON');print(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'root':str(root),'inventory':inv.to_dict(),'would_suppress':inv.would_suppress,'would_unsuppress':inv.would_unsuppress,'artifacts':details},default=str))
