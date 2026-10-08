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
    from db.models import CorporateAction, CorporateActionCoverage
    tables = [ObservationOutcome.__table__, IntelligenceObservation.__table__,
              EvidenceBucket.__table__, CorporateAction.__table__,
              CorporateActionCoverage.__table__]
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
# Rewritten after review found five defects in the first resolver. Each test below names the
# one it pins.

D = ["2026-09-02", "2026-09-03", "2026-09-04"]


#: A VERIFIED adjustment basis, as a source that guarantees completeness would give. Tests that
#: care about pooling say which kind they mean rather than inheriting one.
_VERIFIED_ADJ = {"consistent": True, "evidence": {
    "stock": {"verified_by": "sourced_action_history",
              "completeness_basis": "source_guarantee"}}}
#: The same window, from a source that returned actions but guarantees nothing.
_PROVISIONAL_ADJ = {"consistent": True, "evidence": {
    "stock": {"verified_by": "sourced_action_history",
              "completeness_basis": "response_only"}}}


def _resolve(session, row, closes, **kw):
    from intel_reports.observations import resolve
    kw.setdefault("adjustment_consistent", True)
    kw.setdefault("adjustment", _VERIFIED_ADJ)
    return resolve(session, row, session_closes=closes,
                   expected_sessions=kw.pop("expected", D[:len(closes)]), **kw)


def test_a_pending_outcome_is_unresolved_not_flat(session):
    row, _ = _obs(session)
    out, _ = _resolve(session, row, {D[0]: 101.0, D[1]: 102.0}, expected=D[:2])
    assert out.resolution_state == "UNRESOLVED_INSUFFICIENT_SESSIONS"
    assert out.descriptive_return is None
    assert "rather than counted as flat" in out.resolution_basis


def test_a_pending_outcome_can_later_resolve(session):
    """DEFECT 3: ON CONFLICT DO NOTHING meant the first pending insert became permanent and a
    horizon could never resolve once its sessions elapsed."""
    row, _ = _obs(session, horizon_sessions=3)
    first, created = _resolve(session, row, {D[0]: 101.0}, expected=D[:1])
    assert created is True and first.resolution_state == "UNRESOLVED_INSUFFICIENT_SESSIONS"
    later, created2 = _resolve(session, row, {D[0]: 101.0, D[1]: 103.0, D[2]: 110.0})
    assert created2 is False
    assert later.id == first.id, "the same row advances rather than a second being written"
    assert later.resolution_state == "RESOLVED"
    assert later.superseded_state == "UNRESOLVED_INSUFFICIENT_SESSIONS"
    assert len(later.attempts) == 2, "every attempt is retained"


def test_a_resolved_outcome_is_never_rewritten(session):
    row, _ = _obs(session, horizon_sessions=3)
    _resolve(session, row, {D[0]: 101.0, D[1]: 103.0, D[2]: 110.0})
    again, _ = _resolve(session, row, {D[0]: 1.0, D[1]: 1.0, D[2]: 1.0})
    assert again.resolution_state == "RESOLVED"
    assert round(again.descriptive_return, 4) == 0.10, "the original figures stand"
    assert any(a.get("ignored") for a in again.attempts)


def test_the_excess_return_uses_one_window_on_both_sides(session):
    """DEFECT 1: the stock was measured from its reference close and the benchmark from the
    first subsequent close, then subtracted — two different windows, so not an excess."""
    row, _ = _obs(session, horizon_sessions=3)
    out, _ = _resolve(session, row, {D[0]: 102.0, D[1]: 104.0, D[2]: 110.0},
                      benchmark_reference=50.0,
                      benchmark_closes={D[0]: 51.0, D[1]: 51.5, D[2]: 52.0})
    assert round(out.descriptive_return, 4) == 0.10          # 100 -> 110, reference window
    assert round(out.benchmark_return, 4) == 0.04            # 50 -> 52, SAME window
    assert round(out.excess_return, 4) == 0.06
    # The entry window is reported too, so a like-for-like exists for the simulated entry.
    assert round(out.simulated_executable_return, 4) == 0.0784   # 102 -> 110
    assert round(out.benchmark_entry_return, 4) == 0.0196        # 51 -> 52


