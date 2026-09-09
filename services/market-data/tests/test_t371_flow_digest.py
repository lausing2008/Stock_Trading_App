"""T371-FLOW-DIGEST — dark pool + unusual options activity summary, 3x daily.

USER REQUEST: a summary report on dark pool and unusual options activity, "maybe 3 times daily?
Mid of the market open and 30 mins after the end of the market. And one more at 11:00pm PST."

THE SCHEDULE CAME FROM MEASURED DATA, and two of the three proposed times moved. Across 337
dark-pool and 1,587 options-flow alerts, by ET hour of firing:

    window                      dark pool   options flow
    first 90 min (09:30-11:00)      26%          71%
    midday       (11:00-16:00)       4%           5%     <- the ORIGINAL mid-session slot
    02:00 ET                          ~0     1 alert ever

So institutional flow front-loads hard into the open. A ~12:45 ET digest would have sat in the
4-5% dead zone reporting flow from three hours earlier, so it moved to 11:00 ET. The 16:30 ET
slot was kept exactly as asked.

THE 23:00 PST SLOT IS A FULL-SESSION RECAP, NOT A WINDOW. At 02:00 ET essentially nothing fires,
so a "last few hours" digest there would always be empty. The user's reason — "I would like to
see before I sleep" — is served by recapping the whole session instead.

A CORRECTION TO MY OWN FIRST READING of that distribution: the apparent large "20:00 ET" cluster
is NOT organic firing. It is `options_flow_eod`'s 17:00 ET snapshot job, and 293 of its 383 rows
landed on a single day (2026-09-01) — a backfill. Real intraday firing is even more concentrated
in the open than the raw percentages suggested.

THE HIT RATES ARE THE POINT, and the first live render shows why:

    dark pool    46% next-day (n=93)     <- coin flip
    options flow 40% next-day (n=1475)   <- BELOW coin flip, on a large sample

A digest of institutional prints without that context would read as a stream of actionable
signals. With it, the reader can see what these alerts have actually been worth.
"""
import pathlib
from datetime import date, datetime, timedelta, timezone, time as dtime
from zoneinfo import ZoneInfo

import pytest

SCHED = pathlib.Path(__file__).resolve().parents[1] / "src/services/scheduler.py"
SRC = SCHED.read_text()


# ── The schedule ────────────────────────────────────────────────────────────────────────

def _job_block(job_id: str) -> str:
    """The add_job(...) text for this id, parsed FORWARD from its own add_job( call.

    Deliberately not a backwards scan: that pairs a trigger with the PRECEDING job's id and
    produced three retracted findings earlier in this same session.
    """
    i = SRC.index(f'id="{job_id}"')
    start = SRC.rindex("_scheduler.add_job(", 0, i)
    return SRC[start:i]


@pytest.mark.parametrize("job_id,hour,minute,tz", [
    ("flow_digest_morning", 11, 0, "America/New_York"),
    ("flow_digest_close", 16, 30, "America/New_York"),
    ("flow_digest_night", 23, 0, "America/Los_Angeles"),
])
def test_all_three_jobs_are_registered_at_the_measured_times(job_id, hour, minute, tz):
    blk = _job_block(job_id)
    assert f"hour={hour}" in blk
    assert f"minute={minute}" in blk
    assert f'timezone="{tz}"' in blk


def test_the_night_run_is_scheduled_in_PACIFIC_not_eastern():
    """23:00 PST is 02:00 ET the FOLLOWING day. Scoping mon-fri in Eastern would shift the whole
    week by one day and drop Friday's recap entirely."""
    assert 'timezone="America/Los_Angeles"' in _job_block("flow_digest_night")


@pytest.mark.parametrize("job_id", ["flow_digest_morning", "flow_digest_close",
                                    "flow_digest_night"])
def test_every_job_carries_day_of_week(job_id):
    """AUD-ING-EVALUATORS-WEEKENDFIRE: six sibling jobs in this file fired on weekends because
    they omitted this."""
    assert 'day_of_week="mon-fri"' in _job_block(job_id)


