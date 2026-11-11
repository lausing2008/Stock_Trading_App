"""AUD-DP-TAB-BASELINE (2026-11-10): the baseline each dark-pool alert was judged against.

The Dark Pool tab listed alerts without saying whether a print cleared the relative bar (a
multiple of the symbol's own median captured premium) or only the $1M floor because no usable
baseline existed. The email showed this; the tab could not, because the outcome row never
stored it.

A separate table rather than new columns on `dark_pool_alert_outcomes`: that model lives in
shared/db, and any shared/ edit drifts all twelve backend containers and forces a rebuild of
every service (AUD-REGISTER-POSTBUILD-DRIFT). This table belongs to market-data alone and is
created by create_all at startup, which creates missing tables and never alters existing ones.

One row per (stock, ET day), written beside the outcome row with the same first-fire-of-the-day
rule, so the two always describe the same print. Insert-or-nothing; never rewritten.
"""
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, Date, DateTime, Float, Integer, MetaData, String, Table, select

metadata = MetaData()
baselines = Table(
    "dark_pool_alert_baselines_v1", metadata,
    Column("stock_id", BigInteger, primary_key=True),
    Column("fired_date", Date, primary_key=True),
    Column("baseline_status", String(32), nullable=False),
    Column("baseline_median", Float),
    Column("baseline_prints", Integer),
    Column("baseline_window_days", Integer),
    Column("vs_baseline", Float),
    Column("qualified_by", String(24), nullable=False),   # floor_and_relative | floor_only
    Column("recorded_at", DateTime, nullable=False),
)


def ensure_schema():
    from db import SessionLocal
    with SessionLocal() as session:
        metadata.create_all(session.get_bind())


def row_for(stock_id: int, fired_date, cand: dict) -> dict:
    """Pure: the stored row for one alert candidate."""
    median = cand.get("baseline_median")
    return {
        "stock_id": stock_id, "fired_date": fired_date,
        "baseline_status": str(cand.get("baseline_status") or "unknown")[:32],
        "baseline_median": median,
        "baseline_prints": cand.get("baseline_prints"),
        "baseline_window_days": cand.get("baseline_window_days"),
        "vs_baseline": cand.get("vs_baseline"),
        # The relative bar runs only with a usable median (option_evidence.dark_pool_qualifies).
        "qualified_by": "floor_and_relative" if median else "floor_only",
        "recorded_at": datetime.now(timezone.utc).replace(tzinfo=None),
    }


def record(session, stock_id: int, fired_date, cand: dict) -> bool:
    """Insert-or-nothing inside a savepoint. Never raises into the alert job."""
    try:
        if session.get_bind().dialect.name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        else:
            from sqlalchemy.dialects.postgresql import insert
        with session.begin_nested():
            filed = session.execute(insert(baselines).values(**row_for(stock_id, fired_date, cand))
                                    .on_conflict_do_nothing().returning(baselines.c.stock_id)).scalar()
        session.commit()
        return filed is not None
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass
        return False


def lookup(session, keys) -> dict:
    """{(stock_id, fired_date): row mapping} for the given keys. {} on any failure, including a
    table that does not exist yet (a pre-deploy row simply shows no baseline)."""
    keys = list(keys)
    if not keys:
        return {}
    try:
        stock_ids = {k[0] for k in keys}
        dates = {k[1] for k in keys}
        rows = session.execute(select(baselines).where(
            baselines.c.stock_id.in_(stock_ids), baselines.c.fired_date.in_(dates))).mappings().all()
        return {(r["stock_id"], r["fired_date"]): dict(r) for r in rows}
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass
        return {}
