import json
import subprocess
import sys
from pathlib import Path

def test_real_database_identity_versions_cancellations_and_capture_order():
    probe=Path(__file__).with_name('_dark_pool_ledger_probe.py')
    process=subprocess.run([sys.executable,str(probe)],capture_output=True,text=True,timeout=30)
    assert process.returncode==0,process.stdout+process.stderr
    data=json.loads(process.stdout.split('LEDGER_PROBE_JSON=')[-1])
    assert data['distinct_events']==2 and data['retained_versions']>=7
    assert data['active_unambiguous']==1 and data['stale_capture_cannot_revert']
