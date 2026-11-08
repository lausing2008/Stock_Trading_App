"""Little RZY evaluation v2: causal cross-stream context, predeclared hypotheses and summaries.

Pure functions (no database, network or clock). Definitions are fixed by
docs/2026-11-08/rzy-evaluation-v2-preregistration.md, committed before v2 saw any data; changing
one is a new, separately named analysis.

Every label is CAUSAL: it uses only bars whose close is at or before the trade's signal-bar close
(`signal_time`). A higher timeframe's state therefore lags exactly as it would have live: an
intraday trade at 10:00 sees yesterday's daily bar, never today's.
"""
from __future__ import annotations

import random
from bisect import bisect_right
from datetime import datetime, timedelta
from statistics import mean
from zoneinfo import ZoneInfo

from .little_rzy import Engine, Config

ANALYSIS_VERSION = 'RZY_EVALUATION_V2'
# The v1 snapshot export; trade rules are unchanged since (see the preregistration).
UNSEEN_AFTER = '2026-10-20T14:04:54+00:00'
UNSEEN_LABEL = 'unseen_after_2026-10-20'
SEEN_LABEL = 'seen_by_v1'
TIMEFRAMES = ('5m', '15m', '1h', '1d')
INTRADAY = ('5m', '15m', '1h')
NEXT_HIGHER = {'5m': '15m', '15m': '1h', '1h': '1d'}
GRACE = {'5m': timedelta(hours=20), '15m': timedelta(hours=20), '1h': timedelta(hours=20), '1d': timedelta(days=5)}
REGIME_SYMBOL = 'SPY'
REGIME_SMA = 50
MIN_GROUP = 30
NY = ZoneInfo('America/New_York')
TIME_BUCKETS = (('09:30-10:30', 10 * 60 + 30), ('10:30-12:00', 12 * 60), ('12:00-14:00', 14 * 60),
                ('14:00-15:00', 15 * 60), ('15:00-16:00', 16 * 60))
CONTEXT_SLICES = ('htf_daily', 'htf_next', 'confluence', 'market_regime', 'time_of_day', 'volatility', 'period')


def _dt(value):
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


class _Timeline:
    """Change points of a state over time; `at(t)` is the state in force at t (causal)."""

    def __init__(self):
        self.times, self.states = [], []

    def add(self, when, state):
        if self.states and self.states[-1] == state:
            return
        if self.times and when < self.times[-1]:
            raise ValueError('timeline points must be added in time order')
        if self.times and when == self.times[-1]:
            self.states[-1] = state
            return
        self.times.append(when); self.states.append(state)

    def at(self, when):
        k = bisect_right(self.times, _dt(when)) - 1
        return 'unknown' if k < 0 else self.states[k]


def _segment_ends(segments, grace):
    """(first close, end-of-validity) per segment, never overlapping the next segment's start."""
    spans = sorted(((_dt(s[0]['closed_at']), _dt(s[-1]['closed_at'])) for s in segments if s), key=lambda x: x[0])
    out = []
    for k, (first, last) in enumerate(spans):
        end = last + grace
        if k + 1 < len(spans):
            end = min(end, spans[k + 1][0])
        out.append((first, end))
    return out


def trend_timelines(streams, config=None):
    """{(symbol, timeframe): _Timeline of 'long'|'short'|'neutral'|'unknown'} from the live engine.

    One plain engine pass per segment. A state applies from its bar's close until the next change,
    and becomes 'unknown' at the segment's end plus GRACE (or where the next segment starts)."""
    cfg = config or Config()
    out = {}
    by_key = {}
    for symbol, timeframe, segments in streams:
        by_key.setdefault((symbol, timeframe), []).extend(segments)
    for (symbol, timeframe), segments in by_key.items():
        tl = _Timeline()
        segments = sorted((s for s in segments if s), key=lambda s: s[0]['closed_at'])
        ends = _segment_ends(segments, GRACE.get(timeframe, timedelta(0)))
        for seg, (_, end) in zip(segments, ends):
            engine = Engine(symbol, timeframe, cfg)
            for bar in seg:
                engine.step(bar)
                pivots = engine.s['pivots']
                warm = sum(p['kind'] == 'high' for p in pivots) >= 2 and sum(p['kind'] == 'low' for p in pivots) >= 2
                tl.add(_dt(bar['closed_at']), engine.s['trend'] if warm else 'unknown')
            tl.add(end, 'unknown')
        out[(symbol, timeframe)] = tl
    return out


