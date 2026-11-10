"""Little RZY research variants: entry gates and exit rules replayed inside the engine.

Research only. Nothing here is imported by the live workflow, and no `Config` field is added, so
live streams and their config hash never change (review plan P0-4). A variant is the shipped
engine plus:

- GATES: a triggered setup is refused when a causal feature of it fails a rule. A refused setup
  frees the stream, so a later setup the baseline would have blocked can trade (a slice of
  baseline trades cannot show that).
- EXITS: alternatives to "exit at the next open after any close through the line".

Every feature uses only bars closed at or before the trigger bar's close. With no gates and no
exit options the engine is the baseline, trade for trade (tested).
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import datetime
from statistics import mean
from zoneinfo import ZoneInfo

from .rzy_evaluation import _FullHistory, _REASONS, _friction, simulate_control, BORROW_RATE

NY = ZoneInfo('America/New_York')
EXIT_DEFAULTS = dict(
    line_exit='close',      # 'close' (live) | 'buffer' | 'two_closes' | 'off'
    line_buffer_atr=0.25,   # 'buffer': exit only on a close beyond the line by this many ATR
    partial_at_r=None,      # sell `partial_fraction` at +kR and move the stop to breakeven plus costs
    partial_fraction=0.5,
    breakeven_at_r=None,    # move the stop to breakeven plus costs once +kR has been reached
    time_stop_bars=None,    # exit at the next open when, after this many bars, ...
    time_stop_min_mfe_r=0.5,  # ... the best excursion is still below this
    target_r=None,          # cap the target at k times the initial risk from the fill
    flat_at_close=False,    # intraday: exit at the close of a bar the runner marked session_last
)
RESEARCH_REASONS = dict(_REASONS, research_partial='partial', research_breakeven_stop='breakeven_stop',
                        research_time_stop='time_stop', research_line_buffer='line_buffer',
                        research_two_closes='line_two_closes', research_session_close='session_close')


class Series:
    """Values known as of a time: `at(t)` is the last value whose timestamp is <= t."""

    def __init__(self, times=(), values=()):
        self.times, self.values = list(times), list(values)

    def index(self, when):
        return bisect_right(self.times, when) - 1

    def at(self, when, back=0):
        k = self.index(when) - back
        return self.values[k] if k >= 0 else None

    def last(self, when, n):
        """The n most recent values as of `when`, or None when fewer exist."""
        k = self.index(when)
        return self.values[k - n + 1:k + 1] if k - n + 1 >= 0 else None


def _dt(value):
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


def time_bucket(closed_at):
    local = _dt(closed_at).astimezone(NY)
    minutes = local.hour * 60 + local.minute
    for name, upper in (('09:30-10:30', 630), ('10:30-12:00', 720), ('12:00-14:00', 840), ('14:00-15:00', 900), ('15:00-16:00', 960)):
        if minutes <= upper:
            return name
    return '15:00-16:00'


def trigger_features(engine, st, i):
    """Causal description of a setup at its trigger bar `i` (the bar whose close triggers)."""
    cfg, bar, ind = engine.config, engine.bar, engine.ind
    a, b, c = st['anchors']
    sign = 1 if st['direction'] == 'long' else -1
    t, it = bar(i), ind(i)
    atr = it['atr']
    when = _dt(t['closed_at'])
    ctx = engine.ctx
    vol = lambda lo, hi: mean(bar(k)['volume'] for k in range(lo, hi + 1)) if hi >= lo else None
    impulse_vol, pullback_vol = vol(a['index'], b['index']), vol(b['index'] + 1, c['index'])
    first = engine.s['offset']
    closes = lambda n: [bar(k)['close'] for k in range(i - n + 1, i + 1)] if i - n + 1 >= first else None
    def sma_side(n):
        values = closes(n)
        return None if values is None else sign * (t['close'] - mean(values)) > 0
    def momentum(n):
        return sign * (t['close'] / bar(i - n)['close'] - 1) if i - n >= first else None
    prior = [bar(k)['volume'] for k in range(max(first, i - 20), i)]
    risk = sign * (t['close'] - st['stop'])
    atr_b = ind(b['index'])['atr']
    impulse = sign * (b['price'] - a['price'])
    f = dict(
        vol_ratio=pullback_vol / impulse_vol if impulse_vol and pullback_vol is not None else None,
        trigger_vol_ratio=t['volume'] / mean(prior) if prior and mean(prior) > 0 else None,
        slope_atr=sign * (c['price'] - a['price']) / (c['index'] - a['index']) / atr,
        impulse_atr=impulse / atr_b if atr_b else None,
        retrace=sign * (b['price'] - c['price']) / impulse if impulse > 0 else None,
        stop_atr_plan=risk / atr, rr_plan=st['reward_risk'], d_atr=st['D'] / atr,
        cost_r_plan=(2 * t['close'] * _friction(cfg) + 2 * cfg.commission_per_share) / risk if risk > 0 else None,
        price=t['close'], bars_ac=c['index'] - a['index'], bars_ct=i - c['index'],
        ext_mid_atr=sign * (t['close'] - it['middle_band']) / atr if it['middle_band'] is not None else None,
        outer_band_c=st.get('outer_band_proximity_atr'),
        sma50=sma_side(50), sma200=sma_side(200),
        mom20=momentum(20), mom60=momentum(60), mom120=momentum(120),
        tod=time_bucket(when) if engine.timeframe != '1d' else 'not_applicable',
    )
    htf = ctx.get('htf')
    state = htf.at(when) if htf is not None else 'unknown'
    f['htf'] = ('unknown' if state in ('unknown', None) else 'neutral' if state == 'neutral'
                else 'aligned' if state == st['direction'] else 'opposed')
    regime = ctx.get('regime')
    state = regime.at(when) if regime is not None else None
    f['regime'] = None if state in (None, 'unknown') else (state == 'above') == (sign == 1)
    daily, market = ctx.get('daily'), ctx.get('market')
    # Top-down inputs from the symbol's DAILY closes as of the trigger (for a daily stream these
    # include the trigger bar itself, which has closed).
    f.update(d_sma50=None, d_sma200=None, d_mom126=None)
    if daily is not None:
        last = daily.at(when)
        for n in (50, 200):
            window = daily.last(when, n)
            if window:
                f[f'd_sma{n}'] = sign * (last - mean(window)) > 0
        base = daily.at(when, 126)
        if last and base:
            f['d_mom126'] = sign * (last / base - 1)
    f['rs63'] = None
    if daily is not None and market is not None:
        s0, s1, m0, m1 = daily.at(when, 63), daily.at(when), market.at(when, 63), market.at(when)
        if s0 and s1 and m0 and m1:
            f['rs63'] = sign * ((s1 / s0 - 1) - (m1 / m0 - 1))
    return f


def gate_reason(features, direction, gates):
    """First failed rule, or None. A missing feature fails its rule (unknown is not a pass)."""
    g, f = gates, features
    if g.get('long_only') and direction != 'long': return 'long_only'
    if g.get('htf_aligned') and f['htf'] != 'aligned': return 'htf_aligned'
    if g.get('htf_not_opposed') and f['htf'] == 'opposed': return 'htf_not_opposed'
    if g.get('regime') and f['regime'] is not True: return 'regime'
    if g.get('max_vol_ratio') is not None and not (f['vol_ratio'] is not None and f['vol_ratio'] < g['max_vol_ratio']): return 'max_vol_ratio'
    if g.get('max_cost_r') is not None and not (f['cost_r_plan'] is not None and f['cost_r_plan'] <= g['max_cost_r']): return 'max_cost_r'
    if g.get('min_price') is not None and f['price'] < g['min_price']: return 'min_price'
    if g.get('sma200') and f['sma200'] is not True: return 'sma200'
    if g.get('sma50') and f['sma50'] is not True: return 'sma50'
    if g.get('min_mom120') is not None and not (f['mom120'] is not None and f['mom120'] > g['min_mom120']): return 'min_mom120'
    if g.get('min_rs63') is not None and not (f['rs63'] is not None and f['rs63'] > g['min_rs63']): return 'min_rs63'
    if g.get('d_sma200') and f['d_sma200'] is not True: return 'd_sma200'
    if g.get('d_sma50') and f['d_sma50'] is not True: return 'd_sma50'
    if g.get('min_d_mom126') is not None and not (f['d_mom126'] is not None and f['d_mom126'] > g['min_d_mom126']): return 'min_d_mom126'
    if g.get('min_stop_atr') is not None and f['stop_atr_plan'] < g['min_stop_atr']: return 'min_stop_atr'
    if g.get('min_slope_atr') is not None and f['slope_atr'] < g['min_slope_atr']: return 'min_slope_atr'
    if g.get('skip_tod') and f['tod'] in g['skip_tod']: return 'skip_tod'
    return None


class ResearchEngine(_FullHistory):
    """The shipped engine plus optional gates and exit rules. `context` supplies the causal
    cross-stream inputs: htf (trend timeline), regime, daily and market (close series)."""

    def __init__(self, symbol, timeframe, config=None, gates=None, exits=None, context=None, indicators=None):
        super().__init__(symbol, timeframe, config)
        # Indicators depend only on the bars and the config, never on a variant: a runner may
        # pass the list a previous replay of the SAME segment produced (speed only).
        self._indicators = indicators
        self.gates = dict(gates or {})
        unknown = set(exits or {}) - set(EXIT_DEFAULTS)
        if unknown:
            raise ValueError(f'Unknown exit options: {sorted(unknown)}')
        self.x = dict(EXIT_DEFAULTS, **(exits or {}))
        if self.x['line_exit'] not in ('close', 'buffer', 'two_closes', 'off'):
            raise ValueError('Unknown line_exit')
        self.custom_exits = self.x != EXIT_DEFAULTS
        if self.custom_exits:
            self.LINE_EXIT = self.x['line_exit'] == 'close'
        self.ctx = context or {}
        self.blocked = {}
        self.trend_at = []

    def step(self, candle):
        events = super().step(candle)
        # The stream's trend after each bar, for context-matched controls.
        if len(self.trend_at) < self.s['offset'] + len(self.s['bars']):
            self.trend_at.append(self.s['trend'])
        return events

    def indicators(self):
        if self._indicators is not None:
            return self._indicators[self.s['offset'] + len(self.s['bars']) - 1]
        return super().indicators()

    def _active(self, i):
        ident = self.s['active']
        super()._active(i)
        pos = self.s['positions'].get(ident) if ident else None
        if not pos or pos['state'] != 'PENDING_ENTRY' or pos['trigger_index'] != i:
            return
        st = self.s['structures'][ident]
        st['features'] = trigger_features(self, st, i)
        reason = gate_reason(st['features'], st['direction'], self.gates) if self.gates else None
        if reason:
            # Refused before any order exists: the setup is consumed and the stream is free.
            del self.s['positions'][ident]
            st.update(state='REJECTED', reason=f'research_gate:{reason}')
            self.events = [e for e in self.events if not (e['event_type'] == 'TRIGGERED' and e['structure_id'] == ident)]
            self.blocked[reason] = self.blocked.get(reason, 0) + 1

    def _breakeven(self, pos, sign):
        return pos['fill'] + sign * (2 * self.config.commission_per_share + pos['fill'] * 2 * _friction(self.config))

    def _positions(self, i):
        if not self.custom_exits:
            return super()._positions(i)
        b, x, fr = self.bar(i), self.x, _friction(self.config)
        # Before the engine's own stop/target handling: a partial at +kR. Stop first, as the
        # engine assumes; after the partial the new breakeven stop is counted first as well.
        for ident, pos in self.s['positions'].items():
            if pos['state'] != 'OPEN' or pos.get('exit_pending') or x['partial_at_r'] is None or pos.get('partial_taken'):
                continue
            st = self.s['structures'][ident]; sign = 1 if st['direction'] == 'long' else -1
            if (b['low'] <= pos['stop']) if sign == 1 else (b['high'] >= pos['stop']):
                continue
            level = pos['fill'] + sign * x['partial_at_r'] * pos['risk0']
            if sign * (st['target'] - level) <= 0 or not ((b['high'] >= level) if sign == 1 else (b['low'] <= level)):
                continue
            self._exit(st, pos, level * (1 - sign * fr), x['partial_fraction'], 'research_partial', i)
            pos['partial_taken'] = True
            pos['stop'] = self._breakeven(pos, sign)
            if (b['low'] <= pos['stop']) if sign == 1 else (b['high'] >= pos['stop']):
                self._exit(st, pos, pos['stop'] * (1 - sign * fr), pos['remaining'], 'research_breakeven_stop', i)
        super()._positions(i)
        atr = self.ind(i)['atr']
        for ident, pos in self.s['positions'].items():
            if pos['state'] != 'OPEN':
                continue
            st = self.s['structures'][ident]; sign = 1 if st['direction'] == 'long' else -1
            if pos.get('entry_index') == i and 'risk0' not in pos:
                pos['risk0'] = sign * (pos['fill'] - st['stop']); pos['best'] = 0.
                if x['target_r'] is not None:
                    capped = pos['fill'] + sign * x['target_r'] * pos['risk0']
                    if sign * (st['target'] - capped) > 0:
                        st['target'] = capped
                        # The engine tested the entry bar against the uncapped target.
                        if (b['high'] >= capped) if sign == 1 else (b['low'] <= capped):
                            self._exit(st, pos, capped * (1 - sign * fr), pos['remaining'], 'primary_target', i)
                            continue
            if x['flat_at_close'] and b.get('session_last'):
                # Known from the clock, not from a later price: the session's final bar.
                self._exit(st, pos, b['close'] * (1 - sign * fr), pos['remaining'], 'research_session_close', i)
                continue
            if pos.get('exit_pending'):
                continue
            pos['best'] = max(pos['best'], sign * ((b['high'] if sign == 1 else b['low']) - pos['fill']) / pos['risk0'])
            if x['breakeven_at_r'] is not None and pos['best'] >= x['breakeven_at_r']:
                be = self._breakeven(pos, sign)
                pos['stop'] = max(pos['stop'], be) if sign == 1 else min(pos['stop'], be)
            beyond = sign * (b['close'] - self.line(st, i))
            pending = None
            if x['line_exit'] == 'buffer' and atr is not None and beyond < -x['line_buffer_atr'] * atr:
                pending = 'research_line_buffer'
            elif x['line_exit'] == 'two_closes':
                pos['closes_beyond'] = pos.get('closes_beyond', 0) + 1 if beyond < 0 else 0
                if pos['closes_beyond'] >= 2:
                    pending = 'research_two_closes'
            if pending is None and x['time_stop_bars'] is not None and i - pos['entry_index'] + 1 >= x['time_stop_bars'] \
                    and pos['best'] < x['time_stop_min_mfe_r']:
                pending = 'research_time_stop'
            if pending:
                pos['exit_pending'] = True; pos['pending_reason'] = pending


def run_stream(symbol, timeframe, bars, config, gates=None, exits=None, context=None, indicators=None):
    """Replay one contiguous segment. Returns (engine, closed trades with features)."""
    engine = ResearchEngine(symbol, timeframe, config, gates, exits, context, indicators)
    exit_reason = {}
    for bar in bars:
        for e in engine.step(bar):
            if e['event_type'] == 'PAPER_EXIT' and e.get('paper_state') == 'CLOSED':
                exit_reason[e['structure_id']] = e['reason']
    fr = _friction(config)
    trades = []
    for ident, pos in engine.s['positions'].items():
        st = engine.s['structures'][ident]
        if pos.get('cancelled') or pos['state'] != 'CLOSED':
            continue
        sign = 1 if st['direction'] == 'long' else -1
        risk = pos.get('risk0') or sign * (pos['fill'] - st['stop'])
        if risk <= 0:
            continue
        trig = pos['trigger_index']
        atr = engine.ind(trig)['atr']
        held = [bars[k] for k in range(pos['entry_index'], pos['exit_index'])]
        favourable = max([sign * ((b['high'] if sign == 1 else b['low']) - pos['fill']) for b in held] + [0.])
        adverse = max([sign * (pos['fill'] - (b['low'] if sign == 1 else b['high'])) for b in held] + [0.])
        reason = exit_reason.get(ident)
        if reason == 'structural_invalidation_next_open' and pos.get('pending_reason'):
            reason = pos['pending_reason']
        trades.append(dict(
            symbol=symbol, timeframe=timeframe, direction=st['direction'],
            sequence=st['sequence_number'], sequence_confidence=st['sequence_confidence'],
            signal_time=bars[trig]['closed_at'], entry_time=bars[pos['entry_index']]['time'],
            exit_time=bars[pos['exit_index']]['closed_at'],
            entry_index=pos['entry_index'], exit_index=pos['exit_index'], trigger_index=trig,
            holding_bars=pos['exit_index'] - pos['entry_index'] + 1,
            fill=pos['fill'], risk=risk, net=pos['net'], r=pos['net'] / risk, ret=pos['net'] / pos['fill'],
            cost_r=(2 * pos['fill'] * fr + pos['fees']) / risk,
            stop_atr=risk / atr if atr else None,
            target_atr=sign * (st['target'] - pos['fill']) / atr if atr else None,
            atr_pct=atr / bars[trig]['close'] if atr else None,
            mfe_r=max(favourable, pos['gross']) / risk, mae_r=max(adverse, -pos['gross']) / risk,
            exit_reason=RESEARCH_REASONS.get(reason, reason or 'unknown'), partial_taken=bool(pos.get('partial_taken')),
            **st.get('features', {})))
    return engine, trades


class ContextControls:
    """Random-timing twins drawn only from bars in the SAME context as the trade: the stream's
    own trend equals the trade direction and the close is on the same side of the stream's
    200-bar average as it was at the trade's trigger. Plain twins show whether an entry beats
    random timing; these show whether the trigger adds anything beyond "be in such a trend"."""

    def __init__(self, bars, engine, length=200):
        self.bars, self.engine = bars, engine
        total, sums = 0., []
        for b in bars:
            total += b['close']; sums.append(total)
        self.above = [None if k < length - 1 else b['close'] > (sums[k] - (sums[k - length] if k >= length else 0.)) / length
                      for k, b in enumerate(bars)]

    def draw(self, trade, config, rng, k=20, borrow=BORROW_RATE):
        sign = 1 if trade['direction'] == 'long' else -1
        want = trade.get('sma200')
        holding, inds, trend = trade['holding_bars'], self.engine.s['indicators'], self.engine.trend_at
        candidates = [t for t in range(len(self.bars) - holding) if inds[t]['atr'] and t != trade['trigger_index']
                      and trend[t] == trade['direction']
                      and (None if self.above[t] is None else self.above[t] == (sign == 1)) == want]
        out = []
        for t in rng.sample(candidates, min(k, len(candidates))):
            r = simulate_control(self.bars, inds, t, trade['direction'], trade['stop_atr'], trade['target_atr'], holding, config, borrow)
            if r is not None:
                out.append(r)
        return out
