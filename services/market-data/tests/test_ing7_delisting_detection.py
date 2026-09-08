"""AUD-ING7-DELISTNEVERFIRES — the delisting detector could never fire.

`_record_delisting_signal()` was fully built (Redis counter, 30-day TTL, clear-companion,
fail-open handling) and DOES set `Stock.delisted = True` at its confirmation threshold. It had
nonetheless never fired once: zero `stockai:delisting_signal:*` keys in Redis, zero
`delisted_confirmed` events in 7 days of logs, and both dead tickers still `active=true`.

ROOT CAUSE — yfinance's behaviour depends on HOW you ask:

    history(start="2026-07-11", end="2026-09-09")  -> 1 stale bar (2026-07-17)  SUCCESS
    history(period="1mo")                          -> YFPricesMissingError      RAISES

`ingest_symbol()` always builds an explicit start/end (incremental from `head - 7 days`), and for
a delisted ticker that window still straddles its FINAL REAL BAR. yfinance returns that one stale
bar, the success branch counted it as proof of life, `_clear_delisting_signal()` reset the
counter, and the threshold was unreachable BY CONSTRUCTION. Traced live: a real
`ingest_symbol("SKHYV")` logged `inserted=1` and left no signal key.

THE DISTINCTION THE FIX MUST PRESERVE: SSNLF and SKHYV look identical in the DB (1 bar each,
both active) but have OPPOSITE causes — SKHYV genuinely raises on a relative-period fetch, while
SSNLF returns 23 real bars and is merely under-ingested. Keying off the freshness of the bars
actually RETURNED distinguishes them; keying off the DB state would not.
"""
import pathlib
import re

import pandas as pd

import src.services.ingestion as ing

ING_SRC = pathlib.Path(ing.__file__).read_text()


def _success_branch() -> str:
    """The `if not candidate.empty:` success branch of the adapter loop."""
    i = ING_SRC.index("if not candidate.empty:")
    return ING_SRC[i:ING_SRC.index("log.warning(\"ingest.adapter_empty\"", i)]


# ── The core fix: liveness judged by data, not by absence of an exception ─────────────────

def test_success_branch_can_now_record_a_delisting_signal():
    """THE CORE FIX. Previously this branch could ONLY clear — so a stale-but-successful fetch
    reset the counter forever."""
    body = _success_branch()
    assert "_record_delisting_signal(symbol)" in body, \
        "a stale successful fetch must ACCRUE a signal, not only clear one"
    assert "_clear_delisting_signal(symbol)" in body, "a fresh fetch must still clear"


def test_the_decision_keys_off_the_newest_bar_returned():
    """It must judge the DATA, not the DB state — that is what separates SKHYV (genuinely dead)
    from SSNLF (alive but under-ingested)."""
    body = _success_branch()
    assert 'candidate["ts"]' in body, "must inspect the bars actually returned"
    assert "_DELISTING_STALE_BAR_DAYS" in body


def test_clear_and_record_are_mutually_exclusive():
    """Recording then clearing in the same pass would be a no-op — the original bug in a new
    form. They must be the two arms of one if/else."""
    body = _success_branch()
    rec = body.index("_record_delisting_signal(symbol)")
    clr = body.index("_clear_delisting_signal(symbol)")
    between = body[min(rec, clr):max(rec, clr)]
    assert "else:" in between, "must be if/else arms, not sequential calls"


# ── Threshold choices ────────────────────────────────────────────────────────────────────

def test_stale_window_matches_the_dq_gauge():
    """7 days, matching stale_symbols_d1 (AUD-DQ2-PERSYMBOLSTALENESS), so the two mechanisms
    cannot disagree about what "stale" means."""
    assert ing._DELISTING_STALE_BAR_DAYS == 7


def test_stale_window_is_wider_than_any_holiday_weekend():
    """A long weekend closes the market ~4 calendar days. A narrower window would delist live
    stocks every Thanksgiving."""
    assert ing._DELISTING_STALE_BAR_DAYS >= 5


def test_confirmation_threshold_requires_more_than_one_cycle():
    """Combined with the stale window, a symbol must look dead across TWO separate ingest cycles
    before being flagged — so one transient bad fetch cannot delist a live stock."""
    assert ing._DELISTING_CONFIRM_THRESHOLD >= 2


