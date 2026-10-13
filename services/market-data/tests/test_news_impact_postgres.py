"""The product-impact producer and the audience gate on PostgreSQL — the production engine.

WHY A SEPARATE SUITE. The SQLite probe passed over two shipped defects for reasons SQLite
cannot avoid:

  * SQLite does not enforce a declared varchar length, so a 38-character `evidence_status` in a
    VARCHAR(32) column inserted happily there and failed on every PostgreSQL insert.
  * The SQLite probe injects a `may_send` lambda that checks one preference row and nothing
    else, so it agreed with itself rather than with the shipped gate.

This suite injects nothing and runs on a real server. To run it:

    docker run -d --name stockai-ni-pgtest -e POSTGRES_PASSWORD=probe \
        -e POSTGRES_DB=niprobe -p 55439:5432 postgres:16-alpine
    NEWS_IMPACT_PG_URL=postgresql+psycopg2://postgres:probe@localhost:55439/niprobe \
        python -m pytest services/market-data/tests/test_news_impact_postgres.py

The probe DROPS AND RECREATES the public schema. Throwaway databases only.
"""
import os
import pathlib
import subprocess
import sys

import pytest

_PROBE = pathlib.Path(__file__).resolve().parent / "_news_impact_pg_probe.py"
_URL = os.environ.get("NEWS_IMPACT_PG_URL")

#: Set by a release gate. A SKIPPED test is indistinguishable from a passing one in a summary
#: line, so where this gates a release the absence of a database must FAIL rather than skip.
_REQUIRED = os.environ.get("NEWS_IMPACT_PG_REQUIRED", "").strip().lower() in ("1", "true", "yes")

if _REQUIRED and not _URL:
    raise RuntimeError(
        "NEWS_IMPACT_PG_REQUIRED is set but NEWS_IMPACT_PG_URL is not. These tests cover a "
        "constraint SQLite cannot enforce: refusing to pass without having exercised PostgreSQL.")

pytestmark = pytest.mark.skipif(
    not _URL, reason="NEWS_IMPACT_PG_URL not set; see this module's docstring to run it")


def test_producer_and_audience_gate_on_postgresql():
    proc = subprocess.run([sys.executable, str(_PROBE)], capture_output=True, text=True,
                          timeout=300, env={**os.environ, "NEWS_IMPACT_PG_URL": _URL})
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-3000:]
