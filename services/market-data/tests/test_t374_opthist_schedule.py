"""T374-OPTHIST-SCHEDULE — the option-chain archive had no scheduled capture at all.

USER REQUEST: add QQQ LEAPS to the Strategy Backtester with delta / date range /
QQQ|QQQM|QLD|TQQQ / strike / expiry — then, on learning QQQM/QLD/TQQQ had zero rows, "can we
load the data back for QQQM, QLD and TQQQ?"

WHAT THE INVESTIGATION FOUND, in order:

1. `option_chain_history` holds real, high-quality LEAPS data for QQQ — 0.80-delta calls with
   bid/ask and IV, expiries out to 2028-12-15. The backtest the user wants is genuinely
   supportable for QQQ.

2. QQQM, QLD and TQQQ had **ZERO rows**, so the QQQ-vs-QQQM-vs-QLD-vs-TQQQ comparison in the
   user's own LEAPS playbook page was untestable.

3. **UW serves all four back to ~2023-10-11** (measured by bisection: 2023-10-09 returns 403).
   My first answer — "only 6 months of data, too short for a LEAPS hold" — was about what had
   been CAPTURED, not what was AVAILABLE. I should have probed the API before concluding.

4. THE BIGGER FINDING: **there was no scheduled capture job at all.**
   `capture_option_chain_history()` existed and was correct, but was only reachable from
   `POST /admin/capture-option-chain-history`. The ten symbols present were captured by hand and
   then stopped — newest row 2026-09-04, five days stale and drifting.

   That matters because UW's history is a ROLLING window, not an archive. An uncaptured day does
   not merely stay uncaptured; it eventually becomes UNCAPTURABLE. A one-off manual backfill with
   no daily job is a decaying asset.

5. `option_chain_history` was **1,804 MB of a 2,607 MB database (69%)** with NO retention policy
   — unlike prices_5m (90d) and signals (365d). It gets one here, from the start.

MEASURED COSTS, not assumed: QQQM 1,206 / QLD 588 / TQQQ 1,632 contracts per day = 3,426 total.
A 2-year backfill is ~1.73M rows / ~345 MB / 1,512 requests. Requests are trivial (1.3% of one
day's 120k budget); DB VOLUME is the binding constraint, exactly as OPTHIST-1 recorded.
"""
import pathlib

import pytest

SCHED = pathlib.Path(__file__).resolve().parents[1] / "src/services/scheduler.py"
SRC = SCHED.read_text()


def _fn(name: str) -> str:
    i = SRC.index(f"def {name}(")
    cands = [j for j in (SRC.find("\ndef ", i + 10), SRC.find("\nasync def ", i + 10)) if j != -1]
    return SRC[i:] if not cands else SRC[i:min(cands)]


def _job_block(job_id: str) -> str:
    """Parsed FORWARD from its own add_job( — a backwards scan pairs a trigger with the
    PRECEDING job's id, which produced three retracted findings earlier in this session."""
    i = SRC.index(f'id="{job_id}"')
    return SRC[SRC.rindex("_scheduler.add_job(", 0, i):i]


# ── The universe now covers the comparison set ──────────────────────────────────────────

@pytest.mark.parametrize("sym", ["QQQ", "QQQM", "QLD", "TQQQ"])
def test_the_qqq_family_comparison_set_is_captured(sym):
    """The user's LEAPS playbook compares QQQ vs QQQM vs QLD vs TQQQ; three had zero rows."""
    i = SRC.index("_OPTHIST_SYMBOLS = [")
    block = SRC[i:SRC.index("]", i)]
    assert f'"{sym}"' in block


def test_the_original_ten_symbols_are_retained():
    """Adding the ETFs must not drop what was already being captured."""
    i = SRC.index("_OPTHIST_SYMBOLS = [")
    block = SRC[i:SRC.index("]", i)]
    for sym in ("SPY", "QQQ", "META", "TSLA", "AMD", "NVDA", "MSFT", "AAPL", "AMZN", "PLTR"):
        assert f'"{sym}"' in block


def test_the_universe_is_explicit_not_a_whole_universe_sweep():
    """OPTHIST-1's own design note: DB volume is the binding constraint, so there is
    deliberately no 'capture everything' mode."""
    assert "_symbols_for(" not in _fn("_capture_option_chain_history_daily")


# ── A daily job now exists ──────────────────────────────────────────────────────────────

def test_a_daily_capture_job_is_registered():
    """THE HEADLINE. There was none — the table went stale at 2026-09-04."""
    blk = _job_block("option_chain_history_daily")
    assert "hour=18" in blk and "minute=45" in blk
    assert 'timezone="America/New_York"' in blk
    assert 'day_of_week="mon-fri"' in blk


