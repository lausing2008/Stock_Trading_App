"""Read-only review of source: all artifact/policy mutations use pytest temporary roots.
Assertions witness current defects; they are NOT acceptance tests of desired behavior.
"""
import hashlib
import json
import pathlib
import sys
import threading
sys.path[:0] = ['/app/shared', '/app']
import joblib
import pytest
from src.training import publication as pub
from src.training import trainer

@pytest.fixture
def env(tmp_path, monkeypatch):
    md = tmp_path / 'data/models'
    (md / 'xgboost').mkdir(parents=True)
    monkeypatch.setattr(trainer._settings, 'model_dir', str(md))
    pub._SERVING_CACHE.clear()
    return md, md / 'xgboost/AAA_swing.joblib'

def publish(md, live, payload, protocol='v2', symbol='AAA', style='SWING', family='xgboost'):
    return pub.publish(md, live, lambda p: pathlib.Path(p).write_bytes(payload),
        protocol=protocol, symbol=symbol, style=style, family=family)

def select(md, live):
    live.write_bytes(b'original-v1')
    pub.set_v2_enabled(md, True, 'review')
    pub.authorize(md, 'AAA', 'SWING', 'review')
    result = publish(md, live, b'selected-v2')
    pub.promote(md, 'AAA', 'SWING', 'xgboost', result['path'],
        hashlib.sha256(b'selected-v2').hexdigest(), pub.read_policy(md)['epoch'], 'review')
    return pathlib.Path(result['path'])

def test_resweep_writes_after_authorization_has_completed(env, monkeypatch):
    md, live = env
    bundle = {'oos_suppressed': False, 'metrics': {'evaluation_valid': False, 'cv_auc_mean': .6}}
    joblib.dump(bundle, live)
    before = live.read_bytes()
    reached, resume = threading.Event(), threading.Event()
    original_dump = joblib.dump
    def paused_dump(*args, **kwargs):
        reached.set()
        assert resume.wait(5), 'review synchronization timed out'
        return original_dump(*args, **kwargs)
    monkeypatch.setattr(joblib, 'dump', paused_dump)
    results, errors = [], []
    def worker():
        try: results.append(trainer.resweep_oos_suppression(dry_run=False))
        except BaseException as e: errors.append(e)
    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert reached.wait(5)
        pub.set_v2_enabled(md, True, 'review')
        pub.authorize(md, 'AAA', 'SWING', 'review')
        assert pub.owned_by_v2(pub.read_policy(md), 'AAA', 'SWING')
    finally:
        resume.set()
        thread.join(5)
    assert not errors and not thread.is_alive()
    assert live.read_bytes() != before
    assert joblib.load(live)['oos_suppressed'] is True
    assert results[0]['changed_count'] == 1

def test_wrong_cell_and_unqualified_candidate_can_be_selected(env):
    md, live = env
    pub.set_v2_enabled(md, True, 'review')
    pub.authorize(md, 'AAA', 'SWING', 'review')
    payload = b'BBB-LONG-RF-without-any-evaluation-proof'
    result = publish(md, live, payload, symbol='BBB', style='LONG', family='random_forest')
    pub.promote(md, 'AAA', 'SWING', 'xgboost', result['path'], hashlib.sha256(payload).hexdigest(),
        pub.read_policy(md)['epoch'], 'review')
    assert pub.serving_path(md, live, 'AAA', 'SWING', 'xgboost').read_bytes() == payload

def test_corrupt_policy_silently_rolls_back_selected_serving(env):
    md, live = env
    selected = select(md, live)
    assert pub.serving_path(md, live, 'AAA', 'SWING', 'xgboost') == selected
    (pub.roots(md)[1] / 'policy.json').write_text('{broken')
    assert pub.serving_path(md, live, 'AAA', 'SWING', 'xgboost') == live

def test_selected_hash_is_not_checked_at_serving(env):
    md, live = env
    selected = select(md, live)
    selected.write_bytes(b'replaced-after-approval')
    policy = pub.read_policy(md)
    assert policy['selections']['AAA|SWING|xgboost']['sha256'] != hashlib.sha256(selected.read_bytes()).hexdigest()
    assert pub.serving_path(md, live, 'AAA', 'SWING', 'xgboost').read_bytes() == b'replaced-after-approval'

def test_disable_retrain_deselect_returns_unpinned_new_v1(env):
    md, live = env
    select(md, live)
    pub.set_v2_enabled(md, False, 'review')
    assert publish(md, live, b'new-nightly-v1', protocol='v1')['target'] == 'live'
    pub.deselect(md, 'AAA', 'SWING', 'xgboost', 'review')
    assert pub.serving_path(md, live, 'AAA', 'SWING', 'xgboost').read_bytes() == b'new-nightly-v1'
