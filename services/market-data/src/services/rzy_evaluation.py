"""LITTLE_RZY_INSPIRED_V1 evaluation: real trades from the shipped engine vs matched controls.

Pure functions only (no database, network or clock), so the same code runs in tests and in the
read-only production evaluation script.

Design:
- Each contiguous bar segment is replayed through the production `Engine` in time order; fills
  are next-open with configured costs and stop-before-target ordering (the engine's own rules).
- Execution inputs that stored prices lack are supplied as EXPLICIT research assumptions and
  recorded on every result (``assumptions``); they are never presented as verified data.
- Unit of account is R: net P&L per share divided by initial risk (fill to frozen stop).
- Each trade gets matched random-timing controls on the same stream: same direction, the same
  stop and target distances in ATR at the control's own entry, the same costs, and a time exit
  after the real trade's holding length. The control answers "does the pattern's timing add
  anything beyond direction, volatility-scaled brackets and drift?".
- Nothing is tuned or selected; every variant is reported.
"""
from __future__ import annotations

import math
import random
from dataclasses import asdict, replace
from statistics import mean, median

from .little_rzy import Engine, Config

ASSUMPTIONS = dict(
    execution_eligible='assumed True for research (stored prices carry no verified provider delay)',
    shortable='assumed True for research; short results also reported separately',
    borrow_rate_annual=0.005,
    note='Assumptions make paper fills possible; they are not verified execution or borrow data.',
)


class _FullHistory(Engine):
    # Evaluation needs every bar's indicators for controls; it never persists a checkpoint.
    RETAIN = 10 ** 9
    KEEP_PIVOTS = 10 ** 9
    KEEP_STRUCTURES = 10 ** 9
    KEEP_CLOSED_POSITIONS = 10 ** 9


def research_bars(bars, borrow=ASSUMPTIONS['borrow_rate_annual']):
    return [dict(b, execution_eligible=True, shortable=True, borrow_rate_annual=borrow) for b in bars]


def run_segment(symbol, timeframe, bars, config):
    """Replay one contiguous segment. Returns (engine, closed trades, cancellations, unresolved)."""
    engine = _FullHistory(symbol, timeframe, config)
    exit_reason = {}
    for bar in bars:
        for e in engine.step(bar):
            if e['event_type'] == 'PAPER_EXIT' and e.get('paper_state') == 'CLOSED':
                exit_reason[e['structure_id']] = e['reason']
    trades, cancelled, unresolved = [], [], 0
    for ident, pos in engine.s['positions'].items():
        st = engine.s['structures'][ident]
        if pos.get('cancelled'):
            cancelled.append(pos.get('reason', 'unknown'))
            continue
        if pos['state'] != 'CLOSED':
            unresolved += 1
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
            stop_atr=risk / atr if atr else None,
            target_atr=sign * (st['target'] - pos['fill']) / atr if atr else None,
            exit_reason=_REASONS.get(exit_reason.get(ident), exit_reason.get(ident, 'unknown'))))
    return engine, trades, cancelled, unresolved


_REASONS = {'primary_target': 'target', 'runner_target': 'runner_target',
            'protective_stop_conservative_order': 'stop', 'partial_stop_conservative_order': 'stop_after_partial',
            'structural_invalidation_next_open': 'line_invalidation'}


def simulate_control(bars, indicators, t, direction, stop_atr, target_atr, holding, config):
    """Bracket trade entered at bars[t+1].open: stop first, then target, else close after
    `holding` bars. Same friction and commissions as the engine. Returns R or None."""
    if t + 1 >= len(bars) or indicators[t]['atr'] in (None, 0) or stop_atr is None or target_atr is None:
        return None
    sign = 1 if direction == 'long' else -1
    friction = (config.spread_bps / 2 + config.slippage_bps) / 10000
    atr = indicators[t]['atr']
    raw_open = bars[t + 1]['open']
    fill = raw_open * (1 + sign * friction)
    stop = fill - sign * stop_atr * atr
    target = fill + sign * target_atr * atr
    risk = sign * (fill - stop)
    if risk <= 0:
        return None
    last = min(len(bars) - 1, t + holding)
    exit_px = None
    for k in range(t + 1, last + 1):
        b = bars[k]
        if (b['low'] <= stop) if sign == 1 else (b['high'] >= stop):
            exit_px = (min(b['open'], stop) if sign == 1 else max(b['open'], stop)); break
        if (b['high'] >= target) if sign == 1 else (b['low'] <= target):
            exit_px = target; break
    if exit_px is None:
        exit_px = bars[last]['close']
    exit_px *= (1 - sign * friction)
    net = sign * (exit_px - fill) - 2 * config.commission_per_share
    if sign == -1:
        from datetime import datetime
        held = (datetime.fromisoformat(bars[last]['closed_at']) - datetime.fromisoformat(bars[t + 1]['time'])).total_seconds()
        net -= fill * ASSUMPTIONS['borrow_rate_annual'] * max(0., held) / (365 * 86400)
    return net / risk


