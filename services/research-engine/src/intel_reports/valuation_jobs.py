"""PostgreSQL-backed single-flight valuation jobs. No credentials are persisted."""
from datetime import datetime, timedelta
import uuid

from sqlalchemy import DateTime, Integer, String, Text, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "valuation_report_jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    due_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime)
    lease_token: Mapped[str | None] = mapped_column(String(32))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stage: Mapped[str | None] = mapped_column(String(240))
    error: Mapped[str | None] = mapped_column(Text)
    report_id: Mapped[int | None] = mapped_column(Integer)
    reused: Mapped[int | None] = mapped_column(Integer)


class Control(Base):
    __tablename__ = "valuation_report_job_control"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    active_id: Mapped[str | None] = mapped_column(String(32))
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime)
    scheduled_session: Mapped[str | None] = mapped_column(String(10))


class Cooldown(Exception):
    pass


class LeaseLost(Exception):
    pass


def init_store(engine):
    # A cross-process insert race is handled by ON CONFLICT rather than by overwriting control.
    from sqlalchemy import text
    with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            connection.execute(text("SET LOCAL statement_timeout = '30s'"))
            connection.execute(text("SELECT pg_advisory_xact_lock(843781020)"))
        Base.metadata.create_all(connection)
        if connection.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert
        connection.execute(insert(Control).values(id=1).on_conflict_do_nothing(index_elements=["id"]))


def serialise(job):
    def stamp(value): return value.isoformat() + "Z" if value else None
    return {"job_id": job.id, "state": job.state, "stage": job.stage, "error": job.error,
            "started_at": stamp(job.started_at), "created_at": stamp(job.created_at),
            "finished_at": stamp(job.finished_at), "retry_at": stamp(job.due_at) if job.state == "queued" else None,
            "attempts": job.attempts, "report_id": job.report_id,
            "created": None if job.reused is None else not bool(job.reused), "durable": True}


def enqueue(factory, *, now, scheduled_session=None):
    with factory() as session:
        control = session.execute(select(Control).where(Control.id == 1).with_for_update()).scalar_one()
        if scheduled_session and control.scheduled_session == scheduled_session:
            return None
        active = session.get(Job, control.active_id) if control.active_id else None
        if active and active.state in {"queued", "running"}:
            if scheduled_session:
                control.scheduled_session = scheduled_session
                session.commit()
            return serialise(active)
        if control.cooldown_until and control.cooldown_until > now:
            raise Cooldown()
        job = Job(id=uuid.uuid4().hex, state="queued", trigger="scheduled" if scheduled_session else "manual",
                  created_at=now, due_at=now, attempts=0, stage="Queued for durable report generation")
        session.add(job)
        session.flush()
        control.active_id = job.id
        control.cooldown_until = now + timedelta(minutes=5)
        if scheduled_session:
            control.scheduled_session = scheduled_session
        session.commit()
        return serialise(job)


def read(factory, job_id):
    with factory() as session:
        job = session.get(Job, job_id)
        return serialise(job) if job else None


def claim(factory, *, now):
    with factory() as session:
        control = session.execute(select(Control).where(Control.id == 1).with_for_update()).scalar_one()
        if not control.active_id:
            return None
        job = session.execute(select(Job).where(Job.id == control.active_id).with_for_update()).scalar_one_or_none()
        if not job or job.state not in {"queued", "running"}:
            control.active_id = None
            session.commit()
            return None
        if (job.state == "queued" and job.due_at > now) or (job.state == "running" and job.lease_until and job.lease_until > now):
            return None
        if job.attempts >= 3 or now - job.created_at > timedelta(hours=2):
            job.state, job.finished_at = "failed", now
            job.error = "Report retries exhausted; the last successful report is retained"
            control.active_id = None
            session.commit()
            return None
        job.state, job.started_at = "running", now
        job.attempts += 1
        job.lease_token, job.lease_until = uuid.uuid4().hex, now + timedelta(seconds=120)
        job.error, job.stage = None, "Collecting completed-session research inputs"
        session.commit()
        return {**serialise(job), "lease_token": job.lease_token}


def assert_owner(session, job_id, token, *, now):
    job = session.execute(select(Job).where(Job.id == job_id).with_for_update()).scalar_one_or_none()
    if not job or job.state != "running" or job.lease_token != token or not job.lease_until or job.lease_until <= now:
        raise LeaseLost()
    return job


def progress(factory, job_id, token, *, now, stage=None):
    with factory() as session:
        job = assert_owner(session, job_id, token, now=now)
        job.lease_until = now + timedelta(seconds=120)
        if stage:
            job.stage = stage[:240]
        session.commit()


def finish(factory, job_id, token, *, now, result):
    with factory() as session:
        # Use the same control→job lock order as claim/enqueue to avoid deadlocks.
        control = session.execute(select(Control).where(Control.id == 1).with_for_update()).scalar_one()
        job = assert_owner(session, job_id, token, now=now)
        if result.get("state") == "complete":
            job.state, job.finished_at, job.report_id = "complete", now, result["report_id"]
            job.reused = int(result.get("created") is False)
            job.stage = "Report saved from frozen inputs"
            control.active_id = None
        else:
            contention = bool(result.get("contention"))
            if contention:
                job.attempts = max(0, job.attempts - 1)
            if job.attempts < 3 and now - job.created_at <= timedelta(hours=2):
                job.state = "queued"
                delay = 120 if contention else min(300, 30 * 2 ** max(0, job.attempts - 1))
                job.due_at = now + timedelta(seconds=delay)
                job.stage = "Refresh interrupted; retry scheduled. Last successful report retained."
            else:
                job.state, job.finished_at = "failed", now
                control.active_id = None
            job.error = "Report refresh failed; the last successful report is retained"
        job.lease_token, job.lease_until = None, None
        session.commit()
        return serialise(job)
