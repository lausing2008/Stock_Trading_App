"""Daily ('1d') bar-revision trace and revision_magnitude, on real ORM models (SQLite; PostgreSQL if RZY_PG_URL)."""
import json, os, pathlib, subprocess, sys
import pytest

PROBE = pathlib.Path(__file__).with_name('_bar_provenance_daily_probe.py')
CHECKS = ['magnitude_unit', 'daily_new_bar', 'daily_unchanged', 'daily_rounding_restatement', 'daily_material_restatement',
          'daily_cross_provider', 'daily_same_provider', 'daily_us_forming_vs_completed', 'daily_hk_forming_vs_completed',
          'daily_non_session_completed', 'daily_bounded', 'intraday_key_unchanged']


@pytest.fixture(scope='module')
def evidence():
    env = {k: v for k, v in os.environ.items() if k != 'RZY_PG_URL'}
    run = subprocess.run([sys.executable, str(PROBE)], capture_output=True, text=True, timeout=120, env=env)
    assert run.returncode == 0, run.stderr[-4000:]
    return json.loads(run.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize('check', CHECKS)
def test_daily_bar_trace(evidence, check):
    assert evidence[check]


@pytest.mark.skipif(not os.environ.get('RZY_PG_URL'), reason='RZY_PG_URL is absent; disposable local PostgreSQL only')
def test_daily_bar_trace_on_postgresql():
    run = subprocess.run([sys.executable, str(PROBE)], capture_output=True, text=True, timeout=150)
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-4000:]
    assert set(json.loads(run.stdout.strip().splitlines()[-1])) >= set(CHECKS)