def test_a_missing_session_close_does_not_shift_the_endpoint(session):
    """DEFECT 2: taking the next n STORED rows let a gap pull the endpoint onto a later
    session, measuring a different horizon per symbol."""
    row, _ = _obs(session, horizon_sessions=3)
    out, _ = _resolve(session, row, {D[0]: 101.0, D[1]: None, D[2]: 110.0})
    assert out.resolution_state == "UNRESOLVED_PRICE_MISSING"
    assert "NEVER moved forward" in out.resolution_basis
    assert D[1] in out.resolution_basis


def test_an_unverified_adjustment_is_not_assumed_consistent(session):
    """DEFECT 4: adjustment_consistent defaulted to True — unchecked read as checked-and-fine."""
    from intel_reports.observations import resolve
    row, _ = _obs(session, horizon_sessions=2)
    out, _ = resolve(session, row, session_closes={D[0]: 101.0, D[1]: 105.0},
                     expected_sessions=D[:2], adjustment_consistent=None)
    assert out.resolution_state == "UNRESOLVED_ADJUSTMENT_UNVERIFIED"
    assert "defaulting it to consistent would assert something nobody verified" \
        in out.resolution_basis


def test_an_acquisition_without_documented_proceeds_does_not_resolve(session):
    """DEFECT 4: the cause alone was marking outcomes RESOLVED with no computed return."""
    row, _ = _obs(session)
    out, _ = _resolve(session, row, {}, expected=[], delisting={"cause": "acquisition"})
    assert out.resolution_state == "UNRESOLVED_DELISTED_NO_TERMINAL_PRICE"
    assert out.descriptive_return is None
    assert "A cause alone does not produce a return" in out.resolution_basis


def test_an_acquisition_with_documented_proceeds_resolves_to_a_real_return(session):
    row, _ = _obs(session)
    out, _ = _resolve(session, row, {}, expected=[],
                      delisting={"cause": "acquisition", "proceeds_per_share": 140.0,
                                 "evidence": "merger agreement 2026-07-01"})
    assert out.resolution_state == "RESOLVED_ACQUISITION"
    assert round(out.descriptive_return, 4) == 0.40      # 100 -> 140
    assert "merger agreement" in out.resolution_basis


def test_a_bankruptcy_with_a_documented_zero_resolves_to_minus_one(session):
    row, _ = _obs(session)
    out, _ = _resolve(session, row, {}, expected=[],
                      delisting={"cause": "bankruptcy", "proceeds_per_share": 0.0,
                                 "evidence": "equity cancelled"})
    assert out.resolution_state == "RESOLVED_BANKRUPTCY"
    assert round(out.descriptive_return, 4) == -1.0, "a -100% is a result, not a missing value"


def test_an_exchange_transfer_is_not_scored_here(session):
    row, _ = _obs(session)
    out, _ = _resolve(session, row, {}, expected=[],
                      delisting={"cause": "exchange_transfer"})
    assert out.resolution_state == "UNRESOLVED_IDENTIFIER_FOLLOW_REQUIRED"


def test_an_unsupported_frozen_policy_is_not_scored_under_todays_rules(session):
    """DEFECT 4: the resolver followed its own code rather than the policy frozen with the
    observation, which discards the point of freezing it."""
    from db import IntelligenceObservation
    row, _ = _obs(session, horizon_sessions=2)
    row.resolution_policy = {"version": "res-99-from-the-future"}
    session.commit()
    out, _ = _resolve(session, row, {D[0]: 101.0, D[1]: 105.0}, expected=D[:2])
    assert out.resolution_state == "UNRESOLVED_POLICY_UNSUPPORTED"
    assert "would discard the freezing entirely" in out.resolution_basis


