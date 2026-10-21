"""Chronological, untuned research reports for the same streaming RZY engine."""
from dataclasses import asdict,replace
from math import sqrt
from statistics import mean
from datetime import datetime
from .little_rzy import Engine,Config


class _Ledger(Engine):
    # Reports need every position ever taken; the live retention caps (which prune closed
    # positions from the operational cache) must never shorten a backtest's ledger (RZA-03).
    RETAIN = 10 ** 9
    KEEP_PIVOTS = 10 ** 9
    KEEP_STRUCTURES = 10 ** 9
    KEEP_CLOSED_POSITIONS = 10 ** 9


def split_segments(bars,timeframe):
    """Calendar-aware contiguous segments, the same contract as live evaluation (RZA-04)."""
    from common.market_calendar import session_bounds
    from .rzy_data import break_indices
    if not bars: return []
    norm=[dict(b,time=datetime.fromisoformat(b['time']).isoformat(),closed_at=datetime.fromisoformat(b['closed_at']).isoformat()) for b in bars]
    if timeframe not in ('5m','15m','1h','1d') or any(datetime.fromisoformat(b['time']).tzinfo is None for b in norm):
        return [bars]   # the engine itself rejects naive timestamps / unsupported frames
    cuts=[0]+break_indices(norm,timeframe,session_bounds)+[len(bars)]
    return [bars[a:b] for a,b in zip(cuts,cuts[1:]) if b>a]


def replay_segments(symbol,timeframe,bars,config=None):
    out=[]
    for seg in split_segments(bars,timeframe):
        engine=_Ledger(symbol,timeframe,config); events=[]
        for bar in seg: events.extend(engine.step(bar))
        out.append(dict(first=seg[0]['time'],last=seg[-1]['time'],bars=len(seg),events=events,positions=engine.s['positions']))
    return out


def run(symbol,timeframe,bars,config=None):
    events=[]; equity=[]; exposed=0; trades=[]; unresolved=cancelled=0; carried=0.; offset=0; engine=None; segments=0
    for seg in split_segments(bars,timeframe):
        segments+=1
        engine=_Ledger(symbol,timeframe,config)
        for j,bar in enumerate(seg):
            i=offset+j
            events.extend(engine.step(bar))
            value=carried; any_exposure=False
            for ident,p in engine.s['positions'].items():
                if p.get('cancelled') or 'fill' not in p: continue
                st=engine.s['structures'][ident]; sign=1 if st['direction']=='long' else -1
                value+=p['gross']-p['fees']+sign*(bar['close']-p['fill'])*p['remaining']
                if p['entry_index']<=j<=p.get('exit_index',j): any_exposure=True
            exposed+=any_exposure; equity.append(value)
        for ident,p in engine.s['positions'].items():
            if p.get('cancelled'): cancelled+=1; continue
            if p['state']!='CLOSED': unresolved+=1; continue
            st=engine.s['structures'][ident]
            trades.append(dict(net=p['net'],direction=st['direction'],sequence=st['sequence_number'],sequence_confidence=st['sequence_confidence'],
                timeframe=timeframe,regime=f"{st['direction']}_trend",entry_index=offset+p['entry_index'],exit_index=offset+p['exit_index']))
        # Only realised P&L carries into the next segment; positions still open at a gap are
        # censored (counted as unresolved), never marked into later equity.
        carried+=sum(p['net'] for p in engine.s['positions'].values() if not p.get('cancelled') and p['state']=='CLOSED')
        offset+=len(seg)
    metrics=summarize(trades)
    peak=0.; dd=0.
    for x in equity: peak=max(peak,x); dd=max(dd,peak-x)
    metrics.update(maximum_mark_to_market_drawdown_per_unit=dd,exposure_bar_fraction=exposed/len(bars) if bars else None,
        unresolved_or_boundary_censored=unresolved,cancelled_entries=cancelled,contiguous_segments=segments)
    groups={}
    for key in ('direction','timeframe','sequence','sequence_confidence','regime'):
        groups[key]={str(value):summarize([t for t in trades if t[key]==value]) for value in sorted({t[key] for t in trades},key=str)}
    cfg=engine.config if engine else (config or Config())
    return dict(metrics=metrics,groups=groups,events=events,trades=trades,config=asdict(cfg),config_hash=cfg.hash,
        units='One-share research units; not returns on an actual portfolio',
        caveats=['No live execution','Unresolved trades are reported, not silently scored as wins or losses',
        'Intervals describe observed hit rate, not future performance; dependence and small samples can invalidate simple binomial interpretations',
        'Regime is the confirmed structural trend, not a separately verified macro regime'])


