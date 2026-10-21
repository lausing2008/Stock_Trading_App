"""Bar provenance trace on real ORM models (SQLite; PostgreSQL in make test-integration-pg-schema)."""
import json, os, pathlib, subprocess, sys
import pytest

PROBE = pathlib.Path(__file__).with_name('_bar_provenance_probe.py')
CHECKS = ['first_write_and_unknown_previous', 'unchanged_rewrite_records_nothing', 'cross_provider_revision', 'same_provider_correction',
          'volume_only_and_same_value_switch_counted', 'off_grid_flagged', 'failure_isolated']


@pytest.fixture(scope='module')
def evidence():
    env = {k: v for k, v in os.environ.items() if k != 'RZY_PG_URL'}
    run = subprocess.run([sys.executable, str(PROBE)], capture_output=True, text=True, timeout=120, env=env)
    assert run.returncode == 0, run.stderr[-4000:]
    return json.loads(run.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize('check', CHECKS)
def test_bar_trace(evidence, check):
    assert evidence[check]


@pytest.mark.skipif(not os.environ.get('RZY_PG_URL'), reason='RZY_PG_URL is absent; required in make test-integration-pg-schema')
def test_bar_trace_on_postgresql():
    run = subprocess.run([sys.executable, str(PROBE)], capture_output=True, text=True, timeout=150)
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-4000:]
    assert set(json.loads(run.stdout.strip().splitlines()[-1])) >= set(CHECKS)
