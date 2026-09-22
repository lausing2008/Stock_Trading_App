"""0d / AUD-ALPHAEVAL — day-clustered benchmark-relative alpha for fix-effectiveness snapshots.

WHY THIS METRIC EXISTS. A power analysis on the real distributions says the intuitive metrics
cannot detect whether a fix worked in any usable timeframe:

  * Paper-trade P&L: mean -$66.85, sd $640, 1.31 trades/day -> ~719 trades (~550 trading days)
    to detect that expectancy reached breakeven.
  * Day-clustered signal alpha: day-mean -1.967%, sd across days 5.152% -> ~52 trading days.

WHY CLUSTERING BY DAY IS NOT OPTIONAL. ~144 signals share a single trading day and are heavily
correlated (same market, often the same sector move). Counting them as 144 independent
observations inflates the t-statistic by roughly sqrt(144) = 12x and would manufacture
significance out of a single good or bad week. The decisive test below feeds the SAME returns
once concentrated on one day and once spread across many, and asserts the two do not report the
same confidence.

WHY BENCHMARK-RELATIVE, PER MARKET. Absolute return credits a BUY simply for firing in a rising
market. And the benchmark must match the market: re-running the HK population against 2800.HK
instead of SPY moved its measured alpha from -5.56% to -6.71% — the wrong benchmark was
FLATTERING it, not penalising it.

Real in-memory SQLite with the real models, matching test_fix_effectiveness.py's established
technique for this file. See docs/audits/2026-09-22-news-llm-hmm-prediction-audit.md.
"""
import importlib.util
import pathlib
import sys
import textwrap
from datetime import date, datetime

from sqlalchemy import Integer, create_engine, select
from sqlalchemy.orm import Session

# `db` is a MagicMock under this service's conftest — load the REAL models module directly,
# matching test_fix_effectiveness.py's own established technique for this file.
_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_day_alpha", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_day_alpha"] = _models
_spec.loader.exec_module(_models)

Base = _models.Base
Exchange = _models.Exchange
Market = _models.Market
Price = _models.Price
SignalOutcome = _models.SignalOutcome
Stock = _models.Stock
TimeFrame = _models.TimeFrame

_FIX_EFF_SOURCE = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "fix_effectiveness.py"
).read_text()

_BENCH = {"US": "SPY", "HK": "2800.HK"}


def _extract_alpha_func() -> str:
    start = _FIX_EFF_SOURCE.index("def _compute_day_clustered_alpha(")
    end = _FIX_EFF_SOURCE.index("\ndef _compute_ai_signal_win_rate_metrics(", start)
    return textwrap.dedent(_FIX_EFF_SOURCE[start:end])


def _run_alpha(session, since=None):
    ns = {
        "select": select, "Session": Session, "date": date,
        "Price": Price, "Stock": Stock, "TimeFrame": TimeFrame,
        "SignalOutcome": SignalOutcome,
        "_BENCH_SYMBOL_BY_MARKET": _BENCH,
    }
    exec(_extract_alpha_func(), ns)  # noqa: S102 — isolated eval of real source
    return ns["_compute_day_clustered_alpha"](session, since=since)


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    # SQLite only auto-increments a PK declared exactly INTEGER, not BIGINT.
    for model in (Price, SignalOutcome):
        model.__table__.c.id.type = Integer()
    Base.metadata.create_all(
        engine, tables=[Stock.__table__, Price.__table__, SignalOutcome.__table__]
    )
    return Session(engine)


_ids = [1000]


def _nid() -> int:
    _ids[0] += 1
    return _ids[0]


def _add_stock(session, symbol, market=Market.US) -> int:
    sid = _nid()
    session.add(Stock(id=sid, symbol=symbol, name=symbol, market=market,
                      exchange=Exchange.NASDAQ, active=True, delisted=False))
    return sid


def _add_bars(session, stock_id, bars: dict):
    for d, close in bars.items():
        session.add(Price(id=_nid(), stock_id=stock_id, timeframe=TimeFrame.D1,
                          ts=datetime(d.year, d.month, d.day), open=close, high=close,
                          low=close, close=close, volume=1000))


