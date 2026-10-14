"""Copilot concurrency + startup migration on PostgreSQL. Skipped unless STOCKAI_INTEL_DB names
a THROWAWAY PostgreSQL database — the probe drops its public schema. SQLite serialises writers,
so these races cannot be demonstrated there at all."""
import json
import os
import pathlib
import subprocess
import sys

import pytest

_URL = os.environ.get("STOCKAI_INTEL_DB", "")
pytestmark = pytest.mark.skipif(not _URL.startswith("postgresql"),
                                reason="needs STOCKAI_INTEL_DB=postgresql://... (throwaway)")
_PROBE = pathlib.Path(__file__).resolve().parent / "_copilot_pg_concurrency.py"


@pytest.fixture(scope="module")
def r():
    proc = subprocess.run([sys.executable, str(_PROBE)], capture_output=True, text=True,
                          timeout=600)
    if proc.returncode != 0:
        pytest.fail(f"probe failed:\n{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}")
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["_meta"]["server"] == "postgresql"
    return out


def test_startup_migration_creates_copilot_schema_idempotently(r):
    m = r["migration"]
    assert m["tables"] == ["copilot_conversations", "copilot_messages"]
    assert m["has_claimed_at"] and m["unique"] == ["user_id", "request_id", "role"]
    assert m["fk_ondelete"] == {"copilot_conversations": "CASCADE", "users": "CASCADE"}
    assert m["second_run_ok"]


@pytest.mark.parametrize("case", ["same_id_first", "same_id_retry"])
def test_simultaneous_same_id_requests_pay_once_and_store_once(r, case):
    c = r[case]
    assert c["model_calls"] == 1, "duplicate billing"
    assert c["assistant_rows"] == 1
    assert c["outcomes"].count("('ok', False)") == 1
    assert all(o in ("('ok', False)", "('err', 409)") for o in c["outcomes"])


def test_first_request_race_leaves_one_conversation_and_replays(r):
    c = r["same_id_first"]
    assert c["user_rows"] == 1 and c["conversations"] == 1 and c["later_replayed"]


def test_budget_ceiling_holds_under_concurrency_and_settles_to_actuals(r):
    b = r["budget"]
    assert b["admitted"] == 3 and b["refused_429"] == 7 and b["model_calls"] == 3
    assert b["day_reserved_after_settle"] == 3 * (900 + 50)
    assert b["unsettled_reservations"] == 0
