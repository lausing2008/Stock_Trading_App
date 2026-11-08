"""Real ORM on SQLite: publishing an evaluation run and serving it. No network."""
import importlib.util, json, os, pathlib, sys, tempfile, types, hashlib
from datetime import datetime, timezone
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'shared'), str(ROOT / 'services/market-data')]
db = types.ModuleType('db'); db.__path__ = [str(ROOT / 'shared/db')]; sys.modules['db'] = db
spec = importlib.util.spec_from_file_location('db.models', ROOT / 'shared/db/models.py')
models = importlib.util.module_from_spec(spec); sys.modules['db.models'] = models; spec.loader.exec_module(models)
for key in vars(models): setattr(db, key, getattr(models, key))
from src.services.rzy_models import RzyEvaluationRun, RzyPaperJournal
from src.services.little_rzy import Config
engine = create_engine('sqlite:///' + str(pathlib.Path(tempfile.mkdtemp()) / 'eval.db'))
models.Base.metadata.create_all(engine); Session = sessionmaker(bind=engine, autoflush=False)
db.SessionLocal = Session; db.get_session = lambda: None
os.environ['RZY_EVAL_ROOT'] = str(ROOT / 'services/market-data')
spec = importlib.util.spec_from_file_location('evaluate_little_rzy', ROOT / 'scripts/evaluate_little_rzy.py')
runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)

def write_run(d, exported, edge):
    os.makedirs(d, exist_ok=True)
    summary = dict(analysis_version='RZY_EVALUATION_V2', config_hash=Config().hash,
                   baseline=dict(overall=dict(trades=700, edge_vs_control_r=edge, verdict='no measurable edge vs control')))
    p = os.path.join(d, 'summary.json'); json.dump(summary, open(p, 'w'))
    manifest = dict(created_at='2026-11-08T12:00:00+00:00', git_revision='abc', git_dirty=False, analysis_version='RZY_EVALUATION_V2',
                    snapshot_bars_sha256=hashlib.sha256(exported.encode()).hexdigest(), snapshot_exported_at=exported,
                    code_sha256={'x': '1'}, outputs_sha256={'summary.json': runner.sha256(p)})
    json.dump(manifest, open(os.path.join(d, 'manifest.json'), 'w'))

tmp = tempfile.mkdtemp()
older, newer = os.path.join(tmp, 'older'), os.path.join(tmp, 'newer')
write_run(older, '2026-10-20T14:04:54+00:00', .001)
write_run(newer, '2026-11-08T15:00:00+00:00', .02)
runner.publish(newer); runner.publish(older); runner.publish(newer)   # third call: idempotent
with Session() as s:
    assert s.scalar(select(func.count()).select_from(RzyEvaluationRun)) == 2
# An edited summary no longer matches its manifest and is refused.
bad = os.path.join(tmp, 'bad'); write_run(bad, '2026-11-09T15:00:00+00:00', .5)
json.dump(dict(edited=True), open(os.path.join(bad, 'summary.json'), 'w'))
try:
    runner.publish(bad); raise AssertionError('tampered summary was published')
except SystemExit as exc:
    assert 'does not match' in str(exc)

from src.api.little_rzy import evaluation, router
paths = [r.path for r in router.routes]
assert paths.index('/stocks/rzy/evaluation') < paths.index('/stocks/rzy/{symbol}')
with Session() as s:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    s.add(RzyPaperJournal(journal_id='j1', stream_key='k', symbol='X', timeframe='5m', config_hash=Config().hash, generation=1,
                          structure_id='st', observed_in='forward_observation', first_seen_at=now, updated_at=now, payload={}))
    s.commit()
    out = evaluation(_user=None, session=s)
assert out['latest']['snapshot_exported_at'].startswith('2026-11-08') and out['latest']['edge_vs_control_r'] == .02
assert out['latest']['summary']['baseline']['overall']['trades'] == 700 and out['latest']['current_config'] is True
assert [r['snapshot_exported_at'][:10] for r in out['runs']] == ['2026-11-08', '2026-10-20']
assert out['forward_journal']['by_cohort'] == {'forward_observation': 1}
assert 'not a forecast' in out['positioning'].lower()
print(json.dumps(dict(ok=True)))