def test_the_midday_slot_was_NOT_used():
    """The user's original ~12:45 ET idea sat in the measured 4-5% dead zone."""
    for job_id in ("flow_digest_morning", "flow_digest_close", "flow_digest_night"):
        blk = _job_block(job_id)
        assert "hour=12" not in blk and "hour=13" not in blk


def test_only_the_night_run_uses_the_session_lookback():
    assert 'send_flow_digest("session", "flow_digest_night")' in SRC
    assert 'send_flow_digest("intraday", "flow_digest_morning")' in SRC
    assert 'send_flow_digest("intraday", "flow_digest_close")' in SRC


def test_the_jobs_are_gated_behind_alerting_enabled():
    """BUG-LOCALDEV-ALERTS-UNGATED: these send real email, so a local dev stack must not."""
    i = SRC.index('id="flow_digest_morning"')
    assert "if _is_alerting_enabled():" in SRC[max(0, i - 2600):i]


# ── The window ──────────────────────────────────────────────────────────────────────────

def _window(lookback: str, now_utc: datetime) -> datetime:
    """Mirrors _flow_digest_window; source assertions below pin the real one."""
    et = ZoneInfo("America/New_York")
    now_et = now_utc.astimezone(et)
    if lookback == "session":
        t = now_et.date() if now_et.hour >= 9 else (now_et.date() - timedelta(days=1))
        return datetime.combine(t, dtime(0, 0), tzinfo=et).astimezone(timezone.utc)
    return now_utc - timedelta(hours=6)


def test_the_bedtime_recap_covers_the_session_that_just_closed():
    """THE WHOLE POINT OF THE NIGHT RUN. Firing at 02:00 ET Thursday, it must recap WEDNESDAY —
    the day that actually traded — not the brand-new calendar day."""
    now = datetime(2026, 9, 10, 6, 0, tzinfo=timezone.utc)  # 02:00 ET Thu
    start_et = _window("session", now).astimezone(ZoneInfo("America/New_York"))
    assert start_et.date() == date(2026, 9, 9), "must be Wednesday's session"
    assert start_et.hour == 0


def test_a_session_window_fired_during_the_day_uses_today():
    """The same helper must behave sensibly if invoked manually mid-session."""
    now = datetime(2026, 9, 9, 18, 0, tzinfo=timezone.utc)  # 14:00 ET Wed
    start_et = _window("session", now).astimezone(ZoneInfo("America/New_York"))
    assert start_et.date() == date(2026, 9, 9)


def test_the_intraday_window_is_six_hours():
    now = datetime(2026, 9, 9, 20, 30, tzinfo=timezone.utc)  # 16:30 ET
    assert (now - _window("intraday", now)) == timedelta(hours=6)


def test_the_morning_window_reaches_back_before_the_open():
    """A 6h window at 11:00 ET starts at 05:00 ET, so it catches pre-market prints too."""
    now = datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc)  # 11:00 ET
    start_et = _window("intraday", now).astimezone(ZoneInfo("America/New_York"))
    assert start_et.hour == 5


def test_the_real_helper_uses_a_local_time_import():
    """The module-level `from datetime import ...` has no `time`, so an unqualified dtime()
    would NameError at runtime — and only on the night run, which is the least-observed path."""
    i = SRC.index("def _flow_digest_window(")
    fn = SRC[i:SRC.index("\ndef ", i + 10)]
    assert "from datetime import time as _dtime" in fn
    assert "_dtime(0, 0)" in fn


# ── Hit rates are honest ────────────────────────────────────────────────────────────────

def _fmt(acc, family):
    if acc["rate"] is None:
        return f"{family} 30-day hit rate: not yet measurable (only {acc['n']} resolved"
    return f"{family} 30-day hit rate: {acc['rate'] * 100:.0f}% next-day (n={acc['n']})"


def test_a_thin_sample_shows_its_n_not_a_percentage():
    """Three findings in docs/2026-09-05 reversed once their samples widened, one resting on SIX
    stocks. A rate without an adequate n is worse than no rate."""
    out = _fmt({"n": 4, "rate": None, "adequate": False}, "Dark pool")
    assert "not yet measurable" in out
    assert "%" not in out


