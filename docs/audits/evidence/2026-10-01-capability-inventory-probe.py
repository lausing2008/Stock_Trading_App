"""Read-only schema evidence; matrix report is not a write/permission/end-to-end test."""
import sys,json,pathlib,hashlib
from datetime import datetime,timezone
sys.path[:0]=['/app/shared','/app']
from sqlalchemy import inspect,text
from db.session import engine
out={'at':datetime.now(timezone.utc).isoformat()}
with engine.connect() as c:
 c.execute(text('SET TRANSACTION READ ONLY'))
 c.execute(text("SET LOCAL statement_timeout='10s'"))
 try:
  from db.capability_checks import build_matrix
  from common.capabilities import report
  out['matrix']=report(build_matrix(c))
 except Exception as e: out['matrix_error']=type(e).__name__
 i=inspect(c); tables=set(i.get_table_names());out['schema']={}
 for table in ['notification_outbox','portfolio_exposure_reservations','paper_trades','options_income_positions','options_income_equity_curve']:
  if table not in tables:out['schema'][table]={'exists':False};continue
  out['schema'][table]={'exists':True,'columns':[x['name'] for x in i.get_columns(table)],'unique_constraints':i.get_unique_constraints(table),'unique_indexes':[x for x in i.get_indexes(table) if x.get('unique')]}
 c.rollback()
for name in ['common/capabilities.py','db/capability_checks.py']:
 p=pathlib.Path('/app/shared')/name
 out[name+'_sha256']=hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
if pathlib.Path('/app/src/services/paper_trading_engine.py').exists():
 from common.redis_client import get_redis
 r=get_redis();out['recovery_markers']={str(n):{'value':r.get('paper:consec_loss_recovery:'+str(n)),'ttl':r.ttl('paper:consec_loss_recovery:'+str(n))} for n in [2,5]}
print('INVENTORY_JSON');print(json.dumps(out,default=str))
