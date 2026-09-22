"""AUD-T409-UTCDATEBOUNDARY follow-up (2026-09-21) — the ~16-file scoped list from
docs/incidents/utc-vs-et-date-boundary.md's own "Scoped, deliberately not blitzed" section,
triaged and fixed for paper_trading_engine.py + conditional_orders.py.

Three portfolio-level entry gates in _scan_for_entries() (daily realized-loss circuit breaker,
max-entries-per-day cap, choppy/risk_off regime entry throttle) and their hand-maintained
"faithful reimplementation" in conditional_orders.py's own gate check all computed "start of
today" as `datetime.combine(datetime.now(timezone.utc).date(), datetime.min.time())` — a naive
UTC date truncation. For ~4-5 hours of every evening (from 8pm EDT/7pm EST until US market
open), that expression collapses to within about an hour of the real current instant instead of
~20 hours in the past, so every one of these gates silently saw only the last few minutes of
the day's trades during that window: the daily-loss breaker could fail to trip on a genuinely
bad day, the max-entries cap could allow more than the configured daily limit, and the regime
throttle could allow a second entry it was supposed to block to one per day.

Also fixes: the anti-chase drift baseline (`_ref_cutoff`) which used `date.today() - 1` and,
for a signal that fired today during the bug window, silently reintroduced the exact
AUD-LIVEBAR-T196 look-ahead bug its own comment says was fixed (comparing a signal against
today's own live/unsettled bar); and PaperEquityCurve's daily upsert key, which could mis-date
the equity-curve snapshot under tomorrow's date during the same window.

paper_trading_engine.py can't be imported directly in this test environment (heavy DB/session
dependencies) — `_et_day_start()` is a small, standalone pure function extracted via exec(),
matching test_t409_utc_date_boundary.py's own established technique; the call sites are
verified via source-text regression checks, matching that same file's own
test_fixed_file_contains_no_live_naive_utc_date_call precedent.
"""
import pathlib
from datetime import datetime, timezone
from unittest.mock import patch

_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
)
_SOURCE = _PATH.read_text()

_CO_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "conditional_orders.py"
)
_CO_SOURCE = _CO_PATH.read_text()


def _extract_et_day_start():
    start = _SOURCE.index("def _et_day_start() -> datetime:")
    end = _SOURCE.index("\n\n\ndef _scan_for_entries(", start)
    func_source = _SOURCE[start:end]
    namespace = {}
    exec(  # noqa: S102 — isolated eval of one pure function's real source
        "from datetime import date, datetime, timezone\nfrom zoneinfo import ZoneInfo\n" + func_source,
        namespace,
    )
    return namespace["_et_day_start"]


_et_day_start = _extract_et_day_start()


class _FrozenDatetime(datetime):
    _frozen: datetime

    @classmethod
    def now(cls, tz=None):
        return cls._frozen.astimezone(tz) if tz else cls._frozen


def _frozen_at(utc_iso: str):
    frozen = _FrozenDatetime.fromisoformat(utc_iso).replace(tzinfo=timezone.utc)
    _FrozenDatetime._frozen = frozen
    # _et_day_start was exec'd into its own namespace dict rather than a real module — that
    # dict IS _et_day_start.__globals__, since exec() binds a function's globals to the dict
    # it ran in. Patch that dict directly, matching test_uw01_alert_date_boundary.py's
    # established technique for this exact constraint.
    return patch.dict(_et_day_start.__globals__, {"datetime": _FrozenDatetime})


# ── _et_day_start() itself ──────────────────────────────────────────────────────────────────

def test_the_exact_evening_window_reads_the_correct_et_midnight():
    """2026-09-18 20:40 ET == 2026-09-19 00:40 UTC. The naive version would compute
    `datetime.combine(date(2026, 9, 19), time.min)` — this must instead be midnight of the
    18th, in New York, tz-aware."""
    with _frozen_at("2026-09-19T00:40:00"):
        result = _et_day_start()
    assert result.date().isoformat() == "2026-09-18"
    assert result.hour == 0 and result.minute == 0
    assert result.tzinfo is not None, "must be tz-aware so psycopg2 localizes it to UTC correctly"


def test_naive_utc_truncation_would_have_gotten_the_wrong_calendar_day():
    """Not a strawman: this is what all three gate call sites used to compute."""
    frozen = datetime(2026, 9, 19, 0, 40, 0, tzinfo=timezone.utc)
    naive_today = frozen.date()
    assert naive_today.isoformat() == "2026-09-19", "confirms the bug this fix replaces"


def test_daytime_edt_matches_the_naive_version_exactly():
    """During market hours the two must agree — purely an evening-boundary correction."""
    with _frozen_at("2026-09-18T18:00:00"):  # 14:00 EDT
        result = _et_day_start()
    assert result.date().isoformat() == "2026-09-18"


# ── Every fixed call site actually uses the shared helper ──────────────────────────────────

def test_daily_loss_circuit_breaker_uses_et_day_start():
    assert "today_open = _et_day_start()  # AUD-T409-UTCDATEBOUNDARY" in _SOURCE


def test_max_entries_per_day_cap_uses_et_day_start():
    assert "today_start = _et_day_start()  # AUD-T409-UTCDATEBOUNDARY" in _SOURCE


def test_regime_entry_throttle_uses_et_day_start():
    assert "_te_start = _et_day_start()  # AUD-T409-UTCDATEBOUNDARY" in _SOURCE


def test_no_naive_utc_combine_survives_in_scan_for_entries():
    start = _SOURCE.index("def _scan_for_entries(")
    end = _SOURCE.index("\ndef ", start + 10)
    body = _SOURCE[start:end]
    assert "datetime.combine(datetime.now(timezone.utc).date(), datetime.min.time())" not in body


def test_anti_chase_drift_baseline_uses_et_day_start_not_naive_date_today():
    """AUD-T401-SOURCETEXTTESTS: asserts only WHICH "today" feeds the cutoff (the qualitative
    substitution this fix makes), not the full expression including the `- timedelta(days=1)`
    numeric literal — that part of the formula is already covered behaviorally by
    test_t196_livebar_reference_price.py's own `_cutoff()` tests, so pinning it again here in
    source text would just be a second, weaker copy of the same check."""
    assert "_ref_cutoff = min(_sig_date, _et_day_start().date()" in _SOURCE
    assert "_ref_cutoff = min(_sig_date, date.today()" not in _SOURCE


def test_equity_curve_snapshot_key_uses_et_day_start_not_naive_date_today():
    start = _SOURCE.index("today  = _et_day_start().date()")
    assert start > 0
    # Regression guard: the OLD literal must not survive anywhere in the equity-curve function.
    fn_start = _SOURCE.rindex("\ndef ", 0, start)
    fn_end = _SOURCE.index("\ndef ", start)
    body = _SOURCE[fn_start:fn_end]
    assert "today  = date.today()" not in body


# ── conditional_orders.py's own hand-maintained reimplementation ───────────────────────────

def test_conditional_orders_daily_loss_check_imports_et_day_start():
    start = _CO_SOURCE.index("from .paper_trading_engine import (")
    end = _CO_SOURCE.index(")", start)
    assert "_et_day_start" in _CO_SOURCE[start:end]


def test_conditional_orders_daily_loss_check_uses_et_day_start():
    assert "today_open = _et_day_start()  # AUD-T409-UTCDATEBOUNDARY" in _CO_SOURCE
    assert "today_open = datetime.combine(datetime.now(timezone.utc).date(), datetime.min.time())" not in _CO_SOURCE