def test_an_adequate_sample_shows_the_rate_WITH_its_n():
    out = _fmt({"n": 93, "rate": 0.4624, "adequate": True}, "Dark pool")
    assert "46%" in out and "n=93" in out


def test_the_minimum_sample_threshold_is_a_named_constant():
    assert "_FLOW_DIGEST_MIN_N = 20" in SRC


def test_a_zero_hit_rate_is_reported_not_hidden():
    """Falsy-zero: a genuine 0% on an adequate sample is a real, important finding."""
    out = _fmt({"n": 50, "rate": 0.0, "adequate": True}, "Options flow")
    assert "0%" in out and "n=50" in out


def test_the_hit_rate_helper_never_returns_zero_for_no_data():
    i = SRC.index("def _flow_hit_rate(")
    fn = SRC[i:SRC.index("\ndef ", i + 10)]
    assert '"rate": None' in fn
    # ASSERT ON CODE, NOT PROSE. My first version matched the function's own docstring, which
    # literally says 'Never returns 0.0 for "no data"' — so it failed against correct code.
    # Sixth time this session; strip the docstring and comments first.
    _in_doc = False
    code_lines = []
    for ln in fn.splitlines():
        st = ln.strip()
        if st.startswith('"""') and not _in_doc:
            _in_doc = not st.endswith('"""') or len(st) == 3
            continue
        if _in_doc:
            if st.endswith('"""'):
                _in_doc = False
            continue
        if st.startswith("#"):
            continue
        code_lines.append(ln)
    code = "\n".join(code_lines)
    assert '"rate": None' in code, "the None must be in the executable body, not just prose"
    assert "0.0" not in code.split('"rate": None')[0], "no falsy-zero default before it"


def test_the_hit_rate_reads_already_computed_outcomes():
    """`is_correct_1d` is filled by the nightly evaluators — the digest only aggregates, so it
    cannot disagree with the alert-performance pages."""
    i = SRC.index("def _flow_hit_rate(")
    fn = SRC[i:SRC.index("\ndef ", i + 10)]
    assert "model.is_correct_1d" in fn
    assert "model.is_correct_1d.isnot(None)" in fn, "unresolved outcomes must not count"


def test_the_measured_rates_are_recorded():
    """46% / 40% next-day. Pinned because they are the reason the rates are IN the digest — a
    stream of institutional prints without them reads as actionable signal."""
    dp_rate, of_rate, of_n = 0.46, 0.40, 1475
    assert dp_rate < 0.55, "coin flip"
    assert of_rate < 0.50, "BELOW coin flip, on a large sample"
    assert of_n > 1000, "and not a small-sample artifact"


# ── Skip-if-empty ───────────────────────────────────────────────────────────────────────

def test_it_skips_silently_when_there_is_nothing_to_report():
    """An empty digest trains the reader to ignore the full ones. AUD-DIGEST-HOLIDAYBLIND is the
    standing reminder of what a digest that fires regardless actually costs."""
    i = SRC.index("def send_flow_digest(")
    fn = SRC[i:SRC.index("\ndef ", i + 10)]
    assert "if not dp_rows and not of_rows:" in fn
    assert 'reason="nothing_to_report"' in fn


def test_a_skip_still_records_a_job_status():
    """A silent skip that records nothing is indistinguishable from a job that died — the
    liveness gauges would start reporting it missing."""
    i = SRC.index("def send_flow_digest(")
    fn = SRC[i:SRC.index("\ndef ", i + 10)]
    block = fn[fn.index('reason="nothing_to_report"'):]
    assert "_record_job_status(" in block.split("return")[0]


def test_it_checks_the_US_trading_CALENDAR_not_just_the_weekday():
    """A mon-fri cron is not a market-open check — the AUD-DIGEST-HOLIDAYBLIND family."""
    i = SRC.index("def send_flow_digest(")
    fn = SRC[i:SRC.index("\ndef ", i + 10)]
    assert "_is_us_trading_day(" in fn


