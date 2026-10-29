import json
from pathlib import Path
from common.config import get_settings
from src.training import publication as p
from src.api.routes import _require_v1
from fastapi import HTTPException
md=get_settings().model_dir
policy=p.read_policy(md)
report={'model_dir':md,'policy_exists':(p.roots(md)[1]/'policy.json').exists(),
        'marker_exists':(Path(md)/p._MARKER).exists(),'v2_enabled':policy['v2_enabled'],
        'cohort_count':len(policy['cohort']),'selection_count':len(policy['selections'])}
try:
    p._validate_qualification(None,symbol='MSFT',style='SWING',family='xgboost',sha256='0'*64)
except p.PublicationRefused as e:
    report['promotion_refusal']=e.reason
try:
    _require_v1('v2')
except HTTPException as e:
    report['v2_training_refusal_status']=e.status_code
assert report['v2_enabled'] is False and report['selection_count']==0 and report['cohort_count']==0
assert not report['policy_exists'] and not report['marker_exists']
assert report['promotion_refusal']=='promotion_unavailable:qualification_records_not_implemented'
assert report['v2_training_refusal_status']==409
print(json.dumps(report,sort_keys=True))