def test_a_missing_benchmark_does_not_void_the_absolute_return(session):
    row, _ = _obs(session, horizon_sessions=2)
    out, _ = _resolve(session, row, {D[0]: 101.0, D[1]: 105.0}, expected=D[:2])
    assert out.resolution_state == "RESOLVED"
    assert out.descriptive_return is not None and out.excess_return is None


def test_the_outcome_records_which_return_basis_it_used(session):
    row, _ = _obs(session, horizon_sessions=2)
    out, _ = _resolve(session, row, {D[0]: 101.0, D[1]: 105.0}, expected=D[:2])
    assert out.return_basis == "price_return"


# ---- a CORRECTED resolver supersedes, it is not refused -------------------------------------
#
# AUD-OBS-RESOLVERVERSION. The six-defect resolver fix was deployed and the replay reran — and
# changed nothing. Every stored figure stayed as the DEFECTIVE resolver had written it, because
# "a resolved outcome is never rewritten" applied to a resolver that no longer existed. The two
# cases are different questions: re-running the SAME rules must never rewrite, and a CORRECTED
# resolver must be able to produce a reading without destroying the original.

def _force_fingerprint(monkeypatch, value):
    from intel_reports import observations as O
    monkeypatch.setattr(O, "resolver_fingerprint", lambda: value)


def test_the_fingerprint_is_derived_from_the_resolver_not_maintained_by_hand():
    from intel_reports.observations import resolver_fingerprint
    assert len(resolver_fingerprint()) == 32
    assert resolver_fingerprint() == resolver_fingerprint(), "must be stable within a build"


def _reworded_resolve_fingerprint(monkeypatch, tmp_path, replacements):
    """Swap `resolve` for a variant and ask the REAL fingerprint function about it.

    Deliberately not a re-implementation of the fingerprint rules: a test that recomputes them
    cannot catch a line deleted from the original. The variant is written to a REAL FILE and
    imported, because `resolver_fingerprint` reads its subject with `inspect.getsource`, which
    has nothing to read for a function compiled from a string — the same reason resolution
    would fail outright under a source-less deployment rather than quietly fingerprinting
    everything the same, which would pool corrected and uncorrected rows together.
    """
    import importlib.util, inspect, textwrap
    from intel_reports import observations as O
    variant = textwrap.dedent(inspect.getsource(O.resolve))
    for before, after in replacements:
        assert before in variant, f"the edit {before!r} did not apply"
        variant = variant.replace(before, after)
    path = tmp_path / "variant_resolver.py"
    path.write_text("from datetime import datetime, timezone\n"
                    "from sqlalchemy import select\n\n" + variant)
    spec = importlib.util.spec_from_file_location("variant_resolver", path)
    mod = importlib.util.module_from_spec(spec)
    mod.__dict__.update({k: v for k, v in O.__dict__.items()
                         if k not in ("__name__", "__file__", "__spec__", "__loader__")})
    spec.loader.exec_module(mod)
    monkeypatch.setattr(O, "resolve", mod.resolve)
    return O.resolver_fingerprint()


def test_prose_alone_does_not_mint_a_new_resolver(monkeypatch, tmp_path):
    """Otherwise every doc edit re-scores the corpus and the supersession log becomes noise.

    Covers BOTH kinds of prose. `ast.unparse` drops `#` comments by itself; a docstring is a
    real expression node and survives it, which the first version of this test caught.
    """
    from intel_reports.observations import resolver_fingerprint
    baseline = resolver_fingerprint()
    assert _reworded_resolve_fingerprint(monkeypatch, tmp_path, [
        ('"""Score one observation', '"""REWORDED. Score one observation'),
        ("# A GUARDED TRANSITION.", "# REWORDED COMMENT."),
    ]) == baseline


def test_changing_what_the_resolver_does_always_moves_the_fingerprint(monkeypatch,
                                                                       tmp_path):
    """The other half: a fingerprint insensitive to behaviour would supersede nothing."""
    from intel_reports.observations import resolver_fingerprint
    baseline = resolver_fingerprint()
    assert _reworded_resolve_fingerprint(monkeypatch, tmp_path, [
        ('state = "RESOLVED"', 'state = "RESOLVED_DIFFERENTLY"'),
    ]) != baseline