def _add_outcome(session, *, stock_id, signal_date, entry, exit_, pct_return):
    session.add(SignalOutcome(
        id=_nid(), signal_id=_nid(), stock_id=stock_id, symbol=f"S{stock_id}",
        horizon="SWING", signal_direction="BUY", signal_date=signal_date,
        confidence=50.0,  # NOT NULL; irrelevant to alpha, fixed so it cannot influence results
        entry_date=datetime(entry.year, entry.month, entry.day),
        exit_date=datetime(exit_.year, exit_.month, exit_.day),
        pct_return=pct_return,
    ))


_E, _X = date(2026, 9, 1), date(2026, 9, 8)


def _flat_us_benchmark(session) -> None:
    """SPY flat across the window, so alpha equals the raw return and assertions stay readable."""
    spy = _add_stock(session, "SPY")
    _add_bars(session, spy, {_E: 100.0, _X: 100.0})


# ── The decisive property: clustering by DAY, not by signal ──────────────────────────────────

def test_many_signals_on_one_day_count_as_a_single_observation():
    """20 signals on ONE day must not look like 20 independent observations. With a single day
    there is no between-day variance at all, so no t-statistic is defensible."""
    s = _make_session()
    _flat_us_benchmark(s)
    stock = _add_stock(s, "AAA")
    for _ in range(20):
        _add_outcome(s, stock_id=stock, signal_date=_E, entry=_E, exit_=_X, pct_return=0.02)
    s.commit()
    r = _run_alpha(s)
    assert r["n_signals"] == 20
    assert r["n_days"] == 1, "20 same-day signals must collapse to ONE day-level observation"
    assert r["t_day"] is None, "a t-statistic on a single day is undefined, not 0.0"


def test_same_returns_spread_over_many_days_do_yield_a_t_statistic():
    """The contrast with the test above: genuine between-day variation is what makes a
    t-statistic meaningful."""
    s = _make_session()
    _flat_us_benchmark(s)
    stock = _add_stock(s, "AAA")
    for i, ret in enumerate([0.02, 0.03, 0.01, 0.025, 0.015]):
        d = date(2026, 9, 1 + i)
        _add_bars(s, stock, {})  # no per-stock bars needed; return is supplied directly
        _add_outcome(s, stock_id=stock, signal_date=d, entry=_E, exit_=_X, pct_return=ret)
    s.commit()
    r = _run_alpha(s)
    assert r["n_days"] == 5
    assert r["t_day"] is not None and r["t_day"] > 0


def test_a_single_loud_day_cannot_dominate_the_mean():
    """Day-clustering's real protection: 50 signals on one bad day carry the same weight as one
    signal on a good day. Per-signal averaging would let the loud day swamp the result."""
    s = _make_session()
    _flat_us_benchmark(s)
    stock = _add_stock(s, "AAA")
    for _ in range(50):
        _add_outcome(s, stock_id=stock, signal_date=_E, entry=_E, exit_=_X, pct_return=-0.10)
    _add_outcome(s, stock_id=stock, signal_date=date(2026, 9, 2), entry=_E, exit_=_X,
                 pct_return=0.10)
    s.commit()
    r = _run_alpha(s)
    assert r["n_days"] == 2
    # Day means are -10% and +10% -> mean 0. Per-SIGNAL averaging would give about -9.6%.
    assert abs(r["mean_day_alpha_pct"]) < 0.01, (
        f"expected day-weighted ~0%, got {r['mean_day_alpha_pct']}% — looks per-signal weighted"
    )


# ── Benchmark correctness ────────────────────────────────────────────────────────────────────

def test_alpha_subtracts_the_benchmark_move():
    s = _make_session()
    spy = _add_stock(s, "SPY")
    _add_bars(s, spy, {_E: 100.0, _X: 105.0})            # SPY +5%
    stock = _add_stock(s, "AAA")
    _add_outcome(s, stock_id=stock, signal_date=_E, entry=_E, exit_=_X, pct_return=0.08)  # +8%
    s.commit()
    r = _run_alpha(s)
    assert abs(r["mean_day_alpha_pct"] - 3.0) < 0.01, "8% return minus 5% benchmark = 3% alpha"


