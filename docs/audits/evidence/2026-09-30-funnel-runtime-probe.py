import sys,json,pathlib,hashlib
sys.path.insert(0,'/app/shared');sys.path.insert(0,'/app')
from common.redis_client import get_redis
r=get_redis();out={'recovery':{},'hashes':{}}
for pid in [2,5]:
 k=f'paper:consec_loss_recovery:{pid}';v=r.get(k)
 out['recovery'][str(pid)]={'streak':v.decode() if isinstance(v,bytes) else v,'ttl_seconds':r.ttl(k)}
for path in ['/app/src/services/paper_trading_engine.py','/app/src/services/scheduler.py']:
 out['hashes'][path]=hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
print(json.dumps(out,indent=2))
