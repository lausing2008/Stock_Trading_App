"""Tests for T233-SELFIMPROVE-PHASE2b — gate_harness.py's min_kscore/min_ta_score/min_volume_z
pre-filter replay (_passes_prefilter_gates, _historical_kscore).

gate_harness.py can't be imported directly in this test environment (conftest.py stubs
sqlalchemy itself as a MagicMock) — matches test_correlation_preentry.py's/
test_broker_position_sync.py's established technique exactly: pop the stub, build ONE shared
in-memory engine + real models while real sqlalchemy is active, then restore the stub
immediately so later-collected test files aren't affected. The two functions under test are
extracted from the real source via exec() and run against this real session, so these tests
exercise the actual logic, not a re-implementation.
"""
import sys

_STUBBED_MODULES = ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql", "db")
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util
import pathlib
from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_gh", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_gh"] = _models
_spec.loader.exec_module(_models)

_ENGINE = create_engine("sqlite:///:memory:")
_models.Base.metadata.create_all(
    _ENGINE, tables=[_models.Stock.__table__, _models.Ranking.__table__],
)

for _mod, _stub in _saved_stubs.items():
    if _stub is not None:
        sys.modules[_mod] = _stub
    else:
        sys.modules.pop(_mod, None)

Stock = _models.Stock
Ranking = _models.Ranking
Market = _models.Market
Exchange = _models.Exchange

_GH_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "backtest" / "gate_harness.py"
_GH_SOURCE = _GH_PATH.read_text()


def _extract_functions():
    """Pulls _historical_kscore() and _passes_prefilter_gates()'s real source out of
    gate_harness.py and exec()s them against real sqlalchemy/models."""
    start = _GH_SOURCE.index("def _historical_kscore(")
    end = _GH_SOURCE.index("\ndef replay_extended_gates(", start)
    func_source = _GH_SOURCE[start:end]
    namespace = {"select": select, "Ranking": Ranking, "Session": Session, "date": date}
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source, matching repo convention
    return namespace["_historical_kscore"], namespace["_passes_prefilter_gates"]


_historical_kscore, _passes_prefilter_gates = _extract_functions()


def _make_session() -> Session:
    return Session(_ENGINE)


def _insert_stock(session, stock_id=1, symbol="TEST"):
    session.add(Stock(id=stock_id, symbol=symbol, market=Market.US, exchange=Exchange.NASDAQ, name="Test Co"))
    session.commit()


_next_ranking_id = [1]


def _insert_ranking(session, stock_id, as_of, score):
    # Ranking.id is a BigInteger primary key — SQLite doesn't autoincrement those implicitly
    # (a real Postgres sequence handles it in production); assign explicitly in tests, matching
    # the same pattern already documented for Price/SignalOutcome elsewhere in this test suite.
    session.add(Ranking(id=_next_ranking_id[0], stock_id=stock_id, as_of=as_of, score=score, technical=50.0, momentum=50.0, volatility=1.0))
    _next_ranking_id[0] += 1
    session.commit()


# ── _historical_kscore() — point-in-time correctness is the whole point of this fix ───────────

def test_returns_the_most_recent_ranking_as_of_or_before_the_signal_date():
    session = _make_session()
    _insert_stock(session, stock_id=10, symbol="MRRB")
    _insert_ranking(session, 10, date(2026, 6, 1), 40.0)
    _insert_ranking(session, 10, date(2026, 6, 15), 60.0)
    result = _historical_kscore(session, 10, date(2026, 6, 20))
    assert result == 60.0
    session.close()


def test_never_leaks_a_ranking_computed_after_the_signal_date():
    """The exact bug this function exists to avoid: a naive func.max(Ranking.as_of) with no
    date bound (the LIVE engine's own shortcut, correct only because "now" for live trading
    always means the latest row) would return a FUTURE-relative-to-signal K-Score here. This
    must instead return the June 1 score (40.0), not the July 1 score (90.0) that didn't exist
    yet as of the June 10 signal date."""
    session = _make_session()
    _insert_stock(session, stock_id=11, symbol="NOLEAK")
    _insert_ranking(session, 11, date(2026, 6, 1), 40.0)
    _insert_ranking(session, 11, date(2026, 7, 1), 90.0)
    result = _historical_kscore(session, 11, date(2026, 6, 10))
    assert result == 40.0
    session.close()


