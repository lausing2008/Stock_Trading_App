"""Read selected trusted artifact metrics and stored price coverage; no training."""
import sys,json,pathlib,hashlib
from datetime import datetime,timezone
sys.path[:0]=['/app/shared','/app']
import joblib
from common.config import get_settings
from db.session import engine
from sqlalchemy import text
root=pathlib.Path(get_settings().model_dir);out={'at':datetime.now(timezone.utc).isoformat(),'artifacts':[]}
for name in ['random_forest/CM_long.joblib','xgboost/MU_swing.joblib','xgboost/MU_growth.joblib','xgboost/MU_long.joblib','lightgbm/MU.joblib']:
 p=root/name
 if not p.exists():out['artifacts'].append({'name':name,'exists':False});continue
 b=joblib.load(p);m=b.get('metrics') or {}
 out['artifacts'].append({'name':name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'metadata':{k:b.get(k) for k in ['trained_at','style','horizon','oos_suppressed']},'metrics':{k:v for k,v in m.items() if k.startswith(('n_','embargo','calibration','threshold','evaluation','date','outcome')) or k in ['cv_auc_mean','auc','recall','precision','label_threshold','overfit_gap','split_date_ranges']}})
with engine.connect() as c:
 c.execute(text('SET TRANSACTION READ ONLY'));c.execute(text("SET LOCAL statement_timeout='10s'"))
 out['stored_daily_prices']=[dict(r) for r in c.execute(text("SELECT s.symbol,count(*) AS rows,min(p.ts) AS first,max(p.ts) AS last FROM prices p JOIN stocks s ON s.id=p.stock_id WHERE s.symbol IN ('CM','MU') AND p.timeframe='D1' AND p.ts>=current_date-interval '5 years' GROUP BY s.symbol")).mappings()];c.rollback()
print('SAMPLE_JSON');print(json.dumps(out,default=str,indent=2))
