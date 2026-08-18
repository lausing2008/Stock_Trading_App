"""Tests for wsz-analyst-accuracy-weighting — per-firm analyst price-target accuracy tracking
and the accuracy-weighted consensus computation.

routes.py can't be imported directly in this test environment (conftest.py stubs sqlalchemy/db
as MagicMock(), and this module imports fastapi/yfinance/common.config at module level) — the
pure computation function under test (_compute_weighted_analyst_consensus) has no dependency
on any of that beyond select/func/date/timedelta/AnalystPriceTarget/Stock, so it's extracted
via source-text exec() and run against a REAL in-memory SQLite session with real models,
matching test_gate_harness_extended.py's/test_correlation_preentry.py's established technique
(pop the sqlalchemy/db stub, build one shared engine + real models, restore the stub
immediately) — exercising the actual logic, not a hand-copied reimplementation.

_evaluate_analyst_target_outcomes() (scheduler.py) is covered separately via source-text
regression checks, matching this repo's established pattern for scheduler.py functions
(apscheduler isn't installed locally, so the whole module can't be imported).
"""
import sys

_STUBBED_MODULES = ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql", "db")
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util
import pathlib
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_aaw", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_aaw"] = _models
_spec.loader.exec_module(_models)

_ENGINE = create_engine("sqlite:///:memory:")
_models.Base.metadata.create_all(
    _ENGINE, tables=[_models.Stock.__table__, _models.AnalystPriceTarget.__table__],
)

for _mod, _stub in _saved_stubs.items():
    if _stub is not None:
        sys.modules[_mod] = _stub
    else:
        sys.modules.pop(_mod, None)

Stock = _models.Stock
AnalystPriceTarget = _models.AnalystPriceTarget
Market = _models.Market
Exchange = _models.Exchange

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _extract_compute_weighted_analyst_consensus():
    # Pull the two real module-level constants directly from source too — a hand-copied
    # literal here could silently drift from the real value if it's ever tuned.
    const_start = _ROUTES_SOURCE.index("_ANALYST_ACCURACY_MIN_SAMPLES = ")
    const_end = _ROUTES_SOURCE.index("\n\n\ndef _compute_weighted_analyst_consensus(", const_start)
    const_source = _ROUTES_SOURCE[const_start:const_end]

    start = _ROUTES_SOURCE.index("def _compute_weighted_analyst_consensus(")
    end = _ROUTES_SOURCE.index('\n@router.get("/{symbol}/analyst-consensus")', start)
    func_source = _ROUTES_SOURCE[start:end]

    namespace = {
        "select": select, "func": func, "date": date, "timedelta": timedelta,
        "Stock": Stock, "AnalystPriceTarget": AnalystPriceTarget, "Session": Session,
    }
    exec(const_source, namespace)  # noqa: S102 — isolated eval of real source, matching repo convention
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source, matching repo convention
    return namespace["_compute_weighted_analyst_consensus"]


_compute_weighted_analyst_consensus = _extract_compute_weighted_analyst_consensus()


def _make_session() -> Session:
    return Session(_ENGINE)


_next_stock_id = [2000]


def _insert_stock(session, symbol):
    sid = _next_stock_id[0]
    _next_stock_id[0] += 1
    session.add(Stock(id=sid, symbol=symbol, market=Market.US, exchange=Exchange.NASDAQ, name=f"{symbol} Co"))
    session.commit()
    return sid


_next_apt_id = [1]


def _insert_target(
    session, stock_id, symbol, firm, grade_date, current_price_target,
    outcome_evaluated=False, target_achieved=None,
):
    apt_id = _next_apt_id[0]
    _next_apt_id[0] += 1
    session.add(AnalystPriceTarget(
        id=apt_id, stock_id=stock_id, symbol=symbol, firm=firm, grade_date=grade_date,
        current_price_target=current_price_target,
        outcome_evaluated_at=(datetime.now(timezone.utc) if outcome_evaluated else None),
        target_achieved=target_achieved,
    ))
    session.commit()


