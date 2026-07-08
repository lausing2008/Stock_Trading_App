"""Trade Board endpoints — per-user Kanban cards (game plans + forecast picks)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from db import TradePlan, User, get_session
from .auth import get_current_user

router = APIRouter(prefix="/board", tags=["board"])

VALID_STAGES = {"watch", "planning", "active", "closed"}

# FSM: maps current_stage → set of allowed next stages
VALID_TRANSITIONS: dict[str, set[str]] = {
    "watch":    {"planning", "closed"},  # allow watch→closed for quick discard
    "planning": {"watch", "active", "closed"},
    "active":   {"closed"},
    "closed":   set(),  # terminal state — no further transitions
}


class PlanIn(BaseModel):
    symbol: str
    stage: str = "watch"
    game_plan: dict | None = None
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    notes: str | None = None
    source: str | None = None  # gameplan | forecast | manual
    actual_entry_price: float | None = None
    shares: float | None = None
    trading_style: str | None = None  # SHORT|SWING|LONG


class PlanUpdate(BaseModel):
    stage: str | None = None
    notes: str | None = None
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    exit_price: float | None = None
    actual_entry_price: float | None = None
    shares: float | None = None
    trading_style: str | None = None


class PlanOut(BaseModel):
    id: int
    symbol: str
    stage: str
    game_plan: dict | None
    entry_price: float | None
    stop_loss: float | None
    take_profit: float | None
    notes: str | None
    source: str | None
    exit_price: float | None
    actual_entry_price: float | None
    shares: float | None
    trading_style: str | None
    closed_at: str | None
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


def _out(p: TradePlan) -> PlanOut:
    return PlanOut(
        id=p.id,
        symbol=p.symbol,
        stage=p.stage,
        game_plan=p.game_plan,
        entry_price=p.entry_price,
        stop_loss=p.stop_loss,
        take_profit=p.take_profit,
        notes=p.notes,
        source=p.source,
        exit_price=p.exit_price,
        actual_entry_price=p.actual_entry_price,
        shares=p.shares,
        trading_style=p.trading_style,
        closed_at=p.closed_at.isoformat() if p.closed_at else None,
        created_at=p.created_at.isoformat(),
        updated_at=p.updated_at.isoformat(),
    )


@router.get("", response_model=list[PlanOut])
def list_plans(
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    rows = session.execute(
        select(TradePlan)
        .where(TradePlan.user_id == current.id)
        .order_by(TradePlan.updated_at.desc())
    ).scalars().all()
    return [_out(p) for p in rows]


@router.post("", response_model=PlanOut)
def create_plan(
    body: PlanIn,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if body.stage not in VALID_STAGES:
        raise HTTPException(400, f"stage must be one of {sorted(VALID_STAGES)}")
    plan = TradePlan(
        user_id=current.id,
        symbol=body.symbol.upper(),
        stage=body.stage,
        game_plan=body.game_plan,
        entry_price=body.entry_price,
        stop_loss=body.stop_loss,
        take_profit=body.take_profit,
        notes=body.notes,
        source=body.source,
        actual_entry_price=body.actual_entry_price,
        shares=body.shares,
        trading_style=body.trading_style,
    )
    session.add(plan)
    session.commit()
    session.refresh(plan)
    return _out(plan)


@router.put("/{plan_id}", response_model=PlanOut)
def update_plan(
    plan_id: int,
    body: PlanUpdate,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    plan = session.execute(
        select(TradePlan).where(TradePlan.id == plan_id, TradePlan.user_id == current.id)
    ).scalar_one_or_none()
    if not plan:
        raise HTTPException(404, "Plan not found")
    if body.stage is not None:
        if body.stage not in VALID_STAGES:
            raise HTTPException(400, f"stage must be one of {sorted(VALID_STAGES)}")
        allowed = VALID_TRANSITIONS.get(plan.stage, set())
        if body.stage != plan.stage and body.stage not in allowed:
            raise HTTPException(400, f"Invalid stage transition: {plan.stage} → {body.stage}. Allowed: {sorted(allowed) or 'none'}")
        plan.stage = body.stage
        if body.stage == "closed" and plan.closed_at is None:
            plan.closed_at = datetime.now(timezone.utc)
    if body.notes is not None:
        plan.notes = body.notes
    if body.entry_price is not None:
        plan.entry_price = body.entry_price
    if body.stop_loss is not None:
        plan.stop_loss = body.stop_loss
    if body.take_profit is not None:
        plan.take_profit = body.take_profit
    if body.exit_price is not None:
        plan.exit_price = body.exit_price
    if body.actual_entry_price is not None:
        plan.actual_entry_price = body.actual_entry_price
    if body.shares is not None:
        plan.shares = body.shares
    if body.trading_style is not None:
        plan.trading_style = body.trading_style
    plan.updated_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(plan)
    return _out(plan)


@router.delete("/{plan_id}")
def delete_plan(
    plan_id: int,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    plan = session.execute(
        select(TradePlan).where(TradePlan.id == plan_id, TradePlan.user_id == current.id)
    ).scalar_one_or_none()
    if not plan:
        raise HTTPException(404, "Plan not found")
    if plan.stage == "active":
        from db.models import UserPosition
        pos = session.execute(
            select(UserPosition).where(
                UserPosition.symbol == plan.symbol.upper(),
                UserPosition.user_id == current.id,  # MUST scope to current user
            )
        ).scalar_one_or_none()
        if pos:
            session.delete(pos)
    session.delete(plan)
    session.commit()
    return {"status": "deleted", "id": plan_id}
