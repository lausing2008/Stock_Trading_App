"""Run the AQ04 calibration-basis test outside conftest's Docker-dependency stubs.

This suite's conftest replaces `db` with a MagicMock, which would accept every assertion made
of it. The probe loads the real models and the real calibration module so the question answered
is whether the function that WRITES the production ML weight refuses a corrupt observation.
"""
import subprocess
import sys
from pathlib import Path


def test_ml_weight_calibration_refuses_unadjustable_windows():
    probe = Path(__file__).with_name('_aq04_calibration_probe.py')
    result = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True,
                            timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
