"""Evaluation v2: causal cross-stream labels, excursions, hypotheses and summaries."""
import json
import random
from datetime import datetime, timedelta, timezone

import pytest

from src.services.little_rzy import Config, Engine
from src.services.rzy_evaluation import research_bars, run_segment, evaluate_streams, report
from src.services import rzy_evaluation_context as C


def walk(seed, n, start, minutes, drift=0.0, price=100.):
    rng = random.Random(seed); p = price; t = start; out = []
    for _ in range(n):
        o = p; c = max(1, o + rng.gauss(drift, .5)); h = max(o, c) + abs(rng.gauss(0, .25)); l = min(o, c) - abs(rng.gauss(0, .25)); p = c
        out.append(dict(time=t.isoformat(), closed_at=(t + timedelta(minutes=minutes)).isoformat(), complete=True,
                        open=o, high=h, low=l, close=c, volume=1000.)); t += timedelta(minutes=minutes)
    return out


T0 = datetime(2026, 3, 2, 14, 30, tzinfo=timezone.utc)


def _warm_trend(bars):
    """Reference: replay only `bars` and report the state the timeline must show at their end."""
    e = Engine('X', '1d', Config())
    for b in bars:
        e.step(b)
    p = e.s['pivots']
    warm = sum(x['kind'] == 'high' for x in p) >= 2 and sum(x['kind'] == 'low' for x in p) >= 2
    return e.s['trend'] if warm else 'unknown'


def test_timeline_state_equals_a_replay_truncated_at_the_query_time():
    # The no-look-ahead proof: the label at time t must equal what an engine that had seen only
    # bars closed by t would say, for many t, including times between two bar closes.
    bars = walk(3, 400, T0, 1440, drift=.05)
    tl = C.trend_timelines([('X', '1d', [bars])])[('X', '1d')]
    states = set()
    for k in range(5, 400, 7):
        close = datetime.fromisoformat(bars[k]['closed_at'])
        expected = _warm_trend(bars[:k + 1])
        assert tl.at(close) == expected
        assert tl.at(close + timedelta(hours=3)) == expected  # before the next close: unchanged
        states.add(expected)
    assert {'long', 'short'} & states and 'unknown' in states


def test_state_expires_after_segment_end_plus_grace_and_next_segment_takes_over():
    a = walk(1, 120, T0, 60)
    gap_start = datetime.fromisoformat(a[-1]['closed_at'])
    b = walk(2, 120, gap_start + timedelta(days=3), 60)
    tl = C.trend_timelines([('X', '1h', [a, b])])[('X', '1h')]
    assert tl.at(gap_start + timedelta(hours=19)) == _warm_trend_tf(a, '1h')
    assert tl.at(gap_start + timedelta(hours=21)) == 'unknown'
    # A fresh segment starts cold: its own state, never the previous segment's.
    first_b = datetime.fromisoformat(b[0]['closed_at'])
    assert tl.at(first_b) == 'unknown'


def _warm_trend_tf(bars, tf):
    e = Engine('X', tf, Config())
    for b in bars:
        e.step(b)
    p = e.s['pivots']
    warm = sum(x['kind'] == 'high' for x in p) >= 2 and sum(x['kind'] == 'low' for x in p) >= 2
    return e.s['trend'] if warm else 'unknown'


def test_timeline_rejects_out_of_order_points():
    tl = C._Timeline(); tl.add(T0, 'long')
    with pytest.raises(ValueError):
        tl.add(T0 - timedelta(minutes=1), 'short')
    assert tl.at(T0 - timedelta(seconds=1)) == 'unknown' and tl.at(T0) == 'long'


def test_regime_uses_only_closed_daily_bars_and_needs_fifty_of_them():
    bars = [dict(time=(T0 + timedelta(days=k)).isoformat(), closed_at=(T0 + timedelta(days=k, hours=6, minutes=30)).isoformat(),
                 complete=True, open=100., high=101., low=99., close=100. + (k if k >= 55 else 0), volume=1.) for k in range(60)]
    tl = C.regime_timeline([('SPY', '1d', [bars])])
    assert tl.at(bars[48]['closed_at']) == 'unknown'
    assert tl.at(bars[52]['closed_at']) == 'spy_below_50d'      # close equals its average: not above
    assert tl.at(bars[57]['closed_at']) == 'spy_above_50d'
    # Intraday on day 57, before that day's close, still sees day 56.
    assert tl.at(datetime.fromisoformat(bars[57]['closed_at']) - timedelta(hours=2)) == tl.at(bars[56]['closed_at'])


