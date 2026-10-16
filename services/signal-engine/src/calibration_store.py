"""Point-in-time database adapter for read-only calibration research."""
from datetime import timedelta
from sqlalchemy import select
from db.models import Signal, SignalOutcomeHorizon as Outcome, Stock
from .calibration_research import Observation,compare


def load_comparison(session, *, now, include_provisional=False):
    rows=session.execute(select(Outcome,Signal.first_buy_sell_confidence,Signal.first_buy_sell_at,
                                Stock.market,Signal.first_buy_sell_signal)
        .join(Signal,Signal.id==Outcome.signal_id).join(Stock,Stock.id==Outcome.stock_id)
        .where(Outcome.status=='resolved',Outcome.signal_date>=(now-timedelta(days=180)).date(),
               Outcome.window_days.in_([5,10]),Outcome.horizon_unit=='calendar_days')
        .order_by(Outcome.signal_date.desc(),Outcome.id.desc()).limit(30001)).all()
    if len(rows)>30000:
        return {'status':'bounded_read_exceeded','reason':'More than 30,000 observations; run a bounded offline study.',
                'candidates':[],'production_changed':False}
    obs=[]
    for o,score,issued,market,direction in rows:
        # Current Signal.confidence and Signal.ts are mutable. Never use them as a historical
        # prediction snapshot, or silently repair old rows with today's score.
        bound=(issued is not None and issued.date()==o.signal_date
               and str(getattr(direction,'value',direction))==o.signal_direction)
        obs.append(Observation(o.signal_id,o.symbol,str(getattr(market,'value',market)),
            str(getattr(o.horizon,'value',o.horizon)),o.signal_direction,o.window_days,
            float(score) if score is not None else float('nan'),issued if bound else None,
            o.resolved_at,o.is_correct,o.evidence_status))
    return {**compare(obs,now=now,include_provisional=include_provisional),
            'as_of':now.isoformat(),'score_basis':'frozen first BUY/SELL snapshot; current scores are never substituted'}
