"""Run the startup-DDL serialization test outside conftest's Docker-dependency stubs.

AUD-DEPLOY-MIGRATIONCONVOY (2026-10-12). This suite's conftest replaces `db.session` and
`sqlalchemy` with MagicMocks, which accept every assertion made of them — so the guard has to
be exercised in a subprocess that loads the real modules by path.
"""
import subprocess
import sys
from pathlib import Path


def test_startup_ddl_is_serialized_and_time_bounded():
    probe = Path(__file__).with_name('_ddl_guard_probe.py')
    result = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True,
                            timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
