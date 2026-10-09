import pathlib
import subprocess
import sys


def test_actual_handlers_with_real_database():
    probe = pathlib.Path(__file__).with_name('_option_ledger_probe.py')
    result = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'probe passed' in result.stdout
