"""Authenticated options-flow delivery evidence and historical unknown state."""
from datetime import date, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from db import OptionsFlowRecipientDecision, PriceAlert, User, get_session
from .auth import get_current_user

router = APIRouter(prefix="/options-flow-delivery", tags=["options-flow-delivery"])


@router.get("")
def flow_delivery(current: User = Depends(get_current_user),
                  session: Session = Depends(get_session)):
    rows = session.execute(select(OptionsFlowRecipientDecision).where(
        OptionsFlowRecipientDecision.user_id == current.id,
        OptionsFlowRecipientDecision.fired_date >= date.today() - timedelta(days=14))
        .order_by(OptionsFlowRecipientDecision.decided_at.desc()).limit(100)).scalars().all()
    open_price_alerts = session.execute(select(PriceAlert.id).where(
        PriceAlert.user_id == current.id, PriceAlert.triggered.is_(False))).first() is not None
    return {"eligible_legacy_subscription": open_price_alerts,
        "subscription_note": ("Legacy flow email requires at least one active price alert. "
                              "It does not require that alert to name the flow symbol."),
        "historical_delivery_note": "Rows before per-user tracking have unknown delivery status.",
        "rows": [{"symbol": r.symbol, "option_chain": r.option_chain,
                  "fired_date": r.fired_date.isoformat(), "state": r.state,
                  "reason": r.reason, "decided_at": r.decided_at.isoformat()}
                 for r in rows]}
