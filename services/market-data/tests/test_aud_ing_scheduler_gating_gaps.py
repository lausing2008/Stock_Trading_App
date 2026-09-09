"""AUD-ING-*-HOLIDAYBLIND / WEEKENDFIRE / NOMISFIREGRACE — four scheduler gating gaps.

All four were found in ONE pass while building the Data Pipeline reference page, and all four are
the same family as `AUD-DIGEST-HOLIDAYBLIND` (13 emails sent on Labor Day):

    A `mon-fri` cron is not a market-open check, and `weekday() < 5` is not a trading day.

They are fixed differently ON PURPOSE, because the cost of firing on a non-trading day differs:

  1. `live_price_cache_refresh`  — REAL COST, gated in-function on each market's own calendar.
     `refresh_live_price_cache()` runs a yfinance bulk download over every active non-delisted
     stock (~173). On a US holiday that is ~480 downloads (8h x 60/h) against the one source that
     actually rate-limits this platform, and it writes STALE quotes into `stockai:live_prices`,
     which all 16 every-minute alert scanners read.

  2. `edgar_8k_ingest_daily` — REAL COST, gated in-function. The sweep is rate-limited at
     0.15s/CIK to respect SEC fair-use, so a no-op holiday pass still spends real time against
     SEC's budget and records an "ok" job status for a day with no data. Its HK sibling
     `_ingest_hk_connect_flows()` has always had this guard.

  3. The six `18:0x` evaluators — WASTE ONLY, fixed at the TRIGGER with `day_of_week="mon-fri"`.
     They resolve forward returns by querying real `Price` rows, not calendar arithmetic, so a
     weekend run finds no new bars and exits. Deliberately NOT given an internal date guard: that
     would also suppress a legitimate Monday catch-up after a missed Friday run.

  4. `avg_volume_cache_refresh` — the only interval job in the file with no `misfire_grace_time`.
     Same shape as `AUD-MISFIREGRACE-OPTIONSFLOW`, where 3 of 17 minute-jobs died after one run.
"""
import pathlib
import re

import pytest

SCHED = pathlib.Path(__file__).resolve().parents[1] / "src/services/scheduler.py"
SRC = SCHED.read_text()

_EVALUATORS = [
    "value_area_levels_daily",
    "fix_effectiveness_recheck_daily",
    "squeeze_alert_outcome_eval_daily",
    "prebreakout_alert_outcome_eval_daily",
    "options_flow_alert_outcome_eval_daily",
    "dark_pool_alert_outcome_eval_daily",
]


def _registration_block(job_id: str) -> str:
    """The add_job(...) text immediately preceding this id — i.e. its OWN trigger.

    Deliberately NOT a backwards scan from a trigger line: that pairs a trigger with the
    PRECEDING job's id and is exactly the off-by-one that produced three retracted findings
    earlier in this same session.
    """
    i = SRC.index(f'id="{job_id}"')
    start = SRC.rindex("add_job(", 0, i)
    # Extend PAST the id to the end of the call — kwargs such as misfire_grace_time may follow
    # it. Bounded by the next add_job( so a block can never swallow the following job's kwargs.
    try:
        end = SRC.index("add_job(", i)
    except ValueError:
        end = len(SRC)
    return SRC[start:end]


# ── 1. live_price_cache_refresh ─────────────────────────────────────────────────────────

def _live_fn() -> str:
    i = SRC.index("def _live_price_refresh_job()")
    return SRC[i:SRC.index("_scheduler.add_job(", i)]


def test_live_price_uses_real_trading_day_checks():
    """THE FIX. `weekday() >= 5` excludes weekends but not holidays."""
    fn = _live_fn()
    assert "_is_us_trading_day(" in fn
    assert "_is_hk_trading_day(" in fn


def test_live_price_no_longer_relies_on_a_bare_weekday_check():
    assert "weekday >= 5" not in _live_fn()


def test_live_price_checks_each_market_separately():
    """A US holiday is very often a normal HKEX session and vice versa. Collapsing the two into
    one flag would over- or under-refresh roughly half the time."""
    fn = _live_fn()
    assert "us_open = (" in fn and "hk_open = (" in fn
    assert fn.index("_is_us_trading_day(") < fn.index("_is_hk_trading_day(")


def test_live_price_keeps_its_hour_window():
    """The trading-day check REPLACES the weekday test, not the session-hours test."""
    fn = _live_fn()
    assert "9 <= now_et.hour < 17" in fn
    assert "9 <= now_hk.hour < 17" in fn


def test_live_price_passes_the_market_local_time_to_its_own_check():
    """Passing the wrong zone's datetime would evaluate the wrong calendar date near midnight —
    the same timezone trap as AUD-EXIT-HKENTRYDATE."""
    fn = _live_fn()
    assert "_is_us_trading_day(now_et)" in fn
    assert "_is_hk_trading_day(now_hk)" in fn


# ── 2. EDGAR ────────────────────────────────────────────────────────────────────────────

