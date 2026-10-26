"""Sabotage only a temporary source copy, using the disposable PG URL for pipeline cases."""
import json,os,pathlib,shutil,subprocess,sys,tempfile,py_compile
root=next(p for p in pathlib.Path(__file__).resolve().parents if (p/'services/market-data/tests/_bar_ingest_observations_probe.py').exists())
copy=pathlib.Path(tempfile.mkdtemp(prefix='rzy-observation-mutations-'))
for folder in ('shared','services/market-data/src','scripts'):
    shutil.copytree(root/folder,copy/folder,ignore=shutil.ignore_patterns('__pycache__'))
service=copy/'services/market-data';(service/'tests').mkdir()
for name in ('conftest.py','_bar_provenance_probe.py','_bar_ingest_observations_probe.py','test_bar_ingest_observations.py'):
    shutil.copy2(root/'services/market-data/tests'/name,service/'tests'/name)
pg=dict(os.environ);assert pg.get('RZY_PG_URL'),'Disposable PostgreSQL required'
lite={k:v for k,v in pg.items() if k not in ('RZY_PG_URL','RZY_PG_REQUIRED')}
probe=[sys.executable,str(service/'tests/_bar_ingest_observations_probe.py')]
def run(cmd,env):return subprocess.run(cmd,cwd=service,env=env,capture_output=True,text=True,timeout=150)
baseline=run(probe,pg);assert baseline.returncode==0,baseline.stderr[-2000:]
cases=[
 ('shipped_hook','services/market-data/src/services/ingestion.py','_observe_ingest(session, stock, timeframe, _received_for_observation, rows, _snapshots, adapter.name, force)','None',2,probe,pg),
 ('savepoint_isolation','services/market-data/src/services/ingestion.py','with session.begin_nested():\n            record_ingest_observation(session,stock,timeframe,received,written,off_grid,provider,forced=forced)','record_ingest_observation(session,stock,timeframe,received,written,off_grid,provider,forced=forced)',1,probe,pg),
 ('forming_not_completed','services/market-data/src/services/bar_ingest_observations.py','if close and close<=now and ts>=now-RETENTION:','if close and ts>=now-RETENTION:',1,probe,lite),
 ('unknown_not_zero','services/market-data/src/services/rzy_monitoring.py','if observed is None:missing_write+=1',"if observed is None:missing_write+=1;stages['observed_post_close_write'].append(0)",1,probe,lite),
 ('capture_identity','scripts/capture_rzy_session_evidence.sh','and image==image_after and started==started_after','and True',1,[sys.executable,'-m','pytest','-q','tests/test_bar_ingest_observations.py','-k','identity and image'],lite),
 ('capture_preservation','scripts/capture_rzy_session_evidence.sh','if ! mkdir "$DEST"; then','if ! mkdir -p "$DEST"; then',1,[sys.executable,'-m','pytest','-q','tests/test_bar_ingest_observations.py::test_capture_refuses_existing_evidence_before_any_docker_command'],lite)
]
results=[]
for name,f,old,new,count,cmd,env in cases:
    p=copy/f;original=p.read_text();assert original.count(old)==count,name
    try:
        p.write_text(original.replace(old,new))
        if p.suffix=='.py':py_compile.compile(str(p),doraise=True)
        else:subprocess.run(['/bin/bash','-n',str(p)],check=True)
        r=run(cmd,env);assert r.returncode!=0,name+' accepted sabotage'
        results.append(dict(check=name,applied=True,compiled=True,rejected=True))
    finally:p.write_text(original)
print(json.dumps(dict(isolated_copy=True,baseline_passed=True,checks=results),indent=2))
shutil.rmtree(copy)