def test_the_same_resolver_rerunning_still_never_rewrites(session, monkeypatch):
    _force_fingerprint(monkeypatch, "resolver-A")
    row, _ = _obs(session, horizon_sessions=3)
    first, _ = _resolve(session, row, {D[0]: 101.0, D[1]: 103.0, D[2]: 110.0})
    again, created = _resolve(session, row, {D[0]: 1.0, D[1]: 1.0, D[2]: 1.0})
    assert created is False and again.id == first.id
    assert round(again.descriptive_return, 4) == 0.10
    assert again.superseded_by_id is None


def test_a_corrected_resolver_writes_a_new_row_and_leaves_the_original_intact(session,
                                                                             monkeypatch):
    from db.models import ObservationOutcome
    _force_fingerprint(monkeypatch, "resolver-A")
    row, _ = _obs(session, horizon_sessions=3)
    old, _ = _resolve(session, row, {D[0]: 101.0, D[1]: 103.0, D[2]: 110.0})
    old_id, old_return = old.id, old.descriptive_return

    _force_fingerprint(monkeypatch, "resolver-B")
    new, created = _resolve(session, row, {D[0]: 101.0, D[1]: 103.0, D[2]: 120.0})
    assert created is True, "a corrected resolver must not be silently refused"
    assert new.id != old_id
    assert round(new.descriptive_return, 4) == 0.20

    session.expire_all()
    rows = session.execute(select(ObservationOutcome).where(
        ObservationOutcome.observation_id == row.id)).scalars().all()
    assert len(rows) == 2, "the original is RETAINED, not replaced"
    original = next(r for r in rows if r.id == old_id)
    assert original.descriptive_return == old_return, "its figures are never altered"
    assert original.superseded_by_id == new.id, "supersession is explicit, not inferred"
    assert original.resolver_fingerprint == "resolver-A"
    assert new.superseded_by_id is None
    note = original.attempts[-1]
    assert note["superseded_by"] == new.id
    assert "must not be pooled" in note["note"]


def test_a_row_written_before_fingerprinting_is_superseded_not_claimed(session, monkeypatch):
    """The sentinel is not a guess: the code that wrote those rows is gone."""
    from db.models import ObservationOutcome
    from intel_reports.observations import UNFINGERPRINTED
    _force_fingerprint(monkeypatch, "resolver-A")
    row, _ = _obs(session, horizon_sessions=3)
    old, _ = _resolve(session, row, {D[0]: 101.0, D[1]: 103.0, D[2]: 110.0})
    # Simulate the production rows, which predate the column and carry the backfilled sentinel.
    old.resolver_fingerprint = UNFINGERPRINTED
    session.commit()

    _force_fingerprint(monkeypatch, "resolver-B")
    new, created = _resolve(session, row, {D[0]: 101.0, D[1]: 103.0, D[2]: 120.0})
    assert created is True
    session.expire_all()
    original = session.get(ObservationOutcome, old.id)
    assert original.superseded_by_id == new.id
    assert UNFINGERPRINTED in original.attempts[-1]["note"]


def test_every_attempt_records_which_resolver_made_it(session, monkeypatch):
    _force_fingerprint(monkeypatch, "resolver-A")
    row, _ = _obs(session, horizon_sessions=3)
    out, _ = _resolve(session, row, {D[0]: 101.0, D[1]: 103.0, D[2]: 110.0})
    assert out.attempts[0]["resolver"] == "resolver-A"


# ---- what a published performance figure may read -------------------------------------------

