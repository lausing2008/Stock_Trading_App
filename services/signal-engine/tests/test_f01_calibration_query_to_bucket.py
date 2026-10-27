"""F01 (2026-10-26 RZY/AI review) — the calibration QUERY dropped every eligible outcome.

`_build_confidence_calibration()` selected signal and entry dates but not
`SignalOutcome.exit_date`. `evidence_eligible()` requires `signal < entry <= exit`, and a
SQLAlchemy Row without the column reads as None — so every verified row failed and no bucket
could ever publish. Zero verified production rows masked it.

The AQ05 tests check the eligibility helpers and the builder's SOURCE; neither runs the real
query, which is where the column went missing. This runs the actual function against SQLite
with real models behind it (the suite's conftest stubs `db` as a MagicMock), from stored rows
to published buckets. Witness: docs/audits/evidence/2026-10-26-rzy-ai-intraday-review/
calibration-projection-witness.json.
"""
import importlib.util
import pathlib
import sys
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from api import signals_shared  # noqa: E402

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_f01", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_f01"] = _models
_spec.loader.exec_module(_models)

N_COMPLETE = 35          # above MIN_ELIGIBLE_OUTCOMES (30)
N_DATES = 7              # above MIN_DISTINCT_DATES (5)
MARKET_KEY = "SWING|BUY|US|55-70"
POOLED_KEY = "SWING|BUY|55-70"


@pytest.fixture
def session(monkeypatch):
    for name in ("Signal", "SignalOutcome", "Stock"):
        monkeypatch.setattr(signals_shared, name, getattr(_models, name))
    engine = create_engine("sqlite://")
    _models.Base.metadata.create_all(engine, tables=[
        _models.Stock.__table__, _models.Signal.__table__, _models.SignalOutcome.__table__])
    with Session(engine) as s:
        s.add(_models.Stock(id=1, symbol="TEST", name="TEST", market=_models.Market.US,
                            exchange=_models.Exchange.NASDAQ))
        s.commit()
        yield s


def _add(s, i, *, signal_date, entry_date, exit_date, evidence="verified", correct=True,
         delivery=None):
    s.add(_models.Signal(id=i, stock_id=1, signal=_models.SignalType.BUY,
                         horizon=_models.SignalHorizon.SWING, confidence=60.0,
                         alert_delivery_state=delivery))
    s.add(_models.SignalOutcome(
        id=i, signal_id=i, stock_id=1, symbol="TEST", horizon=_models.SignalHorizon.SWING,
        signal_direction="BUY", signal_date=signal_date, confidence=60.0,
        entry_date=entry_date, exit_date=exit_date, is_correct=correct,
        evidence_status=evidence))


def _complete_cohort(s):
    """35 complete verified outcomes over 7 dates, 21 correct; every 5th was sender-accepted."""
    start = date.today() - timedelta(days=60)
    for i in range(N_COMPLETE):
        sig = start + timedelta(days=i % N_DATES)
        _add(s, i + 1, signal_date=sig, entry_date=sig + timedelta(days=1),
             exit_date=sig + timedelta(days=15), correct=(i % 5) < 3,
             delivery="sender_accepted" if i % 5 == 0 else None)
    s.commit()


def test_complete_verified_outcomes_reach_a_published_bucket(session):
    """THE F01 CASE: before the fix eligible_count was 0 and no bucket existed."""
    _complete_cohort(session)
    got = signals_shared._build_confidence_calibration(session)

    meta = got["__meta__"]
    assert meta["timing_eligible_count"] == N_COMPLETE
    assert meta["eligible_count"] == N_COMPLETE, "the query must project every field the gate reads"
    assert meta["verified_ineligible_count"] == 0

    for key, scope in ((MARKET_KEY, "market"), (POOLED_KEY, "pooled_markets")):
        bucket = got[key]
        assert bucket["status"] == "measured" and bucket["scope"] == scope
        assert bucket["count"] == N_COMPLETE and bucket["distinct_dates"] == N_DATES
        assert bucket["win_rate"] == round(21 / 35, 3)
        assert bucket["delivered"]["count"] == 7


def test_the_strict_contract_still_rejects_incomplete_or_inverted_rows(session):
    """Controls: the fix projects the column; it must not loosen the predicate."""
    _complete_cohort(session)
    sig = date.today() - timedelta(days=30)
    _add(session, 101, signal_date=sig, entry_date=sig + timedelta(days=1), exit_date=None)
    _add(session, 102, signal_date=sig, entry_date=sig + timedelta(days=5),
         exit_date=sig + timedelta(days=2))
    _add(session, 103, signal_date=sig, entry_date=sig + timedelta(days=1),
         exit_date=sig + timedelta(days=15), evidence="provisional")
    _add(session, 104, signal_date=sig, entry_date=sig, exit_date=sig + timedelta(days=15))
    session.commit()

    meta = signals_shared._build_confidence_calibration(session)["__meta__"]
    assert meta["original_count"] == N_COMPLETE + 4
    assert meta["timing_eligible_count"] == N_COMPLETE + 3, "same-day entry removed by AQ05 timing"
    assert meta["evidence_counts"]["verified"] == N_COMPLETE + 2
    assert meta["verified_ineligible_count"] == 2, "missing exit and exit-before-entry are counted"
    assert meta["eligible_count"] == N_COMPLETE


def test_a_thin_verified_cohort_keeps_its_counts_without_a_rate(session):
    """Below the sample floor: rows are eligible and counted, but no rate is published."""
    sig = date.today() - timedelta(days=20)
    for i in range(10):
        d = sig + timedelta(days=i % 5)
        _add(session, i + 1, signal_date=d, entry_date=d + timedelta(days=1),
             exit_date=d + timedelta(days=15))
    session.commit()

    got = signals_shared._build_confidence_calibration(session)
    assert got["__meta__"]["eligible_count"] == 10
    assert got[MARKET_KEY]["win_rate"] is None
    assert got[MARKET_KEY]["status"] == "insufficient_eligible_history"
    assert got[MARKET_KEY]["count"] == 10