def test_the_night_run_checks_the_PREVIOUS_day_for_trading():
    """Firing at 02:00 ET, `today` is a fresh calendar day that has not traded yet. Checking it
    would skip every single night run."""
    i = SRC.index("def send_flow_digest(")
    fn = SRC[i:SRC.index("\ndef ", i + 10)]
    assert "_check_day = _now_et if _now_et.hour >= 9 else (_now_et - timedelta(days=1))" in fn


def test_it_takes_a_distributed_lock_per_label():
    """Three jobs share one function; a single global lock would let one run suppress another."""
    i = SRC.index("def send_flow_digest(")
    fn = SRC[i:SRC.index("\ndef ", i + 10)]
    assert 'f"{_FLOW_DIGEST_LOCK_KEY}:{label}"' in fn


# ── Zero API cost ───────────────────────────────────────────────────────────────────────

def test_it_makes_no_fresh_unusual_whales_calls():
    """THE COST INVARIANT. The 2026-09-04 audit traced 22,031 rate-limit events in 48h to ONE
    uncached per-minute function; a new job that re-fetched would repeat that. This reads the
    outcome rows the alert checkers already wrote."""
    i = SRC.index("def send_flow_digest(")
    fn = SRC[i:SRC.index("\ndef ", i + 10)]
    for forbidden in ("_uw.", "get_dark_pool_prints", "get_flow_alerts", "httpx"):
        assert forbidden not in fn, f"the digest must not call {forbidden}"


def test_it_reads_the_outcome_tables():
    i = SRC.index("def send_flow_digest(")
    fn = SRC[i:SRC.index("\ndef ", i + 10)]
    assert "DarkPoolAlertOutcome" in fn
    assert "OptionsFlowAlertOutcome" in fn


# ── Rendering ───────────────────────────────────────────────────────────────────────────

def test_both_bodies_render_from_the_same_rows():
    """A field added to the HTML but not the text is the shape AUD-CONVICTION-RSIDIV-NOWRITER
    exploited when two surfaces disagreed about what had been measured."""
    i = SRC.index("def _render_flow_digest(")
    fn = SRC[i:]
    assert "dp_html" in fn and "dp_text" in fn
    assert "of_html" in fn and "of_text" in fn
    assert "return html, text" in fn


def test_a_missing_premium_renders_a_dash_not_zero():
    """Three-state: a real $0 is "$0", a missing value is "—"."""
    i = SRC.index("def _money(")
    fn = SRC[i:SRC.index("\n    dp_html", i)]
    assert "if v is None:" in fn
    assert 'return "—"' in fn


def test_the_digest_carries_an_interpretation_caveat():
    """A large block can be a hedge, a roll, or a rebalance with no directional view. Presenting
    prints as signals without saying so is the same overclaim as an unvalidated LLM direction."""
    assert "_caveat" in SRC
    assert "OBSERVATIONS, not" in SRC
    assert "hedge" in SRC


def test_the_caveat_appears_in_both_bodies():
    i = SRC.index("def _render_flow_digest(")
    fn = SRC[i:]
    tail = fn[fn.index("_caveat = "):]
    assert tail.count("{_caveat}") >= 2


def test_the_subject_line_states_the_counts():
    """So an unread digest is still informative in the inbox list."""
    i = SRC.index("def send_flow_digest(")
    fn = SRC[i:SRC.index("\ndef ", i + 10)]
    assert "dark pool /" in fn
    assert "len(dp_rows)" in fn and "len(of_rows)" in fn


def test_the_row_cap_is_a_named_constant():
    """An email, not a report — an unbounded session recap could list hundreds of prints."""
    assert "_FLOW_DIGEST_MAX_ROWS = 12" in SRC
    i = SRC.index("def send_flow_digest(")
    fn = SRC[i:SRC.index("\ndef ", i + 10)]
    assert fn.count("_FLOW_DIGEST_MAX_ROWS") == 2, "capped on BOTH sections"
