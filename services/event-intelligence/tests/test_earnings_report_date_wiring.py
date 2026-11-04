"""Source-text regression checks for AUD264-EARNINGS-FISCAL-QUARTER-FROM-ANNOUNCEMENT-MONTH's
_fetch_earnings_for_symbol() wiring — the function itself makes real yfinance + DB calls, so
it can't be exercised end-to-end here; the pure matching logic it calls
(_match_report_dates_to_history) is tested directly and exhaustively in test_earnings.py.
This file covers the wiring AROUND that call: that the real matched date is actually used
(with a correct fallback), that both upsert paths avoid creating duplicate rows for a shifting
calendar-projected date, and that both reference the new (not the old) uniqueness constraint.
"""
from pathlib import Path

_SRC = (Path(__file__).parent.parent / "src" / "services" / "earnings.py").read_text()


def _fetch_body() -> str:
    start = _SRC.index("def _fetch_earnings_for_symbol(")
    end = _SRC.index("\ndef _compute_strength(")
    return _SRC[start:end]


# ── Historical path (hist.iterrows()) ────────────────────────────────────────────────────────

def test_historical_path_joins_earnings_dates_for_the_real_announcement_date():
    body = _fetch_body()
    assert "ticker.earnings_dates" in body
    assert "_match_report_dates_to_history(hist_rows_for_match, announce_rows)" in body


def test_historical_path_falls_back_to_period_end_when_no_match_found():
    """A join failure (yfinance error, no earnings_dates data) must never crash the sync —
    it must fall back to the period-end date, which was the ENTIRE pre-fix behavior, not a
    new failure mode."""
    body = _fetch_body()
    assert "real_dates_by_period_end.get(period_end.isoformat(), period_end)" in body


def test_earnings_dates_join_itself_is_wrapped_in_its_own_try_except():
    """The join is a best-effort enhancement — its own failure (a bad response shape, a
    network hiccup) must degrade to real_dates_by_period_end = {} rather than aborting the
    whole historical sync for this symbol."""
    body = _fetch_body()
    join_idx = body.index("ticker.earnings_dates")
    preceding = body[max(0, join_idx - 300):join_idx]
    assert "try:" in preceding
    assert "real_dates_by_period_end: dict[str, date] = {}" in body


# ── Both paths avoid duplicate rows when the projected/real date shifts ─────────────────────

def test_historical_path_updates_the_row_for_this_quarter_in_place():
    """If the calendar path already wrote a PENDING row for this exact event under an earlier
    projected date, the historical sync must update that SAME row rather than insert a second
    one. AUD-EARNINGS-PENDINGMATCH: the row is found by date (_history_target), before the
    insert path, never as "any pending row": that lookup is what stopped every report from
    being recorded. Behaviour is covered on PostgreSQL by test_earnings_pending_match.py."""
    body = _fetch_body()
    hist_target_idx = body.index("target = _history_target(")
    conflict_idx = body.index('constraint="uq_earnings_stock_report_date"')
    assert hist_target_idx < conflict_idx
    assert "with s.begin_nested():" in body[:conflict_idx]


def test_neither_path_matches_any_pending_row_of_the_stock():
    """The bare `eps_actual IS NULL` lookup matched the next upcoming report, duplicate
    projections and HK rows without EPS. Both paths now go through a date-windowed helper."""
    body = _fetch_body()
    assert "EarningsEvent.eps_actual.is_(None)" not in body
    assert "_calendar_target(" in body


def test_neither_path_still_references_the_old_dropped_constraint():
    """Both upsert paths (historical + calendar) each still have their own
    on_conflict_do_update() block, but neither may reference the old, now-dropped
    fiscal-quarter constraint — both must target the new report_date-keyed one."""
    body = _fetch_body()
    assert "uq_earnings_stock_period" not in body
    assert body.count('constraint="uq_earnings_stock_report_date"') == 2


def test_calendar_path_sets_report_date_on_the_pending_row_update():
    """The whole point of finding-and-updating the pending row is to advance its report_date
    to the newly-projected estimate — a fix that found the row but never updated its date
    would silently freeze on the FIRST projected date forever. Checks for a real, EXECUTABLE
    assignment line (not merely the substring anywhere in the text, which would also match a
    commented-out line like `# existing_pending.report_date = upcoming` — a real regression
    class already hit once while writing this test)."""
    body = _fetch_body()
    cal_section = body[body.index("# Upcoming earnings date"):]
    real_assignment_lines = [
        line for line in cal_section.splitlines()
        if line.strip() == "existing_pending.report_date = upcoming"
    ]
    assert len(real_assignment_lines) == 1
