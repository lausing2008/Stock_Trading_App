"""D2 price-alert idempotency and D1 watchlist-add disclosure (copilot phase 2 drafts).

Runs `_alert_idempotency_probe.py` on SQLite always, and ALSO on PostgreSQL when $ALERTS_PG_URL
names a throwaway database — there it additionally proves the migration from production's
current table shape and genuinely simultaneous saves.
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest

_PROBE = pathlib.Path(__file__).resolve().parent / "_alert_idempotency_probe.py"
ENGINES = ["sqlite"] + (["postgresql"] if os.environ.get("ALERTS_PG_URL") else [])


@pytest.fixture(scope="module", params=ENGINES)
def r(request):
    env = dict(os.environ)
    if request.param == "sqlite":
        env.pop("ALERTS_PG_URL", None)
    p = subprocess.run([sys.executable, str(_PROBE)], capture_output=True, text=True,
                       timeout=600, env=env)
    if p.returncode != 0:
        pytest.fail(p.stderr[-3000:])
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert out["_meta"]["server"] == request.param
    return out


def test_a_retried_save_returns_the_first_alert(r):
    s = r["sequential"]
    assert s["first"] == ["ok", 201] and s["retry_same"] == ["ok", 200] and s["same_id"]
    assert s["rows_for_key_alice"] == 1


def test_reusing_a_key_with_different_parameters_is_refused(r):
    assert r["sequential"]["same_key_different_alert"] == ["err", 409]


def test_keys_are_scoped_to_the_user_and_optional(r):
    s = r["sequential"]
    assert s["other_user_same_key"] == ["ok", 201]
    assert s["no_key_still_creates_each_time"] == ["ok", "ok"]


def test_simultaneous_saves_with_one_key_create_one_alert(r):
    assert r["simultaneous"] == {"rows": 1, "all_ok": True, "one_id": True, "created_201": 1}


def test_migration_adds_the_columns_and_unique_index_to_an_existing_table(r):
    if r["_meta"]["server"] != "postgresql":
        pytest.skip("the migration path exists only on PostgreSQL")
    m = r["migration"]
    assert m["columns_before"] == [] and m["columns_after"] == ["idempotency_key",
                                                                 "request_fingerprint"]
    assert m["index_unique"]


def test_the_watchlist_preview_discloses_the_effective_email_result(r):
    p = r["preview"]
    assert p["alice"]["will_create"] and "signal-change emails" in p["alice"]["effect"]
    assert "no email address is on your account" in p["bob_no_email_effect"]
    assert "turned off in your alert preferences" in p["alice_prefs_off_effect"]


def test_the_preview_is_read_only_and_matches_what_the_add_does(r):
    p = r["preview"]
    assert p["preview_created_no_list"], "a preview must not create a default list"
    assert p["add_created_subscription"] == 1 and p["after_add"] is True
    assert p["foreign_list"] == 404
