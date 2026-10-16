import subprocess,sys
from pathlib import Path


def test_general_company_events_are_scoped_sourced_idempotent_and_shadow_only():
    p=subprocess.run([sys.executable,str(Path(__file__).with_name('_company_impact_probe.py'))],capture_output=True,text=True,timeout=60)
    assert p.returncode==0,p.stdout+p.stderr
