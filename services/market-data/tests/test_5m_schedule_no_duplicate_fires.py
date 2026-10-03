"""AUD-5M-DUPLICATE-9AM — two scheduled jobs ingested the same bars in the same minute.

THE DEFECT. `us_premarket_5m_9am` fired `_refresh_premarket_5m` at 9:00-9:25 ET, justified in
its own comment as handing off to "us_5m_intraday's own 9:30 start". us_5m_intraday has no
9:30 start: its hour list begins at 9 and its minute list is "30,...,55,0,...,25", and a cron
minute list applies to EVERY hour in the hour list — so it already fired at 9:00, 9:05, 9:10,
9:15, 9:20 and 9:25, the exact six slots the premarket job existed to cover.

Both called `ingest_universe(_symbols_for("US"), "5m")` within the same minute: ~852 duplicate
provider calls per trading day against an API measured refusing 6,566 requests in 12 hours,
plus a same-row race of the same shape the close-burst lock already exists for.

The comment was not careless — it was a correct intent defeated by cron semantics, which is
exactly the kind of thing a schedule test catches and a reading of the code does not. These
tests compute the real fire times from the real triggers rather than describing them.
"""
import ast
import pathlib
import re

_SRC = (pathlib.Path(__file__).resolve().parents[1]
        / "src" / "services" / "scheduler.py").read_text()


def _job_triggers() -> dict[str, dict]:
    """Every add_job's id -> its CronTrigger kwargs, parsed from the real source."""
    tree = ast.parse(_SRC)
    out: dict[str, dict] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_job"):
            continue
        # Some job ids are f-strings (built per market); those are not the ones under test and
        # literal_eval would raise on them, so they are skipped rather than crashing collection.
        _id_node = next((k.value for k in node.keywords if k.arg == "id"), None)
        if not isinstance(_id_node, ast.Constant) or not isinstance(_id_node.value, str):
            continue
        job_id = _id_node.value
        trig = next((a for a in node.args
                     if isinstance(a, ast.Call) and getattr(a.func, "id", "") == "CronTrigger"), None)
        if trig is None:
            continue
        kw = {}
        for k in trig.keywords:
            try:
                kw[k.arg] = ast.literal_eval(k.value)
            except Exception:
                kw[k.arg] = None
        out[job_id] = kw
    return out


def _fire_slots(kw: dict) -> set[tuple[str, int, int]]:
    """(timezone, hour, minute) slots a cron trigger actually fires on.

    The whole point: a minute list applies to EVERY hour in the hour list. Expanding the
    cross-product is what makes the overlap visible; reading the strings is what hid it.
    """
    def _expand(v, default):
        if v is None:
            return default
        return [int(x) for x in str(v).split(",")]
    hours = _expand(kw.get("hour"), [])
    minutes = _expand(kw.get("minute"), [0])
    tz = kw.get("timezone") or "?"
    return {(tz, h, m) for h in hours for m in minutes}


TRIGGERS = _job_triggers()


def test_the_redundant_9am_premarket_job_is_gone():
    assert "us_premarket_5m_9am" not in TRIGGERS


def test_no_two_us_5m_jobs_fire_in_the_same_slot():
    """THE REGRESSION GUARD, stated as the property rather than the symptom."""
    us_jobs = {jid: kw for jid, kw in TRIGGERS.items()
               if "5m" in jid and (kw.get("timezone") or "") == "America/New_York"}
    assert us_jobs, "the US 5m jobs were not found — has this file moved?"
    seen: dict[tuple, str] = {}
    clashes = []
    for jid, kw in us_jobs.items():
        for slot in _fire_slots(kw):
            if slot in seen:
                clashes.append(f"{seen[slot]} and {jid} both fire at {slot[1]:02d}:{slot[2]:02d}")
            seen[slot] = jid
    assert not clashes, "duplicate 5m ingests: " + "; ".join(sorted(clashes)[:8])


def test_the_premarket_job_still_covers_the_premarket_window():
    """Removing the duplicate must not leave a gap — the premarket-gappers email depends on
    PRE-session rows existing from 4:00 ET."""
    slots = _fire_slots(TRIGGERS["us_premarket_5m_early"])
    hours = {h for _, h, _ in slots}
    assert hours == {4, 5, 6, 7, 8}
    assert len(slots) == 5 * 12, "every five minutes across the window"


def test_the_intraday_job_still_covers_from_9am_so_no_gap_was_created():
    """The six slots the removed job used to cover must still be ingested by somebody."""
    slots = _fire_slots(TRIGGERS["us_5m_intraday"])
    for minute in (0, 5, 10, 15, 20, 25):
        assert ("America/New_York", 9, minute) in slots, \
            f"9:{minute:02d} is no longer ingested by any job"


def test_the_stale_handoff_comment_is_gone():
    """The comment asserted a 9:30 start that never existed. Leaving it would teach the next
    reader the same wrong thing.

    The replacement explains the defect WITHOUT reproducing the phrase — the fourth time this
    session that a comment describing an old string matched a test looking for it."""
    assert "us_5m_intraday's own 9:30 start" not in _SRC


def test_hk_and_us_5m_jobs_are_in_their_own_timezones():
    """A shared slot across different timezones is not a clash — this guards the guard."""
    assert TRIGGERS["hk_5m_intraday"]["timezone"] == "Asia/Hong_Kong"
    assert TRIGGERS["us_5m_intraday"]["timezone"] == "America/New_York"


def test_the_duplicate_detector_would_actually_catch_a_reintroduction():
    """A test that cannot fail is not a test. Feed it the OLD schedule and require a clash."""
    old = {
        "us_premarket_5m_9am": {"hour": "9", "minute": "0,5,10,15,20,25",
                                "timezone": "America/New_York"},
        "us_5m_intraday": {"hour": "9,10,11,12,13,14,15",
                           "minute": "30,35,40,45,50,55,0,5,10,15,20,25",
                           "timezone": "America/New_York"},
    }
    seen: dict[tuple, str] = {}
    clashes = []
    for jid, kw in old.items():
        for slot in _fire_slots(kw):
            if slot in seen:
                clashes.append(slot)
            seen[slot] = jid
    assert len(clashes) == 6, f"the historical overlap was six slots, found {len(clashes)}"