def test_hk_uses_its_own_benchmark_not_spy():
    """An HK signal benchmarked against SPY is the precise defect this guards."""
    s = _make_session()
    spy = _add_stock(s, "SPY")
    _add_bars(s, spy, {_E: 100.0, _X: 110.0})            # SPY +10%
    hsi = _add_stock(s, "2800.HK", market=Market.HK)
    _add_bars(s, hsi, {_E: 100.0, _X: 90.0})             # 2800.HK -10%
    hk_stock = _add_stock(s, "9988.HK", market=Market.HK)
    _add_outcome(s, stock_id=hk_stock, signal_date=_E, entry=_E, exit_=_X, pct_return=0.0)
    s.commit()
    r = _run_alpha(s)
    # Flat stock against a -10% HK benchmark is +10% alpha; against SPY it would read -10%.
    assert abs(r["mean_day_alpha_pct"] - 10.0) < 0.01, (
        f"got {r['mean_day_alpha_pct']}% — an HK signal appears to be benchmarked against SPY"
    )


def test_rows_with_no_benchmark_bar_are_dropped_not_treated_as_flat():
    """Substituting 0.0 for a missing benchmark would report the raw return AS IF it were
    alpha — manufacturing alpha on exactly the rows where the benchmark is unknown."""
    s = _make_session()
    # No SPY bars at all.
    _add_stock(s, "SPY")
    stock = _add_stock(s, "AAA")
    _add_outcome(s, stock_id=stock, signal_date=_E, entry=_E, exit_=_X, pct_return=0.25)
    s.commit()
    r = _run_alpha(s)
    assert r["n_signals"] == 0
    assert r["n_days"] == 0
    assert r["mean_day_alpha_pct"] is None, "a dropped row must not surface as 25% alpha"


def test_empty_population_reports_not_measurable_rather_than_zero():
    s = _make_session()
    _flat_us_benchmark(s)
    s.commit()
    r = _run_alpha(s)
    assert r["n_days"] == 0 and r["mean_day_alpha_pct"] is None and r["t_day"] is None


def test_since_filter_excludes_earlier_signals():
    s = _make_session()
    _flat_us_benchmark(s)
    stock = _add_stock(s, "AAA")
    _add_outcome(s, stock_id=stock, signal_date=date(2026, 8, 1), entry=_E, exit_=_X, pct_return=0.5)
    _add_outcome(s, stock_id=stock, signal_date=date(2026, 9, 5), entry=_E, exit_=_X, pct_return=0.02)
    s.commit()
    r = _run_alpha(s, since=date(2026, 9, 1))
    assert r["n_signals"] == 1
    assert abs(r["mean_day_alpha_pct"] - 2.0) < 0.01


# ── Wiring ───────────────────────────────────────────────────────────────────────────────────

def test_alpha_is_composed_at_the_snapshot_route_not_inside_the_domain_metric():
    """Keeps each domain's metric function pure and independently testable, and gives any future
    domain alpha for free."""
    assert 'metrics["alpha"] = _compute_day_clustered_alpha(' in _FIX_EFF_SOURCE
    metric_fn_start = _FIX_EFF_SOURCE.index("def _compute_ai_signal_win_rate_metrics(")
    metric_fn_end = _FIX_EFF_SOURCE.index("\n_SNAPSHOT_METRIC_FNS", metric_fn_start)
    assert "_compute_day_clustered_alpha" not in _FIX_EFF_SOURCE[metric_fn_start:metric_fn_end]


def test_alpha_failure_cannot_break_the_snapshots_primary_metrics():
    """It reads the large prices table; an observability failure must never cost the snapshot
    the metrics it exists to record."""
    start = _FIX_EFF_SOURCE.index('metrics["alpha"] = _compute_day_clustered_alpha(')
    body = _FIX_EFF_SOURCE[start - 200:start + 400]
    assert "try:" in body and "except Exception" in body
