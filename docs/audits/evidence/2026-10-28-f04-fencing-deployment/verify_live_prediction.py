import json
import httpx
from src.generators.signals import _ml_service_token,_settings
with httpx.Client(timeout=60) as c:
    r=c.post(_settings.ml_prediction_url+'/ml/predict_ensemble_three',
        json={'symbol':'MSFT','style':'SWING'},headers={'Authorization':'Bearer '+_ml_service_token()})
report={'status_code':r.status_code,'symbol':'MSFT','style':'SWING'}
assert r.status_code==200
body=r.json()
for key in ('model','ml_available','ml_unavailable_reason','eligible_contributors','oos_suppressed','bullish_probability'):
    report[key]=body.get(key)
assert report['ml_unavailable_reason']!='serving_unavailable'
print(json.dumps(report,sort_keys=True))
