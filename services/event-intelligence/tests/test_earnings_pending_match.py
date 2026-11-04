"""AUD-EARNINGS-PENDINGMATCH: each reported quarter is written to its own row, on PostgreSQL.

From Oct 19 to Nov 3 2026, 1 of 48 earnings reports was recorded. Both write paths took "the
first row with eps_actual IS NULL" as the event, the history pass then tried to move the next
upcoming row onto its oldest quarter's date, and the refused update poisoned the session so every
later row was dropped. The old source-text tests asserted that lookup was present. The probe runs
the real _fetch_earnings_for_symbol against PostgreSQL; SQLite does not reproduce the poisoning.
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest

URL = os.environ.get("EARNINGS_PG_URL")
REQUIRED = os.environ.get("EARNINGS_PG_REQUIRED") == "1"


@pytest.mark.skipif(not URL and not REQUIRED, reason="EARNINGS_PG_URL is absent; required in make test-integration-pg-schema")
def test_each_reported_quarter_lands_on_its_own_row():
    assert URL, "EARNINGS_PG_REQUIRED=1 but EARNINGS_PG_URL is not set"
    run = subprocess.run([sys.executable, str(pathlib.Path(__file__).with_name("_earnings_pending_match_probe.py"))],
                         capture_output=True, text=True, timeout=180)
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-4000:]
    r = json.loads(run.stdout.strip().splitlines()[-1])

    # The production failure: the newest quarter lands on its pending row at the real date, no row
    # is dropped, and the calendar adds the next quarter.
    assert r["reported"]["rows"] == [["2026-02-12", 5.03], ["2026-05-04", 4.47], ["2026-08-03", 4.73],
                                     ["2026-11-04", 4.76], ["2027-02-10", None]]
    assert r["reported"]["dropped"] == {} and r["reported"]["written"] == 4
    assert r["reported"]["newest"] == "2026-11-04"

    # Two projections of one report become one, and its issuer document follows the kept row.
    assert r["duplicates"]["rows"] == [["2026-05-06", 0.13], ["2026-11-04", None]]
    assert r["duplicates"]["doc_on"] == "2026-11-04"

    # HK rows without EPS stay where they are; the projection gets its own row.
    assert r["hk_no_eps"]["rows"] == [["2025-12-31", None], ["2026-03-31", None], ["2026-06-30", None],
                                      ["2026-08-27", None]]

    # A period-end stand-in moves to the real date once known; an existing stand-in duplicate is
    # folded into the real-date row.
    assert r["stand_in"]["rows"] == [["2026-08-05", 0.48], ["2026-11-03", 0.54], ["2027-02-04", None]]

    # A calendar still showing the past report writes nothing.
    assert r["stale_calendar"]["rows"] == [["2026-10-29", 1.89]]

    # One failing row is counted and the others are still written.
    assert r["isolated"]["rows"] == [["2026-05-01", 1.1], ["2026-10-30", 1.3]]
    assert sum(r["isolated"]["dropped"].values()) == 1 and "PendingRollbackError" not in r["isolated"]["dropped"]
    assert "earnings.history_row_failed" in r["warnings"]
