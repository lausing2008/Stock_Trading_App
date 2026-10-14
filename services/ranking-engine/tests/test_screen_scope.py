"""COPILOT-SCREEN: /rankings/screen's scope and coverage, against a real database.

The copilot restricts a non-admin's screen to their own watchlist and must say how much it
actually searched. `_screen_probe.py` runs the route's real SQL on SQLite; these assert it.
"""
import json
import pathlib
import subprocess
import sys

import pytest

_PROBE = pathlib.Path(__file__).resolve().parent / "_screen_probe.py"


@pytest.fixture(scope="module")
def r():
    p = subprocess.run([sys.executable, str(_PROBE)], capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        pytest.fail(p.stderr[-3000:])
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_existing_callers_are_unchanged(r):
    u = r["unscoped"]
    assert u["total"] == 4 and u["symbols"] == ["DDD", "AAA", "CCC", "BBB"]
    assert not u["has_scope_keys"]


def test_scope_restricts_the_universe_and_reports_coverage(r):
    s = r["scoped"]
    assert s["symbols"] == ["AAA"], "only in-scope stocks above the floor"
    assert s["universe"] == 2 and s["requested"] == 4
    assert s["unranked"] == ["OLD", "ZZZ"], "requested but never screened must be listed"


def test_an_empty_scope_screens_nothing_rather_than_everything(r):
    assert r["empty_scope"] == {"total": 0, "universe": 0}
