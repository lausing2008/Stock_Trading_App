"""Read-only, authenticated long-option review tied to one measured flow candidate."""
from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from db import User, get_session
from db.models import OptionChainHistory, OptionsFlowAlertOutcome
from .auth import get_current_user
from ..services.long_option_playbook import build

router = APIRouter(prefix="/long-option-playbook", tags=["long-option-playbook"])


@router.get("")
def review(option_chain: str = Query(..., min_length=8, max_length=64),
           fired_date: date = Query(...),
           _: User = Depends(get_current_user),
           session: Session = Depends(get_session)):
    flow = session.scalar(select(OptionsFlowAlertOutcome).where(
        OptionsFlowAlertOutcome.option_chain == option_chain,
        OptionsFlowAlertOutcome.fired_date == fired_date))
    if flow is None:
        raise HTTPException(404, "Flow candidate not found")
    wanted_type = "call" if flow.direction == "bullish" else "put"
    today = date.today()
    chains = session.execute(select(OptionChainHistory).where(
        OptionChainHistory.symbol == flow.symbol,
        OptionChainHistory.option_type == wanted_type,
        OptionChainHistory.expiry >= today + timedelta(days=14),
        OptionChainHistory.expiry <= today + timedelta(days=60),
        OptionChainHistory.as_of <= fired_date)
        .order_by(OptionChainHistory.as_of.desc()).limit(2000)).scalars().all()
    return build(flow, chains)