def matched_controls(trade, bars, indicators, config, rng, k=20):
    """k random-timing twins from the same segment; never reuses the real entry bar."""
    holding = trade['holding_bars']
    candidates = [t for t in range(len(bars) - 1)
                  if indicators[t]['atr'] and t != trade['trigger_index']]
    if not candidates:
        return []
    out = []
    for t in rng.sample(candidates, min(k, len(candidates))):
        r = simulate_control(bars, indicators, t, trade['direction'], trade['stop_atr'], trade['target_atr'], holding, config)
        if r is not None:
            out.append(r)
    return out


def bootstrap_mean_ci(values, rng, n=2000):
    if len(values) < 2:
        return None
    means = sorted(mean(rng.choices(values, k=len(values))) for _ in range(n))
    return [means[int(.025 * n)], means[int(.975 * n) - 1]]


def wilson(wins, n, z=1.96):
    if not n:
        return None
    p = wins / n; den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [max(0., centre - half), min(1., centre + half)]


def summarize(trades, rng):
    n = len(trades)
    if not n:
        return dict(trades=0, verdict='no trades')
    rs = [t['r'] for t in trades]
    wins = [r for r in rs if r > 0]; losses = [r for r in rs if r <= 0]
    edges = [t['r'] - t['control_mean_r'] for t in trades if t.get('control_mean_r') is not None]
    curve = 0.; peak = 0.; dd = 0.
    for t in sorted(trades, key=lambda x: x['exit_time']):
        curve += t['r']; peak = max(peak, curve); dd = max(dd, peak - curve)
    out = dict(
        trades=n, win_rate=len(wins) / n, win_rate_wilson95=wilson(len(wins), n),
        mean_r=mean(rs), mean_r_ci95=bootstrap_mean_ci(rs, rng), median_r=median(rs),
        profit_factor=(sum(wins) / -sum(losses)) if sum(losses) < 0 else None,
        avg_win_r=mean(wins) if wins else None, avg_loss_r=mean(losses) if losses else None,
        mean_return_pct=100 * mean(t['ret'] for t in trades),
        total_r=sum(rs), max_drawdown_r=dd, avg_holding_bars=mean(t['holding_bars'] for t in trades),
        exits={k: sum(1 for t in trades if t['exit_reason'] == k) for k in sorted({t['exit_reason'] for t in trades})},
        control_mean_r=mean(t['control_mean_r'] for t in trades if t.get('control_mean_r') is not None) if edges else None,
        edge_vs_control_r=mean(edges) if edges else None,
        edge_vs_control_ci95=bootstrap_mean_ci(edges, rng) if edges else None,
    )
    ci = out['edge_vs_control_ci95']
    out['verdict'] = ('insufficient sample (<30 trades)' if n < 30 else
                      'no measurable edge vs control' if not ci or ci[0] <= 0 <= ci[1] else
                      'better than control' if ci[0] > 0 else 'worse than control')
    return out


VARIANTS = lambda cfg: {
    'baseline': cfg,
    'no_bollinger_context': replace(cfg, bollinger_context=False),
    'no_chase_filter_off': replace(cfg, no_chase_filter=False),
    'unrestricted_sequence': replace(cfg, sequence_filter=False),
    'partial_runner': replace(cfg, partial_runner=True),
    'line_stop': replace(cfg, structural_stop=False),
}


def evaluate_streams(streams, config=None, seed=7, controls=20):
    """streams: iterable of (symbol, timeframe, [segment bars...]) with research bars.
    Returns {variant: {'trades': [...], 'cancelled': {...}, 'unresolved': n}}."""
    cfg = config or Config()
    rng = random.Random(seed)
    results = {}
    for name, variant in VARIANTS(cfg).items():
        trades, cancelled, unresolved = [], {}, 0
        for symbol, timeframe, segments in streams:
            for seg in segments:
                engine, tr, canc, unres = run_segment(symbol, timeframe, seg, variant)
                unresolved += unres
                for reason in canc:
                    for part in reason.split(','):
                        cancelled[part] = cancelled.get(part, 0) + 1
                inds = engine.s['indicators']
                for t in tr:
                    ctrl = matched_controls(t, seg, inds, variant, rng, controls)
                    t['control_mean_r'] = mean(ctrl) if ctrl else None
                    t['variant'] = name
                trades.extend(tr)
        results[name] = dict(trades=trades, cancelled=cancelled, unresolved=unresolved, config=asdict(variant))
    return results


def report(results, seed=11):
    rng = random.Random(seed)
    out = {}
    for name, res in results.items():
        trades = res['trades']
        by = lambda key: {str(v): summarize([t for t in trades if key(t) == v], rng) for v in sorted({key(t) for t in trades}, key=str)}
        out[name] = dict(
            overall=summarize(trades, rng),
            long_only=summarize([t for t in trades if t['direction'] == 'long'], rng),
            by_timeframe=by(lambda t: t['timeframe']),
            by_direction=by(lambda t: t['direction']),
            by_timeframe_direction=by(lambda t: f"{t['timeframe']}:{t['direction']}"),
            by_month=by(lambda t: f"{t['timeframe']}:{t['entry_time'][:7]}"),
            by_sequence=by(lambda t: 'unknown' if t['sequence_confidence'] != 'known' else str(min(t['sequence'], 4)) + ('+' if t['sequence'] >= 4 else '')),
            cancelled=res['cancelled'], unresolved_at_segment_end=res['unresolved'])
    return out
