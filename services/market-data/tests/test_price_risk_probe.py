"""Run the price-risk end-to-end test outside conftest's Docker-dependency stubs.

The conftest replaces `db` with a MagicMock, which accepts every assertion made of it. The
probe loads the real models, the real detector and the real audience gate so the questions
answered are the ones that matter: does a split produce a magnitude, does shadow mode send,
and does the detection job ever enqueue anything.
"""
import subprocess
import sys
from pathlib import Path


def test_price_risk_detection_end_to_end():
    probe = Path(__file__).with_name('_price_risk_probe.py')
    result = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True,
                            timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
