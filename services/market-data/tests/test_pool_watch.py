"""Runs the pool_watch probe outside conftest's stubs (it needs a real SQLAlchemy pool)."""
import subprocess
import sys
from pathlib import Path


def test_pool_watch_end_to_end():
    probe = Path(__file__).with_name('_pool_watch_probe.py')
    result = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
