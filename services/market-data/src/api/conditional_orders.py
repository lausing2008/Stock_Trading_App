"""T286-CONDITIONAL-ORDER API — CRUD for single-hop "if TRIGGER then ACTION" orders.

Portfolio-scoped (PaperPortfolio has no user_id — paper portfolios are app-wide, not
per-user), so every endpoint here operates against a portfolio_id, not a bare user. Uses
get_current_user (same as paper_portfolio.py) since any authenticated app user can create a
conditional order on any portfolio — matching this app's existing convention that paper
portfolios are shared, app-wide resources, not access-controlled per user.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from db import ConditionalOrder, PaperPortfolio, get_session
from .auth import get_current_user
from common.logging import get_logger

log = get_logger("conditional_orders_api")

router = APIRouter(prefix="/conditional-orders", tags=["conditional-orders"])

_VALID_ACTION_TYPES = {"buy", "sell_partial", "sell_all", "tighten_stop", "close_position", "alert_only"}
_VALID_METRICS = {"price", "rsi", "volume_ratio", "signal", "position_pnl_pct", "time"}
_VALID_OPS = {"gte", "lte", "eq"}


class ConditionDict(BaseModel):
    metric: str
    op: str
    value: float | str


class CreateConditionalOrderRequest(BaseModel):
    portfolio_id: int
    symbol: str
    action_type: str
    conditions: list[ConditionDict]
    trigger_logic: str = "AND"
    action_value: float | None = None
    note: str | None = None
    email: str | None = None
    expires_at: datetime | None = None


def _validate(body: CreateConditionalOrderRequest) -> None:
    if body.action_type not in _VALID_ACTION_TYPES:
        raise HTTPException(400, f"Unknown action_type '{body.action_type}' — must be one of {sorted(_VALID_ACTION_TYPES)}")
    if body.trigger_logic not in ("AND", "OR"):
        raise HTTPException(400, "trigger_logic must be 'AND' or 'OR'")
    if not body.conditions:
        raise HTTPException(400, "At least one condition is required — an order with no conditions can never trigger")
    for cond in body.conditions:
        if cond.metric not in _VALID_METRICS:
            raise HTTPException(400, f"Unknown metric '{cond.metric}' — must be one of {sorted(_VALID_METRICS)}")
        if cond.op not in _VALID_OPS:
            raise HTTPException(400, f"Unknown op '{cond.op}' — must be one of {sorted(_VALID_OPS)}")
    if body.action_type == "tighten_stop" and body.action_value is None:
        raise HTTPException(400, "tighten_stop requires action_value (the new stop price)")
    if body.action_type == "sell_partial" and body.action_value is not None and not (0 < body.action_value <= 1):
        raise HTTPException(400, "sell_partial's action_value must be a fraction between 0 and 1")


def _order_out(o: ConditionalOrder) -> dict:
    return {
        "id": o.id, "portfolio_id": o.portfolio_id, "symbol": o.symbol,
        "action_type": o.action_type, "action_value": o.action_value,
        "conditions": o.conditions, "trigger_logic": o.trigger_logic,
        "note": o.note, "email": o.email, "status": o.status, "status_reason": o.status_reason,
        "expires_at": o.expires_at.isoformat() if o.expires_at else None,
        "resulting_trade_id": o.resulting_trade_id,
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "triggered_at": o.triggered_at.isoformat() if o.triggered_at else None,
    }


@router.post("")
def create_conditional_order(
    body: CreateConditionalOrderRequest,
    _: str = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _validate(body)
    portfolio = session.get(PaperPortfolio, body.portfolio_id)
    if portfolio is None:
        raise HTTPException(404, f"Unknown portfolio_id {body.portfolio_id}")

    order = ConditionalOrder(
        portfolio_id=body.portfolio_id, symbol=body.symbol.upper(),
        action_type=body.action_type, action_value=body.action_value,
        conditions=[c.model_dump() for c in body.conditions], trigger_logic=body.trigger_logic,
        note=body.note, email=body.email, expires_at=body.expires_at,
    )
    session.add(order)
    session.commit()
    session.refresh(order)
    log.info("conditional_order.created", order_id=order.id, portfolio_id=order.portfolio_id,
             symbol=order.symbol, action_type=order.action_type)
    return _order_out(order)


@router.get("")
def list_conditional_orders(
    portfolio_id: int | None = None,
    status: str | None = None,
    _: str = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    stmt = select(ConditionalOrder).order_by(ConditionalOrder.created_at.desc())
    if portfolio_id is not None:
        stmt = stmt.where(ConditionalOrder.portfolio_id == portfolio_id)
    if status is not None:
        stmt = stmt.where(ConditionalOrder.status == status)
    orders = session.execute(stmt).scalars().all()
    return {"orders": [_order_out(o) for o in orders]}


@router.get("/{order_id}")
def get_conditional_order(
    order_id: int,
    _: str = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    order = session.get(ConditionalOrder, order_id)
    if order is None:
        raise HTTPException(404, f"Unknown conditional order {order_id}")
    return _order_out(order)


@router.delete("/{order_id}")
def cancel_conditional_order(
    order_id: int,
    _: str = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Cancel a pending order — matches this feature's own single-hop design: there is no
    'edit' endpoint, since a chain the user wants differently is just a new order they create
    after cancelling this one, not a stateful chain link to modify in place."""
    order = session.get(ConditionalOrder, order_id)
    if order is None:
        raise HTTPException(404, f"Unknown conditional order {order_id}")
    if order.status != "pending":
        raise HTTPException(400, f"Cannot cancel an order in status '{order.status}' — only pending orders can be cancelled")
    order.status = "cancelled"
    order.status_reason = "Cancelled by user"
    session.commit()
    log.info("conditional_order.cancelled", order_id=order_id)
    return _order_out(order)