def test_the_daily_job_is_NOT_gated_behind_alerting_enabled():
    """It WRITES DATA and sends no email, so a local dev stack should still build its archive.
    I first registered it inside the gate — contradicting my own comment — and the
    test_alerts_env_gate parity test caught it."""
    i = SRC.index('id="option_chain_history_daily"')
    assert SRC[max(0, i - 3000):i].rfind("if _is_alerting_enabled():") == -1


def test_the_daily_job_checks_the_us_trading_calendar():
    """A mon-fri cron is not a market-open check — the AUD-DIGEST-HOLIDAYBLIND family."""
    assert "_is_us_trading_day()" in _fn("_capture_option_chain_history_daily")


def test_it_captures_YESTERDAY_not_today():
    """A chain is only settled after the close."""
    fn = _fn("_capture_option_chain_history_daily")
    assert "_date.today() - _td(days=1)" in fn


def test_it_captures_a_TRAILING_WINDOW_so_a_missed_run_self_heals():
    """THE POINT OF THE WINDOW. In a rolling-window archive a missed day is permanent, so the
    job must re-attempt recent days rather than only ever the last one."""
    fn = _fn("_capture_option_chain_history_daily")
    assert "_OPTHIST_BACKFILL_WINDOW_DAYS" in fn
    assert "_OPTHIST_BACKFILL_WINDOW_DAYS = 5" in SRC


def test_the_window_is_cheap_because_skip_existing_is_on():
    """Without this the trailing window would re-fetch thousands of settled rows every night."""
    assert "skip_existing=True" in _fn("_capture_option_chain_history_daily")


def test_the_daily_job_records_a_job_status_on_every_path():
    """Including the not-a-trading-day skip — a silent skip is indistinguishable from a job that
    died, and the liveness gauges key on this."""
    fn = _fn("_capture_option_chain_history_daily")
    assert fn.count("_record_job_status(") == 3, "ok-skip, ok-done, error"


# ── Retention ───────────────────────────────────────────────────────────────────────────

def test_a_purge_job_exists():
    """option_chain_history was 69% of the entire database with NO retention policy — the same
    unbounded-growth shape scheduler_jobs still has."""
    blk = _job_block("option_chain_history_purge")
    assert 'day_of_week="sun"' in blk


def test_the_retention_window_outlives_the_source():
    """The archive's whole purpose is to OUTLIVE UW's rolling ~2-year window, so retention is
    deliberately generous rather than tight."""
    assert "_OPTHIST_RETENTION_DAYS = 800" in SRC
    assert 800 > 730, "longer than UW's own ~2-year window"


def test_the_purge_deletes_by_as_of_not_fetched_at():
    """fetched_at is when WE captured it; as_of is the market date the data describes. Purging on
    fetched_at would delete a freshly-backfilled 2024 row immediately."""
    fn = _fn("_purge_option_chain_history")
    assert "WHERE as_of < :c" in fn
    assert "fetched_at" not in fn


def test_the_purge_logs_how_many_rows_it_removed():
    """A purge that reports nothing makes an over-aggressive cutoff invisible."""
    fn = _fn("_purge_option_chain_history")
    assert "rows=res.rowcount" in fn


def test_the_purge_runs_after_the_main_db_purge():
    """db_purge_weekly is Sun 15:00 PT; this is 15:30 PT — sequential, not contending."""
    blk = _job_block("option_chain_history_purge")
    assert "hour=15" in blk and "minute=30" in blk
    assert 'timezone="America/Los_Angeles"' in blk


# ── The measured facts, pinned ──────────────────────────────────────────────────────────

def test_the_rolling_window_boundary_is_recorded():
    """Measured by bisection 2026-09-09: 2023-10-11 serves, 2023-10-09 returns 403. Recorded so
    a future backfill does not waste requests on dates that cannot return data."""
    assert "2023-10-11" in SRC


def test_the_per_symbol_cost_is_recorded():
    """So the next person adding a symbol can size it without re-measuring."""
    for frag in ("1,206", "588", "1,632", "3,426"):
        assert frag in SRC, f"contracts/day figure {frag} should be recorded"


def test_the_binding_constraint_is_documented_as_disk_not_requests():
    """1,512 requests is 1.3% of one day's 120k budget; ~345 MB is the real cost. Getting this
    backwards is what would lead someone to 'just capture everything'."""
    # Anchor on the CONSTANT the note belongs to, not a fixed character window — my first
    # version used SRC[i:i+2600] from the marker and the phrase sat just past it, failing
    # against correct code.
    i = SRC.index("_OPTHIST_SYMBOLS = [")
    block = SRC[max(0, i - 2200):i]
    assert "DB VOLUME is the binding constraint" in block


def test_the_backfill_arithmetic():
    """3,426 contracts/day x 504 trading days ~ 1.73M rows."""
    contracts_per_day, trading_days = 3426, 504
    assert contracts_per_day * trading_days > 1_700_000
    # 1,512 requests = 3 symbols x 504 days, ~1.3% of the daily quota.
    assert (3 * trading_days) / 120_000 < 0.02
