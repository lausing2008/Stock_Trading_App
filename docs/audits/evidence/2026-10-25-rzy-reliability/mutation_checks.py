"""Run sabotage checks in an isolated source copy; never edits the working tree or production."""
import json
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
import tempfile
root=next(p for p in Path(__file__).resolve().parents if (p/'services/market-data/tests/_little_rzy_probe.py').exists())
copy=Path(tempfile.mkdtemp(prefix='rzy-mutations-'))
shutil.copytree(root/'shared',copy/'shared',ignore=shutil.ignore_patterns('__pycache__'))
service=copy/'services/market-data'
shutil.copytree(root/'services/market-data/src',service/'src',ignore=shutil.ignore_patterns('__pycache__'))
(service/'tests').mkdir()
for name in ('conftest.py','_little_rzy_probe.py','test_little_rzy.py','test_rzy_email.py','test_session_grid_and_reconcile.py','test_rzy_aggregation.py'):
    shutil.copy2(root/'services/market-data/tests'/name,service/'tests'/name)
env=dict(os.environ)
for k in ('RZY_PG_URL','RZY_PG_REQUIRED'):env.pop(k,None)
probe=[sys.executable,str(service/'tests/_little_rzy_probe.py')]
def run(command):
    return subprocess.run(command,cwd=service,env=env,capture_output=True,text=True,timeout=120)
baseline=run(probe)
assert baseline.returncode==0,baseline.stderr[-1500:]
checks=[
 ('projected_reads','rzy_workflow.py',
  "rows=session.execute(select(*columns).where(Price.stock_id==stock.id,Price.timeframe==tf).order_by(Price.ts.desc()).limit(6000)).all()",
  "rows=session.scalars(select(Price).where(Price.stock_id==stock.id,Price.timeframe==tf).order_by(Price.ts.desc()).limit(6000)).all()",probe),
 ('journal_wired_to_evaluate','rzy_workflow.py',
  "record_positions(session,key,engine,stored,persisted_at,\n            'bootstrap' if prior is None else 'rebuild' if rebuilt else 'incremental')",
  "None  # deliberately omit shipped recording",probe),
 ('cohort_isolation','rzy_journal.py',
  "forward=[r for r in rows if not r['superseded'] and r['observed_in']=='forward_observation']",
  "forward=list(rows)",probe),
 ('unmailed_latency_population','rzy_monitoring.py',
  "and r.observed_in=='incremental']","and r.observed_in=='incremental' and r.queued_at is not None]",probe),
 ('execution_figure_fidelity','rzy_workflow.py',
  "'actual_paper_fill','paper_fill_time','exit_price','exit_quantity','paper_state','net_pnl_per_unit','reason')",
  "'unused')",probe),
 ('latest_bin_freshness','rzy_data.py',
  "stale=expected_close is None or latest<expected_close",
  "stale=expected_close is None or (expected_close-latest)>timedelta(minutes=MINUTES[timeframe])",
  [sys.executable,'-m','pytest','-q','tests/test_rzy_aggregation.py::test_one_missing_latest_five_minute_candle_is_stale_after_settling']),
 ('internal_gap_status','scheduler.py',
  'detail["missing_candles"] or detail["final_candle_missing"]', 'detail["final_candle_missing"]',
  [sys.executable,'-m','pytest','-q','tests/test_session_grid_and_reconcile.py::test_internal_gap_is_partial_even_when_final_candle_is_finalized']),
 ('fill_email_distinction','rzy_email.py',
  "'Simulated entry filled — no broker order'","'Research update'",
  [sys.executable,'-m','pytest','-q','tests/test_rzy_email.py::test_simulated_fill_is_distinct_from_trigger_and_broker_execution'])
]
results=[]
for name,filename,old,new,command in checks:
    path=service/'src/services'/filename; original=path.read_text()
    assert original.count(old)==1,(name,'mutation must apply exactly once')
    try:
        path.write_text(original.replace(old,new));py_compile.compile(str(path),doraise=True)
        result=run(command)
        caught=result.returncode!=0
        results.append(dict(check=name,mutation_applied=True,compiled=True,test_rejected_mutation=caught))
        if not caught:raise AssertionError(f'{name}: test accepted the sabotage')
    finally:path.write_text(original)
print(json.dumps(dict(baseline_passed=True,isolated_copy=True,checks=results),indent=2))
shutil.rmtree(copy)