class _Fixed:
    def __init__(self, state):
        self.state = state

    def at(self, _):
        return self.state


def _trade(tf='5m', direction='long', when='2026-03-02T14:40:00+00:00', sym='X', atr_pct=.01, r=0., ctrl=0.):
    return dict(symbol=sym, timeframe=tf, direction=direction, signal_time=when, atr_pct=atr_pct, r=r, control_mean_r=ctrl)


def test_labels_alignment_confluence_time_of_day_and_period():
    tls = {('X', '15m'): _Fixed('long'), ('X', '1h'): _Fixed('neutral'), ('X', '1d'): _Fixed('short')}
    t = C.label_trades([_trade()], tls, _Fixed('spy_above_50d'))[0]
    assert (t['htf_daily'], t['htf_next'], t['market_regime']) == ('opposed', 'aligned', 'spy_above_50d')
    assert t['confluence'] == 'minority_aligned'          # 1 of 3 others aligned
    assert t['time_of_day'] == '09:30-10:30'              # 14:40 UTC = 09:40 New York in March (EST)
    assert t['period'] == C.SEEN_LABEL
    tls = {('X', '5m'): _Fixed('short'), ('X', '15m'): _Fixed('short'), ('X', '1h'): _Fixed('short')}
    d = C.label_trades([_trade(tf='1d', direction='short', when='2026-11-02T21:00:00+00:00')], tls)[0]
    assert d['htf_daily'] == 'not_applicable' and d['time_of_day'] == 'not_applicable' and d['htf_next'] == 'not_applicable'
    assert d['confluence'] == 'all_aligned' and d['period'] == C.UNSEEN_LABEL and d['market_regime'] == 'unknown'
    missing = C.label_trades([_trade(tf='15m')], {('X', '1h'): _Fixed('long'), ('X', '1d'): _Fixed('long')})[0]
    assert missing['confluence'] == 'incomplete' and missing['htf_daily'] == 'aligned'


def test_time_buckets_follow_new_york_time_across_daylight_saving():
    assert C.time_bucket('2026-07-01T13:35:00+00:00') == '09:30-10:30'   # EDT
    assert C.time_bucket('2026-07-01T14:35:00+00:00') == '10:30-12:00'
    assert C.time_bucket('2026-12-01T20:55:00+00:00') == '15:00-16:00'   # EST, last hour
    # A bar CLOSING exactly on a boundary belongs to the bucket it traded in (14:55-15:00 bar).
    assert C.time_bucket('2026-12-01T20:00:00+00:00') == '14:00-15:00'
    assert C.time_bucket('2026-12-01T17:30:00+00:00') == '12:00-14:00'
    assert C.time_bucket('2026-12-01T21:00:00+00:00') == '15:00-16:00'   # the 15:55 bar closes at 16:00
    assert C.time_bucket('2026-12-01T13:00:00+00:00') == 'outside_regular_session'   # 08:00 New York
    assert C.time_bucket('2026-12-01T21:05:00+00:00') == 'outside_regular_session'


def test_volatility_thirds_are_computed_within_each_timeframe():
    trades = [_trade(tf='5m', atr_pct=x) for x in (.001, .002, .003, .004, .005, .006)] + \
             [_trade(tf='1d', atr_pct=x) for x in (.02, .03, .04)]
    C.label_trades(trades, {})
    assert [t['volatility'] for t in trades[:6]] == ['low', 'low', 'mid', 'mid', 'high', 'high']
    assert [t['volatility'] for t in trades[6:]] == ['low', 'mid', 'high']


def test_difference_ci_separates_real_differences_and_not_noise():
    rng = random.Random(1)
    a = [_trade(sym=f'S{k}', r=1. + rng.gauss(0, .2)) for k in range(40) for _ in range(3)]
    b = [_trade(sym=f'S{k}', r=rng.gauss(0, .2)) for k in range(40) for _ in range(3)]
    point, ci = C.difference_ci(a, b, random.Random(2))
    assert point == pytest.approx(1., abs=.1) and ci[0] > 0
    point, ci = C.difference_ci(a, a, random.Random(2))
    assert point == 0 and ci[0] <= 0 <= ci[1]


