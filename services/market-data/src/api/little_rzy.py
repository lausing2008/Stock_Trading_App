"""Authenticated RZY research, explicit opt-in preferences and read-only paper replay."""
from dataclasses import asdict
from datetime import datetime,timezone
from fastapi import APIRouter,Depends,HTTPException,Query
from pydantic import BaseModel,Field,field_validator
from sqlalchemy import select
from db import get_session,AlertPreference
from .auth import get_current_user
from ..services import rzy_workflow as W
from ..services.rzy_models import RzyPreference,RzyStream
from ..services.little_rzy import Config,VERSION
from ..services.rzy_backtest import replay_segments

router=APIRouter(prefix='/stocks/rzy',tags=['little-rzy'])

class Preferences(BaseModel):
    pattern_email: bool=False
    morning_digest: bool=False
    timeframes: list[str]=Field(default_factory=lambda:['5m','15m','1h'],max_length=4,min_length=1)
    @field_validator('timeframes')
    @classmethod
    def valid_timeframes(cls,value):
        if len(set(value))!=len(value) or any(v not in W.MINUTES for v in value): raise ValueError('Choose unique supported timeframes: 5m,15m,1h,1d')
        return value

@router.get('/preferences')
def preferences(user=Depends(get_current_user),session=Depends(get_session)):
    row=session.get(RzyPreference,user.id)
    return dict(pattern_email=W.eligible(session,user,row,'little_rzy'),morning_digest=W.eligible(session,user,row,'short_term_digest'),timeframes=row.timeframes if row else ['5m','15m','1h'],
        schedule='20 minutes before US regular open (normally 09:10 America/New_York)',scope='my US watchlist',strategy_version=VERSION)

@router.put('/preferences')
def save_preferences(body:Preferences,user=Depends(get_current_user),session=Depends(get_session)):
    if (body.pattern_email or body.morning_digest) and not (user.email or '').strip(): raise HTTPException(400,'Add an email address before enabling email')
    row=session.get(RzyPreference,user.id)
    if row is None: row=RzyPreference(user_id=user.id); session.add(row)
    for kind,enabled in [('little_rzy',body.pattern_email),('short_term_digest',body.morning_digest)]:
        pref=session.scalar(select(AlertPreference).where(AlertPreference.user_id==user.id,AlertPreference.alert_type==kind))
        if pref is None: pref=AlertPreference(user_id=user.id,alert_type=kind); session.add(pref)
        pref.enabled=enabled
    row.timeframes=body.timeframes; row.updated_at=datetime.now(timezone.utc).replace(tzinfo=None)
    session.commit()
    return preferences(user,session)

@router.get('/digest')
def digest(user=Depends(get_current_user),session=Depends(get_session)):
    return W.digest_preview(session,user.id)

@router.get('/paper')
def paper(user=Depends(get_current_user),session=Depends(get_session)):
    symbols=W.watchlist_symbols(session,user.id)
    streams=session.scalars(select(RzyStream).where(RzyStream.symbol.in_(symbols))).all() if symbols else []
    return dict(strategy_version=VERSION,note='Separate deterministic research paper ledger, one-share units. No live broker execution or changes to existing portfolio balances.',
        positions=[dict(symbol=row.symbol,timeframe=row.timeframe,config_hash=row.config_hash,generation=row.checkpoint.get('generation',1),structure_id=k,**p)
                   for row in streams if row.config_hash==Config().hash for k,p in row.checkpoint.get('positions',{}).items()][-100:],
        provenance='Current configuration only; each row names its stream generation. A rebuilt stream replaces its open-position cache; superseded history is in the event log.')

class ReplayIn(BaseModel):
    symbol: str=Field(pattern=r'^[A-Z0-9.^\-]{1,20}$')
    timeframe: str=Field(pattern=r'^(5m|15m|1h|1d)$')
    bars:list[dict]=Field(max_length=6000,min_length=1)
    config:dict=Field(default_factory=dict)

@router.post('/replay')
def replay_endpoint(body:ReplayIn,_user=Depends(get_current_user)):
    try:
        config=Config(**body.config)
        segments=replay_segments(body.symbol,body.timeframe,body.bars,config)
    except (ValueError,TypeError,KeyError) as exc: raise HTTPException(400,str(exc))
    return dict(strategy_version=VERSION,config=asdict(config),config_hash=config.hash,
        gap_policy='Bars are split at calendar-aware gaps; each contiguous segment is replayed by a fresh engine, as live evaluation does.',
        segments=[dict(first=s['first'],last=s['last'],bars=s['bars']) for s in segments],
        events=[dict(e,segment=i) for i,s in enumerate(segments) for e in s['events']],
        positions={f'{i}:{k}':p for i,s in enumerate(segments) for k,p in s['positions'].items()})

@router.get('/{symbol}')
def review(symbol:str,timeframe:str=Query('5m',pattern=r'^(5m|15m|1h|1d)$'),include_superseded:bool=False,_user=Depends(get_current_user),session=Depends(get_session)):
    if len(symbol)>20: raise HTTPException(400,'Invalid symbol')
    try:
        result=W.evaluate(session,symbol.upper(),timeframe)
        if include_superseded: result['superseded_events']=W.superseded_events(session,symbol.upper(),timeframe)
        return result
    except (ValueError,TypeError) as exc: return dict(strategy_version=VERSION,status='data_rejected',reason=str(exc),events=[],structures=[],paper_positions=[])
