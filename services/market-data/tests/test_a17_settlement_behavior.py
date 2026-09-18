"""AUD-A17 — BEHAVIORAL tests for settle_expired_positions.

WHY THIS FILE EXISTS. test_t398_options_income_engine.py states its own rationale for covering
the DB-facing functions with source-TEXT checks instead of behaviour: "nothing meaningful to
assert against a MagicMock session". A17 is the counterexample. `settle_expired_positions` was
shipped with the settlement tuple assigned to the same name as the integer counter, so it raised
TypeError on the FIRST successful settlement of every run — while its source-text test kept
passing, because the text it asserted on was still there.

A fake session is enough to exercise the real function end to end: the only SQLAlchemy behaviour
it needs is "return these positions" and "record commit/rollback". That is what these do, and it
is the same sabotage discipline docs/incidents/ci-failure-masking.md already demands elsewhere —
a test that cannot go red is not a test.
"""
from datetime import date

import pytest

from src.services import options_income_engine as eng


class _Col:
    """Stand-in for a SQLAlchemy column: comparisons yield a marker instead of raising, so the
    real query-building code in the function under test runs untouched."""
    def __eq__(self, o): return True
    def __le__(self, o): return True
    def __hash__(self): return id(self)


class _FakeModel:
    portfolio_id = _Col(); stage = _Col(); expiry = _Col(); symbol = _Col()

    @classmethod
    def where(cls, *a, **k): return cls


@pytest.fixture(autouse=True)
def _patch_models(monkeypatch):
    monkeypatch.setattr(eng, "OptionsIncomePosition", _FakeModel, raising=False)
    monkeypatch.setattr(eng, "Stock", _FakeModel, raising=False)
    monkeypatch.setattr(eng, "select", lambda *a, **k: _FakeModel, raising=False)


class FakePos:
    def __init__(self, pid=1, symbol="AMD", strike=500.0, entry=493.41):
        self.id = pid; self.symbol = symbol; self.stock_id = 207; self.stage = "open"
        self.expiry = date(2026, 9, 25); self.strategy = "CASH_SECURED_PUT"
        self.strike = strike; self.total_premium_collected = 300.0; self.contracts = 1
        self.underlying_entry_price = entry; self.collateral_reserved = 50000.0
        self.close_date = None; self.underlying_close_price = None; self.assigned = None
        self.pnl = None; self.pct_return_on_collateral = None; self.close_reason = None


class FakePortfolio:
    def __init__(self, cash=66467.0): self.id = 2; self.current_cash = cash


class FakeSession:
    def __init__(self, positions):
        self._p = positions; self.commits = 0; self.rollbacks = 0
    def execute(self, *a, **k):
        outer = self
        class R:
            def scalars(s):
                class S:
                    def all(s2): return outer._p
                return S()
            def scalar_one_or_none(s): return None
        return R()
    def commit(self): self.commits += 1
    def rollback(self): self.rollbacks += 1


def _settles_at(price):
    return lambda session, stock_id, expiry: (price, date(2026, 9, 25))


def test_no_expired_positions_returns_zero_and_does_not_commit():
    sess = FakeSession([])
    assert eng.settle_expired_positions(sess, FakePortfolio(), as_of=date(2026, 9, 26)) == 0
    assert sess.commits == 0


def test_single_position_returns_integer_one(monkeypatch):
    """The exact A17 regression: this raised TypeError before the fix."""
    monkeypatch.setattr(eng, "_settlement_close", _settles_at(505.0))
    pos, pf, sess = FakePos(), FakePortfolio(), None
    sess = FakeSession([pos])
    n = eng.settle_expired_positions(sess, pf, as_of=date(2026, 9, 26))
    assert n == 1 and isinstance(n, int)
    assert pos.stage == "closed"
    assert sess.commits == 1 and sess.rollbacks == 0


def test_multiple_positions_all_counted(monkeypatch):
    """A17 also RESET the counter every iteration, so the count was wrong even without the raise."""
    monkeypatch.setattr(eng, "_settlement_close", _settles_at(505.0))
    positions = [FakePos(1, "AMD"), FakePos(2, "TQQQ"), FakePos(3, "PLTR")]
    sess = FakeSession(positions)
    n = eng.settle_expired_positions(sess, FakePortfolio(), as_of=date(2026, 9, 26))
    assert n == 3
    assert all(p.stage == "closed" for p in positions)
    assert sess.commits == 1, "one commit for the whole batch, not one per position"


def test_missing_settlement_price_returns_int_not_none(monkeypatch):
    """Declared `-> int`. The missing-price path returned None before the fix."""
    monkeypatch.setattr(eng, "_settlement_close", lambda s, sid, e: None)
    pos = FakePos()
    sess = FakeSession([pos])
    n = eng.settle_expired_positions(sess, FakePortfolio(), as_of=date(2026, 9, 26))
    assert n == 0 and isinstance(n, int)
    assert pos.stage == "open", "left open for retry, never settled against a substitute close"
    assert sess.commits == 0


def test_mixed_available_and_missing_prices(monkeypatch):
    calls = {"n": 0}
    def _mixed(session, stock_id, expiry):
        calls["n"] += 1
        return (505.0, date(2026, 9, 25)) if calls["n"] != 2 else None
    monkeypatch.setattr(eng, "_settlement_close", _mixed)
    positions = [FakePos(1, "AMD"), FakePos(2, "TQQQ"), FakePos(3, "PLTR")]
    sess = FakeSession(positions)
    assert eng.settle_expired_positions(sess, FakePortfolio(), as_of=date(2026, 9, 26)) == 2
    assert [p.stage for p in positions] == ["closed", "open", "closed"]


def test_failure_mid_batch_rolls_back_and_reraises(monkeypatch):
    """The case that would have persisted corrupt state.

    run_options_income_step catches this exception and only LOGS it, then carries on to
    _snapshot_income_equity_curve, whose commit() is unconditional. Without the rollback here
    that later commit persists a settlement this function reported as FAILED.
    """
    calls = {"n": 0}
    def _explodes_on_second(session, stock_id, expiry):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("price lookup blew up")
        return (505.0, date(2026, 9, 25))
    monkeypatch.setattr(eng, "_settlement_close", _explodes_on_second)
    positions = [FakePos(1, "AMD"), FakePos(2, "TQQQ")]
    sess = FakeSession(positions)
    with pytest.raises(RuntimeError):
        eng.settle_expired_positions(sess, FakePortfolio(), as_of=date(2026, 9, 26))
    assert sess.rollbacks == 1, "the whole batch must be rolled back"
    assert sess.commits == 0, "nothing may be committed when the batch failed"


def test_cash_released_matches_settlement_economics(monkeypatch):
    """Cash conservation: the portfolio gains exactly what settle_position_economics releases."""
    monkeypatch.setattr(eng, "_settlement_close", _settles_at(520.0))  # above strike -> OTM put
    pos = FakePos(strike=500.0, entry=493.41)
    pf = FakePortfolio(cash=10_000.0)
    econ = eng.settle_position_economics(
        strategy=pos.strategy, strike=pos.strike,
        premium_collected=pos.total_premium_collected, contracts=pos.contracts,
        underlying_entry_price=pos.underlying_entry_price, close_price=520.0,
    )
    eng.settle_expired_positions(FakeSession([pos]), pf, as_of=date(2026, 9, 26))
    assert pf.current_cash == pytest.approx(10_000.0 + econ["cash_released"])
    assert pos.assigned is False and pos.close_reason == "expired_otm"
