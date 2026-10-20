"""LITTLE_RZY_INSPIRED_V1 evaluation: real trades from the shipped engine vs matched controls.

Pure functions only (no database, network or clock), so the same code runs in tests and on a
saved input-bar snapshot.

Design:
- Each contiguous bar segment is replayed through the production `Engine` in time order; fills
  are next-open with configured costs and stop-before-target ordering (the engine's own rules).
- Execution inputs that stored prices lack are supplied as EXPLICIT research assumptions and
  recorded on every result; they are never presented as verified data.
- Unit of account is R: net P&L per share divided by initial risk (fill to frozen stop).
- Each trade gets matched random-timing controls on the same segment: same direction, the same
  stop and target distances in ATR at the control's own entry, the same costs and borrow rate,
  and a time exit after the real trade's holding length. A control is drawn only where that
  whole window is observable (RZA-10); its borrow accrues only while it is actually open (RZA-09).
- Edge intervals are bootstrapped by SYMBOL (trades of one symbol are dependent); the
  minimum detectable edge says how large an effect the sample could have revealed.
- Nothing is tuned or selected; every variant is reported. The development/held-out split is
  chronological and descriptive: no parameter was fitted on either period.
"""
from __future__ import annotations

import math
import random
from dataclasses import asdict, replace
from datetime import datetime
from statistics import mean, median, pstdev

from .little_rzy import Engine, Config

BORROW_RATE = 0.005
ASSUMPTIONS = dict(
    execution_eligible='assumed True for research (stored prices carry no verified provider delay)',
    shortable='assumed True for research; long-only results reported separately',
    borrow_rate_annual=BORROW_RATE,
    note='Assumptions make paper fills possible; they are not verified execution or borrow data.',
)
HELD_OUT_FRACTION = 0.30


class _FullHistory(Engine):
    # Evaluation needs every bar's indicators and every position; it never persists a checkpoint.
    RETAIN = 10 ** 9
    KEEP_PIVOTS = 10 ** 9
    KEEP_STRUCTURES = 10 ** 9
    KEEP_CLOSED_POSITIONS = 10 ** 9


class _FullHistoryStopTargetOnly(_FullHistory):
    # Research variant only: an open position exits at its stop or target, never on a close
    # through the line. Entry rules and pre-entry invalidation are unchanged.
    LINE_EXIT = False


def research_bars(bars, borrow=BORROW_RATE):
    return [dict(b, execution_eligible=True, shortable=True, borrow_rate_annual=borrow) for b in bars]


_REASONS = {'primary_target': 'target', 'runner_target': 'runner_target',
            'protective_stop_conservative_order': 'stop', 'partial_stop_conservative_order': 'stop_after_partial',
            'structural_invalidation_next_open': 'line_invalidation'}


def _friction(config):
    return (config.spread_bps / 2 + config.slippage_bps) / 10000


