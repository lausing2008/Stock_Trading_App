"""Are stored evaluations and captured expectations actually immutable, against real PostgreSQL?

A fake session cannot establish any of this. ON CONFLICT DO NOTHING, a unique constraint across
three columns, and a row lock are database behaviours; testing them against a double proves the
double. So these run against a real server, the same harness `make test-integration` provides.

What they are trying to break, in order of how much it would matter:
  * a re-run under changed rules OVERWRITING what the screen previously concluded
  * a revision overwriting the figure a market actually reacted to
  * an expectation captured after the release being accepted as an expectation
"""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

PG_URL = os.environ.get("BUDGET_PG_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="BUDGET_PG_URL not set")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))
sys.path.insert(0, str(ROOT / "services" / "research-engine" / "src"))


@pytest.fixture()
def session():
    from db.models import (Base, EstimateSnapshot, MacroExpectation,
                           QualityValueEvaluation)
    engine = create_engine(PG_URL, future=True)
    tables = [QualityValueEvaluation.__table__, EstimateSnapshot.__table__,
              MacroExpectation.__table__]
    Base.metadata.drop_all(engine, tables=tables)
    Base.metadata.create_all(engine, tables=tables)
    S = sessionmaker(bind=engine, future=True)
    s = S()
    yield s
    s.close()
    Base.metadata.drop_all(engine, tables=tables)
    engine.dispose()


def _evaluation(symbol="MU", state="insufficient_evidence"):
    from intel_reports.quality_value import (
        compose, durability_gate, valuation_gate, value_trap_gate, Gate, GateStatus,
        BUSINESS_QUALITY, ENTRY_CONDITION)
    return compose(symbol, [Gate(BUSINESS_QUALITY, GateStatus.PASS), durability_gate(),
                            valuation_gate(), Gate(ENTRY_CONDITION, GateStatus.PASS),
                            value_trap_gate({})])


# ---- evaluations are immutable -----------------------------------------------------------

def test_an_evaluation_is_stored_with_its_policy_and_cutoff(session):
    from intel_reports.quality_value_store import record_evaluation
    from intel_reports.quality_value import POLICY_VERSION, policy_fingerprint
    cutoff = datetime(2026, 10, 6, 12, 0)
    row, created = record_evaluation(session, _evaluation(), cutoff=cutoff,
                                     evidence_refs={"financial_statements": [1, 2]})
    assert created is True
    assert row.policy_version == POLICY_VERSION
    assert row.policy_fingerprint == policy_fingerprint()
    assert row.cutoff == cutoff
    assert row.state == "insufficient_evidence"
    assert row.evidence_refs == {"financial_statements": [1, 2]}
    assert len(row.gates) == 5


def test_rerunning_identical_rules_over_the_same_cutoff_writes_nothing(session):
    from intel_reports.quality_value_store import record_evaluation
    from db import QualityValueEvaluation
    cutoff = datetime(2026, 10, 6, 12, 0)
    record_evaluation(session, _evaluation(), cutoff=cutoff)
    row, created = record_evaluation(session, _evaluation(), cutoff=cutoff)
    assert created is False, "a re-run must be idempotent, not a duplicate"
    assert session.execute(
        select(QualityValueEvaluation)).scalars().all().__len__() == 1


def test_a_changed_policy_lands_beside_the_old_row_and_never_on_top_of_it(session, monkeypatch):
    """THE ONE THAT MATTERS: a threshold change must not rewrite what the screen concluded."""
    from intel_reports import quality_value_store as store
    from db import QualityValueEvaluation
    cutoff = datetime(2026, 10, 6, 12, 0)
    record_evaluation = store.record_evaluation
    first, _ = record_evaluation(session, _evaluation(), cutoff=cutoff)
    original_state, original_fp = first.state, first.policy_fingerprint

    monkeypatch.setattr(store, "policy_fingerprint", lambda: "0000deadbeef0000")
    second, created = record_evaluation(session, _evaluation(symbol="MU"), cutoff=cutoff)
    assert created is True

    rows = session.execute(select(QualityValueEvaluation)
                           .order_by(QualityValueEvaluation.id)).scalars().all()
    assert len(rows) == 2, "the earlier verdict must survive the rule change"
    assert rows[0].policy_fingerprint == original_fp
    assert rows[0].state == original_state
    assert rows[1].policy_fingerprint == "0000deadbeef0000"


def test_a_later_cutoff_is_a_new_row_not_an_update(session):
    from intel_reports.quality_value_store import record_evaluation
    from db import QualityValueEvaluation
    record_evaluation(session, _evaluation(), cutoff=datetime(2026, 10, 6))
    record_evaluation(session, _evaluation(), cutoff=datetime(2026, 10, 7))
    assert len(session.execute(select(QualityValueEvaluation)).scalars().all()) == 2


def test_the_unique_constraint_is_really_enforced_by_the_database(session):
    """Not by application code: a concurrent run must lose, not duplicate."""
    from db import QualityValueEvaluation
    from intel_reports.quality_value_store import record_evaluation
    record_evaluation(session, _evaluation(), cutoff=datetime(2026, 10, 6))
    row = session.execute(select(QualityValueEvaluation)).scalars().first()
    with pytest.raises(Exception) as exc:
        session.execute(text(
            "INSERT INTO quality_value_evaluations "
            "(symbol, cutoff, policy_version, policy_fingerprint, state, gates) "
            "VALUES (:s, :c, :v, :f, 'x', '[]'::json)"),
            {"s": row.symbol, "c": row.cutoff, "v": row.policy_version,
             "f": row.policy_fingerprint})
    assert "uq_qv_evaluation_symbol_cutoff_policy" in str(exc.value)
    session.rollback()


def test_latest_is_scoped_to_the_current_policy(session, monkeypatch):
    """DIFFERENT SYMBOLS ON PURPOSE. An earlier version of this test stored both rows under
    'MU'; `latest_evaluations` dedupes by symbol, so it returned 1 whether or not the
    fingerprint filter was there, and the sabotage that removed the filter passed. Only a
    symbol that exists ONLY under the old policy can detect the leak."""
    from intel_reports import quality_value_store as store
    store.record_evaluation(session, _evaluation(symbol="OLDONLY"),
                            cutoff=datetime(2026, 10, 6))
    monkeypatch.setattr(store, "policy_fingerprint", lambda: "0000deadbeef0000")
    store.record_evaluation(session, _evaluation(symbol="NEWONLY"),
                            cutoff=datetime(2026, 10, 6))
    symbols = {r.symbol for r in store.latest_evaluations(session)}
    assert symbols == {"NEWONLY"}, \
        f"verdicts from superseded rules must not be listed as current, got {symbols}"


def test_history_shows_both_policies_so_a_change_of_answer_is_attributable(session, monkeypatch):
    from intel_reports import quality_value_store as store
    store.record_evaluation(session, _evaluation(), cutoff=datetime(2026, 10, 6))
    monkeypatch.setattr(store, "policy_fingerprint", lambda: "0000deadbeef0000")
    store.record_evaluation(session, _evaluation(), cutoff=datetime(2026, 10, 6))
    hist = store.evaluation_history(session, "MU")
    assert len({h.policy_fingerprint for h in hist}) == 2


# ---- estimates: a revision is a new observation -------------------------------------------

def test_a_revised_estimate_is_a_new_row_and_the_original_survives(session):
    from intel_reports.quality_value_store import capture_estimate
    from db import EstimateSnapshot
    base = dict(symbol="MU", target_period="FY2027", metric="eps", provider="yfinance",
                units="USD", accounting_basis="adjusted")
    capture_estimate(session, value=12.50, captured_at=datetime(2026, 10, 1), **base)
    capture_estimate(session, value=13.75, captured_at=datetime(2026, 10, 6), **base)
    rows = session.execute(select(EstimateSnapshot)
                           .order_by(EstimateSnapshot.captured_at)).scalars().all()
    assert [r.value for r in rows] == [12.50, 13.75]
    assert rows[0].accounting_basis == "adjusted"


def test_the_same_observation_twice_is_idempotent(session):
    from intel_reports.quality_value_store import capture_estimate
    from db import EstimateSnapshot
    args = dict(symbol="MU", target_period="FY2027", metric="eps", provider="yfinance",
                value=12.5, captured_at=datetime(2026, 10, 1))
    capture_estimate(session, **args)
    _, created = capture_estimate(session, **args)
    assert created is False
    assert len(session.execute(select(EstimateSnapshot)).scalars().all()) == 1


def test_an_estimate_records_a_null_basis_rather_than_omitting_the_question(session):
    """NULL means 'the provider did not say' — recoverable, unlike a missing column."""
    from intel_reports.quality_value_store import capture_estimate
    from db import EstimateSnapshot
    capture_estimate(session, symbol="MU", target_period="FY2027", metric="eps",
                     provider="yfinance", value=12.5, captured_at=datetime(2026, 10, 1))
    row = session.execute(select(EstimateSnapshot)).scalars().first()
    assert row.accounting_basis is None and row.units is None


def test_the_providers_own_as_of_is_distinct_from_our_capture_time(session):
    from intel_reports.quality_value_store import capture_estimate
    from db import EstimateSnapshot
    capture_estimate(session, symbol="MU", target_period="FY2027", metric="eps",
                     provider="yfinance", value=12.5,
                     source_as_of=datetime(2026, 9, 29), captured_at=datetime(2026, 10, 6))
    row = session.execute(select(EstimateSnapshot)).scalars().first()
    assert row.source_as_of < row.captured_at


# ---- macro: the expectation must precede the print -----------------------------------------

def test_an_expectation_captured_after_publication_is_refused(session):
    from intel_reports.quality_value_store import capture_macro_expectation, BackdatedCapture
    with pytest.raises(BackdatedCapture, match="contaminated by the result"):
        capture_macro_expectation(
            session, release_key="US_CPI_YOY", reference_period="2026-09",
            expectation=2.9, expectation_source="consensus",
            expectation_captured_at=datetime(2026, 10, 6, 13, 0),
            published_at=datetime(2026, 10, 6, 12, 30))


def test_an_expectation_captured_before_publication_is_accepted(session):
    from intel_reports.quality_value_store import capture_macro_expectation
    from db import MacroExpectation
    _, created = capture_macro_expectation(
        session, release_key="US_CPI_YOY", reference_period="2026-09", expectation=2.9,
        expectation_source="consensus",
        expectation_captured_at=datetime(2026, 10, 5, 9, 0),
        published_at=datetime(2026, 10, 6, 12, 30))
    assert created is True
    row = session.execute(select(MacroExpectation)).scalars().first()
    assert row.expectation == 2.9
    assert row.expectation_captured_at < row.published_at


def test_the_first_print_is_never_overwritten_by_a_revision(session):
    """A market reacted to the first number. The revised one is a different fact."""
    from intel_reports.quality_value_store import (capture_macro_expectation,
                                                   record_first_actual)
    from db import MacroExpectation
    capture_macro_expectation(session, release_key="US_NFP", reference_period="2026-09",
                              expectation=150000, expectation_source="consensus",
                              expectation_captured_at=datetime(2026, 10, 1))
    assert record_first_actual(session, release_key="US_NFP", reference_period="2026-09",
                               actual=119000) == "first"
    assert record_first_actual(session, release_key="US_NFP", reference_period="2026-09",
                               actual=119000) == "unchanged"
    assert record_first_actual(session, release_key="US_NFP", reference_period="2026-09",
                               actual=98000) == "revision"
    row = session.execute(select(MacroExpectation)).scalars().first()
    assert row.first_actual == 119000, "the figure the market reacted to must survive"
    assert len(row.revisions) == 1 and row.revisions[0]["value"] == 98000
    assert row.revisions[0]["supersedes"] == 119000


def test_an_actual_with_no_captured_expectation_is_refused_not_invented(session):
    """An expectation that was never captured cannot be reconstructed from the result."""
    from intel_reports.quality_value_store import record_first_actual, BackdatedCapture
    with pytest.raises(BackdatedCapture, match="cannot be reconstructed from the result"):
        record_first_actual(session, release_key="US_CPI_YOY", reference_period="2026-09",
                            actual=3.0)


# ---- AUD-CAPTURE-NOTIDEMPOTENT -------------------------------------------------------------
# The unit test for idempotency passed an identical `captured_at` BY HAND, so it proved only
# that the constraint works when the caller has already solved the problem. In production the
# job computes its own `now`, two runs were microseconds apart, and the second inserted 324
# duplicate rows. This drives the REAL capture path end to end instead.

def test_two_capture_runs_in_one_day_do_not_duplicate(session):
    from intel_reports.prospective_capture import analyst_forwards_to_capture
    from intel_reports.quality_value_store import capture_estimate
    from db import EstimateSnapshot

    class F:
        target_price, forward_pe, fetched_at = 1535.57, 5.15, datetime(2026, 10, 6)

    for moment in (datetime(2026, 10, 7, 0, 27, 41, 228769),
                   datetime(2026, 10, 7, 11, 3, 9, 462155)):
        for row in analyst_forwards_to_capture([("MU", F())], now=moment):
            capture_estimate(session, **row)

    rows = session.execute(select(EstimateSnapshot)).scalars().all()
    assert len(rows) == 2, \
        f"one observation per metric per day; got {len(rows)} from two runs"
    assert {r.metric for r in rows} == {"analyst_target_price", "forward_pe"}


def test_the_next_day_is_a_new_observation_not_a_duplicate(session):
    from intel_reports.prospective_capture import analyst_forwards_to_capture
    from intel_reports.quality_value_store import capture_estimate
    from db import EstimateSnapshot

    class F:
        target_price, forward_pe, fetched_at = 1535.57, 5.15, datetime(2026, 10, 6)

    for day in (datetime(2026, 10, 7, 9, 0), datetime(2026, 10, 8, 9, 0)):
        for row in analyst_forwards_to_capture([("MU", F())], now=day):
            capture_estimate(session, **row)

    targets = session.execute(
        select(EstimateSnapshot).where(EstimateSnapshot.metric == "analyst_target_price")
        .order_by(EstimateSnapshot.captured_at)).scalars().all()
    assert len(targets) == 2, "consecutive days must each be recorded"
    assert targets[0].captured_at.date() != targets[1].captured_at.date()


# ---- frozen RULES are not enough; the INPUTS must be frozen too ---------------------------
# The review's point: "A rules fingerprint and cutoff alone cannot reproduce an evaluation if
# its underlying records later change." financial_statements is refreshed in place and a market
# capitalisation is refetched, so a reference locates what a row has BECOME, not what was read.

def test_a_stored_evaluation_carries_the_values_it_was_computed_from(session):
    from intel_reports.quality_value_store import record_evaluation, freeze_inputs
    row, _ = record_evaluation(
        session, _evaluation(), cutoff=datetime(2026, 10, 7),
        evidence_refs=freeze_inputs(
            {"fundamentals": {"revenue": 37_378_000_000.0, "annual_periods": 4,
                              "retrieval_age_days": 29},
             "prices": {"sma20": 1012.4, "recent_closes": [1045.56, 1063.96]}},
            refs={"financial_statements": [814, 815, 816, 817]}))
    frozen = row.evidence_refs["frozen_inputs"]
    assert frozen["fundamentals"]["revenue"] == 37_378_000_000.0
    assert frozen["prices"]["sma20"] == 1012.4
    assert row.evidence_refs["refs"]["financial_statements"] == [814, 815, 816, 817]
    assert len(row.evidence_refs["input_digest"]) == 32


def test_the_frozen_inputs_verify_against_their_own_digest(session):
    from intel_reports.quality_value_store import record_evaluation, freeze_inputs, verify_inputs
    row, _ = record_evaluation(session, _evaluation(), cutoff=datetime(2026, 10, 7),
                               evidence_refs=freeze_inputs({"fundamentals": {"revenue": 1.0}}))
    v = verify_inputs(row.evidence_refs)
    assert v["verifiable"] is True and v["intact"] is True


def test_an_altered_frozen_input_fails_verification(session):
    """Premises moving on is normal. The stored copy being edited is not."""
    from intel_reports.quality_value_store import freeze_inputs, verify_inputs
    stored = freeze_inputs({"fundamentals": {"revenue": 1.0}})
    stored["frozen_inputs"]["fundamentals"]["revenue"] = 999.0
    v = verify_inputs(stored)
    assert v["verifiable"] is True and v["intact"] is False
    assert v["expected"] != v["recomputed"]


def test_a_references_only_row_declares_itself_unreproducible(session):
    from intel_reports.quality_value_store import verify_inputs
    v = verify_inputs({"financial_statements": [1, 2]})
    assert v["verifiable"] is False
    assert "cannot be reproduced" in v["reason"]


def test_the_digest_is_order_independent_so_a_rekey_is_not_a_change(session):
    from intel_reports.quality_value_store import freeze_inputs
    a = freeze_inputs({"b": 2, "a": 1})
    b = freeze_inputs({"a": 1, "b": 2})
    assert a["input_digest"] == b["input_digest"]


def test_a_changed_value_changes_the_digest(session):
    from intel_reports.quality_value_store import freeze_inputs
    assert (freeze_inputs({"revenue": 1.0})["input_digest"]
            != freeze_inputs({"revenue": 1.01})["input_digest"])
