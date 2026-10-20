"""Evaluation framework: real engine trades, matched controls and no look-ahead."""
import random
from datetime import datetime, timedelta, timezone
import pytest
from src.services.little_rzy import Config
from src.services.rzy_evaluation import (research_bars, run_segment, simulate_control, evaluate_streams,
                                         report, summarize, VARIANTS)


def walk(seed, n=2500, drift=0.0):
    rng = random.Random(seed); p = 100.; t = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc); out = []
    for k in range(n):
        o = p; c = max(1, o + rng.gauss(drift, .5)); h = max(o, c) + abs(rng.gauss(0, .25)); l = min(o, c) - abs(rng.gauss(0, .25)); p = c
        out.append(dict(time=t.isoformat(), closed_at=(t + timedelta(minutes=5)).isoformat(), complete=True,
                        open=o, high=h, low=l, close=c, volume=1000.)); t += timedelta(minutes=5)
    return out


def flat(n, price=100.):
    t = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    return [dict(time=(t + timedelta(minutes=5 * k)).isoformat(), closed_at=(t + timedelta(minutes=5 * k + 5)).isoformat(),
                 complete=True, open=price, high=price + .1, low=price - .1, close=price, volume=1000.) for k in range(n)]


def test_trade_r_reconciles_with_engine_net_and_exit_reasons_are_engine_events():
    cfg = Config()
    found = 0
    for seed in range(4):
        engine, trades, cancelled, _ = run_segment('X', '5m', research_bars(walk(seed)), cfg)
        for t in trades:
            pos = engine.s['positions'][next(k for k, p in engine.s['positions'].items() if p.get('entry_index') == t['entry_index'])]
            assert t['r'] == pytest.approx(pos['net'] / t['risk']) and t['risk'] > 0
            assert t['exit_reason'] in ('target', 'stop', 'line_invalidation')
            assert t['holding_bars'] >= 1 and t['stop_atr'] > 0 and t['target_atr'] > 0
            found += 1
    assert found >= 5


def test_control_bracket_target_stop_first_gap_and_time_exit():
    cfg = Config(); fr = (cfg.spread_bps / 2 + cfg.slippage_bps) / 1e4
    inds = [dict(atr=1.0)] * 30
    # Target only: +2 ATR long reached on bar 3.
    bars = flat(30); bars[3].update(high=103.5, close=103)
    r = simulate_control(bars, inds, 0, 'long', 1.0, 2.0, 10, cfg)
    fill = 100 * (1 + fr); risk = 1.0
    assert r == pytest.approx(((fill + 2) * (1 - fr) - fill - 2 * cfg.commission_per_share) / risk)
    # Both touched in one bar: stop is assumed first.
    bars = flat(30); bars[2].update(high=103, low=98)
    assert simulate_control(bars, inds, 0, 'long', 1.0, 2.0, 10, cfg) < 0
    # Gap through the stop fills at the worse open.
    bars = flat(30); bars[2].update(open=95, high=95.2, low=94.8, close=95)
    r = simulate_control(bars, inds, 0, 'long', 1.0, 2.0, 10, cfg)
    assert r == pytest.approx((95 * (1 - fr) - fill - 2 * cfg.commission_per_share) / risk)
    # Neither: exit at the close of the last holding bar.
    bars = flat(30); bars[5]['close'] = 100.4
    r = simulate_control(bars, inds, 0, 'long', 1.0, 2.0, 5, cfg)
    assert r == pytest.approx((100.4 * (1 - fr) - fill - 2 * cfg.commission_per_share) / risk)


def test_control_cannot_see_beyond_its_holding_window():
    cfg = Config(); inds = [dict(atr=1.0)] * 30
    a = flat(30); b = flat(30); b[20].update(high=150, low=50)
    assert simulate_control(a, inds, 0, 'short', 1.0, 2.0, 10, cfg) == simulate_control(b, inds, 0, 'short', 1.0, 2.0, 10, cfg)


def test_random_walk_shows_no_false_edge_and_every_variant_is_reported():
    streams = [(f'S{seed}', '5m', [research_bars(walk(seed, 2000))]) for seed in range(12)]
    results = evaluate_streams(streams, controls=10)
    assert set(results) == set(VARIANTS(Config()))
    rep = report(results)
    base = rep['baseline']['overall']
    assert base['trades'] >= 30
    assert base['verdict'] != 'better than control', base
    assert set(rep['baseline']) >= {'overall', 'long_only', 'by_timeframe', 'by_direction', 'by_month', 'by_sequence', 'cancelled'}