class TestComputeWeightedAnalystConsensus:
    def test_unknown_symbol_returns_none_consensus_not_a_crash(self):
        session = _make_session()
        result = _compute_weighted_analyst_consensus(session, "NOSUCHSTOCK")
        assert result["simple_mean"] is None
        assert result["weighted_mean"] is None
        assert result["n_firms"] == 0
        session.close()

    def test_no_recent_targets_returns_none_consensus(self):
        session = _make_session()
        _insert_stock(session, "EMPTYCO")
        result = _compute_weighted_analyst_consensus(session, "EMPTYCO")
        assert result["simple_mean"] is None
        assert result["weighted_mean"] is None
        session.close()

    def test_simple_mean_is_the_unweighted_average_of_the_most_recent_targets(self):
        session = _make_session()
        sid = _insert_stock(session, "SIMPLE1")
        today = date.today()
        _insert_target(session, sid, "SIMPLE1", "FirmA", today - timedelta(days=5), 100.0)
        _insert_target(session, sid, "SIMPLE1", "FirmB", today - timedelta(days=3), 200.0)
        result = _compute_weighted_analyst_consensus(session, "SIMPLE1")
        assert result["simple_mean"] == 150.0
        assert result["n_firms"] == 2
        session.close()

    def test_targets_older_than_the_lookback_window_are_excluded(self):
        session = _make_session()
        sid = _insert_stock(session, "OLDSYM")
        today = date.today()
        _insert_target(session, sid, "OLDSYM", "FirmA", today - timedelta(days=5), 100.0)
        _insert_target(session, sid, "OLDSYM", "FirmOld", today - timedelta(days=200), 999.0)  # way outside 90d window
        result = _compute_weighted_analyst_consensus(session, "OLDSYM")
        assert result["n_firms"] == 1
        assert result["simple_mean"] == 100.0
        session.close()

    def test_only_the_most_recent_target_per_firm_counts_not_every_historical_one(self):
        """A firm that re-issued 2 targets in the recent window must contribute ONCE, with
        its LATEST view — not be double-counted into the mean."""
        session = _make_session()
        sid = _insert_stock(session, "REISSUE")
        today = date.today()
        _insert_target(session, sid, "REISSUE", "FirmA", today - timedelta(days=10), 100.0)
        _insert_target(session, sid, "REISSUE", "FirmA", today - timedelta(days=2), 150.0)  # latest — should win
        result = _compute_weighted_analyst_consensus(session, "REISSUE")
        assert result["n_firms"] == 1
        assert result["simple_mean"] == 150.0
        session.close()

    def test_a_firm_with_insufficient_scored_history_gets_equal_weight_not_its_raw_accuracy(self):
        """A firm with only 2 scored targets (below _ANALYST_ACCURACY_MIN_SAMPLES=5), at a
        NON-1.0 accuracy (0%, deliberately — a fixture where the thin-history accuracy happens
        to equal the equal-weight fallback value of 1.0 would pass even if the min-samples
        floor were silently removed, since both paths land on the identical weight by
        coincidence), must still get equal weight (1.0), not its own raw 0% accuracy weight —
        proving the floor is genuinely gating on sample count, not just reachable by luck."""
        session = _make_session()
        sid = _insert_stock(session, "THINHIST")
        today = date.today()
        _insert_target(session, sid, "THINHIST", "ThinFirm", today - timedelta(days=5), 100.0)
        _insert_target(session, sid, "THINHIST", "NoHistFirm", today - timedelta(days=5), 200.0)
        # ThinFirm has only 2 scored (< 5 floor), BOTH MISSED (0% accuracy) — if the floor were
        # removed, ThinFirm's weight would collapse toward 0 and the weighted mean would swing
        # heavily toward NoHistFirm's 200.0. With the floor intact, both firms get equal
        # weight (1.0), so the weighted mean must equal the simple mean of 150.0.
        old = today - timedelta(days=400)
        _insert_target(session, sid, "THINHIST", "ThinFirm", old, 50.0, outcome_evaluated=True, target_achieved=False)
        _insert_target(session, sid, "THINHIST", "ThinFirm", old - timedelta(days=1), 51.0, outcome_evaluated=True, target_achieved=False)
        result = _compute_weighted_analyst_consensus(session, "THINHIST")
        assert result["weighted_mean"] == result["simple_mean"] == 150.0
        session.close()

    def test_a_firm_with_enough_scored_history_is_weighted_by_its_real_accuracy(self):
        """FirmHigh (5 scored, 100% accurate) must pull the weighted mean toward its own
        target relative to FirmLow (5 scored, 0% accurate) — proving the weight actually
        changes the outcome, not just passes through unweighted."""
        session = _make_session()
        sid = _insert_stock(session, "REALWEIGHT")
        today = date.today()
        _insert_target(session, sid, "REALWEIGHT", "FirmHigh", today - timedelta(days=5), 100.0)
        _insert_target(session, sid, "REALWEIGHT", "FirmLow", today - timedelta(days=5), 300.0)
        old = today - timedelta(days=400)
        for i in range(5):
            _insert_target(session, sid, "REALWEIGHT", "FirmHigh", old - timedelta(days=i), 10.0 + i, outcome_evaluated=True, target_achieved=True)
            _insert_target(session, sid, "REALWEIGHT", "FirmLow", old - timedelta(days=i + 10), 20.0 + i, outcome_evaluated=True, target_achieved=False)
        result = _compute_weighted_analyst_consensus(session, "REALWEIGHT")
        simple = (100.0 + 300.0) / 2  # 200.0
        # FirmHigh (weight~1.0) should pull the weighted mean well below the simple 200.0,
        # toward its own 100.0 target, since FirmLow's weight is ~0.0.
        assert result["weighted_mean"] < simple
        assert result["weighted_mean"] < 150.0  # closer to FirmHigh's 100 than the midpoint
        # Confirm the per-firm accuracy figures are surfaced correctly too.
        firm_high = next(f for f in result["firms"] if f["firm"] == "FirmHigh")
        firm_low = next(f for f in result["firms"] if f["firm"] == "FirmLow")
        assert firm_high["accuracy_pct"] == 100.0
        assert firm_low["accuracy_pct"] == 0.0
        session.close()

    def test_accuracy_is_computed_across_all_symbols_a_firm_has_covered_not_just_this_one(self):
        """A firm's track record on A DIFFERENT symbol must still count toward its accuracy
        weight here — accuracy is a firm-level property, not symbol-scoped."""
        session = _make_session()
        sid_a = _insert_stock(session, "CROSSA")
        sid_b = _insert_stock(session, "CROSSB")
        today = date.today()
        old = today - timedelta(days=400)
        # FirmX's ENTIRE scored history is on a different symbol (CROSSB).
        for i in range(5):
            _insert_target(session, sid_b, "CROSSB", "FirmX", old - timedelta(days=i), 10.0 + i, outcome_evaluated=True, target_achieved=True)
        _insert_target(session, sid_a, "CROSSA", "FirmX", today - timedelta(days=5), 500.0)
        _insert_target(session, sid_a, "CROSSA", "FirmY", today - timedelta(days=5), 100.0)  # no history
        result = _compute_weighted_analyst_consensus(session, "CROSSA")
        firm_x = next(f for f in result["firms"] if f["firm"] == "FirmX")
        assert firm_x["accuracy_pct"] == 100.0
        assert firm_x["n_scored_targets"] == 5
        session.close()

    def test_null_current_price_target_rows_are_excluded_from_the_consensus(self):
        """A row with no captured price target (e.g. a plain reiteration action) must never
        contribute a phantom $0 or None into the mean."""
        session = _make_session()
        sid = _insert_stock(session, "NULLTARGET")
        today = date.today()
        _insert_target(session, sid, "NULLTARGET", "FirmA", today - timedelta(days=5), 100.0)
        apt_id = _next_apt_id[0]
        _next_apt_id[0] += 1
        session.add(AnalystPriceTarget(
            id=apt_id, stock_id=sid, symbol="NULLTARGET", firm="FirmNoTarget",
            grade_date=today - timedelta(days=3), current_price_target=None,
        ))
        session.commit()
        result = _compute_weighted_analyst_consensus(session, "NULLTARGET")
        assert result["n_firms"] == 1
        assert result["simple_mean"] == 100.0
        session.close()
