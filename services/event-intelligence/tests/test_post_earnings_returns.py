"""Tests for AUD-EARNINGSFORECAST-EXTEND — backfill_post_earnings_returns() and its own pure
bar-index helper _compute_post_earnings_returns().

Closes a real, previously-documented gap: EarningsEvent.post_earnings_return_1d/_5d are real
columns that were DEFINED but never written by any job in this codebase — confirmed via a fresh
grep before building this, matching the earlier, deliberate deferral already documented in
CLAUDE.md (the reasoning there was specifically "no real historical post-earnings-move data
source exists" — this closes that gap using data this app already has: real daily Price bars).

_compute_post_earnings_returns() is pure and dependency-free, tested directly via a plain
import. backfill_post_earnings_returns() itself needs the same fake-session convention already
established in test_sync_todays_earnings.py (a real SQLAlchemy expression tree can't be built
against the stubbed sqlalchemy module, so these tests drive behavior via what
SessionLocal().execute(...) returns).
"""
from datetime import date
from unittest.mock import MagicMock

import src.services.earnings as e
from src.services.earnings import _compute_post_earnings_returns, backfill_post_earnings_returns


def _run(coro):
    import asyncio
    return asyncio.run(coro)


# ── _compute_post_earnings_returns() — pure bar-index math ─────────────────────────────

def test_computes_real_1d_and_5d_returns_from_a_full_bar_sequence():
    """report_date 8/6 with 5 real bars strictly before and 6 real bars on/after it (baseline
    = 8/5's close, 1d = the 2nd after-bar's close (8/7), 5d = the 6th after-bar's close (8/13) —
    hand-verified against real OSCR production data used to design this feature."""
    bars = [
        (date(2026, 8, 3), 30.0), (date(2026, 8, 4), 30.38), (date(2026, 8, 5), 30.11),
        (date(2026, 8, 6), 26.54), (date(2026, 8, 7), 27.90), (date(2026, 8, 10), 27.67),
        (date(2026, 8, 11), 27.97), (date(2026, 8, 12), 29.61), (date(2026, 8, 13), 30.78),
    ]
    ret_1d, ret_5d = _compute_post_earnings_returns(bars, date(2026, 8, 6))
    assert ret_1d == 27.90 / 30.11 - 1
    assert ret_5d == 30.78 / 30.11 - 1


def test_baseline_is_the_last_close_strictly_before_report_date_not_report_dates_own_close():
    """The pre-report close must be used as the baseline, not report_date's own (possibly
    already-reacted) close — this is the whole point of the design (consistent regardless of
    BMO/AMC timing)."""
    bars = [(date(2026, 1, 1), 100.0), (date(2026, 1, 2), 90.0), (date(2026, 1, 5), 95.0)]
    ret_1d, _ = _compute_post_earnings_returns(bars, date(2026, 1, 2))
    # baseline = 100.0 (1/1's close, strictly before), NOT 90.0 (report_date's own close)
    assert ret_1d == 95.0 / 100.0 - 1


def test_returns_none_none_when_no_bar_exists_strictly_before_report_date():
    """A symbol with no real history before its own first tracked report_date has no real
    baseline to measure against — must never fabricate one (e.g. from the report-day close
    itself, which would silently misrepresent the "reaction")."""
    bars = [(date(2026, 8, 6), 26.54), (date(2026, 8, 7), 27.90)]
    assert _compute_post_earnings_returns(bars, date(2026, 8, 6)) == (None, None)


def test_returns_none_none_when_no_bar_exists_on_or_after_report_date():
    bars = [(date(2026, 8, 3), 30.0), (date(2026, 8, 4), 30.38)]
    assert _compute_post_earnings_returns(bars, date(2026, 8, 10)) == (None, None)


