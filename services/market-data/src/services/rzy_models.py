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


class RzyOccurrence(Base):
    """Immutable first-observation record per RZY occurrence, independent of generation pruning.

    Generation archives and their event rows are pruned to the last few generations, so they
    cannot answer "when was this first seen" or "which event is current" (audit F02/F08). This
    small row is written once per occurrence and only its latest_* and queued_* fields move.
    """
    __tablename__ = 'little_rzy_occurrences'
    occurrence_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    stream_key: Mapped[str] = mapped_column(String(96), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    timeframe: Mapped[str] = mapped_column(String(8))
    event_type: Mapped[str] = mapped_column(String(32))
    signal_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: data cutoff of the scan that first produced it, and the wall-clock time it was persisted
    first_cutoff: Mapped[datetime] = mapped_column(DateTime)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    first_generation: Mapped[int] = mapped_column(Integer)
    #: incremental (a newly processed bar) | rebuild | bootstrap — replayed history vs new
    observed_in: Mapped[str] = mapped_column(String(16))
    first_figures: Mapped[dict] = mapped_column(JSON)
    latest_generation: Mapped[int] = mapped_column(Integer)
    latest_event_id: Mapped[str] = mapped_column(String(64))
    queued_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    queued_figures: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RzyRebuild(Base):
    """Append-only rebuild log; generation archives are pruned, these counts are not (F08)."""
    __tablename__ = 'little_rzy_rebuilds'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stream_key: Mapped[str] = mapped_column(String(96), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    timeframe: Mapped[str] = mapped_column(String(8))
    generation: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(64))
    rebuilt_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class RzyScanPhases(Base):
    """Where a scan's wall time went (OD-02, 2026-10-22): bar loading/preparation, evaluation,
    snapshot/commit, other. Measured to choose the incremental-scan design from evidence."""
    __tablename__ = 'little_rzy_scan_phases'
    scan_time: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    mode: Mapped[str] = mapped_column(String(16))
    phases: Mapped[dict] = mapped_column(JSON)
