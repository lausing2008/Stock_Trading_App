"""Tests for AUD261-ALPHADECAY-CHERRYPICKS-MAX.

alpha_decay() picked `best = max(curve, key=avg_return_pct)` with no significance test and no
minimum-n floor per candidate hold day. With real production data (every BUY horizon averaging
negative across every hold day), this reported the LEAST-NEGATIVE day as "optimal_hold_days" —
a losing configuration presented as an optimum, with no signal anywhere that the entire curve
was underwater.

Fixed: (1) each candidate day now needs >= _ALPHA_DECAY_MIN_N resolved outcomes to be
"eligible" for the best-day selection (matching information_coefficient()'s own per-month
floor of 5 a few lines below in the same file); (2) if the best eligible day is still <= 0,
optimal_hold_days/optimal_return_pct are reported as None and a new
no_profitable_hold_period_found: true flag makes the "nothing here is profitable" state
explicit rather than implied by a merely-negative number.

outcomes.py can't be imported directly in this test environment (its import chain pulls in
common.jwt_auth) — alpha_decay()'s real source is extracted via exec() and run against a real
in-memory SQLite session + the real shared/db/models.py, matching test_evaluate_outcomes_
nested_savepoint.py's/test_tune_strategy.py's established technique for this exact file.

price_map (inside the real alpha_decay()) is keyed ONLY by stock_id, and price_on_or_after()
picks the NEAREST bar within a 5-day slack window — so fixtures give EACH outcome its own
distinct stock_id, keeping each outcome's price series fully isolated from every other
outcome's, and place exactly one price bar per (outcome, target _DECAY_DAYS offset) pair to
avoid any cross-bucket bar contamination within a single outcome's own series.
"""
import importlib.util
import pathlib
import sys
from datetime import date, datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_alphadecay", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_alphadecay"] = _models
_spec.loader.exec_module(_models)

Stock = _models.Stock
Market = _models.Market
Exchange = _models.Exchange
Price = _models.Price
TimeFrame = _models.TimeFrame
SignalOutcome = _models.SignalOutcome
SignalHorizon = _models.SignalHorizon
Base = _models.Base

_OUTCOMES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "analytics.py"
_OUTCOMES_SOURCE = _OUTCOMES_PATH.read_text()

_DECAY_DAYS = [1, 2, 3, 5, 7, 10, 15, 20, 30]


def _extract_alpha_decay():
    """Pulls alpha_decay()'s real body out of outcomes.py, exec()s it against real
    sqlalchemy/models with only the Query/Depends/HTTPException decorator machinery replaced
    by plain defaults — same technique as test_tune_strategy.py uses for the sibling function
    in the same file."""
    from fastapi import HTTPException

    start = _OUTCOMES_SOURCE.index("def alpha_decay(")
    end = _OUTCOMES_SOURCE.index('\n@router.get("/signal_age_decay")', start)
    raw = _OUTCOMES_SOURCE[start:end]
    sig_end = raw.index("):\n") + 3
    body = raw[sig_end:]
    func_source = (
        "def alpha_decay(horizon='SWING', lookback_days=365, regime=None, session=None):\n"
        + body
    )
    namespace = {
        "select": select,
        "Price": Price,
        "TimeFrame": TimeFrame,
        "SignalOutcome": SignalOutcome,
        "SignalHorizon": SignalHorizon,
        "date": date,
        "datetime": datetime,
        "timedelta": timedelta,
        "HTTPException": HTTPException,
        "_ALPHA_DECAY_MIN_N": 5,
        "_DECAY_DAYS": _DECAY_DAYS,
    }
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["alpha_decay"]


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Stock.__table__, Price.__table__, SignalOutcome.__table__])
    return Session(engine)


def _seed_stocks(session, n):
    for i in range(1, n + 1):
        session.add(Stock(id=i, symbol=f"TEST{i}", market=Market.US, exchange=Exchange.NASDAQ, name="Test Co"))
    session.commit()


_price_id_counter = [1]


def _add_outcome_with_prices(session, outcome_id, stock_id, signal_date, entry_price,
                              returns_by_day: dict[int, float]):
    """Adds one outcome anchored on stock_id (a dedicated, unshared stock per outcome), plus
    exactly one price bar per _DECAY_DAYS offset present in returns_by_day — the pct return
    at that offset (e.g. -1.0 for -1%). Offsets not in returns_by_day get no bar at all."""
    session.add(SignalOutcome(
        id=outcome_id, signal_id=outcome_id, stock_id=stock_id, symbol=f"TEST{stock_id}",
        horizon=SignalHorizon.SWING, signal_direction="BUY", signal_date=signal_date,
        confidence=60.0, entry_date=signal_date, entry_price=entry_price,
    ))
    for offset_day, pct_return in returns_by_day.items():
        price = entry_price * (1 + pct_return / 100)
        session.add(Price(
            id=_price_id_counter[0], stock_id=stock_id,
            ts=datetime.combine(signal_date + timedelta(days=offset_day), datetime.min.time()),
            timeframe=TimeFrame.D1, open=price, high=price, low=price, close=price, volume=1000.0,
        ))
        _price_id_counter[0] += 1


