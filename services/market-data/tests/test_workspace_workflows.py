"""Behavioral tests through real SQLAlchemy tables and the provider boundary."""

import json, pathlib, subprocess, sys
import pytest


@pytest.fixture(scope="module")
def r():
    p = subprocess.run(
        [
            sys.executable,
            str(pathlib.Path(__file__).with_name("_workspace_workflows_probe.py")),
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert p.returncode == 0, p.stderr[-4000:]
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_account_layout_compare_and_swap_and_isolation(r):
    assert r["layout"]["created"] == 1 and r["layout"]["updated"] == 2
    assert r["layout"]["stale_create"] is None and r["layout"]["other_missing"]
    assert (
        r["layout"]["owner_layout"]["order"] == ["plan", "chart"] and r["extra_refused"]
    )


def test_close_requires_fresh_post_close_provider_data(r):
    assert r["intraday"] != "triggered" and r["confirmed"] == "triggered"
    assert r["stale_observation"] == "stale" and r["missing"] == "unavailable"


def test_exchange_sessions_and_calendar_gaps(r):
    assert r["weekend"] == "2026-10-09" and r["holiday"] == "2026-12-24"
    assert r["hk_close"] == "2026-10-09" and r["calendar_gap"] is None


def test_close_event_is_one_shot_and_durable(r):
    assert r["one_notification"] == 1 and r["deactivated"]


def test_connections_require_reviewed_relationships_and_deduplicate(r):
    assert (
        r["unapproved_no_candidates"] == 0
        and r["approved_candidates"] == 1
        and r["duplicate_candidates"] == 0
    )
    assert (
        r["pending"] == "pending"
        and r["has_falsifier"]
        and r["relationships_frozen"].startswith("https://")
    )
    assert len(r["new_tables"]) == 4


def test_actual_routes_preserve_owner_scope_and_request_identity(r):
    assert r["route_idempotent"] and r["route_other_empty"] and r["route_other_delete"]
    assert r["route_key_conflict"] and r["route_disabled_replay"]
    assert r["unpublished_hidden"] and r["review_queue_guard"]


def test_publication_and_revisions_keep_prospective_evidence(r):
    assert r["frozen_baseline"] == 50 and r["second_review_refused"]
    assert abs(r["prospective_return"] - 10) < 1e-9
    assert r["revision_links_previous"] and r["history_preserved"]
    assert r["skipped_revision_supersedes"] and r["old_pending_refused"]
