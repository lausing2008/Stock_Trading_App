"""Real models, adapter, persistent state, outbox and send-time consent in a subprocess."""
import json,pathlib,subprocess,sys
import pytest

@pytest.fixture(scope='module')
def evidence():
    run=subprocess.run([sys.executable,str(pathlib.Path(__file__).with_name('_little_rzy_probe.py'))],capture_output=True,text=True,timeout=90)
    assert run.returncode==0,run.stderr[-5000:]
    return json.loads(run.stdout.strip().splitlines()[-1])

@pytest.mark.parametrize('check',['persistence_and_sender_optout','one_provider_acceptance','calendar_scope_digest_dedup','digest_optout','price_basis_completion_gaps'])
def test_real_workflow(evidence,check):assert evidence[check]