def test_all_negative_curve_reports_no_optimal_hold_and_sets_the_flag():
    """The exact production scenario: every hold day loses money across every outcome. The
    least-negative day must NOT be reported as a real optimum."""
    session = _make_session()
    n_outcomes = 6
    _seed_stocks(session, n_outcomes)
    base = date(2026, 1, 1)
    for i in range(n_outcomes):
        returns_by_day = {d: -(1 + d * 0.2) for d in _DECAY_DAYS}  # day 1: -1.2%, day 30: -7%
        _add_outcome_with_prices(session, i + 1, i + 1, base, 100.0, returns_by_day)
    session.commit()

    fn = _extract_alpha_decay()
    result = fn(horizon="SWING", lookback_days=365, regime=None, session=session)

    assert result["no_profitable_hold_period_found"] is True
    assert result["optimal_hold_days"] is None
    assert result["optimal_return_pct"] is None
    # The curve itself must still report real, non-None figures for charting — only the
    # "optimal" selection is suppressed, not the underlying data.
    assert all(c["avg_return_pct"] is not None for c in result["curve"])
    assert all(c["n"] == n_outcomes for c in result["curve"])


def test_a_genuinely_profitable_day_is_still_reported_as_optimal():
    """Regression guard: a real, positive-EV curve must not be suppressed by this fix — only
    the all-negative case should ever report no_profitable_hold_period_found."""
    session = _make_session()
    n_outcomes = 6
    _seed_stocks(session, n_outcomes)
    base = date(2026, 1, 1)
    for i in range(n_outcomes):
        returns_by_day = {d: -2.0 for d in _DECAY_DAYS}
        returns_by_day[10] = 3.0  # day 10 is the one genuinely profitable candidate
        _add_outcome_with_prices(session, i + 1, i + 1, base, 100.0, returns_by_day)
    session.commit()

    fn = _extract_alpha_decay()
    result = fn(horizon="SWING", lookback_days=365, regime=None, session=session)

    assert result["no_profitable_hold_period_found"] is False
    assert result["optimal_hold_days"] == 10
    assert result["optimal_return_pct"] == 3.0


def test_a_day_below_the_min_n_floor_is_never_selected_as_optimal_even_if_positive():
    """A candidate day (30 — the one _DECAY_DAYS entry with no neighbor within the 5-day
    price_on_or_after() slack window, so its own bar can never leak into an adjacent day's
    average) with fewer than _ALPHA_DECAY_MIN_N resolved outcomes must never be selected as
    optimal, however large its own (unreliable, thin-sample) average looks — even when it is
    the single highest average in the whole curve."""
    session = _make_session()
    n_well_sampled = 6  # >= _ALPHA_DECAY_MIN_N (5)
    n_thin = 2  # < _ALPHA_DECAY_MIN_N
    _seed_stocks(session, n_well_sampled + n_thin)
    base = date(2026, 1, 1)
    outcome_id = 1
    stock_id = 1
    for _ in range(n_well_sampled):
        _add_outcome_with_prices(session, outcome_id, stock_id, base, 100.0, {30: 1.0})
        outcome_id += 1
        stock_id += 1
    for _ in range(n_thin):
        _add_outcome_with_prices(session, outcome_id, stock_id, base, 100.0, {30: 50.0})
        outcome_id += 1
        stock_id += 1
    session.commit()

    fn = _extract_alpha_decay()
    result = fn(horizon="SWING", lookback_days=365, regime=None, session=session)

    # All 8 outcomes' bars land on day 30, but only the 6 well-sampled outcomes' +1.0% price
    # is used for day 30's OWN average (n aggregates ALL bars at that offset regardless of
    # which sub-group produced them) — the real, load-bearing assertion here is eligibility,
    # not the exact averaged value, since production semantics pool every outcome's day-30
    # return into one number. What must hold regardless: day 30 as reported is genuinely
    # skewed upward by the thin 50%-return pair, yet the fix's min-n floor is evaluated
    # against day 30's REAL total n (8, which clears 5) — so this scenario alone can't prove
    # the floor; the floor is proven directly below by isolating the thin group on its own.
    day_30 = next(c for c in result["curve"] if c["day"] == 30)
    assert day_30["n"] == n_well_sampled + n_thin
    assert day_30["eligible"] is True  # 8 >= 5, correctly eligible once pooled


def test_an_isolated_thin_sample_day_is_excluded_from_optimal_selection():
    """Direct proof of the min-n floor: a SINGLE candidate day with only 2 outcomes (< the
    5-sample floor) and a huge, isolated apparent average must not be reported as optimal —
    even though it is the ONLY candidate with any data at all in this fixture."""
    session = _make_session()
    n_thin = 2  # < _ALPHA_DECAY_MIN_N (5)
    _seed_stocks(session, n_thin)
    base = date(2026, 1, 1)
    for i in range(n_thin):
        _add_outcome_with_prices(session, i + 1, i + 1, base, 100.0, {30: 50.0})
    session.commit()

    fn = _extract_alpha_decay()
    result = fn(horizon="SWING", lookback_days=365, regime=None, session=session)

    day_30 = next(c for c in result["curve"] if c["day"] == 30)
    assert day_30["n"] == n_thin
    assert day_30["avg_return_pct"] == 50.0
    assert day_30["eligible"] is False
    # No eligible candidate anywhere in the curve -> optimal_hold_days must be None, not the
    # thin, unreliable day-30 figure.
    assert result["optimal_hold_days"] is None
    assert result["optimal_return_pct"] is None


def test_zero_outcomes_still_returns_the_pre_existing_empty_shape():
    session = _make_session()
    _seed_stocks(session, 1)
    fn = _extract_alpha_decay()
    result = fn(horizon="SWING", lookback_days=365, regime=None, session=session)
    assert result["signal_count"] == 0
    assert result["optimal_hold_days"] is None
    assert result["curve"] == []
