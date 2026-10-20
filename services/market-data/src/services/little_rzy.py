"""LITTLE_RZY_INSPIRED_V1: deterministic completed-bar research and paper simulator.

No sessions, network, wall clock, model or orders. JSON checkpoints resume the same
state machine used by replay. Defaults are engineering assumptions, not fitted edges.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from statistics import mean, pstdev

VERSION = "LITTLE_RZY_INSPIRED_V1"


@dataclass(frozen=True)
class Config:
    bb_length: int = 20
    bb_std: float = 2.0
    atr_length: int = 14
    pivot_left: int = 7
    pivot_right: int = 7
    near_band_atr: float = .25
    touch_atr: float = .10
    stop_buffer_atr: float = .25
    min_impulse_atr: float = 1.5
    retracement_min: float = .20
    retracement_max: float = .80
    min_rr: float = 1.5
    max_armed_bars: int = 20
    preferred_sequences: tuple[int,...] = (1,2)
    exhaustion_min_sequence: int = 3
    exhaustion_lookback: int = 5
    partial_fraction: float = .5
    runner_multiple: float = 2.0
    sequence_filter: bool = True
    bollinger_context: bool = True
    no_chase_filter: bool = True
    partial_runner: bool = False
    structural_stop: bool = True
    commission_per_share: float = .005
    spread_bps: float = 2.0
    slippage_bps: float = 2.0
    min_bar_volume: float = 1.0
    reversal_window: int = 20

    def __post_init__(self):
        for key in ('bb_length', 'atr_length', 'pivot_left', 'pivot_right', 'max_armed_bars', 'reversal_window', 'exhaustion_min_sequence', 'exhaustion_lookback'):
            value = getattr(self, key)
            if type(value) is not int or not 1 <= value <= 1000:
                raise ValueError(f'{key} must be an integer in 1..1000')
        if not self.preferred_sequences or len(set(self.preferred_sequences))!=len(self.preferred_sequences) or any(type(n) is not int or n<1 for n in self.preferred_sequences):
            raise ValueError('preferred_sequences must contain distinct positive integers')
        object.__setattr__(self,'preferred_sequences',tuple(self.preferred_sequences))
        if not 0<self.partial_fraction<1 or self.runner_multiple<=1: raise ValueError('Invalid partial fraction or runner multiple')
        for key, value in asdict(self).items():
            if key=='preferred_sequences': continue
            if key in ('sequence_filter', 'bollinger_context', 'no_chase_filter', 'partial_runner', 'structural_stop'):
                if type(value) is not bool: raise ValueError(f'{key} must be boolean')
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
                raise ValueError(f'{key} must be finite and nonnegative')
        if not 0 <= self.retracement_min < self.retracement_max <= 1 or self.min_rr <= 0 or self.bb_std <= 0:
            raise ValueError('Invalid retracement, reward/risk or band configuration')

    @property
    def hash(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:20]


def stable(*parts):
    return hashlib.sha256('|'.join(str(p) for p in parts).encode()).hexdigest()


class Engine:
    # Bars/indicators are retained by absolute index with s['offset'] dropped from the front.
    # 1,200 covers the 1,000-bar chart grid plus warmup; live references are never dropped.
    RETAIN = 1200
    TRIM_BATCH = 100
    KEEP_PIVOTS = 12
    KEEP_STRUCTURES = 60
    KEEP_CLOSED_POSITIONS = 200

    def __init__(self, symbol, timeframe, config=None, checkpoint=None):
        self.config = config or Config()
        self.symbol, self.timeframe = symbol, timeframe
        self.s = checkpoint or dict(bars=[], indicators=[], pivots=[], trend='neutral', epoch=None,
            epoch_known=False, neutral_observed=False, seen_trend=False, sequence=0,
            active=None, structures={}, positions={}, reversal_context={}, last_structure=None)
        if self.s.get('config_hash', self.config.hash) != self.config.hash:
            raise ValueError('Checkpoint configuration mismatch')
        self.s['config_hash'] = self.config.hash
        self.s.setdefault('offset', 0)
        self.events = []

    def bar(self, i):
        return self.s['bars'][i - self.s['offset']]

    def ind(self, i):
        return self.s['indicators'][i - self.s['offset']]

    @property
    def last_index(self):
        return self.s['offset'] + len(self.s['bars']) - 1

    def emit(self, kind, structure=None, **extra):
        b = self.s['bars'][-1]
        structure = structure or {}
        payload = dict(strategy_version=VERSION, config_hash=self.config.hash, symbol=self.symbol,
            timeframe=self.timeframe, bar_time=b['time'], event_type=kind, direction=structure.get('direction'),
            position_action='WATCH_ONLY', signal_time=b['closed_at'], data_as_of=b['closed_at'],
            provider=b.get('provider', 'unknown'), delay=b.get('delay', 'unknown'), session=b.get('session', 'unknown'),
            price_basis=b.get('price_basis', 'unknown'), data_freshness=b.get('freshness', 'unknown'),
            trend_epoch_id=structure.get('trend_epoch_id', self.s['epoch']), structure_id=structure.get('id'),
            sequence_number=structure.get('sequence_number'), sequence_confidence=structure.get('sequence_confidence', 'unknown'),
            anchors=structure.get('anchors'), extreme=structure.get('extreme'), D=structure.get('D'), context_at_C=structure.get('context_at_C'),
            primary_target=structure.get('target'), runner_target=structure.get('runner'),
            stop=structure.get('stop'), planned_entry=structure.get('planned_entry'), reward_risk=structure.get('reward_risk'),
            state=structure.get('state'), passed_rules=[], failed_rules=[], rejection_reason=None,
            **{k: v for k, v in self.s['indicators'][-1].items() if k != 'tr'})
        payload.update(extra)
        payload['event_id'] = stable(VERSION, self.config.hash, self.symbol, self.timeframe,
            structure.get('id', ''), kind, b['closed_at'], extra.get('reason', ''))
        self.events.append(payload)
        return payload

    def line(self, structure, i):
        a, _, c = structure['anchors']
        return a['price'] + (c['price'] - a['price']) * (i - a['index']) / (c['index'] - a['index'])

    def finish_structure(self, st, state, reason):
        st['state'] = state
        st['reason'] = reason
        self.emit(state, st, rejection_reason=reason if state == 'REJECTED' else None, reason=reason, failed_rules=[reason] if state in ('REJECTED','INVALIDATED','EXPIRED') else [])
        if self.s['active'] == st['id']:
            self.s['active'] = None

    def indicators(self):
        bars, cfg = self.s['bars'], self.config
        b = bars[-1]
        prev = bars[-2]['close'] if len(bars) > 1 else b['close']
        tr = max(b['high'] - b['low'], abs(b['high'] - prev), abs(b['low'] - prev))
        atr = None
        total = self.s['offset'] + len(bars)
        if total == cfg.atr_length:
            atr = mean([v['tr'] for v in self.s['indicators']] + [tr])
        elif total > cfg.atr_length:
            atr = (self.s['indicators'][-1]['atr'] * (cfg.atr_length - 1) + tr) / cfg.atr_length
        mid = upper = lower = pct = width = None
        if total >= cfg.bb_length:
            closes = [x['close'] for x in bars[-cfg.bb_length:]]
            mid, sd = mean(closes), pstdev(closes)
            upper, lower = mid + cfg.bb_std * sd, mid - cfg.bb_std * sd
            pct = (b['close'] - lower) / (upper - lower) if upper > lower else None
            width = (upper - lower) / mid if mid > 0 else None
        return dict(tr=tr, atr=atr, middle_band=mid, upper_band=upper, lower_band=lower, percent_b=pct, bandwidth=width)

    def step(self, candle):
        """Consume one new completed candle; duplicates are a no-op, corrections fail closed."""
        b = dict(candle)
        for key in ('open', 'high', 'low', 'close', 'volume'):
            v = b.get(key)
            if isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) or (v < 0 if key == 'volume' else v <= 0):
                raise ValueError(f'Invalid {key}')
        if b.get('complete') is not True or not b.get('time') or not b.get('closed_at'):
            raise ValueError('Only timestamped completed bars are accepted')
        for key in ('time', 'closed_at'):
            stamp = datetime.fromisoformat(b[key])
            if stamp.tzinfo is None: raise ValueError('Candle timestamps require an explicit timezone')
            b[key] = stamp.astimezone(timezone.utc).isoformat()
        if b['high'] < max(b['open'], b['close'], b['low']) or b['low'] > min(b['open'], b['close']):
            raise ValueError('Inconsistent OHLC')
        if datetime.fromisoformat(b['closed_at']) < datetime.fromisoformat(b['time']):
            raise ValueError('Candle closes before it opens')
        if self.s['bars'] and b['time'] <= self.s['bars'][-1]['time']:
            old = next((x for x in reversed(self.s['bars']) if x['time'] == b['time']), None)
            if old == b: return []
            raise ValueError('Out-of-order or revised bar; explicit replay required')
        self.events = []
        self.s['bars'].append(b)
        self.s['indicators'].append(self.indicators())
        i = self.last_index
        self._positions(i)
        self._pivots(i)
        self._active(i)
        if self.s['structures']:
            self._exhaustion(list(self.s['structures'].values())[-1], i)
        self._reversal(i)
        self._trim()
        return self.events

    def _trim(self):
        """Bound checkpoint size without dropping anything a live object still references."""
        s = self.s
        keep = self.last_index + 1 - self.RETAIN
        if len(s['pivots']) >= 3: keep = min(keep, s['pivots'][-3]['index'])
        for p in s['positions'].values():
            if p['state'] != 'CLOSED': keep = min(keep, p['trigger_index'])
        if keep - s['offset'] >= self.TRIM_BATCH:
            drop = keep - s['offset']
            del s['bars'][:drop]; del s['indicators'][:drop]
            s['offset'] = keep
        if len(s['pivots']) > self.KEEP_PIVOTS: del s['pivots'][:-self.KEEP_PIVOTS]
        if len(s['positions']) > self.KEEP_CLOSED_POSITIONS:
            closed = [k for k, p in s['positions'].items() if p['state'] == 'CLOSED']
            for k in closed[:len(closed) - self.KEEP_CLOSED_POSITIONS]: del s['positions'][k]
        if len(s['structures']) > self.KEEP_STRUCTURES:
            live = {s['active']} | {c.get('structure_id') for c in s['reversal_context'].values()} | set(s['positions'])
            ids = list(s['structures'])
            for k in ids[:len(ids) - self.KEEP_STRUCTURES]:
                if k not in live: del s['structures'][k]

    def _pivots(self, i):
        cfg = self.config
        j = i - cfg.pivot_right
        if j < cfg.pivot_left: return
        window = [self.bar(k) for k in range(j - cfg.pivot_left, i + 1)]
        candidate = self.bar(j)
        # Earliest equal extreme wins; an outside candle that is both types is ambiguous.
        kinds = []
        for kind, field, sign in [('high', 'high', 1), ('low', 'low', -1)]:
            value = sign * candidate[field]
            if all(value >= sign*x[field] for x in window) and all(value > sign*x[field] for x in window[:cfg.pivot_left]):
                kinds.append((kind, field))
        if len(kinds) != 1: return
        kind, field = kinds[0]
        pivot = dict(kind=kind, price=candidate[field], index=j, pivot_time=candidate['time'], confirmed_at=self.bar(i)['closed_at'])
        pivots = self.s['pivots']
        if pivots and pivots[-1]['kind'] == kind:
            improves = pivot['price'] > pivots[-1]['price'] if kind == 'high' else pivot['price'] < pivots[-1]['price']
            if not improves: return
            pivots[-1] = pivot
        else: pivots.append(pivot)
        self.emit('PIVOT_CONFIRMED', pivot=pivot)
        highs = [x for x in pivots if x['kind'] == 'high'][-2:]
        lows = [x for x in pivots if x['kind'] == 'low'][-2:]
        trend = 'neutral'
        if len(highs) == len(lows) == 2:
            if highs[1]['price'] > highs[0]['price'] and lows[1]['price'] > lows[0]['price']: trend = 'long'
            if highs[1]['price'] < highs[0]['price'] and lows[1]['price'] < lows[0]['price']: trend = 'short'
            if trend == 'neutral': self.s['neutral_observed'] = True
        if trend != self.s['trend']:
            if self.s['active']:
                self.finish_structure(self.s['structures'][self.s['active']], 'EXPIRED', 'trend_epoch_changed')
            if trend != 'neutral':
                self.s['epoch_known'] = self.s['seen_trend'] or self.s['neutral_observed']
                self.s['seen_trend'] = True
                self.s['epoch'] = stable(self.symbol, self.timeframe, trend, self.bar(i)['closed_at'])
                self.s['sequence'], self.s['last_structure'] = 0, None
            else: self.s['epoch'] = None
            self.s['trend'] = trend
        if trend != 'neutral' and len(pivots) >= 3:
            a, b, c = pivots[-3:]
            if c['kind'] == ('high' if trend == 'short' else 'low'):
                self._detect(a, b, c, i)

    def _detect(self, a, b, c, i):
        cfg, bar, ind = self.config, self.bar, self.ind
        direction = self.s['trend']; sign = 1 if direction == 'long' else -1
        ident = stable(self.symbol, self.timeframe, direction, a['pivot_time'], b['pivot_time'], c['pivot_time'])
        if ident in self.s['structures']: return
        previous = self.s['last_structure']
        if previous and not (a['index'] > previous[0] and b['index'] > previous[1] and c['index'] > previous[2]): return
        impulse = sign*(b['price'] - a['price'])
        retrace = sign*(b['price'] - c['price']) / impulse if impulse > 0 else -1
        atr_b, atr_c = ind(b['index'])['atr'], ind(c['index'])['atr']
        if atr_b is None or atr_c is None or impulse < cfg.min_impulse_atr*atr_b or not cfg.retracement_min <= retrace <= cfg.retracement_max: return
        if sign*(c['price'] - a['price']) <= 0: return
        field = 'high' if direction == 'long' else 'low'
        extreme_i = max(range(a['index'], c['index']+1), key=lambda k: sign*bar(k)[field])
        if not a['index'] < extreme_i < c['index']: return
        st = dict(id=ident, direction=direction, anchors=[dict(a), dict(b), dict(c)], trend_epoch_id=self.s['epoch'], state='DETECTED', armed_index=i,
            sequence_confidence='known' if self.s['epoch_known'] else 'unknown', extreme=dict(index=extreme_i,time=bar(extreme_i)['time'],price=bar(extreme_i)[field]))
        distance = sign*(bar(extreme_i)[field] - self.line(st, extreme_i))
        target = bar(extreme_i)[field] + sign*distance
        if distance <= 0 or target <= 0: return
        self.s['sequence'] += 1
        self.s['last_structure'] = [a['index'], b['index'], c['index']]
        st.update(D=distance, target=target, runner=bar(extreme_i)[field]+sign*cfg.runner_multiple*distance, sequence_number=self.s['sequence'])
        cind,cbar=ind(c['index']),bar(c['index'])
        st['context_at_C']={k:cind[k] for k in ('atr','middle_band','upper_band','lower_band','percent_b','bandwidth')}
        self.s['structures'][ident] = st
        self.emit('DETECTED', st)
        if cfg.partial_runner and st['runner']<=0:
            self.finish_structure(st,'REJECTED','runner_target_not_positive'); return
        mid = cind['middle_band']
        if mid is None: self.finish_structure(st, 'REJECTED', 'insufficient_bollinger_history'); return
        st['outer_band_proximity_atr'] = (cbar['high']-cind['upper_band'])/atr_c if direction=='short' and atr_c>0 else (cind['lower_band']-cbar['low'])/atr_c if atr_c>0 else None
        # Increment sequence BEFORE context/sequence rejection, once per advancing structure.
        if cfg.bollinger_context and (cbar['high'] < mid-cfg.near_band_atr*atr_c if direction=='short' else cbar['low'] > mid+cfg.near_band_atr*atr_c):
            self.finish_structure(st,'REJECTED','bollinger_pullback_context'); return
        for j in range(c['index']+1, i+1):
            if sign*(bar(j)['close']-self.line(st,j)) < 0:
                self.finish_structure(st,'INVALIDATED','invalidated_before_confirmation'); return
            if sign*(bar(j)[field]-target) >= 0:
                self.finish_structure(st,'COMPLETED','target_before_arming_no_trade'); return
        if cfg.sequence_filter and st['sequence_confidence']=='known' and st['sequence_number'] not in cfg.preferred_sequences:
            self.finish_structure(st,'REJECTED','sequence_filter'); return
        if self.s['active']:
            self.finish_structure(self.s['structures'][self.s['active']],'EXPIRED','superseded_by_new_structure')
        st['state']='ARMED'; self.s['active']=ident
        self.emit('ARMED',st,passed_rules=['confirmed_pivots','trend','impulse','retracement','frozen_measurement','preconfirmation_replay'])
        self._exhaustion(st, i)

    def _active(self, i):
        ident=self.s['active']
        if not ident: return
        st=self.s['structures'][ident]; cfg=self.config
        b, prev=self.bar(i), self.bar(i-1)
        ind=self.ind(i); sign=1 if st['direction']=='long' else -1
        line=self.line(st,i)
        if sign*(b['close']-line)<0:
            context=self.s['reversal_context'].get(st['direction'],{})
            if context.get('structure_id')==ident: context['invalidated_index']=i
            self.finish_structure(st,'INVALIDATED','close_beyond_structure_line'); return
        if sign*((b['high'] if sign==1 else b['low'])-st['target'])>=0:
            self.s['reversal_context'][st['direction']]=dict(context_index=i,structure_id=ident)
            self.finish_structure(st,'COMPLETED','target_before_entry_no_trade'); return
        if i-st['armed_index']>cfg.max_armed_bars:
            self.finish_structure(st,'EXPIRED','armed_lifetime'); return
        if i<=st['armed_index'] or ind['atr'] is None or ind['percent_b'] is None: return
        tolerance=cfg.touch_atr*ind['atr']
        touch=b['low']<=line+tolerance and b['high']>=line-tolerance
        confirm=sign*(b['close']-line)>0 and sign*(b['close']-b['open'])>0 and (b['close']>prev['high'] if sign==1 else b['close']<prev['low'])
        if not touch or not confirm: return
        if cfg.no_chase_filter and (b['close']>ind['upper_band'] if sign==1 else b['close']<ind['lower_band']):
            self.emit('ENTRY_BLOCKED',st,failed_rules=['NO_CHASE_FILTER'],rejection_reason='NO_CHASE_FILTER',trendline_value=line); return
        terms=planning_reference(st,b,i,ind,cfg)
        stop,rr=terms['stop'],terms['reward_risk']
        st.update(stop=stop,planned_entry=b['close'],reward_risk=rr)
        if stop<=0 or rr<cfg.min_rr:
            self.finish_structure(st,'REJECTED','planned_reward_risk'); return
        if any(p['state'] in ('OPEN','PARTIAL','PENDING_ENTRY') for p in self.s['positions'].values()):
            self.finish_structure(st,'REJECTED','overlapping_paper_position'); return
        st['state']='TRIGGERED'; self.s['active']=None
        self.s['positions'][ident]=dict(state='PENDING_ENTRY',trigger_index=i,trigger_volume=b['volume'],remaining=1.,gross=0.,fees=0.)
        self.emit('TRIGGERED',st,position_action='OPEN_LONG' if sign==1 else 'OPEN_SHORT',trendline_value=line,
            passed_rules=['confirmed_structure','line_touch','directional_close','previous_bar_break','planned_reward_risk'],paper_state='PENDING_ENTRY')

    def _positions(self, i):
        b=self.bar(i); cfg=self.config
        friction=(cfg.spread_bps/2+cfg.slippage_bps)/10000
        for ident,pos in self.s['positions'].items():
            if pos['state']=='CLOSED': continue
            st=self.s['structures'][ident]; sign=1 if st['direction']=='long' else -1
            if pos['state']=='PENDING_ENTRY':
                if i<=pos['trigger_index']: continue
                fill=b['open']*(1+sign*friction)
                risk=sign*(fill-st['stop']); reward=sign*(st['target']-fill)
                failures=[]
                if st['trend_epoch_id']!=self.s['epoch']: failures.append('trend_epoch_changed')
                if not b.get('execution_eligible',False): failures.append('data_basis_or_freshness_unverified')
                # At the next open, only the trigger candle's completed volume is known.
                trigger_volume=pos.get('trigger_volume')
                if trigger_volume is None: trigger_volume=self.bar(pos['trigger_index'])['volume']
                if trigger_volume<cfg.min_bar_volume: failures.append('liquidity_requirement')
                if sign==-1 and (b.get('shortable') is not True or b.get('borrow_rate_annual') is None): failures.append('shortability_or_borrow_cost_unverified')
                borrow=b.get('borrow_rate_annual')
                if sign==-1 and (not isinstance(borrow,(float,int)) or not math.isfinite(borrow) or borrow<0):
                    failures.append('borrow_cost_invalid')
                total_cost=fill*friction+cfg.commission_per_share*2+(fill*borrow/365 if sign==-1 and isinstance(borrow,(float,int)) and math.isfinite(borrow) and borrow>=0 else 0)
                rr=(reward-total_cost)/(risk+total_cost) if risk>0 else 0
                if risk<=0 or reward<=0 or rr<cfg.min_rr: failures.append('actual_fill_reward_risk_or_gap')
                if failures:
                    pos.update(state='CLOSED',cancelled=True,reason=','.join(failures))
                    st.update(state='REJECTED',reason=','.join(failures))
                    self.emit('ENTRY_CANCELLED',st,failed_rules=failures,rejection_reason=','.join(failures),actual_paper_fill=None); continue
                pos.update(state='OPEN',fill=fill,entry_index=i,entry_time=b['time'],borrow_rate_annual=borrow if sign==-1 else 0.,stop=st['stop'],fees=cfg.commission_per_share)
                self.emit('PAPER_FILL',st,actual_paper_fill=fill,paper_fill_time=b['time'],paper_state='OPEN',reward_risk=rr,position_action='OPEN_LONG' if sign==1 else 'OPEN_SHORT')
            if pos['state'] not in ('OPEN','PARTIAL'): continue
            if pos.get('exit_pending'):
                self._exit(st,pos,b['open']*(1-sign*friction),pos['remaining'],'structural_invalidation_next_open',i); continue
            stop=pos['stop']; target=st['runner'] if pos['state']=='PARTIAL' else st['target']
            stop_hit=b['low']<=stop if sign==1 else b['high']>=stop
            target_hit=b['high']>=target if sign==1 else b['low']<=target
            if stop_hit:
                raw=min(b['open'],stop) if sign==1 else max(b['open'],stop)
                self._exit(st,pos,raw*(1-sign*friction),pos['remaining'],'protective_stop_conservative_order',i); continue
            if target_hit:
                if cfg.partial_runner and pos['state']=='OPEN':
                    self._exit(st,pos,target*(1-sign*friction),cfg.partial_fraction,'primary_partial',i)
                    pos['state']='PARTIAL'; pos['partial_done']=True; pos['stop']=pos['fill']+sign*(2*cfg.commission_per_share+pos['fill']*2*friction)
                    # Unknown order after partial: count the new protective stop first.
                    if b['low']<=pos['stop'] if sign==1 else b['high']>=pos['stop']:
                        self._exit(st,pos,pos['stop']*(1-sign*friction),pos['remaining'],'partial_stop_conservative_order',i)
                    elif b['high']>=st['runner'] if sign==1 else b['low']<=st['runner']:
                        self._exit(st,pos,st['runner']*(1-sign*friction),pos['remaining'],'runner_target',i)
                else: self._exit(st,pos,target*(1-sign*friction),pos['remaining'],'primary_target' if pos['state']=='OPEN' else 'runner_target',i)
            if pos['state']!='CLOSED' and sign*(b['close']-self.line(st,i))<0:
                pos['exit_pending']=True
                context=self.s['reversal_context'].get(st['direction'],{})
                if context.get('structure_id')==ident: context['invalidated_index']=i
                self.emit('EXIT_PENDING',st,position_action='CLOSE_LONG' if sign==1 else 'COVER_SHORT',paper_state=pos['state'])

    def _exit(self,st,pos,price,quantity,reason,i):
        sign=1 if st['direction']=='long' else -1
        pos['gross']+=sign*(price-pos['fill'])*quantity
        pos['fees']+=self.config.commission_per_share*quantity
        if sign==-1:
            start=datetime.fromisoformat(pos.get('entry_time') or self.bar(pos['entry_index'])['time'])
            end=datetime.fromisoformat(self.bar(i)['closed_at'])
            rate=pos.get('borrow_rate_annual')
            if rate is None: rate=self.bar(pos['entry_index']).get('borrow_rate_annual') or 0.
            pos['fees']+=pos['fill']*quantity*rate*max(0,(end-start).total_seconds())/(365*86400)
        pos['remaining']=max(0.,pos['remaining']-quantity)
        if reason=='primary_partial':
            self.s['reversal_context'][st['direction']]=dict(context_index=i,structure_id=st['id'])
        if pos['remaining']==0:
            pos.update(state='CLOSED',exit_index=i,net=pos['gross']-pos['fees'])
            # A runner stopped after the primary target was banked still completed the measured move.
            st['state']='COMPLETED' if 'target' in reason or pos.get('partial_done') else 'INVALIDATED'
            if 'target' in reason:
                self.s['reversal_context'][st['direction']]=dict(context_index=i,structure_id=st['id'])
        self.emit('PAPER_EXIT',st,actual_paper_fill=pos['fill'],exit_price=price,exit_quantity=quantity,reason=reason,
            paper_state=pos['state'],net_pnl_per_unit=pos['gross']-pos['fees'],position_action='CLOSE_LONG' if sign==1 else 'COVER_SHORT')

    def _exhaustion(self,st,i):
        if st['sequence_number']<self.config.exhaustion_min_sequence or st.get('exhaustion_emitted'): return
        # "Current structure" means the current trend epoch; an old epoch's last structure is not.
        if st.get('trend_epoch_id')!=self.s['epoch']: return
        older=[s for s in self.s['structures'].values() if s['direction']==st['direction'] and s['id']!=st['id'] and s['trend_epoch_id']==st['trend_epoch_id']]
        if not older or st['D']>=older[-1]['D']: return
        sign=1 if st['direction']=='long' else -1; bar=self.bar; ind=self.ind
        band='upper_band' if sign==1 else 'lower_band'
        previous=any(ind(j)[band] is not None and sign*(bar(j)['close']-ind(j)[band])>0 for j in range(max(self.s['offset'],i-self.config.exhaustion_lookback),i))
        if previous and ind(i)[band] is not None and sign*(bar(i)['close']-ind(i)[band])<=0:
            self.s['reversal_context'][st['direction']]=dict(context_index=i,structure_id=st['id'])
            st['exhaustion_emitted']=True
            self.emit('EXHAUSTION_WATCH',st,reason='research_heuristic_sequence_outer_band_smaller_D')

    def _reversal(self,i):
        if i==0: return
        for direction,context in self.s['reversal_context'].items():
            origin=context.get('context_index'); invalid=context.get('invalidated_index')
            st=self.s['structures'].get(context.get('structure_id'))
            if origin is not None and st and i>origin and invalid is None:
                direction_sign=1 if direction=='long' else -1
                if direction_sign*(self.bar(i)['close']-self.line(st,i))<0:
                    context['invalidated_index']=i; invalid=i
            if origin is None or invalid is None or invalid<origin or i<=invalid or i-origin>self.config.reversal_window or context.get('emitted'): continue
            before,now=self.ind(i-1)['middle_band'],self.ind(i)['middle_band']
            if before is None or now is None: continue
            sign=1 if direction=='short' else -1
            if sign*(self.bar(i-1)['close']-before)<=0 and sign*(self.bar(i)['close']-now)>0:
                context['emitted']=True
                self.emit('REVERSAL_WATCH',self.s['structures'].get(context['structure_id']),direction='long' if sign==1 else 'short',reason='midline_cross_after_completed_or_exhausted_invalidated_structure')


def planning_reference(st,bar,index,ind,cfg):
    """Reused numeric terms; caller must still prove the actual close trigger or next-open fill."""
    a,_,c=st['anchors'];line=a['price']+(c['price']-a['price'])*(index-a['index'])/(c['index']-a['index'])
    sign=1 if st['direction']=='long' else -1
    atr=ind.get('atr')
    if atr is None:return dict(stop=None,reward_risk=None,entry_reference=bar['close'],trendline=line)
    base=(min(c['price'],bar['low'],line) if sign==1 else max(c['price'],bar['high'],line)) if cfg.structural_stop else line
    stop=base-sign*cfg.stop_buffer_atr*atr
    risk=sign*(bar['close']-stop);reward=sign*(st['target']-bar['close'])
    return dict(stop=stop,reward_risk=reward/risk if risk>0 else 0,entry_reference=bar['close'],trendline=line)


def replay(symbol,timeframe,bars,config=None):
    engine=Engine(symbol,timeframe,config)
    events=[]
    for bar in bars: events.extend(engine.step(bar))
    return engine,events
