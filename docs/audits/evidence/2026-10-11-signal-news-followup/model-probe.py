"""Read existing trusted model metadata only; no inference, training, or writes."""
import json
from pathlib import Path
import joblib
from common.config import get_settings
root=Path(get_settings().model_dir)
rows=[]
for model in ('xgboost','random_forest','gradient_boosting'):
    for symbol in ('MSFT','NVDA','INTC'):
        for p in sorted((root/model).glob(symbol+'*.joblib')):
            if p.stem.split('_')[0] != symbol:
                continue
            bundle=joblib.load(p)
            m=bundle.get('metrics', {})
            rows.append({'artifact':str(p.relative_to(root)),
                'trained_at':bundle.get('trained_at'),
                'suppressed':bundle.get('oos_suppressed'),
                'reason':bundle.get('suppression_reason'),
                'metrics':{k:v for k,v in m.items() if k in (
                    'auc','cv_auc_mean','precision','recall','overfit_gap',
                    'evaluation_valid','n_test','threshold_evaluation_mode',
                    'embargo_shortfall','suppression_reason')}})
print(json.dumps(rows,default=str,indent=2))
