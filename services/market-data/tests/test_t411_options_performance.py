"""T411-IVHV: the three-week gain/loss table — the underlying beside the ATM call and put.

These run the REAL queries against a REAL database in a subprocess (`_t411_perf_probe.py`);
see that file's docstring for why the suite's own process cannot. The point of testing at this
level is that the feature's core decision — WHICH contract counts as "the one that was at the
money" — lives in SQL, and a wrong pick yields a table that is entirely plausible and about a
different instrument than the one it names.

The probe's fixture is built so every answer is known independently: the underlying rises
exactly 1.0 per session from 100 to 114 over 15 sessions (+14.00%), option marks are
intrinsic + a linearly decaying time value, and three expiries exist — one dying inside the
window, one ten days past it, one sixty days past.
"""
import json
import pathlib
import subprocess
import sys

import pytest

_PROBE = pathlib.Path(__file__).resolve().parent / "_t411_perf_probe.py"


def _run(scenario: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(_PROBE), scenario],
        capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, f"probe failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def normal():
    return _run("normal")


# ── which contract gets picked ──────────────────────────────────────────────────────────

def test_the_chosen_strike_is_the_one_nearest_the_money_at_entry(normal):
    """Spot opens at 100.0 and strikes 90/100/110/130 are all listed. Picking anything but 100
    means the table is about a contract the reader did not ask about."""
    assert normal["entry_spot"] == 100.0
    assert normal["contracts"]["call"]["strike"] == 100.0
    assert normal["contracts"]["put"]["strike"] == 100.0


def test_the_chosen_expiry_survives_the_whole_window(normal):
    """Three expiries exist. The one dying inside the window would stop being quoted partway
    down the table and the remaining rows would silently lose their option columns — so the
    nearest expiry with real runway must win, not the nearest expiry outright."""
    for side in ("call", "put"):
        expiry = normal["contracts"][side]["expiry"]
        assert expiry > normal["end_date"], f"{side} expiry {expiry} does not clear the window"
    # And it must be the NEAREST such expiry (10 days past the end), not the 60-day one.
    assert normal["contracts"]["call"]["expiry"] == "2026-10-08"


def test_one_contract_is_held_across_every_row_not_reselected_daily(normal):
    """The whole claim of the table. If the contract were re-picked each session the marks would
    come from different instruments and `marks_available` would not be able to equal the session
    count for a single option symbol."""
    assert normal["sessions"] == 15
    for side in ("call", "put"):
        assert normal["contracts"][side]["marks_available"] == 15


def test_a_contract_with_no_two_sided_quote_at_entry_is_skipped(normal):
    """A midpoint of a one-sided quote is a number with no market behind it. When the 100 strike
    is unquoted at entry the selector must fall back to a real strike rather than use it."""
    unquoted = _run("entry_unquoted")
    for side in ("call", "put"):
        assert unquoted["contracts"][side]["strike"] != 100.0
        assert unquoted["contracts"][side]["entry_mark"] > 0


# ── the arithmetic ──────────────────────────────────────────────────────────────────────

def test_the_underlying_column_matches_an_independently_computed_move(normal):
    """100 -> 114 is +14.00%, and each session is a +1.0 step off a rising base."""
    rows = normal["rows"]
    assert rows[0]["close"] == 100.0
    assert rows[-1]["close"] == 114.0
    assert rows[-1]["cum_pct"] == 14.0
    assert rows[1]["change_pct"] == 1.0            # 100 -> 101
    assert rows[2]["change_pct"] == 0.99           # 101 -> 102, rounded to 2dp


def test_the_first_row_has_no_daily_change_rather_than_a_zero(normal):
    """There is no prior session inside the window, so the change is undefined. A 0.0 would read
    as a real flat day and would be counted in the up/down tally."""
    first = normal["rows"][0]
    assert first["change_pct"] is None
    assert first["call"]["change_pct"] is None
    assert first["put"]["change_pct"] is None
    # Its cumulative figure IS defined — it is the entry, so zero by construction.
    assert first["cum_pct"] == 0.0
    assert first["call"]["cum_pct"] == 0.0