def run_segment(symbol, timeframe, bars, config, engine_cls=_FullHistory):
    """Replay one contiguous segment. Returns (engine, closed trades, cancellations, unresolved)."""
    engine = engine_cls(symbol, timeframe, config)
    exit_reason = {}
    for bar in bars:
        for e in engine.step(bar):
            if e['event_type'] == 'PAPER_EXIT' and e.get('paper_state') == 'CLOSED':
                exit_reason[e['structure_id']] = e['reason']
    fr = _friction(config)
    trades, cancelled, unresolved = [], [], []
    for ident, pos in engine.s['positions'].items():
        st = engine.s['structures'][ident]
        if pos.get('cancelled'):
            cancelled.append(pos.get('reason', 'unknown'))
            continue
        if pos['state'] != 'CLOSED':
            unresolved.append(dict(symbol=symbol, timeframe=timeframe, direction=st['direction'],
                                   entry_time=bars[pos['entry_index']]['time'] if 'entry_index' in pos else None))
            continue
        sign = 1 if st['direction'] == 'long' else -1
        risk = sign * (pos['fill'] - st['stop'])
        if risk <= 0:
            continue
        trig = pos['trigger_index']
        atr = engine.ind(trig)['atr']
        trades.append(dict(
            symbol=symbol, timeframe=timeframe, direction=st['direction'],
            sequence=st['sequence_number'], sequence_confidence=st['sequence_confidence'],
            entry_time=bars[pos['entry_index']]['time'], exit_time=bars[pos['exit_index']]['closed_at'],
            entry_index=pos['entry_index'], exit_index=pos['exit_index'], trigger_index=trig,
            holding_bars=pos['exit_index'] - pos['entry_index'] + 1,
            fill=pos['fill'], stop=st['stop'], target=st['target'], risk=risk,
            net=pos['net'], r=pos['net'] / risk, ret=pos['net'] / pos['fill'],
            # Approximate: friction on both sides at the fill price, plus commissions and borrow.
            cost_r=(2 * pos['fill'] * fr + pos['fees']) / risk,
            stop_atr=risk / atr if atr else None,
            target_atr=sign * (st['target'] - pos['fill']) / atr if atr else None,
            exit_reason=_REASONS.get(exit_reason.get(ident), exit_reason.get(ident, 'unknown'))))
    return engine, trades, cancelled, unresolved


def simulate_control(bars, indicators, t, direction, stop_atr, target_atr, holding, config, borrow=BORROW_RATE):
    """Bracket trade entered at bars[t+1].open: stop first, then target, else close after
    `holding` bars. Same friction, commissions and borrow rate as the engine. Returns R, or None
    when the full `holding` window is not observable (never a silently shortened control)."""
    if holding < 1 or t + holding > len(bars) - 1:
        return None
    if indicators[t]['atr'] in (None, 0) or stop_atr is None or target_atr is None:
        return None
    sign = 1 if direction == 'long' else -1
    friction = _friction(config)
    atr = indicators[t]['atr']
    fill = bars[t + 1]['open'] * (1 + sign * friction)
    stop = fill - sign * stop_atr * atr
    target = fill + sign * target_atr * atr
    risk = sign * (fill - stop)
    if risk <= 0:
        return None
    last = t + holding
    exit_px = None; exit_k = last
    for k in range(t + 1, last + 1):
        b = bars[k]
        if (b['low'] <= stop) if sign == 1 else (b['high'] >= stop):
            exit_px = (min(b['open'], stop) if sign == 1 else max(b['open'], stop)); exit_k = k; break
        if (b['high'] >= target) if sign == 1 else (b['low'] <= target):
            exit_px = target; exit_k = k; break
    if exit_px is None:
        exit_px = bars[last]['close']
    exit_px *= (1 - sign * friction)
    net = sign * (exit_px - fill) - 2 * config.commission_per_share
    if sign == -1:
        # Same convention as the engine: entry bar open to the actual exit bar's close.
        held = (datetime.fromisoformat(bars[exit_k]['closed_at']) - datetime.fromisoformat(bars[t + 1]['time'])).total_seconds()
        net -= fill * borrow * max(0., held) / (365 * 86400)
    return net / risk


def matched_controls(trade, bars, indicators, config, rng, k=20, borrow=BORROW_RATE):
    """k random-timing twins whose whole holding window lies inside the segment."""
    holding = trade['holding_bars']
    candidates = [t for t in range(len(bars) - 1 - holding + 1)
                  if t + holding <= len(bars) - 1 and indicators[t]['atr'] and t != trade['trigger_index']]
    out = []
    for t in rng.sample(candidates, min(k, len(candidates))):
        r = simulate_control(bars, indicators, t, trade['direction'], trade['stop_atr'], trade['target_atr'], holding, config, borrow)
        if r is not None:
            out.append(r)
    return out


def bootstrap_mean_ci(values, rng, n=2000):
    if len(values) < 2:
        return None
    means = sorted(mean(rng.choices(values, k=len(values))) for _ in range(n))
    return [means[int(.025 * n)], means[int(.975 * n) - 1]]