def _edgar_fn() -> str:
    i = SRC.index("def _ingest_edgar_8k()")
    return SRC[i:SRC.index("\ndef ", i + 10)]


def test_edgar_now_checks_the_us_trading_calendar():
    """Its HK sibling always did; this is the same guard on the same class of job."""
    assert "if not _is_us_trading_day():" in _edgar_fn()


def test_edgar_returns_before_posting_on_a_non_trading_day():
    fn = _edgar_fn()
    guard = fn.index("if not _is_us_trading_day():")
    post = fn.index("_post(")
    assert guard < post, "the guard must precede the outbound POST"
    assert "return" in fn[guard:post]


def test_edgar_still_records_a_job_status_when_it_skips():
    """A skip that records nothing looks identical to a job that silently died — the liveness
    gauges would start reporting it as missing."""
    fn = _edgar_fn()
    block = fn[fn.index("if not _is_us_trading_day():"):]
    assert "_record_job_status(" in block.split("return")[0]
    assert "edgar.ingest_skipped" in block


def test_the_hk_sibling_still_has_its_own_guard():
    """Guards against a 'consolidation' that removes the one that was already right."""
    i = SRC.index("def _ingest_hk_connect_flows()")
    fn = SRC[i:SRC.index("\ndef ", i + 10)]
    assert "_is_hk_trading_day()" in fn


# ── 3. The six 18:0x evaluators ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("job_id", _EVALUATORS)
def test_evaluator_no_longer_fires_on_weekends(job_id):
    """THE FIX. These were the only daily crons in the file without day_of_week."""
    assert 'day_of_week="mon-fri"' in _registration_block(job_id)


@pytest.mark.parametrize("job_id", _EVALUATORS)
def test_evaluator_keeps_its_time_and_timezone(job_id):
    """Adding day_of_week must not disturb the deliberately staggered 18:00/05/15/20/25/30
    sequence — they run in order so they don't contend on the DB at once."""
    blk = _registration_block(job_id)
    assert 'timezone="America/New_York"' in blk
    assert "hour=18" in blk or "hour=18," in blk


def test_the_six_evaluators_remain_staggered():
    minutes = []
    for job_id in _EVALUATORS:
        m = re.search(r"minute=(\d+)", _registration_block(job_id))
        minutes.append(int(m.group(1)))
    assert minutes == sorted(minutes), "still in ascending order"
    assert len(set(minutes)) == len(minutes), "no two evaluators share a minute"


def test_the_reasoning_for_a_trigger_fix_is_recorded():
    """Why this is a cadence fix and not an internal date guard — so nobody 'hardens' it into
    one and breaks Monday catch-up."""
    i = SRC.index("AUD-ING-EVALUATORS-WEEKENDFIRE")
    block = SRC[i:i + 1800]
    assert "Monday catch-up" in block or "Monday catch-up" in block.replace("\n    # ", " ")
    assert "not by calendar arithmetic" in block.replace("\n    # ", " ")


# ── 4. avg_volume_cache_refresh ─────────────────────────────────────────────────────────

def test_avg_volume_now_has_a_misfire_grace_time():
    assert "misfire_grace_time=300" in _registration_block("avg_volume_cache_refresh")


def test_every_interval_job_now_declares_a_misfire_grace_time():
    """THE PARITY ASSERTION THAT WOULD HAVE CAUGHT THIS. AUD-MISFIREGRACE-OPTIONSFLOW killed 3 of
    17 minute-jobs through exactly this omission, so the check belongs repo-wide, not per-job."""
    offenders = []
    for m in re.finditer(r'"interval",', SRC):
        blk = SRC[m.start():m.start() + 420]
        jid = re.search(r'id="([^"]+)"', blk)
        if jid and "misfire_grace_time" not in blk:
            offenders.append(jid.group(1))
    assert offenders == [], f"interval jobs with no misfire_grace_time: {offenders}"


def test_the_grace_is_wider_than_the_minute_jobs():
    """A 4-hourly job has no reason to be strict about a few minutes of lateness, and a wider
    grace makes a DROP less likely, not more."""
    blk = _registration_block("avg_volume_cache_refresh")
    grace = int(re.search(r"misfire_grace_time=(\d+)", blk).group(1))
    assert grace > 60


# ── The shared lesson ───────────────────────────────────────────────────────────────────

def test_all_four_fixes_name_the_bug_class():
    """So a future reader finds the family, not four unrelated one-offs."""
    for marker in (
        "AUD-ING-LIVEPRICE-HOLIDAYBLIND",
        "AUD-ING-EDGAR-HOLIDAYBLIND",
        "AUD-ING-EVALUATORS-WEEKENDFIRE",
        "AUD-ING-AVGVOL-NOMISFIREGRACE",
    ):
        assert marker in SRC, f"{marker} not recorded in source"


def test_the_shared_calendar_is_the_only_holiday_source():
    """These fixes must not hand-roll a holiday list — there were once FOUR drifted copies."""
    fn = _live_fn() + _edgar_fn()
    assert "NYSE_HOLIDAYS = frozenset({" not in fn
    assert "date(20" not in fn
