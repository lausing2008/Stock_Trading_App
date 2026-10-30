"""Runs intraday_isolation_suite.py (the signal-engine witnesses of the Intraday Stage C guard
coverage matrix) outside this directory's stubbing conftest, which would make every compiled
statement a MagicMock."""
import pathlib
import subprocess
import sys

SUITE = pathlib.Path(__file__).with_name("intraday_isolation_suite.py")


def test_signal_engine_daily_isolation_witnesses():
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--noconftest", "-W", "ignore",
                        str(SUITE)], capture_output=True, text=True, timeout=300, cwd=str(SUITE.parent))
    assert r.returncode == 0, r.stdout[-5000:] + r.stderr[-2000:]
    assert " passed" in r.stdout and "skipped" not in r.stdout