def regime_timeline(streams, symbol=REGIME_SYMBOL, length=REGIME_SMA):
    """SPY daily close vs its `length`-day simple average, as of each daily close."""
    tl = _Timeline()
    segments = sorted((seg for sym, tf, segs in streams if sym == symbol and tf == '1d' for seg in segs if seg),
                      key=lambda s: s[0]['closed_at'])
    for seg, (_, end) in zip(segments, _segment_ends(segments, GRACE['1d'])):
        closes = []
        for bar in seg:
            closes.append(bar['close'])
            if len(closes) >= length:
                sma = sum(closes[-length:]) / length
                tl.add(_dt(bar['closed_at']), 'spy_above_50d' if bar['close'] > sma else 'spy_below_50d')
            else:
                tl.add(_dt(bar['closed_at']), 'unknown')
        tl.add(end, 'unknown')
    return tl


def _alignment(state, direction):
    if state in ('unknown', None):
        return 'unknown'
    if state == 'neutral':
        return 'neutral'
    return 'aligned' if state == direction else 'opposed'


def time_bucket(signal_time):
    local = _dt(signal_time).astimezone(NY)
    minutes = local.hour * 60 + local.minute
    # Regular-session bars close from 09:35 to 16:00; anything else is labelled, never binned.
    if minutes <= 9 * 60 + 30 or minutes > 16 * 60:
        return 'outside_regular_session'
    for name, upper in TIME_BUCKETS:
        if minutes <= upper:
            return name
    return TIME_BUCKETS[-1][0]