def test_publishable_excludes_a_superseded_row(session, monkeypatch):
    from intel_reports.observations import publishable_outcomes, PROSPECTIVE
    row, _ = _obs(session, horizon_sessions=3)
    _force_fingerprint(monkeypatch, "resolver-A")
    _resolve(session, row, {D[0]: 101.0, D[1]: 103.0, D[2]: 110.0})
    assert len(publishable_outcomes(session, origin=PROSPECTIVE)) == 1
    _force_fingerprint(monkeypatch, "resolver-B")
    _resolve(session, row, {D[0]: 101.0, D[1]: 103.0, D[2]: 120.0})
    got = publishable_outcomes(session, origin=PROSPECTIVE)
    assert len(got) == 1, "the superseded original must not be pooled with its replacement"
    assert round(got[0][0].descriptive_return, 4) == 0.20


def test_publishable_excludes_a_stale_row_that_was_never_re_resolved(session, monkeypatch):
    """`superseded_by IS NULL` alone is not enough. An old row nobody has re-resolved has no
    successor to point at — it was produced by a resolver whose defects are the reason the
    current one exists, and it would pass a supersession-only filter."""
    from intel_reports.observations import (publishable_outcomes, resolver_fingerprint,
                                            PROSPECTIVE, UNFINGERPRINTED)
    row, _ = _obs(session, horizon_sessions=3)
    _force_fingerprint(monkeypatch, "resolver-OLD")
    old, _ = _resolve(session, row, {D[0]: 101.0, D[1]: 103.0, D[2]: 110.0})
    old.resolver_fingerprint = UNFINGERPRINTED
    session.commit()
    monkeypatch.undo()
    assert old.superseded_by_id is None, "nothing re-resolved it, so nothing points at it"
    assert resolver_fingerprint() != UNFINGERPRINTED
    assert publishable_outcomes(session, origin=PROSPECTIVE) == []


def test_publishable_never_pools_replay_with_prospective(session):
    from intel_reports.observations import publishable_outcomes, PROSPECTIVE, REPLAY
    pro, _ = _obs(session, horizon_sessions=3)
    rep, _ = _obs(session, horizon_sessions=3, origin=REPLAY,
                  observed_at=OBSERVED - timedelta(days=1))
    _resolve(session, pro, {D[0]: 101.0, D[1]: 103.0, D[2]: 110.0})
    _resolve(session, rep, {D[0]: 101.0, D[1]: 103.0, D[2]: 120.0})
    assert len(publishable_outcomes(session, origin=PROSPECTIVE)) == 1
    assert len(publishable_outcomes(session, origin=REPLAY)) == 1


def test_publishable_excludes_a_replaced_row_after_a_resolver_rollback(session, monkeypatch):
    """The case that makes the supersession filter independently necessary.

    Revert the resolver and its fingerprint returns to an earlier value, which again matches a
    row that was explicitly replaced. Reading it would publish a figure a correction had
    already withdrawn.
    """
    from intel_reports.observations import publishable_outcomes, PROSPECTIVE
    row, _ = _obs(session, horizon_sessions=3)
    _force_fingerprint(monkeypatch, "resolver-A")
    old, _ = _resolve(session, row, {D[0]: 101.0, D[1]: 103.0, D[2]: 110.0})
    _force_fingerprint(monkeypatch, "resolver-B")
    _resolve(session, row, {D[0]: 101.0, D[1]: 103.0, D[2]: 120.0})
    session.expire_all()
    assert session.get(type(old), old.id).superseded_by_id is not None

    _force_fingerprint(monkeypatch, "resolver-A")  # rolled back
    assert publishable_outcomes(session, origin=PROSPECTIVE) == [], \
        "a withdrawn figure must not return to publication because the code was reverted"


# ---- an INVALID CAPTURE stays excluded however often it is re-resolved -----------------------
#
# AUD-OBS-INVALIDCAPTURE. MU's observations 1-3 sit at a weekend cutoff produced by the
# session-walk bug fixed in aa48c5d4. They were excluded only because their outcome carried an
# old resolver fingerprint — which is a property of the SCORING. Re-resolving them under the
# current resolver would have walked them straight back into publication, because the defect is
# in the CAPTURE and no amount of re-scoring repairs it.