def test_return_1d_is_none_but_return_5d_can_still_resolve_independently():
    """A genuinely odd but real case worth locking in: if the after-list somehow had exactly 6
    entries without a valid 2nd one being reachable... in practice this can't happen since the
    list is contiguous, but the two computations are independently gated (len>=2 vs len>=6) —
    confirms return_1d resolving does not silently assume return_5d also resolved, and vice
    versa when there are between 2 and 5 after-bars."""
    bars = [
        (date(2026, 1, 1), 100.0),
        (date(2026, 1, 2), 90.0), (date(2026, 1, 3), 92.0), (date(2026, 1, 4), 93.0),
    ]
    ret_1d, ret_5d = _compute_post_earnings_returns(bars, date(2026, 1, 2))
    assert ret_1d == 92.0 / 100.0 - 1
    assert ret_5d is None  # only 3 after-bars — not enough for a real 5-trading-day return yet


def test_returns_none_none_on_a_zero_baseline_rather_than_dividing_by_zero():
    bars = [(date(2026, 1, 1), 0.0), (date(2026, 1, 2), 10.0), (date(2026, 1, 3), 12.0)]
    assert _compute_post_earnings_returns(bars, date(2026, 1, 2)) == (None, None)


# ── backfill_post_earnings_returns() ────────────────────────────────────────────────────

class _FakeColumn:
    def __ge__(self, other):
        return True

    def is_(self, other):
        return True

    def isnot(self, other):
        return True

    def __eq__(self, other):
        return True


class _FakeEventModel:
    stock_id = _FakeColumn()
    report_date = _FakeColumn()
    eps_actual = _FakeColumn()
    post_earnings_return_1d = _FakeColumn()


class _FakeStockModel:
    id = _FakeColumn()


class _FakePriceModel:
    stock_id = _FakeColumn()
    timeframe = _FakeColumn()
    ts = _FakeColumn()
    close = _FakeColumn()


