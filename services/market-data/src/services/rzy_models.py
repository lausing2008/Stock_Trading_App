"""Service-owned RZY tables registered before the existing locked startup create_all."""
from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Integer
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
