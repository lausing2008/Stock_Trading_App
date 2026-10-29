"""Run the real signal-engine ML fetch function with mocked HTTP only; no external I/O."""
import ast
import json
from pathlib import Path
import sys
from types import SimpleNamespace

repo = Path(sys.argv[1])
path = repo/'services/signal-engine/src/generators/signals.py'
node = next(n for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef) and n.name=='_fetch_ml_data')
log = SimpleNamespace(warning=lambda *a,**k: None, debug=lambda *a,**k: None)
result = {}
for status in [500,200]:
    calls = []
    class Client:
        def __init__(self,*a,**kw): pass
        def __enter__(self): return self
        def __exit__(self,*a): return False
        def post(self,url,**kw):
            calls.append(url)
            return SimpleNamespace(status_code=status, json=lambda: {'ml_available':False,
                'ml_unavailable_reason':'serving_unavailable','model':'none_served'})
    ns={'httpx':SimpleNamespace(Client=Client),'log':log,
        '_settings':SimpleNamespace(ml_prediction_url='http://offline-fixture'),
        '_ml_service_token':lambda:'fixture-only'}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),ns)
    output=ns['_fetch_ml_data']('AAA','SWING')
    result[str(status)]={'endpoint_attempts':len(calls),'probability':output[0],'quality':output[1],'metadata':output[2]}
assert result['500']['endpoint_attempts']==3 and result['500']['metadata']=={}
assert result['200']['endpoint_attempts']==1 and result['200']['metadata']['ml_unavailable_reason']=='no_eligible_model'
print(json.dumps(result,indent=2))
