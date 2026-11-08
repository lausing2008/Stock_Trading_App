"""Publishing an evaluation run and the /stocks/rzy/evaluation endpoint, on a real ORM (SQLite)."""
import pathlib, subprocess, sys


def test_publish_is_idempotent_refuses_tampering_and_endpoint_serves_latest():
    run = subprocess.run([sys.executable, str(pathlib.Path(__file__).with_name('_rzy_evaluation_probe.py'))],
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout[-2500:] + run.stderr[-5000:]
    assert '"ok": true' in run.stdout