def test_excursions_bound_the_realised_result():
    found = 0
    for seed in range(4):
        _, trades, _, _ = run_segment('X', '5m', research_bars(walk(seed, 2500, T0, 5)), Config())
        for t in trades:
            assert t['mfe_r'] >= 0 and t['mae_r'] >= 0
            assert t['mfe_r'] >= t['r'] - 1e-9                       # gross >= net
            assert t['mae_r'] >= -t['r'] - t['cost_r'] - 1e-9        # adverse >= realised gross loss
            assert t['signal_time'] <= t['entry_time'] or t['signal_time'][:16] <= t['entry_time'][:16]
            found += 1
    assert found >= 5


def _synthetic_run():
    streams = []
    for k, sym in enumerate(['A', 'B', 'C', 'SPY']):
        streams.append((sym, '5m', [research_bars(walk(10 + k, 1800, T0, 5))]))
        streams.append((sym, '1h', [research_bars(walk(20 + k, 600, T0, 60))]))
        streams.append((sym, '1d', [research_bars(walk(30 + k, 300, T0 - timedelta(days=200), 1440, drift=.05))]))
    results = evaluate_streams(streams, controls=5, only={'baseline', 'line_stop'})
    tls = C.trend_timelines(streams); regime = C.regime_timeline(streams)
    for res in results.values():
        C.label_trades(res['trades'], tls, regime)
    rep = dict(strategy_version='V', config_hash='h', snapshot=dict(exported_at='x', coverage={}), assumptions={'a': 'b'},
               variants=report(results, slices=C.CONTEXT_SLICES))
    return results, rep


def test_end_to_end_summary_and_markdown_are_complete_and_serialisable():
    results, rep = _synthetic_run()
    base = rep['variants']['baseline']
    for field in C.CONTEXT_SLICES:
        assert f'by_{field}' in base
    assert 'by_symbol' in base and base['overall']['mean_mfe_r'] is not None
    hyps = C.hypotheses(results['baseline']['trades'])
    assert [h['id'] for h in hyps] == ['H3', 'H1', 'H2', 'H4', 'H5'] and hyps[0]['role'] == 'primary'
    assert all(h['verdict'] for h in hyps)
    score = C.scorecard(base)
    assert score['expected_by_chance_each_side'] == round(.025 * score['cells_tested'], 1)
    summary = C.build_summary(rep, hyps, score)
    json.dumps(summary)
    assert {v['name'] for v in summary['variants']} == {'baseline', 'line_stop'}
    md = C.render_markdown(summary)
    assert 'Predeclared questions' in md and 'H3' in md and 'scorecard' in md


def test_small_groups_are_reported_as_insufficient_not_as_findings():
    trades = [_trade(sym=f'S{k}', r=5., ctrl=0.) for k in range(10)]
    for t in trades:
        t.update(htf_daily='aligned', htf_next='aligned', confluence='all_aligned', period=C.UNSEEN_LABEL)
    hyps = {h['id']: h for h in C.hypotheses(trades)}
    assert hyps['H2']['verdict'] == 'insufficient sample'
    assert hyps['H3']['verdict'] == 'insufficient sample'


def test_scorecard_counts_only_cells_with_enough_trades_and_compares_with_chance():
    cell = lambda n, v: dict(trades=n, edge_vs_control_r=.1, edge_ci95_by_symbol=[.01, .2], verdict=v)
    rep = dict(overall=cell(500, 'better than control'),
               by_timeframe={'5m': cell(40, 'better than control'), '1h': cell(10, 'better than control')},
               by_month={f'm{k}': cell(35, 'no measurable edge vs control') for k in range(38)} | {'w': cell(31, 'worse than control')})
    sc = C.scorecard(rep)
    assert sc['cells_tested'] == 40 and sc['better'] == 1 and sc['worse'] == 1
    assert sc['expected_by_chance_each_side'] == 1.0
    assert sc['better_cells'][0]['cell'] == '5m'