def test_returns_none_when_no_ranking_exists_on_or_before_the_date():
    session = _make_session()
    _insert_stock(session, stock_id=2, symbol="NEW")
    _insert_ranking(session, 2, date(2026, 6, 15), 55.0)
    result = _historical_kscore(session, 2, date(2026, 6, 1))  # before the only ranking row
    assert result is None
    session.close()


def test_exact_as_of_match_is_included_not_just_strictly_before():
    session = _make_session()
    _insert_stock(session, stock_id=3, symbol="EXACT")
    _insert_ranking(session, 3, date(2026, 6, 10), 70.0)
    result = _historical_kscore(session, 3, date(2026, 6, 10))
    assert result == 70.0
    session.close()


# ── _passes_prefilter_gates() — pure comparison logic, no DB dependency ────────────────────────

def test_kscore_below_min_blocks():
    reason = _passes_prefilter_gates({"min_kscore": 50.0}, kscore=40.0, reasons={})
    assert reason == "kscore_below_min"


def test_kscore_at_or_above_min_passes():
    assert _passes_prefilter_gates({"min_kscore": 50.0}, kscore=50.0, reasons={}) is None
    assert _passes_prefilter_gates({"min_kscore": 50.0}, kscore=80.0, reasons={}) is None


def test_missing_kscore_blocks_when_require_kscore_is_true_the_default():
    reason = _passes_prefilter_gates({"min_kscore": 50.0}, kscore=None, reasons={})
    assert reason == "no_ranking"


def test_missing_kscore_passes_when_require_kscore_is_explicitly_false():
    reason = _passes_prefilter_gates({"min_kscore": 50.0, "require_kscore": False}, kscore=None, reasons={})
    assert reason is None


def test_ta_score_below_min_blocks():
    reason = _passes_prefilter_gates(
        {"min_kscore": 0, "require_kscore": False, "min_ta_score": 0.65},
        kscore=None, reasons={"ta_score": 0.40},
    )
    assert reason == "ta_score_below_min"


def test_ta_score_gate_disabled_at_zero_never_blocks():
    """min_ta_score=0.0 is the gate's own disabled state (matches _scan_for_entries' own
    `if _min_ta > 0:` no-op check) — must never reject regardless of the actual ta_score."""
    reason = _passes_prefilter_gates(
        {"min_kscore": 0, "require_kscore": False, "min_ta_score": 0.0},
        kscore=None, reasons={"ta_score": 0.01},
    )
    assert reason is None


def test_missing_ta_score_defaults_to_1_0_and_never_blocks():
    """Matches the live gate's own fail-open default exactly — a missing ta_score must not be
    treated as 0 (which would spuriously block everything once min_ta_score > 0)."""
    reason = _passes_prefilter_gates(
        {"min_kscore": 0, "require_kscore": False, "min_ta_score": 0.65},
        kscore=None, reasons={},
    )
    assert reason is None


def test_volume_z_below_min_blocks():
    reason = _passes_prefilter_gates(
        {"min_kscore": 0, "require_kscore": False, "min_volume_z": -1.5},
        kscore=None, reasons={"volume_z": -2.0},
    )
    assert reason == "volume_z_below_min"


def test_missing_volume_z_is_fail_open_and_never_blocks():
    """T232-DL5: a missing volume_z must NOT be treated as 0 (average) — it must skip the
    gate entirely (fail-open), matching the live gate's own explicit distinction."""
    reason = _passes_prefilter_gates(
        {"min_kscore": 0, "require_kscore": False, "min_volume_z": -1.5},
        kscore=None, reasons={},
    )
    assert reason is None


def test_first_failing_gate_short_circuits_the_rest():
    """kscore is checked before ta_score/volume_z — a candidate failing on kscore should
    report that reason, not silently continue checking (and potentially reporting) a later gate."""
    reason = _passes_prefilter_gates(
        {"min_kscore": 50.0, "min_ta_score": 0.65, "min_volume_z": -1.5},
        kscore=10.0, reasons={"ta_score": 0.99, "volume_z": 5.0},
    )
    assert reason == "kscore_below_min"


def test_candidate_clearing_all_three_gates_passes():
    reason = _passes_prefilter_gates(
        {"min_kscore": 50.0, "min_ta_score": 0.65, "min_volume_z": -1.5},
        kscore=60.0, reasons={"ta_score": 0.70, "volume_z": 0.5},
    )
    assert reason is None