def test_an_invalidated_capture_is_excluded_even_after_re_resolution(session, monkeypatch):
    from db.models import IntelligenceObservation
    from intel_reports.observations import publishable_outcomes, PROSPECTIVE
    row, _ = _obs(session, horizon_sessions=3)
    _force_fingerprint(monkeypatch, "resolver-A")
    _resolve(session, row, {D[0]: 101.0, D[1]: 103.0, D[2]: 110.0})
    assert len(publishable_outcomes(session, origin=PROSPECTIVE)) == 1

    obs = session.get(IntelligenceObservation, row.id)
    obs.invalidated_reason = ("captured at a non-trading-day cutoff produced by the session-"
                             "walk defect fixed in aa48c5d4; the inputs cannot be reproduced")
    obs.invalidated_at = OBSERVED
    session.commit()
    assert publishable_outcomes(session, origin=PROSPECTIVE) == []

    # The decisive step: re-resolve under the CURRENT resolver, which is exactly what would
    # make it eligible again if exclusion rested on the outcome's fingerprint.
    monkeypatch.undo()
    out, created = _resolve(session, row, {D[0]: 101.0, D[1]: 103.0, D[2]: 110.0})
    assert created is True, "a fresh resolver did write a new outcome row"
    assert out.resolution_state == "RESOLVED"
    assert publishable_outcomes(session, origin=PROSPECTIVE) == [], \
        "a defective CAPTURE cannot be repaired by re-scoring it"


def test_the_invalidation_reason_is_recorded_in_words_not_a_flag(session):
    from db.models import IntelligenceObservation
    row, _ = _obs(session)
    obs = session.get(IntelligenceObservation, row.id)
    obs.invalidated_reason = "x" * 400  # a sentence, not a boolean
    session.commit()
    session.expire_all()
    assert len(session.get(IntelligenceObservation, row.id).invalidated_reason) == 400


def test_resolve_records_the_adjustment_finding_rather_than_only_a_verdict(session):
    """An UNRESOLVED_ADJUSTMENT_MISSING that does not say WHAT moved is unactionable."""
    row, _ = _obs(session, horizon_sessions=3)
    out, _ = _resolve(session, row, {D[0]: 101.0, D[1]: 103.0, D[2]: 110.0},
                      adjustment_consistent=False,
                      adjustment_basis="the adjustment factor moves inside the outcome window "
                                       "(1 change(s), first on 2026-09-03 for stock)")
    assert out.resolution_state == "UNRESOLVED_ADJUSTMENT_MISSING"
    assert "2026-09-03" in out.resolution_basis
    assert "not a reason to void" in out.resolution_basis


def test_a_resolved_basis_states_the_entry_convention_it_actually_applied(session):
    row, _ = _obs(session, horizon_sessions=3)
    out, _ = _resolve(session, row, {D[0]: 101.0, D[1]: 103.0, D[2]: 110.0},
                      adjustment_basis="the adjustment factor is flat across every session")
    assert "OPENED after the observation instant and has since closed" in out.resolution_basis
    assert "flat across every session" in out.resolution_basis


def test_an_unresolved_row_is_ineligible_but_still_counted(session):
    """It must never become a zero in a mean, and must still be answerable as a denominator."""
    from intel_reports.observations import (publishable_outcomes, coverage_counts, PROSPECTIVE)
    row, _ = _obs(session, horizon_sessions=3)
    _resolve(session, row, {D[0]: 101.0, D[1]: 103.0, D[2]: 110.0},
             adjustment_consistent=None, adjustment=None)
    assert publishable_outcomes(session, origin=PROSPECTIVE, eligibility="verified") == []
    assert publishable_outcomes(session, origin=PROSPECTIVE, eligibility="provisional") == []
    got = publishable_outcomes(session, origin=PROSPECTIVE, eligibility="ineligible")
    assert len(got) == 1, "it is still answerable as a denominator"
    out = got[0][0]
    assert out.resolution_state == "UNRESOLVED_ADJUSTMENT_UNVERIFIED"
    assert out.evidence_status == "unverified"
    assert out.descriptive_return is None and out.excess_return is None, \
        "an unverified window has no return, not a zero return"
    assert coverage_counts(session, origin=PROSPECTIVE)["pools"]["ineligible"]["total"] == 1


