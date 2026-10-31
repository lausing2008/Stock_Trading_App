"""Runs the paper-trading Yahoo cache probe outside conftest's stubs."""
import subprocess
import sys
from pathlib import Path


def test_paper_yahoo_caches():
    probe = Path(__file__).with_name('_paper_yf_cache_probe.py')
    result = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