def test_small_samples_are_labelled_insufficient():
    trades = [dict(symbol='S', r=1., ret=.01, holding_bars=3, exit_time='2026-01-01', exit_reason='target', control_mean_r=0.)] * 5
    assert summarize(trades, random.Random(1))['verdict'] == 'insufficient sample (<30 trades)'


def _short_case(cfg):
    from datetime import datetime as _d
    bars = flat(12); inds = [dict(atr=1.0)] * 12
    bars[1].update(open=100, high=101.5, low=99.9, close=101.2)   # short stop 101 hit on the entry bar
    return bars, inds


def test_rza09_short_control_borrow_stops_at_the_actual_exit_bar():
    cfg = Config(commission_per_share=0, spread_bps=0, slippage_bps=0)
    bars, inds = _short_case(cfg)
    r5 = simulate_control(bars, inds, 0, 'short', 1.0, 2.0, 5, cfg, borrow=.5)
    r10 = simulate_control(bars, inds, 0, 'short', 1.0, 2.0, 10, cfg, borrow=.5)
    # Exit on bar 1 after 300 s whatever the cap; borrow = 100 * 0.5 * 300 / year.
    expected = -1 - 100 * .5 * 300 / (365 * 86400)
    assert r5 == pytest.approx(expected) and r10 == pytest.approx(expected)
    # A time exit still accrues borrow for its full window.
    bars = flat(12)
    held = simulate_control(bars, [dict(atr=1.0)] * 12, 0, 'short', 1.0, 2.0, 5, cfg, borrow=.5)
    assert held == pytest.approx(-100 * .5 * 1500 / (365 * 86400))


def test_rza10_controls_never_use_a_shortened_window():
    cfg = Config(); bars = flat(12); inds = [dict(atr=1.0)] * 12
    assert simulate_control(bars, inds, 10, 'long', 1.0, 2.0, 10, cfg) is None
    assert simulate_control(bars, inds, 1, 'long', 1.0, 2.0, 10, cfg) is not None
    from src.services.rzy_evaluation import matched_controls
    trade = dict(holding_bars=10, trigger_index=-1, direction='long', stop_atr=1.0, target_atr=2.0)
    picked = []
    class Spy(random.Random):
        def sample(self, pop, k): picked.extend(pop); return super().sample(pop, k)
    matched_controls(trade, bars, inds, cfg, Spy(3), k=50)
    assert picked and max(picked) + 10 <= len(bars) - 1


def test_stop_target_only_research_variant_drops_only_the_line_exit():
    from src.services.rzy_evaluation import _FullHistoryStopTargetOnly
    from src.services.little_rzy import Engine
    assert Engine.LINE_EXIT is True and _FullHistoryStopTargetOnly.LINE_EXIT is False
    cfg = Config(); reasons = set(); base = set()
    for seed in range(4):
        bars = research_bars(walk(seed))
        _, tr, _, _ = run_segment('X', '5m', bars, cfg, _FullHistoryStopTargetOnly); reasons |= {t['exit_reason'] for t in tr}
        _, tr, _, _ = run_segment('X', '5m', bars, cfg); base |= {t['exit_reason'] for t in tr}
    assert 'line_invalidation' in base and 'line_invalidation' not in reasons and reasons <= {'target', 'stop'}


def test_edge_statistic_detects_a_planted_edge_and_widens_under_symbol_dependence():
    rng = random.Random(5)
    def trades(edge, symbols, per):
        out = []
        for s in range(symbols):
            shared = rng.gauss(0, .6)            # symbol-level effect: trades within a symbol co-move
            for _ in range(per):
                c = rng.gauss(-.1, .3); out.append(dict(symbol=f'S{s}', r=c + edge + shared + rng.gauss(0, .8), control_mean_r=c,
                                                        ret=.0, holding_bars=4, exit_time='2026-01-01', exit_reason='stop', cost_r=.1))
        return out
    planted = summarize(trades(.6, 60, 10), random.Random(1))
    assert planted['verdict'] == 'better than control'
    null = [summarize(trades(0., 60, 10), random.Random(i))['verdict'] for i in range(5)]
    assert 'better than control' not in null
    s = summarize(trades(0., 20, 30), random.Random(2))
    by_trade, by_symbol = s['edge_ci95_by_trade'], s['edge_ci95_by_symbol']
    assert (by_symbol[1] - by_symbol[0]) > 2 * (by_trade[1] - by_trade[0])
    assert s['min_detectable_edge_r'] > 0