# ── The SSNLF-vs-SKHYV distinction ───────────────────────────────────────────────────────

def _would_record(newest_bar_age_days: int) -> bool:
    """Mirrors the call site's decision."""
    return newest_bar_age_days > ing._DELISTING_STALE_BAR_DAYS


def test_a_genuinely_dead_ticker_accrues_a_signal():
    """SKHYV: its fetch returns only a bar from 2026-07-17, ~53 days stale."""
    assert _would_record(53) is True


def test_an_under_ingested_but_LIVE_ticker_does_not():
    """SSNLF: a real fetch returns 23 CURRENT bars, so the newest is ~0-3 days old. It must
    clear, not accrue — it needs a force re-ingest, not a delisting."""
    assert _would_record(0) is False
    assert _would_record(3) is False


def test_the_boundary_is_exclusive():
    """Exactly at the window is not yet stale — `>`, not `>=`. An off-by-one here would delist
    on the 7th day of a long market closure.

    Asserts on the REAL source, not just the local mirror: a sabotage flipping the real `>` to
    `>=` passed all 14 tests because _would_record() is a copy. A mirror function only tests the
    mirror unless the operator itself is pinned.
    """
    assert _would_record(7) is False
    assert _would_record(8) is True
    body = _success_branch()
    assert "_age_days > _DELISTING_STALE_BAR_DAYS" in body, "must be strictly greater-than"
    assert "_age_days >= _DELISTING_STALE_BAR_DAYS" not in body


# ── The flag must actually take effect ───────────────────────────────────────────────────

def test_ingest_universe_must_KEEP_including_delisted_symbols():
    """A correction to my own first attempt at this fix.

    I initially added `Stock.delisted.is_(False)` to _symbols_for() to stop wasting quota on
    dead tickers. A PRE-EXISTING test (test_delisted_excluded_from_scheduler_jobs.py) caught it,
    and that test is right: ingestion is what DETECTS and RECONFIRMS delisting, and
    _record_delisting_signal() only ever runs on symbols this function returns. Excluding them
    would disable the detection mechanism itself — and would make a mistaken flag PERMANENT,
    since a wrongly-flagged symbol could never be re-fetched and cleared.

    The wasted-quota concern is real but belongs downstream, where consumers filter on the flag
    themselves. Pin the invariant so a future "optimisation" does not re-break it.
    """
    sched = pathlib.Path(pathlib.Path(ing.__file__).parent / "scheduler.py").read_text()
    fn = sched[sched.index("def _symbols_for("):sched.index("_REDIS_REFRESH_FAILED_KEY")]
    assert "Stock.active.is_(True)" in fn, "the active filter must remain"
    assert "Stock.delisted" not in fn, (
        "must NOT filter delisted here — ingestion is what reconfirms delisting, and excluding "
        "flagged symbols makes the flag unrecoverable"
    )


def test_active_is_not_also_cleared():
    """`active` (user intent) and `delisted` (market fact) are different facts. Conflating them
    would make a delisting look like a deliberate deactivation and be impossible to undo
    correctly if the detection were ever wrong."""
    body = _success_branch()
    assert ".active = False" not in body
    assert "active=False" not in body


def test_delisted_column_is_non_nullable_so_is_False_is_safe():
    """`is_(False)` would silently exclude rows where the column is NULL. Verified in production:
    zero NULLs across 193 rows, and the model declares a server_default."""
    models = pathlib.Path(ing.__file__).parents[3].joinpath(
        "shared/db/models.py")
    if models.exists():
        src = models.read_text()
        line = [l for l in src.splitlines() if "delisted:" in l][0]
        assert "default=False" in line and "server_default" in line


# ── Safety properties of the existing machinery ──────────────────────────────────────────

def test_record_signal_still_fails_open():
    """A Redis hiccup must never block ingestion."""
    import inspect
    body = inspect.getsource(ing._record_delisting_signal)
    assert "except Exception" in body
    assert "ingest.delisting_signal_failed" in body


def test_stale_detection_is_logged_before_recording():
    """A silent delisting would be very hard to debug. The stale bar itself must be visible."""
    body = _success_branch()
    assert "ingest.stale_newest_bar" in body
    assert "age_days" in body and "newest_bar" in body