def cluster_bootstrap_ci(values_by_cluster, rng, n=2000):
    """Resample whole symbols: trades of one symbol are not independent observations."""
    clusters = [v for v in values_by_cluster.values() if v]
    if len(clusters) < 2:
        return None
    means = []
    for _ in range(n):
        pick = [x for c in rng.choices(clusters, k=len(clusters)) for x in c]
        means.append(mean(pick))
    means.sort()
    return [means[int(.025 * n)], means[int(.975 * n) - 1]]


def wilson(wins, n, z=1.96):
    if not n:
        return None
    p = wins / n; den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [max(0., centre - half), min(1., centre + half)]


def summarize(trades, rng, unresolved=0):
    n = len(trades)
    if not n:
        return dict(trades=0, unresolved=unresolved, verdict='no trades')
    rs = [t['r'] for t in trades]
    wins = [r for r in rs if r > 0]; losses = [r for r in rs if r <= 0]
    paired = [t for t in trades if t.get('control_mean_r') is not None]
    edges = [t['r'] - t['control_mean_r'] for t in paired]
    by_symbol = {}
    for t in paired:
        by_symbol.setdefault(t['symbol'], []).append(t['r'] - t['control_mean_r'])
    curve = 0.; peak = 0.; dd = 0.
    for t in sorted(trades, key=lambda x: x['exit_time']):
        curve += t['r']; peak = max(peak, curve); dd = max(dd, peak - curve)
    out = dict(
        trades=n, symbols=len({t['symbol'] for t in trades}), unresolved=unresolved,
        win_rate=len(wins) / n, win_rate_wilson95=wilson(len(wins), n),
        mean_r=mean(rs), mean_r_ci95=bootstrap_mean_ci(rs, rng), median_r=median(rs),
        profit_factor=(sum(wins) / -sum(losses)) if sum(losses) < 0 else None,
        avg_win_r=mean(wins) if wins else None, avg_loss_r=mean(losses) if losses else None,
        mean_cost_r=mean(t['cost_r'] for t in trades if t.get('cost_r') is not None) if any(t.get('cost_r') is not None for t in trades) else None,
        gross_mean_r=mean(t['r'] + t['cost_r'] for t in trades if t.get('cost_r') is not None) if any(t.get('cost_r') is not None for t in trades) else None,
        mean_return_pct=100 * mean(t['ret'] for t in trades),
        total_r=sum(rs), max_drawdown_r=dd,
        avg_holding_bars=mean(t['holding_bars'] for t in trades), median_holding_bars=median(t['holding_bars'] for t in trades),
        exits={k: sum(1 for t in trades if t['exit_reason'] == k) for k in sorted({t['exit_reason'] for t in trades})},
        trades_with_controls=len(paired),
        control_mean_r=mean(t['control_mean_r'] for t in paired) if paired else None,
        edge_vs_control_r=mean(edges) if edges else None,
        edge_ci95_by_trade=bootstrap_mean_ci(edges, rng) if edges else None,
        edge_ci95_by_symbol=cluster_bootstrap_ci(by_symbol, rng) if edges else None,
        # Two-sided 5%, 80% power, by trade (optimistic under dependence).
        min_detectable_edge_r=2.8 * pstdev(edges) / math.sqrt(len(edges)) if len(edges) > 1 else None,
    )
    ci = out['edge_ci95_by_symbol']
    out['verdict'] = ('insufficient sample (<30 trades)' if n < 30 else
                      'no measurable edge vs control' if not ci or ci[0] <= 0 <= ci[1] else
                      'better than control' if ci[0] > 0 else 'worse than control')
    return out


def variants(cfg):
    """name -> (config, engine class). Live defaults are 'baseline'; the rest are research."""
    return {
        'baseline': (cfg, _FullHistory),
        'no_bollinger_context': (replace(cfg, bollinger_context=False), _FullHistory),
        'no_chase_filter_off': (replace(cfg, no_chase_filter=False), _FullHistory),
        'unrestricted_sequence': (replace(cfg, sequence_filter=False), _FullHistory),
        'partial_runner': (replace(cfg, partial_runner=True), _FullHistory),
        'line_stop': (replace(cfg, structural_stop=False), _FullHistory),
        'research_stop_target_only': (cfg, _FullHistoryStopTargetOnly),
    }


