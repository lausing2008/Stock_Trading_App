"""AUD-PAPER-PREMARKET — a trading DAY is not a trading HOUR.

THE DEFECT. `_refresh_5m` gated only on `_is_us_trading_day()` / `_is_hk_trading_day()` — a
DATE check. Its cron fires from 9:00 local (the minute list covers the whole hour and the hour
list starts at 9), so the paper-trading monitor ran at 9:00-9:25 for BOTH markets, before
either exchange opened. `paper_trading_step` has no session gate of its own.

MEASURED, not hypothetical. Two US exits fired at 09:00 ET, and one is exactly the failure
mode `AUD-PT-CROSSMARKETSWEEP` already documented:

    SCHD  exited 2026-09-16 09:00:58 ET at $34.2955
          that day's range was 33.44 - 34.03 — the exit is ABOVE THE HIGH
          booked +$96.30 on a fill that could not have happened

At 09:00 the freshest REGULAR-session price available is the prior day's close. The other
(NATL, 2026-09-22 09:00:45, $46.03) landed inside its day's range, so it is merely premature
rather than impossible — the distinction is worth keeping: one is a wrong number, the other is
a right number at the wrong time.

THE FIX IS A SESSION PREDICATE, NOT A SCHEDULE EDIT. Narrowing the cron would have worked
until someone edited the cron. Gating on the venue's actual session protects every caller of
`_refresh_5m`, including ones not yet written, and it fixed HK in the same change — HK's own
job fires at 9:00-9:25 HKT, before HKEX opens at 9:30, which the schedule-shaped fix would
have missed entirely.

INGESTION STAYS UNGATED. Premarket bars are wanted; the premarket-gappers brief depends on
them. Only the steps that move money are gated.
"""
import ast
import pathlib
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "shared"))

_SCHED = (pathlib.Path(__file__).resolve().parents[1]
          / "src" / "services" / "scheduler.py").read_text()

from common.market_calendar import is_regular_session  # noqa: E402

_NY, _HK = ZoneInfo("America/New_York"), ZoneInfo("Asia/Hong_Kong")


def _at(tz, y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=tz).astimezone(timezone.utc)


# ── the predicate ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("h,m,expected", [
    (9, 0, False), (9, 25, False), (9, 29, False),   # the window that traded
    (9, 30, True), (12, 0, True), (15, 59, True),
    (16, 0, False), (16, 5, False), (20, 0, False),  # close is exclusive
    (4, 0, False), (8, 55, False),                   # premarket ingest window
])
def test_us_regular_session_boundaries(h, m, expected):
    assert is_regular_session("US", _at(_NY, 2026, 9, 22, h, m)) is expected


@pytest.mark.parametrize("h,m,expected", [
    (9, 0, False), (9, 25, False),                   # HK had the same defect
    (9, 30, True), (11, 59, True),
    (12, 0, False), (12, 30, False), (12, 59, False),  # the lunch closure is real
    (13, 0, True), (15, 59, True), (16, 0, False),
])
def test_hk_regular_session_boundaries_including_lunch(h, m, expected):
    assert is_regular_session("HK", _at(_HK, 2026, 9, 22, h, m)) is expected


def test_a_holiday_is_never_a_session_however_good_the_time_looks():
    """2026-10-01 is an HKEX holiday; 11:00 HKT would otherwise be mid-session."""
    assert is_regular_session("HK", _at(_HK, 2026, 10, 1, 11, 0)) is False
    assert is_regular_session("US", _at(_NY, 2026, 9, 7, 11, 0)) is False  # Labor Day


def test_a_weekend_is_never_a_session():
    assert is_regular_session("US", _at(_NY, 2026, 9, 19 + 1, 11, 0)) is False  # Sunday


# ── the gate, in the real source ────────────────────────────────────────────────────────

def _refresh_5m_body() -> str:
    tree = ast.parse(_SCHED)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_refresh_5m")
    return ast.get_source_segment(_SCHED, fn) or ""


def test_the_paper_trading_step_is_gated_on_the_session():
    body = _refresh_5m_body()
    i = body.index("_run_paper_trading_step(")
    guard = body[:i]
    assert "_is_regular_session(market)" in guard, \
        "the money-moving step must be gated on the venue's session"


def test_the_intraday_trigger_check_is_gated_too():
    """It arms real intraday entries off an ATR cross; a cross computed against a premarket
    print is not a cross the session has made."""
    body = _refresh_5m_body()
    i = body.index("_check_short_intraday_triggers(market)")
    assert "if _is_regular_session(market):" in body[max(0, i - 300):i]


def test_ingestion_is_NOT_gated():
    """The premarket-gappers brief depends on these bars existing. Gating the ingest would
    fix the trading defect by breaking a feature."""
    body = _refresh_5m_body()
    ingest_at = body.index('ingest_universe(symbols, "5m")')
    gate_at = body.index("_is_regular_session(market)")
    assert ingest_at < gate_at, "the ingest must run before, and independently of, the gate"


def test_skipping_is_observable():
    """A silent skip is indistinguishable from a broken scheduler."""
    assert "scheduler.paper_trading_5m_outside_session" in _SCHED


def test_the_session_predicate_is_shared_not_a_local_copy():
    """Four copies of the NYSE holiday table was this codebase's last lesson on this."""
    assert "from common.market_calendar import is_regular_session" in _SCHED
    body = _refresh_5m_body()
    for inlined in ("570", "960", "hour * 60"):
        assert inlined not in body, f"{inlined} looks like inlined session arithmetic"


# ── premarket cadence ───────────────────────────────────────────────────────────────────

def _trigger(job_id: str) -> dict:
    for n in ast.walk(ast.parse(_SCHED)):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "add_job"):
            continue
        idn = next((k.value for k in n.keywords if k.arg == "id"), None)
        if not isinstance(idn, ast.Constant) or idn.value != job_id:
            continue
        trig = next((a for a in n.args if isinstance(a, ast.Call)
                     and getattr(a.func, "id", "") == "CronTrigger"), None)
        return {k.arg: (k.value.value if isinstance(k.value, ast.Constant) else None)
                for k in trig.keywords}
    raise AssertionError(f"{job_id} not found")


def test_premarket_ingest_runs_every_15_minutes_not_every_5():
    kw = _trigger("us_premarket_5m_early")
    minutes = [int(x) for x in str(kw["minute"]).split(",")]
    assert minutes == [0, 15, 30, 45]
    assert len([int(x) for x in str(kw["hour"]).split(",")]) == 5
    assert len(minutes) * 5 == 20, "20 fires/day, down from 60"


def test_the_last_premarket_tick_precedes_the_brief_that_reads_it():
    """The 08:00 ET brief takes the latest PRE bar. A tick in the same minute would race it."""
    pre = _trigger("us_premarket_5m_early")
    brief = _trigger("premarket_brief_us")
    last_tick = max(int(x) for x in str(pre["minute"]).split(","))
    assert int(brief["hour"]) == 8 and int(brief["minute"]) == 0
    assert last_tick == 45, "the final premarket tick must land before the brief, not on it"


def test_near_the_open_keeps_five_minute_cadence():
    """The reduction is confined to the quiet premarket window."""
    kw = _trigger("us_5m_intraday")
    minutes = sorted(int(x) for x in str(kw["minute"]).split(","))
    assert minutes == [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55]
