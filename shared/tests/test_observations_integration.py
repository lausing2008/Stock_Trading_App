"""Observations and outcomes against real PostgreSQL — immutability, idempotency, resolution.

A fake session cannot establish any of this: ON CONFLICT DO NOTHING, a five-column unique
constraint and FK cascade are database behaviours, and testing them against a double proves the
double.
"""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

PG_URL = os.environ.get("BUDGET_PG_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="BUDGET_PG_URL not set")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))
sys.path.insert(0, str(ROOT / "services" / "research-engine" / "src"))

OBSERVED = datetime(2026, 9, 1, 20, 5)


@pytest.fixture()
def session():
    from db.models import (Base, EvidenceBucket, IntelligenceObservation, ObservationOutcome)
    engine = create_engine(PG_URL, future=True)
    tables = [ObservationOutcome.__table__, IntelligenceObservation.__table__,
              EvidenceBucket.__table__]
    Base.metadata.drop_all(engine, tables=tables)
    Base.metadata.create_all(engine, tables=tables)
    s = sessionmaker(bind=engine, future=True)()
    yield s
    s.close()
    Base.metadata.drop_all(engine, tables=tables)
    engine.dispose()


def _obs(session, **over):
    from intel_reports.observations import record_observation, PROSPECTIVE
    kw = dict(subject_key="stock:MU", symbol="MU", origin=PROSPECTIVE, observed_at=OBSERVED,
              horizon_sessions=20, horizon_label="1-4w", direction="NEUTRAL",
              support_quality="MEDIUM", reference_price=100.0,
              reference_price_as_of=OBSERVED, reference_price_source="daily close",
              reference_price_basis="formed_at_close", benchmark_symbol="SPY",
              confirmation_rule="close above 110", invalidation_rule="close below 90",
              bucket_ids=[1, 2, 3], frozen_inputs={"close": 100.0}, summary={"direction": "NEUTRAL"})
    kw.update(over)
    return record_observation(session, **kw)


# ---- capture is immutable and idempotent ---------------------------------------------------

def test_an_observation_stores_its_frozen_policy_and_rules(session):
    row, created = _obs(session)
    assert created is True
    assert row.resolution_policy["horizon_unit"] == "trading_sessions"
    assert "never counted as flat" in row.resolution_policy["insufficient_sessions"]
    assert row.execution_assumptions["modelled"] == []
    assert "bid/ask spread" in row.execution_assumptions["unmodelled"]
    assert row.frozen_inputs_digest and len(row.frozen_inputs_digest) == 32
    assert row.predictive_confidence is None, "never set at capture"


def test_rerunning_the_same_observation_creates_no_duplicate(session):
    from db import IntelligenceObservation
    first, _ = _obs(session)
    again, created = _obs(session)
    assert created is False and again.id == first.id
    assert len(session.execute(select(IntelligenceObservation)).scalars().all()) == 1


def test_a_later_source_change_leaves_the_frozen_record_intact(session):
    """The whole point: the inputs move and the record does not."""
    row, _ = _obs(session, frozen_inputs={"close": 100.0, "revenue": 37378e6})
    original_digest, original_inputs = row.frozen_inputs_digest, dict(row.frozen_inputs)
    # The upstream figure is restated afterwards. Re-running must not rewrite the record.
    again, created = _obs(session, frozen_inputs={"close": 999.0, "revenue": 1.0})
    assert created is False
    session.refresh(again)
    assert again.frozen_inputs == original_inputs
    assert again.frozen_inputs_digest == original_digest


def test_replay_and_prospective_are_separate_rows_not_one(session):
    from intel_reports.observations import REPLAY
    from db import IntelligenceObservation
    _obs(session)
    _obs(session, origin=REPLAY)
    rows = session.execute(select(IntelligenceObservation)).scalars().all()
    assert len(rows) == 2 and {r.origin for r in rows} == {"prospective", "replay"}


# ---- resolution, under the policy frozen at capture ----------------------------------------

def test_a_pending_outcome_is_unresolved_not_flat(session):
    from intel_reports.observations import resolve
    row, _ = _obs(session)
    out, _ = resolve(session, row, closes=[101.0, 102.0])
    assert out.resolution_state == "UNRESOLVED_INSUFFICIENT_SESSIONS"
    assert out.descriptive_return is None, "a pending outcome is never a zero return"
    assert "excluded from every aggregate rather than counted as flat" in out.resolution_basis


def test_a_resolved_outcome_reports_both_returns_separately(session):
    from intel_reports.observations import resolve
    row, _ = _obs(session, horizon_sessions=3)
    out, _ = resolve(session, row, closes=[102.0, 104.0, 110.0],
                     benchmark_closes=[50.0, 51.0, 52.0])
    assert out.resolution_state == "RESOLVED"
    assert round(out.descriptive_return, 4) == 0.10          # 100 -> 110 from the reference
    assert round(out.simulated_executable_return, 4) == 0.0784  # 102 -> 110, next tradeable
    assert out.descriptive_return != out.simulated_executable_return
    assert round(out.benchmark_return, 4) == 0.04
    assert round(out.excess_return, 4) == 0.06


def test_a_missing_benchmark_does_not_void_the_absolute_return(session):
    from intel_reports.observations import resolve
    row, _ = _obs(session, horizon_sessions=2)
    out, _ = resolve(session, row, closes=[101.0, 105.0], benchmark_closes=None)
    assert out.resolution_state == "RESOLVED"
    assert out.descriptive_return is not None and out.excess_return is None


def test_an_acquisition_resolves_rather_than_voiding(session):
    """Voiding an acquisition premium and a bankruptcy together removes the tails in opposite
    directions."""
    from intel_reports.observations import resolve
    row, _ = _obs(session)
    out, _ = resolve(session, row, closes=[], delisting_cause="acquisition")
    assert out.resolution_state == "RESOLVED_ACQUISITION"
    assert out.delisting_cause == "acquisition"


def test_a_bankruptcy_resolves_rather_than_voiding(session):
    from intel_reports.observations import resolve
    row, _ = _obs(session)
    out, _ = resolve(session, row, closes=[], delisting_cause="bankruptcy")
    assert out.resolution_state == "RESOLVED_BANKRUPTCY"


def test_only_an_absent_terminal_price_remains_unresolved_on_a_delisting(session):
    from intel_reports.observations import resolve
    row, _ = _obs(session)
    out, _ = resolve(session, row, closes=[], delisting_cause="no_terminal_price")
    assert out.resolution_state == "UNRESOLVED_DELISTED_NO_TERMINAL_PRICE"
    assert "must disclose the exclusion" in out.resolution_basis


def test_a_split_does_not_void_but_missing_adjustment_data_stays_unresolved(session):
    from intel_reports.observations import resolve
    row, _ = _obs(session, horizon_sessions=2)
    ok, _ = resolve(session, row, closes=[101.0, 105.0], adjustment_consistent=True)
    assert ok.resolution_state == "RESOLVED", "a split is an adjustment basis, not a void"
    row2, _ = _obs(session, horizon_sessions=2, observed_at=OBSERVED + timedelta(days=1))
    bad, _ = resolve(session, row2, closes=[101.0, 105.0], adjustment_consistent=False)
    assert bad.resolution_state == "UNRESOLVED_ADJUSTMENT_MISSING"


def test_a_missing_resolution_close_is_never_substituted(session):
    from intel_reports.observations import resolve
    row, _ = _obs(session, horizon_sessions=3)
    out, _ = resolve(session, row, closes=[101.0, 102.0, None])
    assert out.resolution_state == "UNRESOLVED_PRICE_MISSING"
    assert "never substituted" in out.resolution_basis


def test_resolving_twice_does_not_duplicate(session):
    from intel_reports.observations import resolve
    from db import ObservationOutcome
    row, _ = _obs(session, horizon_sessions=2)
    resolve(session, row, closes=[101.0, 105.0])
    _, created = resolve(session, row, closes=[101.0, 105.0])
    assert created is False
    assert len(session.execute(select(ObservationOutcome)).scalars().all()) == 1


def test_the_outcome_records_which_return_basis_it_used(session):
    from intel_reports.observations import resolve
    row, _ = _obs(session, horizon_sessions=2)
    out, _ = resolve(session, row, closes=[101.0, 105.0])
    assert out.return_basis == "price_return"