def test_the_option_columns_are_measured_from_the_entry_mark(normal):
    """Call: entry mark 6.0, final mark 14.5 (intrinsic 14 + time value 0.5) = +141.67%.
    Put: entry 6.0, final 0.5 = -91.67%. Both computed by hand from the fixture."""
    last = normal["rows"][-1]
    assert normal["contracts"]["call"]["entry_mark"] == 6.0
    assert last["call"]["cum_pct"] == pytest.approx(141.67, abs=0.01)
    assert last["put"]["cum_pct"] == pytest.approx(-91.67, abs=0.01)


def test_the_call_and_the_underlying_can_disagree_in_magnitude(normal):
    """The reason the table exists. The stock moved +14% while the call moved +141% — the two
    columns are not scaled versions of each other, and a table showing only the underlying
    cannot tell the reader what the option actually did."""
    assert normal["summary"]["underlying"]["total_pct"] == 14.0
    assert normal["summary"]["call"]["total_pct"] > 100.0
    assert normal["summary"]["put"]["total_pct"] < 0.0


def test_each_row_carries_the_quoted_spread(normal):
    """Marks are midpoints, and a midpoint is not a fill. The spread has to travel with the
    number so the reader can see the gap rather than be asked to assume it away."""
    for row in normal["rows"]:
        assert row["call"]["spread_pct"] is not None
        assert row["call"]["spread_pct"] > 0


def test_the_mark_basis_is_stated_in_the_payload(normal):
    assert "midpoint" in normal["mark_basis"].lower()


# ── the tally ───────────────────────────────────────────────────────────────────────────

def test_up_down_counts_exclude_the_undefined_first_session(normal):
    """15 sessions give 14 measurable day-over-day changes. Counting the first as flat would
    inflate every denominator on the table by one."""
    s = normal["summary"]
    assert s["underlying"]["measured_sessions"] == 14
    assert s["underlying"]["up"] == 14
    assert s["underlying"]["down"] == 0
    assert s["underlying"]["up"] + s["underlying"]["down"] + s["underlying"]["flat"] == 14


def test_the_put_tally_is_the_mirror_of_the_call_on_a_one_way_move(normal):
    s = normal["summary"]
    assert s["call"]["up"] == 14 and s["call"]["down"] == 0
    assert s["put"]["down"] == 14 and s["put"]["up"] == 0


# ── the unavailable cases, which must be distinguishable ────────────────────────────────

def test_a_symbol_with_no_archived_chain_says_so():
    """The archive covers 35 symbols, so this is the COMMON case, not an edge case. It must be
    reported as its own reason rather than as an error or an empty table."""
    out = _run("no_option_history")
    assert out["available"] is False
    assert out["reason"] == "no_option_history"
    assert out["sessions_available"] == 0


def test_a_symbol_with_only_one_archived_session_is_a_different_reason():
    """'We do not archive this symbol' and 'we do, but cannot compare one day to itself' call
    for completely different responses from a reader, so they must not share a reason string."""
    out = _run("one_session_only")
    assert out["available"] is False
    assert out["reason"] == "insufficient_option_history"
    assert out["sessions_available"] == 1


def test_no_contract_with_runway_still_returns_the_underlying_but_says_why():
    """The archive has sessions but every listed expiry dies inside the window. The underlying
    column is still real and worth showing; what must not happen is showing it with empty option
    columns and no explanation, which reads as 'the options did nothing'."""
    out = _run("expiry_inside_only")
    assert out["available"] is True
    assert out["contracts"] == {}
    assert out["contracts_reason"] == "no_contract_with_runway"
    assert out["summary"]["underlying"]["total_pct"] is not None
    assert "call" not in out["summary"]


def test_a_missing_capture_day_leaves_a_hole_rather_than_carrying_a_stale_mark():
    """The archive job has misfired before (see docs/incidents/scheduler-misfire-data-gaps.md).
    A missing day must not be filled with the previous day's mark, which would show a real
    position as having been flat on a day it was not."""
    out = _run("mid_window_gap")
    assert out["available"] is True
    assert out["contracts"]["call"]["marks_available"] == 14, "the gap must not be back-filled"
    gapped = [r for r in out["rows"] if r["call"]["mark"] is None]
    assert len(gapped) == 1
    assert gapped[0]["close"] is not None, "the underlying is still known on that day"