def label_trades(trades, timelines, regime=None, unseen_after=UNSEEN_AFTER):
    """Adds the predeclared context labels to each trade, in place. Returns the trades."""
    cut = _dt(unseen_after)
    for t in trades:
        when = _dt(t['signal_time'])
        tf, sym, direction = t['timeframe'], t['symbol'], t['direction']
        state = lambda other: timelines[(sym, other)].at(when) if (sym, other) in timelines else 'unknown'
        t['htf_daily'] = _alignment(state('1d'), direction) if tf in INTRADAY else 'not_applicable'
        t['htf_next'] = _alignment(state(NEXT_HIGHER[tf]), direction) if tf in NEXT_HIGHER else 'not_applicable'
        others = [_alignment(state(o), direction) for o in TIMEFRAMES if o != tf]
        aligned = sum(a == 'aligned' for a in others)
        t['confluence'] = ('incomplete' if 'unknown' in others else 'all_aligned' if aligned == len(others)
                           else 'majority_aligned' if aligned * 2 > len(others) else 'minority_aligned')
        t['market_regime'] = regime.at(when) if regime is not None else 'unknown'
        t['time_of_day'] = time_bucket(when) if tf in INTRADAY else 'not_applicable'
        t['period'] = UNSEEN_LABEL if when > cut else SEEN_LABEL
    # Volatility thirds within each timeframe (descriptive: thresholds come from the sample).
    for tf in {t['timeframe'] for t in trades}:
        group = sorted(t['atr_pct'] for t in trades if t['timeframe'] == tf and t.get('atr_pct') is not None)
        if len(group) < 3:
            for t in trades:
                if t['timeframe'] == tf:
                    t['volatility'] = 'unknown'
            continue
        lo, hi = group[len(group) // 3], group[2 * len(group) // 3]
        for t in trades:
            if t['timeframe'] != tf:
                continue
            v = t.get('atr_pct')
            t['volatility'] = 'unknown' if v is None else 'low' if v < lo else 'mid' if v < hi else 'high'
    return trades


def _edges_by_symbol(trades):
    out = {}
    for t in trades:
        if t.get('control_mean_r') is not None:
            out.setdefault(t['symbol'], []).append(t['r'] - t['control_mean_r'])
    return out


def difference_ci(group_a, group_b, rng, n=2000):
    """Mean edge of A minus mean edge of B, bootstrapped by resampling whole symbols (a symbol's
    trades in both groups move together). Returns (point, [lo, hi]) or (point, None)."""
    a, b = _edges_by_symbol(group_a), _edges_by_symbol(group_b)
    flat_a = [x for v in a.values() for x in v]; flat_b = [x for v in b.values() for x in v]
    if not flat_a or not flat_b:
        return None, None
    point = mean(flat_a) - mean(flat_b)
    symbols = sorted(set(a) | set(b))
    if len(symbols) < 2:
        return point, None
    diffs = []
    for _ in range(n):
        pick = rng.choices(symbols, k=len(symbols))
        xa = [x for s in pick for x in a.get(s, ())]; xb = [x for s in pick for x in b.get(s, ())]
        if xa and xb:
            diffs.append(mean(xa) - mean(xb))
    if len(diffs) < n * 0.9:
        return point, None
    diffs.sort()
    return point, [diffs[int(.025 * len(diffs))], diffs[int(.975 * len(diffs)) - 1]]


def _edge_ci(trades, rng, n=2000):
    by = _edges_by_symbol(trades)
    flat = [x for v in by.values() for x in v]
    if not flat:
        return None, None
    clusters = [v for v in by.values() if v]
    if len(clusters) < 2:
        return mean(flat), None
    means = sorted(mean([x for c in rng.choices(clusters, k=len(clusters)) for x in c]) for _ in range(n))
    return mean(flat), [means[int(.025 * n)], means[int(.975 * n) - 1]]


def _verdict_single(n, ci):
    if n < MIN_GROUP:
        return 'insufficient sample'
    if not ci or ci[0] <= 0 <= ci[1]:
        return 'no measurable edge'
    return 'edge above zero' if ci[0] > 0 else 'edge below zero'


def _verdict_diff(na, nb, ci):
    if na < MIN_GROUP or nb < MIN_GROUP:
        return 'insufficient sample'
    if not ci or ci[0] <= 0 <= ci[1]:
        return 'no measurable difference'
    return 'first group better' if ci[0] > 0 else 'first group worse'


def hypotheses(trades, seed=23):
    """The five predeclared tests on one variant's (baseline's) labelled trades."""
    rng = random.Random(seed)
    intraday = [t for t in trades if t['timeframe'] in INTRADAY]
    out = []

    def single(ident, role, question, group):
        point, ci = _edge_ci(group, rng)
        out.append(dict(id=ident, role=role, question=question, kind='edge', trades=len(group),
                        symbols=len({t['symbol'] for t in group}), edge_r=point, ci95=ci,
                        verdict=_verdict_single(len(group), ci)))

    def diff(ident, role, question, a, b, labels):
        point, ci = difference_ci(a, b, rng)
        ea, _ = _edge_ci(a, random.Random(seed)); eb, _ = _edge_ci(b, random.Random(seed))
        out.append(dict(id=ident, role=role, question=question, kind='difference', groups=labels,
                        trades=[len(a), len(b)], edge_r=[ea, eb], difference_r=point, ci95=ci,
                        verdict=_verdict_diff(len(a), len(b), ci)))

    diff('H3', 'primary', 'Do intraday trades aligned with the daily trend have a larger edge than intraday trades that are not?',
         [t for t in intraday if t.get('htf_daily') == 'aligned'],
         [t for t in intraday if t.get('htf_daily') in ('opposed', 'neutral')], ['aligned with daily', 'not aligned'])
    single('H1', 'secondary', 'Is the baseline edge above zero on all data?', trades)
    single('H2', 'secondary', 'Is the baseline edge above zero on sessions no earlier evaluation saw?',
           [t for t in trades if t.get('period') == UNSEEN_LABEL])
    diff('H4', 'secondary', 'Same as H3 using the next higher timeframe (5m→15m, 15m→1h, 1h→daily).',
         [t for t in intraday if t.get('htf_next') == 'aligned'],
         [t for t in intraday if t.get('htf_next') in ('opposed', 'neutral')], ['aligned with next higher', 'not aligned'])
    diff('H5', 'secondary', 'Do trades whose other three timeframes all agree beat the rest?',
         [t for t in trades if t.get('confluence') == 'all_aligned'],
         [t for t in trades if t.get('confluence') in ('majority_aligned', 'minority_aligned')], ['all aligned', 'others'])
    return out


def scorecard(variant_report):
    """How many slice cells were tested, and how many looked better/worse than chance alone would
    explain. Each cell has about a 2.5% chance per side of a false 'better' or 'worse'."""
    cells = []
    for key, value in variant_report.items():
        if not key.startswith('by_') or not isinstance(value, dict):
            continue
        for name, s in value.items():
            if isinstance(s, dict) and s.get('trades', 0) >= MIN_GROUP:
                cells.append(dict(slice=key[3:], cell=name, trades=s['trades'], edge_r=s.get('edge_vs_control_r'),
                                  ci95=s.get('edge_ci95_by_symbol'), verdict=s.get('verdict')))
    better = [c for c in cells if c['verdict'] == 'better than control']
    worse = [c for c in cells if c['verdict'] == 'worse than control']
    return dict(cells_tested=len(cells), better=len(better), worse=len(worse),
                expected_by_chance_each_side=round(0.025 * len(cells), 1),
                better_cells=better, worse_cells=worse,
                reading=('A handful of "better" cells is expected by chance when many are tested; treat an isolated '
                         'one as a lead for later sessions, not a finding.'))


KEY_FIELDS = ('trades', 'symbols', 'win_rate', 'win_rate_wilson95', 'mean_r', 'mean_r_ci95', 'median_r', 'profit_factor',
              'avg_win_r', 'avg_loss_r', 'mean_cost_r', 'total_r', 'max_drawdown_r', 'avg_holding_bars', 'exits',
              'control_mean_r', 'edge_vs_control_r', 'edge_ci95_by_symbol', 'min_detectable_edge_r',
              'mean_mfe_r', 'median_mfe_r', 'mean_mae_r', 'reached_1r_rate', 'target_exit_rate', 'unresolved', 'verdict')
SUMMARY_SLICES = ('by_timeframe', 'by_direction', 'by_timeframe_direction', 'by_period', 'by_htf_daily', 'by_htf_next',
                  'by_confluence', 'by_market_regime', 'by_time_of_day', 'by_volatility', 'by_sequence', 'by_month')
VARIANT_NOTES = {
    'baseline': 'The live rules.',
    'no_bollinger_context': 'Skips the Bollinger-band pullback check.',
    'no_chase_filter_off': 'Allows entries that close outside the band (no-chase filter off).',
    'unrestricted_sequence': 'Allows every pullback in a trend, not only the 1st and 2nd.',
    'partial_runner': 'Takes half at the target and runs the rest to a 2x target.',
    'line_stop': 'Puts the stop at the trendline instead of below the structure.',
    'research_stop_target_only': 'Exits only at the stop or target, never on a close through the line.',
}


def _pick(s):
    return {k: s.get(k) for k in KEY_FIELDS if k in s}


def build_summary(rep, hyps, score):
    """Compact, UI-ready summary of one run (stored and served; the full report stays on disk)."""
    base = rep['variants'].get('baseline', {})
    h3 = next((h for h in hyps if h['id'] == 'H3'), None)
    h1 = next((h for h in hyps if h['id'] == 'H1'), None)
    h2 = next((h for h in hyps if h['id'] == 'H2'), None)
    return dict(
        analysis_version=ANALYSIS_VERSION, strategy_version=rep.get('strategy_version'), config_hash=rep.get('config_hash'),
        unseen_after=UNSEEN_AFTER, snapshot=dict(exported_at=rep.get('snapshot', {}).get('exported_at'),
                                                 coverage=rep.get('snapshot', {}).get('coverage'),
                                                 bars_sha256=rep.get('snapshot', {}).get('bars_sha256')),
        headline=dict(all_data=h1 and h1['verdict'], unseen=h2 and h2['verdict'], daily_alignment=h3 and h3['verdict']),
        hypotheses=hyps, scorecard={k: v for k, v in score.items() if k not in ('better_cells', 'worse_cells')} | dict(
            better_cells=score['better_cells'][:20], worse_cells=score['worse_cells'][:20]),
        baseline=dict(overall=_pick(base.get('overall', {})), long_only=_pick(base.get('long_only', {})),
                      **{k: {n: _pick(s) for n, s in base.get(k, {}).items()} for k in SUMMARY_SLICES}),
        variants=[dict(name=n, note=VARIANT_NOTES.get(n, ''), overall=_pick(v.get('overall', {})))
                  for n, v in rep['variants'].items()],
        assumptions=rep.get('assumptions'), method=rep.get('method'))


def _fmt(x, digits=3, pct=False):
    if x is None:
        return '—'
    if pct:
        return f'{100 * x:.1f}%'
    return f'{x:+.{digits}f}' if isinstance(x, float) else str(x)


def _r(x):
    return '—' if x is None else f'{x:+.3f}R'


def _ci(ci):
    return '—' if not ci else f'{ci[0]:+.3f} to {ci[1]:+.3f}'


def render_markdown(summary):
    """Human-readable report written beside report.json."""
    o = summary['baseline']['overall']
    lines = [f"# Little RZY evaluation — {summary['analysis_version']}", '',
             f"Snapshot exported {summary['snapshot']['exported_at']}; strategy {summary['strategy_version']} "
             f"(config {summary['config_hash']}). Unseen period: signals after {summary['unseen_after']}.", '',
             '## Predeclared questions', '', '| ID | Role | Question | Trades | Result | 95% CI | Verdict |', '|---|---|---|---|---|---|---|']
    for h in summary['hypotheses']:
        if h['kind'] == 'edge':
            lines.append(f"| {h['id']} | {h['role']} | {h['question']} | {h['trades']} | edge {_r(h['edge_r'])} | {_ci(h['ci95'])} | {h['verdict']} |")
        else:
            lines.append(f"| {h['id']} | {h['role']} | {h['question']} | {h['trades'][0]} vs {h['trades'][1]} | "
                         f"{_fmt(h['edge_r'][0])} vs {_fmt(h['edge_r'][1])} (diff {_r(h['difference_r'])}) | {_ci(h['ci95'])} | {h['verdict']} |")
    lines += ['', '## Baseline (live rules), all data', '',
              f"{o.get('trades')} trades, win rate {_fmt(o.get('win_rate'), pct=True)}, mean {_r(o.get('mean_r'))}, "
              f"edge vs random timing {_r(o.get('edge_vs_control_r'))} (95% CI by symbol {_ci(o.get('edge_ci95_by_symbol'))}): **{o.get('verdict')}**.",
              f"Average best move during a trade (MFE) {_r(o.get('mean_mfe_r'))}; average worst move (MAE) {_r(o.get('mean_mae_r'))}; "
              f"{_fmt(o.get('reached_1r_rate'), pct=True)} of trades reached +1R at some point; {_fmt(o.get('target_exit_rate'), pct=True)} exited at the target.", '']
    for key, title in (('by_timeframe', 'By timeframe'), ('by_period', 'Seen vs unseen'), ('by_htf_daily', 'Daily-trend alignment'),
                       ('by_htf_next', 'Next-higher-timeframe alignment'), ('by_confluence', 'Agreement across timeframes'),
                       ('by_market_regime', 'Market regime (SPY vs 50-day average)'), ('by_time_of_day', 'Time of day (New York)'),
                       ('by_volatility', 'Volatility third'), ('by_direction', 'Direction')):
        rows = summary['baseline'].get(key) or {}
        if not rows:
            continue
        lines += [f'### {title}', '', '| Group | Trades | Win rate | Mean R | Edge R | Edge 95% CI | Verdict |', '|---|---|---|---|---|---|---|']
        for name, s in rows.items():
            lines.append(f"| {name} | {s.get('trades')} | {_fmt(s.get('win_rate'), pct=True)} | {_fmt(s.get('mean_r'))} | "
                         f"{_fmt(s.get('edge_vs_control_r'))} | {_ci(s.get('edge_ci95_by_symbol'))} | {s.get('verdict')} |")
        lines.append('')
    lines += ['## Rule variants', '', '| Variant | What changes | Trades | Mean R | Edge R | Edge 95% CI | Verdict |', '|---|---|---|---|---|---|---|']
    for v in summary['variants']:
        s = v['overall']
        lines.append(f"| {v['name']} | {v['note']} | {s.get('trades')} | {_fmt(s.get('mean_r'))} | {_fmt(s.get('edge_vs_control_r'))} | "
                     f"{_ci(s.get('edge_ci95_by_symbol'))} | {s.get('verdict')} |")
    sc = summary['scorecard']
    lines += ['', '## Many-comparisons scorecard', '',
              f"{sc['cells_tested']} slice cells with at least {MIN_GROUP} trades were tested. {sc['better']} looked better than control and "
              f"{sc['worse']} worse; about {sc['expected_by_chance_each_side']} of each would be expected by chance alone.", '',
              '## Assumptions', '', *[f'- {k}: {v}' for k, v in (summary.get('assumptions') or {}).items()], '']
    return '\n'.join(lines)
