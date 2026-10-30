"""Frozen cohort definitions and append-only outcome observations."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import uuid

from sqlalchemy import DateTime, Integer, JSON, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from .valuation_jobs import Base
from . import valuation_performance as P


class Cohort(Base):
    __tablename__ = "valuation_report_cohorts"
    source_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    definition: Mapped[dict] = mapped_column(JSON, nullable=False)
    next_due: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    last_attempted: Mapped[datetime | None] = mapped_column(DateTime)
    lease_token: Mapped[str | None] = mapped_column(String(32))
    last_error: Mapped[str | None] = mapped_column(String(180))


class Outcome(Base):
    __tablename__ = "valuation_report_outcomes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    __table_args__ = (UniqueConstraint("source_id", "fingerprint", name="uq_valuation_outcome_fingerprint"),)


def register(factory, report, *, now=None):
    definition = P.freeze(report)
    with factory() as session:
        existing = session.get(Cohort, report.id)
        if existing:
            if existing.definition != definition:
                raise ValueError("Frozen cohort membership changed")
            return
        session.add(Cohort(source_id=report.id, definition=definition,
                           next_due=(now or datetime.now(timezone.utc)).replace(tzinfo=None)))
        session.commit()


def latest(factory, source_id):
    with factory() as session:
        value = session.execute(select(Outcome).where(Outcome.source_id == source_id)
                                .order_by(Outcome.created_at.desc(), Outcome.id.desc()).limit(1)).scalar_one_or_none()
        return value.payload if value else None


def status(factory, source_id):
    with factory() as session:
        cohort = session.get(Cohort, source_id)
        if cohort and cohort.last_error:
            return {"status": "retrying", "note": cohort.last_error}
        return {"status": "tracked" if cohort else "not_tracked", "note": "Frozen cohorts are evaluated in the background; future horizons remain pending"
                if cohort else "No measurement cohort has been registered for this snapshot yet"}


def refresh(factory, price_loader, *, now, limit=3):
    now = P.utc(now)
    naive = now.replace(tzinfo=None)
    with factory() as session:
        cohorts = session.execute(select(Cohort).where(Cohort.next_due <= naive)
                    .order_by(Cohort.next_due.asc(), Cohort.source_id.asc()).limit(limit)
                    .with_for_update(skip_locked=True)).scalars().all()
        definitions = []
        for cohort in cohorts:
            token = uuid.uuid4().hex
            definitions.append((cohort.definition, token))
            cohort.lease_token = token
            cohort.next_due = naive + timedelta(minutes=15)
            cohort.last_attempted = naive
        session.commit()
    for definition, token in definitions:
        source_id = definition["report_id"]
        try:
            schedule = P.plan(definition)
            mature = any(now >= item["due_at"] for item in schedule.values())
            members = sorted({s for values in definition["groups"].values() for s in values})
            prices, metadata = {}, None
            if mature and members:
                loaded = price_loader(sorted(set(members + ["SPY"])))
                metadata = {k: v for k, v in loaded.items() if k not in {"series", "assembled_at"}}
                metadata["symbol_observations"] = {s: {k: v for k, v in values.items() if k != "bars"}
                                                     for s, values in (loaded.get("series") or {}).items()}
                for symbol, series in (loaded.get("series") or {}).items():
                    prices[symbol] = {b["date"]: b["adj_close"] for b in series.get("bars", [])}
            value = P.evaluate(definition, prices, now=now)
            value["price_source"] = metadata
            fingerprint = hashlib.sha256(json.dumps({k: v for k, v in value.items() if k != "evaluated_at"},
                                                    sort_keys=True, allow_nan=False).encode()).hexdigest()
            due = P.next_due(value, now=now)
            with factory() as session:
                cohort = session.execute(select(Cohort).where(Cohort.source_id == source_id).with_for_update()).scalar_one()
                if cohort.lease_token != token:
                    continue
                known = session.execute(select(Outcome).where(Outcome.source_id == source_id,
                            Outcome.fingerprint == fingerprint)).scalar_one_or_none()
                if not known:
                    session.add(Outcome(id=uuid.uuid4().hex, source_id=source_id, fingerprint=fingerprint,
                                        created_at=naive, payload=value))
                cohort.next_due = due.replace(tzinfo=None) if due else None
                cohort.last_attempted = naive
                cohort.lease_token = None
                cohort.last_error = None
                session.commit()
        except Exception as exc:
            # Preserve existing observations and retry on a later refresh rather than writing
            # zero returns or treating an evaluation failure as a company recommendation.
            with factory() as session:
                cohort = session.get(Cohort, source_id)
                if cohort.lease_token == token:
                    cohort.next_due = naive + timedelta(days=1)
                    cohort.last_attempted = naive
                    cohort.lease_token = None
                    cohort.last_error = "Outcome calendar coverage needs extending" if isinstance(exc, ValueError) and "calendar" in str(exc).lower() else "Outcome inputs could not be verified; retry scheduled and prior observations retained"
                session.commit()
