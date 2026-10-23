"""R10: latest observed provider state plus immutable observed versions.

No legacy tuple-key rows are migrated or inferred to have identities. A provider tracking
ID is not an exchange-wide ID. Observed response time orders captures, not market revisions.
"""
import hashlib
import json
import math
from datetime import datetime, timezone
from .dark_pool_evidence import CLOCK_TOLERANCE
from sqlalchemy import MetaData, Table, Column, String, DateTime, Float, BigInteger, Boolean, JSON, Index, case, or_, and_

metadata = MetaData()
events = Table('dark_pool_events_v2', metadata,
    Column('symbol', String(32), primary_key=True), Column('tracking_id', String(128), primary_key=True),
    Column('executed_at', DateTime), Column('price', Float),
    Column('size', BigInteger), Column('premium', Float), Column('canceled', Boolean), Column('ambiguous', Boolean, nullable=False),
    Column('payload_hash', String(64), nullable=False), Column('payload', JSON, nullable=False),
    Column('observed_at', DateTime, nullable=False))
Index('ix_dark_pool_v2_symbol_executed', events.c.symbol, events.c.executed_at)
versions = Table('dark_pool_event_versions_v2', metadata,
    Column('symbol', String(32), primary_key=True), Column('tracking_id', String(128), primary_key=True),
    Column('payload_hash', String(64), primary_key=True), Column('payload', JSON, nullable=False),
    Column('observed_at', DateTime, nullable=False))


def ensure_schema():
    from db import SessionLocal
    with SessionLocal() as session:
        metadata.create_all(session.get_bind())


def _utc(raw):
    dt = datetime.fromisoformat(raw.replace('Z','+00:00')) if isinstance(raw,str) else raw
    if not isinstance(dt,datetime) or dt.tzinfo is None:
        raise ValueError('missing aware event clock')
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def event_values(symbol, row):
    tracking_id = getattr(row,'tracking_id',None)
    observed = getattr(row,'observed_at',None)
    if not tracking_id or not observed or type(getattr(row,'canceled',None)) is not bool:
        return None
    try:
        observed_at = _utc(observed)
        canceled = row.canceled
        # Identity and the explicit cancellation flag suffice for a tombstone. Providers
        # may zero or omit trade figures on cancellation; those must not preserve an active row.
        if canceled:
            executed_at = price = size = premium = None
        else:
            executed_at = _utc(row.executed_at) if row.executed_at else None
            price = float(row.price) if row.price is not None else None
            size = int(row.size) if row.size is not None else None
            if (executed_at is None or price is None or size is None
                    or not math.isfinite(price) or price <= 0 or size <= 0
                    or executed_at > observed_at + CLOCK_TOLERANCE):
                return None
            premium = float(row.premium) if row.premium is not None else price*size
            if not math.isfinite(premium) or premium < 0:
                return None
        payload = {'executed_at':executed_at.isoformat() if executed_at else None, 'price':price,'size':size,'premium':premium,
                   'canceled':row.canceled,'nbbo_bid':None if canceled else row.nbbo_bid,
                   'nbbo_ask':None if canceled else row.nbbo_ask,'venue':None if canceled else row.venue}
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True,allow_nan=False).encode()).hexdigest()
        return dict(symbol=symbol, tracking_id=str(tracking_id), executed_at=executed_at,price=price,
                    size=size,premium=premium,canceled=row.canceled,ambiguous=False,payload=payload,payload_hash=digest,observed_at=observed_at)
    except (ValueError,TypeError,AttributeError,OverflowError):
        return None


def persist(session, symbol, rows):
    valid = [v for row in rows if (v:=event_values(symbol,row)) is not None]
    if not valid:
        return 0
    if session.get_bind().dialect.name == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    # A SAVEPOINT prevents a ledger error from poisoning the caller's legacy transaction.
    with session.begin_nested():
        for values in valid:
            version={k:values[k] for k in ('symbol','tracking_id','payload_hash','payload','observed_at')}
            session.execute(insert(versions).values(**version).on_conflict_do_nothing())
            stmt=insert(events).values(**values)
            changes={k:stmt.excluded[k] for k in values if k not in ('symbol','tracking_id')}
            changes['ambiguous']=case((events.c.observed_at == values['observed_at'],
                or_(events.c.ambiguous,events.c.payload_hash != values['payload_hash'])),else_=False)
            session.execute(stmt.on_conflict_do_update(
                index_elements=['symbol','tracking_id'], set_=changes,
                where=or_(events.c.observed_at < values['observed_at'],
                          and_(events.c.observed_at == values['observed_at'],events.c.payload_hash != values['payload_hash']))))
    session.commit()
    return len(valid)


def capture(symbol, rows):
    """Capture only actual provider responses; cache re-deliveries need no extra session."""
    from db import SessionLocal
    with SessionLocal() as session:
        return persist(session,symbol,rows)