# ---- the three questions are separate, and a provisional figure is a different population ----

def test_a_returned_but_unguaranteed_history_is_PROVISIONAL_not_verified(session):
    """A source returning no further actions establishes what it RETURNED. It does not establish
    that none occurred, and keeping the calculation usable is not a reason to call it verified —
    that is an argument from inconvenience, not evidence."""
    from intel_reports.observations import publishable_outcomes, PROSPECTIVE
    row, _ = _obs(session, horizon_sessions=3)
    out, _ = _resolve(session, row, {D[0]: 101.0, D[1]: 103.0, D[2]: 110.0},
                      adjustment=_PROVISIONAL_ADJ)
    assert out.resolution_state == "RESOLVED", "the figure WAS calculable"
    assert out.descriptive_return is not None
    assert out.evidence_status == "provisional"
    assert out.performance_eligibility == "provisional"
    assert publishable_outcomes(session, origin=PROSPECTIVE, eligibility="verified") == [], \
        "a provisional figure must never reach a verified aggregate"
    assert len(publishable_outcomes(session, origin=PROSPECTIVE,
                                    eligibility="provisional")) == 1


def test_a_guaranteed_history_is_verified_and_eligible(session):
    from intel_reports.observations import publishable_outcomes, PROSPECTIVE
    row, _ = _obs(session, horizon_sessions=3)
    out, _ = _resolve(session, row, {D[0]: 101.0, D[1]: 103.0, D[2]: 110.0})
    assert (out.evidence_status, out.performance_eligibility) == ("verified", "verified")
    assert len(publishable_outcomes(session, origin=PROSPECTIVE, eligibility="verified")) == 1


def test_the_weakest_instrument_decides_the_evidence_status(session):
    """An excess return resting on a verified stock basis and a provisional benchmark basis is
    provisional: the comparison is only as sound as its weaker half."""
    from intel_reports.evidence_buckets import evidence_status
    mixed = {"consistent": True, "evidence": {
        "stock": {"verified_by": "sourced_action_history",
                  "completeness_basis": "source_guarantee"},
        "benchmark": {"verified_by": "sourced_action_history",
                      "completeness_basis": "response_only"}}}
    assert evidence_status(mixed) == "provisional"


def test_a_flat_provider_factor_is_corroboration_not_proof(session):
    """It rules out an action the provider recognised; it does not describe the provider's
    methodology, so it cannot establish a chosen basis outright."""
    from intel_reports.evidence_buckets import evidence_status
    assert evidence_status({"consistent": True, "evidence": {
        "stock": {"verified_by": "provider_adjustment_factor"}}}) == "provisional"


def test_the_provisional_label_says_what_it_rests_on_and_how_to_close_it(session):
    from intel_reports.evidence_buckets import EVIDENCE_LABEL, PROVISIONAL_REMEDY
    assert EVIDENCE_LABEL["provisional"] == (
        "Provisional — based on returned corporate actions; completeness unverified")
    # The remedy is stronger evidence, NOT acceptance.
    assert "documents completeness" in PROVISIONAL_REMEDY
    assert "second independent source" in PROVISIONAL_REMEDY


def test_coverage_counts_disclose_what_an_aggregate_excluded(session):
    """A 100% win rate over two eligible rows reads like a result unless the denominator is
    beside it."""
    from intel_reports.observations import coverage_counts, PROSPECTIVE
    good, _ = _obs(session, horizon_sessions=3)
    _resolve(session, good, {D[0]: 101.0, D[1]: 103.0, D[2]: 110.0})
    prov, _ = _obs(session, horizon_sessions=3, observed_at=OBSERVED - timedelta(days=1))
    _resolve(session, prov, {D[0]: 101.0, D[1]: 103.0, D[2]: 110.0},
             adjustment=_PROVISIONAL_ADJ)
    blocked, _ = _obs(session, horizon_sessions=3, observed_at=OBSERVED - timedelta(days=2))
    _resolve(session, blocked, {D[0]: 101.0, D[1]: 103.0, D[2]: 110.0},
             adjustment_consistent=None, adjustment=None)
    pools = coverage_counts(session, origin=PROSPECTIVE)["pools"]
    assert pools["verified"]["total"] == 1
    assert pools["provisional"]["total"] == 1
    assert pools["ineligible"]["total"] == 1


