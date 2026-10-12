"""Run the real-SQLite event test outside conftest's Docker-dependency stubs."""
import subprocess
import sys
from pathlib import Path


def test_news_impact_real_sqlite():
    probe = Path(__file__).with_name('_news_impact_probe.py')
    result = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True,
                            timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