VARIANTS = lambda cfg: {k: v[0] for k, v in variants(cfg).items()}


def evaluate_streams(streams, config=None, seed=7, controls=20, only=None, borrow=BORROW_RATE):
    """streams: iterable of (symbol, timeframe, [segment bars...]) with research bars.
    Returns {variant: {'trades', 'cancelled', 'unresolved', 'config', 'engine'}}."""
    cfg = config or Config()
    results = {}
    streams = list(streams)
    for name, (variant, engine_cls) in variants(cfg).items():
        if only and name not in only:
            continue
        rng = random.Random(f'{seed}:{name}')
        trades, cancelled, unresolved = [], {}, []
        for symbol, timeframe, segments in streams:
            for seg in segments:
                engine, tr, canc, unres = run_segment(symbol, timeframe, seg, variant, engine_cls)
                unresolved.extend(unres)
                for reason in canc:
                    for part in reason.split(','):
                        cancelled[part] = cancelled.get(part, 0) + 1
                inds = engine.s['indicators']
                for t in tr:
                    ctrl = matched_controls(t, seg, inds, variant, rng, controls, borrow)
                    t['control_mean_r'] = mean(ctrl) if ctrl else None
                    t['controls'] = len(ctrl)
                    t['variant'] = name
                trades.extend(tr)
        results[name] = dict(trades=trades, cancelled=cancelled, unresolved=unresolved,
                             config=asdict(variant), engine=engine_cls.__name__, line_exit=engine_cls.LINE_EXIT)
    return results


def chronological_cut(coverage, fraction=HELD_OUT_FRACTION):
    """Per timeframe: the time after which the last `fraction` of the covered span begins."""
    cuts = {}
    for tf, c in coverage.items():
        if c.get('first') and c.get('last'):
            a, b = datetime.fromisoformat(c['first']), datetime.fromisoformat(c['last'])
            cuts[tf] = (a + (b - a) * (1 - fraction)).isoformat()
    return cuts


def report(results, cuts=None, seed=11):
    rng = random.Random(seed)
    cuts = cuts or {}
    out = {}
    for name, res in results.items():
        trades, unresolved = res['trades'], res['unresolved']
        def by(key, items=trades, unres=unresolved):
            values = sorted({key(t) for t in items} | {key(u) for u in unres if key(u) is not None}, key=str)
            return {str(v): summarize([t for t in items if key(t) == v], rng, sum(1 for u in unres if key(u) == v)) for v in values}
        period = lambda t: (None if t.get('entry_time') is None or t['timeframe'] not in cuts else
                            f"{t['timeframe']}:{'held_out' if t['entry_time'] >= cuts[t['timeframe']] else 'development'}")
        out[name] = dict(
            engine=res['engine'], line_exit=res['line_exit'],
            overall=summarize(trades, rng, len(unresolved)),
            long_only=summarize([t for t in trades if t['direction'] == 'long'], rng, sum(1 for u in unresolved if u['direction'] == 'long')),
            by_timeframe=by(lambda t: t['timeframe']),
            by_direction=by(lambda t: t['direction']),
            by_timeframe_direction=by(lambda t: f"{t['timeframe']}:{t['direction']}"),
            by_period=by(period),
            by_period_direction=by(lambda t: None if period(t) is None else f"{period(t)}:{t['direction']}"),
            by_month=by(lambda t: None if t.get('entry_time') is None else f"{t['timeframe']}:{t['entry_time'][:7]}"),
            by_sequence=by(lambda t: None if 'sequence' not in t else ('unknown' if t['sequence_confidence'] != 'known' else str(min(t['sequence'], 4)) + ('+' if t['sequence'] >= 4 else ''))),
            cancelled=res['cancelled'], unresolved_at_segment_end=len(unresolved))
    return out
