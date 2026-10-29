"""Independent acceptance checks converted from the RF-01/RF-02 witnesses. Temporary files only.
"""
import errno
import json
from pathlib import Path
import sys
sys.path[:0] = ['/app/shared', '/app']
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from src.api import routes
from src.training import publication as pub
from src.training import trainer

@pytest.fixture
def ctx(tmp_path, monkeypatch):
    md = tmp_path/'models'
    (md/'xgboost').mkdir(parents=True)
    monkeypatch.setattr(trainer._settings, 'model_dir', str(md))
    pub._SERVING_CACHE.clear(); pub._BUNDLE_CACHE.clear()
    pub.set_v2_enabled(md, False, by='review')
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.get_current_username] = lambda: 'review'
    return md, TestClient(app, raise_server_exceptions=False)

@pytest.mark.parametrize('route', ['predict', 'predict_ensemble', 'predict_ensemble_three'])
def test_policy_stat_error_returns_explicit_unavailable(ctx, monkeypatch, route):
    md, client = ctx
    pfile = pub.roots(md)[1]/'policy.json'
    original = Path.stat
    def stat(path, *a, **kw):
        if path == pfile:
            raise PermissionError(errno.EACCES, 'review: policy directory unreadable', str(path))
        return original(path, *a, **kw)
    monkeypatch.setattr(Path, 'stat', stat)
    out = client.post('/ml/'+route, json={'symbol':'AAA','style':'SWING'})
    assert out.status_code == 200
    assert out.json()['ml_available'] is False
    assert out.json()['ml_unavailable_reason'] == 'serving_unavailable'


def test_empty_selected_record_is_refused(ctx):
    md, client = ctx
    live = md/'xgboost/AAA_swing.joblib'
    live.write_bytes(b'legacy-model')
    policy = pub.read_policy(md)
    policy['selections']['AAA|SWING|xgboost'] = {}
    (pub.roots(md)[1]/'policy.json').write_text(json.dumps(policy))
    with pytest.raises(pub.ServingUnavailable, match='publication_policy_unavailable'):
        pub.load_serving(md, live, 'AAA','SWING','xgboost',
            lambda src: src.read() if hasattr(src,'read') else Path(src).read_bytes())
