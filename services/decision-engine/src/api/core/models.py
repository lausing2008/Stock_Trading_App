"""Pydantic schemas for the Decision Engine."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Request ────────────────────────────────────────────────────────────────────

class DecisionRequest(BaseModel):
    style: str = Field("SWING", description="SCALP | SWING | GROWTH | INCOME")
    portfolio_id: int | None = None
    equity: float = Field(10_000.0, ge=0, description="Current portfolio equity in dollars")
    open_positions: int = Field(0, ge=0)
    max_positions: int = Field(6, ge=1)
    daily_pnl_pct: float = Field(0.0, description="Today's P&L as fraction of equity (e.g. -0.015)")
    max_daily_loss_pct: float = Field(0.04, description="Hard stop: block entries if daily loss exceeds this")
    market: str = Field("US", description="US | HK")
    live_price: float | None = Field(None, description="Live price; fetched from signal if omitted")
    game_plan: dict | None = Field(None, description="entry1, entry2, breakout, stop, take_profit")
    config_overrides: dict[str, Any] = Field(default_factory=dict)


# ── Sub-objects in response ────────────────────────────────────────────────────

class ScoreItem(BaseModel):
    layer: str
    pts: int
    note: str


class PositionPlan(BaseModel):
    shares: float
    size_pct: float
    dollar_risk: float
    entry_price: float
    stop_price: float
    target_1: float
    target_2: float
    rr_ratio: float


class Factors(BaseModel):
    signal_direction: str | None = None
    signal_confidence: float | None = None
    ml_bull_prob: float | None = None
    research_recommendation: str | None = None
    research_score: float | None = None
    regime: str = "neutral"
    volume_z: float | None = None
    days_to_earnings: int | None = None
    signal_age_h: float | None = None
    conf_delta: float | None = None
    cross_style_buys: int = 0


class Multipliers(BaseModel):
    regime: float = 1.0
    research: float = 1.0
    confidence: float = 1.0
    consensus: float = 1.0
    earnings: float = 1.0


# ── Main response ──────────────────────────────────────────────────────────────

class DecisionResult(BaseModel):
    symbol: str
    style: str
    verdict: str                              # BUY | SCALE | HOLD | SKIP | BLOCKED
    score: int
    min_score: int
    position: PositionPlan | None = None      # None when verdict is BLOCKED / SKIP / HOLD
    factors: Factors
    multipliers: Multipliers
    score_breakdown: list[ScoreItem]
    blocked_reason: str | None = None
    latency_ms: int = 0
    timestamp: str = ""


class BatchDecisionRequest(BaseModel):
    symbols: list[str]
    style: str = "SWING"
    portfolio_id: int | None = None
    equity: float = 10_000.0
    open_positions: int = 0
    max_positions: int = 6
    daily_pnl_pct: float = 0.0
    max_daily_loss_pct: float = 0.04
    market: str = "US"
    config_overrides: dict[str, Any] = Field(default_factory=dict)