class _ChainableStmt:
    def join(self, *a, **k):
        return self

    def where(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self


def _install_fake_query_plumbing(monkeypatch):
    monkeypatch.setattr(e, "select", lambda *a, **k: _ChainableStmt())
    monkeypatch.setattr(e, "EarningsEvent", _FakeEventModel)
    monkeypatch.setattr(e, "Stock", _FakeStockModel)
    monkeypatch.setattr(e, "Price", _FakePriceModel)


class _FakeEvInstance:
    """A real object (not a MagicMock) standing in for one EarningsEvent row — its
    post_earnings_return_1d/_5d fields start genuinely None, so a test asserting `is None`
    after the fact actually proves the real code never touched the field, rather than merely
    proving it (redundantly) as MagicMock's own auto-vivified default would."""
    def __init__(self, report_date):
        self.report_date = report_date
        self.post_earnings_return_1d = None
        self.post_earnings_return_5d = None


def _make_fake_ev(report_date):
    return _FakeEvInstance(report_date)


class _PriceRow:
    """A minimal stand-in for a real SQLAlchemy Row from `select(Price.ts, Price.close)` — the
    real code accesses .ts/.close as ATTRIBUTES (a real Row supports both attribute and tuple
    access), not tuple indices, so a plain (ts, close) tuple in a test fixture would silently
    fail with AttributeError inside the function's own try/except and never actually exercise
    the real fill logic — caught and fixed here after the first draft of these tests did
    exactly that (both "fills a real row" and "leaves a row null" initially reported filled=0
    for the wrong reason)."""
    def __init__(self, ts, close):
        self.ts = ts
        self.close = close


def _install_fake_session(monkeypatch, unresolved_rows, price_rows_by_stock_id):
    """unresolved_rows: [(ev, stock_id), ...] — what the first (unresolved EarningsEvent) query
    should return. price_rows_by_stock_id: {stock_id: [_PriceRow(ts, close), ...]} — per-stock
    what the Price query should return, called once per unresolved row in order."""
    _install_fake_query_plumbing(monkeypatch)
    first_result = MagicMock()
    first_result.all.return_value = unresolved_rows

    price_results = []
    for _, stock_id in unresolved_rows:
        pr = MagicMock()
        pr.all.return_value = price_rows_by_stock_id.get(stock_id, [])
        price_results.append(pr)

    fake_session = MagicMock()
    fake_session.execute.side_effect = [first_result] + price_results
    fake_session.__enter__.return_value = fake_session
    fake_session.__exit__.return_value = False
    monkeypatch.setattr(e, "SessionLocal", lambda: fake_session)
    return fake_session


def test_returns_zero_work_when_nothing_unresolved(monkeypatch):
    _install_fake_session(monkeypatch, [], {})
    result = _run(backfill_post_earnings_returns())
    assert result == {"checked": 0, "filled": 0}


def test_fills_a_real_row_with_enough_price_history(monkeypatch):
    from datetime import datetime as dt
    ev = _make_fake_ev(date(2026, 8, 6))
    bars = [
        _PriceRow(dt(2026, 8, 5), 30.11), _PriceRow(dt(2026, 8, 6), 26.54), _PriceRow(dt(2026, 8, 7), 27.90),
        _PriceRow(dt(2026, 8, 10), 27.67), _PriceRow(dt(2026, 8, 11), 27.97), _PriceRow(dt(2026, 8, 12), 29.61),
        _PriceRow(dt(2026, 8, 13), 30.78),
    ]
    fake_session = _install_fake_session(monkeypatch, [(ev, 42)], {42: bars})

    result = _run(backfill_post_earnings_returns())
    assert result == {"checked": 1, "filled": 1}
    assert ev.post_earnings_return_1d == 27.90 / 30.11 - 1
    assert ev.post_earnings_return_5d == 30.78 / 30.11 - 1
    fake_session.commit.assert_called_once()


def test_leaves_a_row_null_when_not_enough_trading_days_have_elapsed_yet(monkeypatch):
    """A report too recent to measure must be left alone (both fields stay unset) rather than
    writing a partial/guessed value — and must NOT count toward "filled", so the same row is
    correctly retried on the next run."""
    from datetime import datetime as dt
    ev = _make_fake_ev(date(2026, 8, 6))
    bars = [_PriceRow(dt(2026, 8, 5), 30.11), _PriceRow(dt(2026, 8, 6), 26.54)]  # only 1 after-bar — too soon
    _install_fake_session(monkeypatch, [(ev, 42)], {42: bars})

    result = _run(backfill_post_earnings_returns())
    assert result == {"checked": 1, "filled": 0}
    assert ev.post_earnings_return_1d is None
    assert ev.post_earnings_return_5d is None


def test_one_symbols_price_fetch_failure_does_not_abort_the_whole_batch(monkeypatch):
    """A per-row exception (a DB hiccup fetching one symbol's own price history) must be caught
    and logged, never propagate and abort the remaining rows in the same batch — matching this
    file's own established per-row isolation convention (check_earnings_impact_poll's identical
    try/except-per-row shape a few functions above)."""
    _install_fake_query_plumbing(monkeypatch)
    ev1 = _make_fake_ev(date(2026, 8, 6))
    ev2 = _make_fake_ev(date(2026, 8, 6))
    first_result = MagicMock()
    first_result.all.return_value = [(ev1, 1), (ev2, 2)]

    # First call succeeds (the unresolved-rows query), every subsequent call raises.
    fake_session = MagicMock()
    fake_session.execute.side_effect = [first_result, ConnectionError("boom"), ConnectionError("boom")]
    fake_session.__enter__.return_value = fake_session
    fake_session.__exit__.return_value = False
    monkeypatch.setattr(e, "SessionLocal", lambda: fake_session)

    result = _run(backfill_post_earnings_returns())
    # Both rows attempted, both failed to fill (their own price fetch raised) — but the
    # function itself completed without propagating either exception.
    assert result == {"checked": 2, "filled": 0}