# ---- corporate-action storage and the coverage claim -----------------------------------------

def test_storing_actions_is_idempotent_and_writes_a_coverage_claim(session):
    from datetime import date as _date
    from db.models import CorporateAction, CorporateActionCoverage
    from intel_reports.corporate_actions import normalise, store
    acts = normalise({"2026-06-03": 2.0}, {"2026-06-10": 0.75}, symbol="MU",
                     retrieved_at=OBSERVED)
    kw = dict(covers_from=_date(2026, 1, 1), covers_to=_date(2026, 12, 31),
              retrieved_at=OBSERVED)
    first = store(session, "MU", acts, **kw)
    assert first["actions_created"] == 2
    again = store(session, "MU", acts, **kw)
    assert again["actions_created"] == 0 and again["actions_already_held"] == 2
    assert session.query(CorporateAction).count() == 2
    assert session.query(CorporateActionCoverage).count() == 1


def test_a_coverage_claim_widens_and_never_narrows(session):
    """Narrowing on a re-run would silently un-verify windows a wider fetch had verified."""
    from datetime import date as _date
    from db.models import CorporateActionCoverage
    from intel_reports.corporate_actions import store
    store(session, "MU", [], covers_from=_date(2020, 1, 1), covers_to=_date(2026, 12, 31),
          retrieved_at=OBSERVED)
    store(session, "MU", [], covers_from=_date(2026, 6, 1), covers_to=_date(2026, 6, 30),
          retrieved_at=OBSERVED)
    cov = session.query(CorporateActionCoverage).one()
    assert (cov.requested_from, cov.requested_to) == (_date(2020, 1, 1), _date(2026, 12, 31))
    assert cov.evidenced_from == _date(2020, 1, 1)


def test_a_requested_span_past_the_retrieval_date_is_not_evidenced(session):
    """AUD-OBS-COVERAGEFUTURE. The first pilot asked for a span ending 2026-12-31 and the row
    claimed it — from a fetch that could not have seen November's corporate actions, because
    they had not happened."""
    from datetime import date as _date
    from db.models import CorporateActionCoverage
    from intel_reports.corporate_actions import store
    store(session, "MU", [], covers_from=_date(2025, 1, 1), covers_to=_date(2026, 12, 31),
          retrieved_at=OBSERVED)          # OBSERVED is 2026-09-01
    cov = session.query(CorporateActionCoverage).one()
    assert cov.requested_to == _date(2026, 12, 31), "what was asked for is kept"
    assert cov.evidenced_to == OBSERVED.date(), "what it can speak for stops at retrieval"
    assert cov.evidenced_to < cov.requested_to


def test_an_empty_history_still_claims_its_evidenced_span(session):
    """A window with no actions is covered as fully as one with ten — inferring the span from
    the rows would make an empty result claim nothing, which is the ambiguity this table exists
    to remove. The STRENGTH of that claim is a separate field."""
    from datetime import date as _date
    from db.models import CorporateActionCoverage
    from intel_reports.corporate_actions import store, RESPONSE_ONLY
    store(session, "MU", [], covers_from=_date(2026, 1, 1), covers_to=_date(2026, 6, 30),
          retrieved_at=OBSERVED)
    cov = session.query(CorporateActionCoverage).one()
    assert (cov.evidenced_from, cov.evidenced_to) == (_date(2026, 1, 1), _date(2026, 6, 30))
    assert cov.completeness_basis == RESPONSE_ONLY, \
        "an empty response establishes 'none returned', not 'none occurred'"
