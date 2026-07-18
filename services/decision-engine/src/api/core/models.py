"""Pydantic schemas for the Decision Engine."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ── Request ────────────────────────────────────────────────────────────────────

class DecisionRequest(BaseModel):
    # T232-DL-STYLEPARAMS3X: corrected 2026-07-04 — SCALP/INCOME never existed in the real
    # trading engine; SHORT/LONG are the two real styles that were previously missing here.
    style: str = Field("SWING", description="SHORT | SWING | GROWTH | LONG")
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
    """T234-DE-SIZER-DISCARDED: illustrative sizing preview only.

    Computed by sizer.py, which is a related but INDEPENDENT sizing model from the
    one paper_trading_engine.py actually uses for real (paper) trades — see sizer.py's
    module docstring for the confirmed divergences. paper_trading_engine.py never reads
    this field. Useful for a human or API caller previewing "what would the system do",
    not as a source of truth for what the live trading engine will actually size.
    """
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
    breadth: float = 1.0
    vix: float = 1.0


class RiskFlag(BaseModel):
    """T258-WHATCOULDGOWRONG-AGENT: one adversarial-check finding.

    category/severity are the LLM's own classification of a concrete failure mode it was
    asked to argue FOR (not evidence the trade will actually fail — see risk_agent.py's
    module docstring for why no probability_of_failure number is emitted here).
    """
    category: str      # macro | sector | company | technical
    severity: str      # low | medium | high
    note: str          # one concise sentence


# ── Main response ──────────────────────────────────────────────────────────────

class DecisionResult(BaseModel):
    symbol: str
    style: str
    verdict: str                              # BUY | HOLD | SKIP | BLOCKED
    score: int
    min_score: int
    position: PositionPlan | None = None      # None when verdict is BLOCKED / SKIP / HOLD; ILLUSTRATIVE ONLY — see PositionPlan docstring
    factors: Factors
    multipliers: Multipliers
    score_breakdown: list[ScoreItem]
    blocked_reason: str | None = None
    latency_ms: int = 0
    timestamp: str = ""
    # T203: LLM reasoning layer (optional — only populated when llm_scoring_enabled=True)
    llm_verdict: str | None = None            # BUY | HOLD | SKIP from Claude
    llm_reasoning: str | None = None         # one-sentence rationale
    # T247-DECISIONENGINE-LLMVERDICT-ORDERING: llm_verdict reflects the LLM's OWN standalone
    # view, computed before the micro-position sizing-floor check (routes.py's
    # _MIN_COMBINED_MULT) can later override the final `verdict` to SKIP. The two fields can
    # legitimately disagree (llm_verdict="BUY", verdict="SKIP") when the LLM liked the trade
    # but stacked sizing multipliers made the resulting position too small to be worth taking
    # — this flag makes that an explicit, intentional signal rather than a silent
    # inconsistency a consumer might mistake for a bug.
    llm_verdict_overridden_by_sizing: bool = False
    # T258-WHATCOULDGOWRONG-AGENT: optional adversarial pre-trade risk enumeration (only
    # populated when risk_check_enabled=True). None (not []) means the check didn't run at
    # all — a real finding of "no risks identified" is not a case the LLM is asked to report,
    # since a forced-adversarial prompt asking it to argue against the trade will essentially
    # always find something to say; distinguishing "didn't run" from "found nothing" would
    # invite over-trusting an empty list as a clean bill of health.
    risks: list[RiskFlag] | None = None


class BatchDecisionRequest(BaseModel):
    symbols: list[str]
    style: str = "SWING"

    @field_validator("symbols")
    @classmethod
    def _check_symbols_len(cls, v: list[str]) -> list[str]:
        if len(v) > 30:
            raise ValueError("batch decide accepts at most 30 symbols per request")
        return v
    portfolio_id: int | None = None
    equity: float = 10_000.0
    open_positions: int = 0
    max_positions: int = 6
    daily_pnl_pct: float = 0.0
    max_daily_loss_pct: float = 0.04
    market: str = "US"
    config_overrides: dict[str, Any] = Field(default_factory=dict)