def summarize(trades):
    n=len(trades); wins=[t['net'] for t in trades if t['net']>0]; losses=[t['net'] for t in trades if t['net']<=0]
    rate=len(wins)/n if n else None; interval=None
    if n:
        z=1.96; den=1+z*z/n; center=(rate+z*z/(2*n))/den
        half=z*sqrt(rate*(1-rate)/n+z*z/(4*n*n))/den
        interval=[max(0,center-half),min(1,center+half)]
    return dict(trade_count=n,net_expectancy_per_unit=mean([t['net'] for t in trades]) if n else None,
        profit_factor=sum(wins)/abs(sum(losses)) if sum(losses)<0 else None,
        win_rate=rate,win_rate_wilson_95=interval,average_win=mean(wins) if wins else None,average_loss=mean(losses) if losses else None,
        conclusion='insufficient evidence' if n<30 else 'descriptive results only; no profitable-edge claim')


def compare(symbol,timeframe,bars,config=None,folds=3):
    cfg=config or Config()
    if len(bars)<max(60,cfg.bb_length+cfg.pivot_left+cfg.pivot_right): raise ValueError('At least 60 completed candles are required for chronological evaluation')
    if not 2<=folds<=10: raise ValueError('folds must be 2..10')
    variants={'baseline':cfg,'no_bollinger_context':replace(cfg,bollinger_context=False),
        'no_chase_filter':replace(cfg,no_chase_filter=False),'unrestricted_sequence':replace(cfg,sequence_filter=False),
        'partial_runner':replace(cfg,partial_runner=True),'line_stop':replace(cfg,structural_stop=False)}
    cut=int(len(bars)*.7)
    result={}
    for name,variant in variants.items():
        training=run(symbol,timeframe,bars[:cut],variant); held_out=run(symbol,timeframe,bars[cut:],variant)
        windows=[]
        for fold in range(1,folds):
            boundary=len(bars)*fold//folds; end=len(bars)*(fold+1)//folds
            train=run(symbol,timeframe,bars[:boundary],variant); test=run(symbol,timeframe,bars[boundary:end],variant)
            windows.append(dict(train_end=bars[boundary-1]['closed_at'],test_start=bars[boundary]['time'],test_end=bars[end-1]['closed_at'],train=train['metrics'],test=test['metrics']))
        result[name]=dict(config=asdict(variant),config_hash=variant.hash,training=training['metrics'],held_out=held_out['metrics'],held_out_groups=held_out['groups'],walk_forward=windows)
    return dict(strategy_version='LITTLE_RZY_INSPIRED_V1',symbol=symbol,timeframe=timeframe,variants=result,
        partition_policy='Chronological 70/30 plus expanding walk-forward. Each partition starts a fresh engine; no positions or outcome labels cross boundaries. Warmup and unknown initial sequences are retained. No parameter tuning or best-variant selection.',
        costs='Commissions, half-spread and slippage on entry/exit; recorded borrow rate over elapsed holding time for shorts. Planned short R/R also reserves one day of borrow cost.',
        limitations=['Requires declared completed, consistent-basis bars and verified execution/borrow inputs; otherwise paper entries cancel',
        'No measured edge is established by synthetic fixtures','No assumption of favorable intrabar ordering; stops before targets',
        'Unrealized borrow charges accrue in final trade fees; the displayed MTM drawdown can understate interim borrow costs'])
