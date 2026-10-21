"""Service-owned RZY tables registered before the existing locked startup create_all."""
from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, JSON, String, Integer
from sqlalchemy.orm import Mapped, mapped_column
from db import Base


class RzyStream(Base):
    __tablename__ = 'little_rzy_streams'
    stream_key: Mapped[str] = mapped_column(String(96), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    timeframe: Mapped[str] = mapped_column(String(8))
    config_hash: Mapped[str] = mapped_column(String(24))
    checkpoint: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class RzyEvent(Base):
    __tablename__ = 'little_rzy_events'
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    timeframe: Mapped[str] = mapped_column(String(8))
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class RzyPreference(Base):
    __tablename__ = 'little_rzy_preferences'
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    timeframes: Mapped[list] = mapped_column(JSON, default=lambda: ['5m','15m','1h'])
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class RzyStreamGeneration(Base):
    """Summary of a superseded stream state, kept when stored history forces a rebuild."""
    __tablename__ = 'little_rzy_stream_generations'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stream_key: Mapped[str] = mapped_column(String(96), index=True)
    generation: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSON)
    archived_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class RzyStreamSnapshot(Base):
    """Latest scan summary per stream, so previews never recompute streams during a request."""
    __tablename__ = 'little_rzy_stream_snapshots'
    stream_key: Mapped[str] = mapped_column(String(96), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    timeframe: Mapped[str] = mapped_column(String(8))
    config_hash: Mapped[str] = mapped_column(String(24))
    checked_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(48))
    summary: Mapped[dict] = mapped_column(JSON)


class RzyScanRun(Base):
    """One row per completed scan: bounded-processing-time and missed-alert evidence (OD-02)."""
    __tablename__ = 'little_rzy_scan_runs'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    mode: Mapped[str] = mapped_column(String(16))
    duration_seconds: Mapped[float] = mapped_column(Float)
    streams: Mapped[int] = mapped_column(Integer)
    statuses: Mapped[dict] = mapped_column(JSON)
    new_emailable_events: Mapped[int] = mapped_column(Integer)
    too_late_to_mail: Mapped[int] = mapped_column(Integer)
    queued: Mapped[int] = mapped_column(Integer)
