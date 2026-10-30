from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.intel_reports import valuation_jobs as J

NOW = datetime(2026, 10, 30, 5)


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.db'}")
    J.init_store(engine)
    yield sessionmaker(bind=engine)
    engine.dispose()


def test_enqueue_survives_new_sessions_and_deduplicates_manual_refresh(factory):
    first = J.enqueue(factory, now=NOW)
    assert J.enqueue(factory, now=NOW + timedelta(seconds=1))["job_id"] == first["job_id"]
    assert J.read(factory, first["job_id"])["state"] == "queued"
    claimed = J.claim(factory, now=NOW)
    assert claimed["job_id"] == first["job_id"]
    assert J.claim(factory, now=NOW + timedelta(seconds=1)) is None
    J.finish(factory, claimed["job_id"], claimed["lease_token"], now=NOW + timedelta(seconds=2),
             result={"state": "complete", "report_id": 55, "created": False})
    assert J.read(factory, first["job_id"])["created"] is False
    with pytest.raises(J.Cooldown): J.enqueue(factory, now=NOW + timedelta(seconds=3))
    assert J.enqueue(factory, now=NOW + timedelta(minutes=5))["job_id"] != first["job_id"]
    assert not {"authorization", "jwt", "token", "username"} & {c.name for c in J.Job.__table__.columns}


def test_crashed_worker_is_reclaimed_and_old_lease_cannot_update_or_finish(factory):
    J.enqueue(factory, now=NOW)
    old = J.claim(factory, now=NOW)
    after_restart = NOW + timedelta(seconds=121)
    new = J.claim(factory, now=after_restart)
    assert new["job_id"] == old["job_id"] and new["lease_token"] != old["lease_token"]
    assert new["attempts"] == 2
    with pytest.raises(J.LeaseLost):
        J.progress(factory, old["job_id"], old["lease_token"], now=after_restart)
    with pytest.raises(J.LeaseLost):
        J.finish(factory, old["job_id"], old["lease_token"], now=after_restart, result={"state": "complete", "report_id": 1})
    J.finish(factory, new["job_id"], new["lease_token"], now=after_restart,
             result={"state": "complete", "report_id": 2})
    assert J.read(factory, new["job_id"])["report_id"] == 2


def test_retry_backoff_is_durable_bounded_and_preserves_last_report_reference(factory):
    queued = J.enqueue(factory, now=NOW)
    now = NOW
    for attempt in range(1, 4):
        claimed = J.claim(factory, now=now)
        assert claimed["attempts"] == attempt
        result = J.finish(factory, claimed["job_id"], claimed["lease_token"], now=now, result={"state": "failed"})
        if attempt < 3:
            assert result["state"] == "queued"
            assert J.claim(factory, now=now + timedelta(seconds=1)) is None
            now += timedelta(seconds=30 * 2 ** (attempt - 1))
        else:
            assert result["state"] == "failed"
    assert J.claim(factory, now=now + timedelta(hours=1)) is None
    assert J.read(factory, queued["job_id"])["report_id"] is None


def test_input_contention_does_not_consume_attempt_budget_but_has_age_limit(factory):
    J.enqueue(factory, now=NOW)
    claimed = J.claim(factory, now=NOW)
    result = J.finish(factory, claimed["job_id"], claimed["lease_token"], now=NOW,
                      result={"state": "failed", "contention": True})
    assert result["state"] == "queued" and result["attempts"] == 0
    assert J.claim(factory, now=NOW + timedelta(hours=3)) is None
    assert J.read(factory, claimed["job_id"])["state"] == "failed"


def test_scheduled_session_is_recorded_once_across_restarts(factory):
    first = J.enqueue(factory, now=NOW, scheduled_session="2026-10-29")
    assert J.enqueue(factory, now=NOW + timedelta(hours=1), scheduled_session="2026-10-29") is None
    assert J.enqueue(factory, now=NOW)["job_id"] == first["job_id"]
    with factory() as session:
        assert len(session.execute(select(J.Job)).scalars().all()) == 1


def test_before_save_owner_guard_rejects_an_expired_worker(factory):
    J.enqueue(factory, now=NOW)
    claimed = J.claim(factory, now=NOW)
    with factory() as session:
        with pytest.raises(J.LeaseLost):
            J.assert_owner(session, claimed["job_id"], claimed["lease_token"], now=NOW + timedelta(seconds=121))
