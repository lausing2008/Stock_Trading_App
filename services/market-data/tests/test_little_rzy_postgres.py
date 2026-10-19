"""Required by make test-integration-pg-schema; the production dialect must actually run."""
import os,pathlib,subprocess,sys
import pytest
URL=os.environ.get('RZY_PG_URL')
if os.environ.get('RZY_PG_REQUIRED')=='1' and not URL:raise RuntimeError('RZY_PG_REQUIRED needs a disposable local RZY_PG_URL')
@pytest.mark.skipif(not URL,reason='RZY_PG_URL is absent; required in make test-integration-pg-schema')
def test_rzy_persistence_and_delivery_on_postgresql():
    run=subprocess.run([sys.executable,str(pathlib.Path(__file__).with_name('_little_rzy_probe.py'))],capture_output=True,text=True,timeout=150)
    assert run.returncode==0,run.stdout[-2500:]+run.stderr[-5000:]
