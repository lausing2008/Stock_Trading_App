"""Run the paper entry receipt probe outside conftest's Docker-dependency stubs (it needs the
real models and the real `_open_paper_trade`)."""
import subprocess
import sys
from pathlib import Path


def test_paper_entry_receipts_end_to_end():
    probe = Path(__file__).with_name('_entry_receipts_probe.py')
    result = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True,
                            timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr
