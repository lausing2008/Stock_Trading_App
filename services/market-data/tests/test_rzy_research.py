"""Research variants: the default is the shipped engine; gates and exits do what they say."""
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from src.services.little_rzy import Config
from src.services.rzy_evaluation import research_bars, run_segment, _FullHistoryStopTargetOnly
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))  # research code lives outside the service image
from rzy_research_engine import ResearchEngine, Series, run_stream, trigger_features, gate_reason, ContextControls  # noqa: E402

T0 = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)


def walk(seed, n=3000):
    rng = random.Random(seed); p = 100.; t = T0; out = []
    for _ in range(n):
        o = p; c = max(1, o + rng.gauss(0, .5)); h = max(o, c) + abs(rng.gauss(0, .25)); l = min(o, c) - abs(rng.gauss(0, .25)); p = c
        out.append(dict(time=t.isoformat(), closed_at=(t + timedelta(minutes=5)).isoformat(), complete=True,
                        open=o, high=h, low=l, close=c, volume=1000. + rng.random() * 1000)); t += timedelta(minutes=5)
    return research_bars(out)


def key(t):
    return (t['entry_index'], t['exit_index'], round(t['r'], 12), t['exit_reason'])


def trades(seeds=range(8), **kw):
    out = []
    for seed in seeds:
        out.extend(run_stream('X', '5m', walk(seed), Config(), **kw)[1])
    return out


def test_default_research_engine_is_the_shipped_engine_trade_for_trade():
    for seed in range(6):
        bars = walk(seed)
        base = run_segment('X', '5m', bars, Config())[1]
        assert [key(t) for t in run_stream('X', '5m', bars, Config())[1]] == [key(t) for t in base]
        # A gate that never refuses and exit options equal to the defaults change nothing either.
        same = run_stream('X', '5m', bars, Config(), gates=dict(min_price=0.01), exits=dict(line_exit='close'))[1]
        assert [key(t) for t in same] == [key(t) for t in base]
    assert len(trades()) >= 10


def test_line_exit_off_equals_the_existing_stop_target_only_variant():
    for seed in range(6):
        bars = walk(seed)
        ref = run_segment('X', '5m', bars, Config(), _FullHistoryStopTargetOnly)[1]
        got = run_stream('X', '5m', bars, Config(), exits=dict(line_exit='off'))[1]
        assert [key(t) for t in got] == [key(t) for t in ref]


def test_gate_refuses_before_any_position_and_frees_the_stream():
    cfg = Config()
    blocked = 0
    for seed in range(10):
        bars = walk(seed)
        base = run_stream('X', '5m', bars, cfg)[1]
        engine, got = run_stream('X', '5m', bars, cfg, gates=dict(long_only=True))
        assert all(t['direction'] == 'long' for t in got)
        blocked += engine.blocked.get('long_only', 0)
        assert not any(p['state'] != 'CLOSED' and engine.s['structures'][k]['direction'] == 'short' for k, p in engine.s['positions'].items())
        refused = [s for s in engine.s['structures'].values() if s.get('reason') == 'research_gate:long_only']
        assert all(s['id'] not in engine.s['positions'] and s['state'] == 'REJECTED' for s in refused)
        # Refusing shorts can only ADD longs (ones the baseline rejected as overlapping an open
        # short); it never removes one. Rare: none in these walks or on the real daily set.
        assert {t['entry_index'] for t in base if t['direction'] == 'long'} <= {t['entry_index'] for t in got}
    assert blocked >= 5


def test_every_gate_rule_is_enforced_on_the_trades_it_lets_through():
    got = trades(gates=dict(max_vol_ratio=1.0, max_cost_r=0.5, sma50=True, min_stop_atr=1.0))
    assert got
    for t in got:
        assert t['vol_ratio'] < 1.0 and t['cost_r_plan'] <= 0.5 and t['sma50'] is True and t['stop_atr_plan'] >= 1.0


def test_unknown_context_fails_a_gate_instead_of_passing_it():
    f = dict(htf='unknown', regime=None, vol_ratio=None, cost_r_plan=None, price=10., sma200=None, sma50=None,
             mom120=None, rs63=None, stop_atr_plan=2., slope_atr=.1, tod='10:30-12:00', d_sma200=None, d_sma50=None, d_mom126=None)
    for gate in (dict(htf_aligned=True), dict(regime=True), dict(max_vol_ratio=1.), dict(max_cost_r=.1), dict(sma200=True),
                 dict(min_mom120=0.), dict(min_rs63=0.), dict(d_sma200=True), dict(d_sma50=True), dict(min_d_mom126=0.)):
        assert gate_reason(f, 'long', gate) is not None, gate
    assert gate_reason(f, 'long', {}) is None and gate_reason(f, 'long', dict(htf_not_opposed=True)) is None


def test_features_ignore_context_stamped_after_the_trigger_close():
    bars = walk(3)
    times = [datetime.fromisoformat(b['closed_at']) for b in bars]
    closes = [b['close'] for b in bars]
    def run(shift):
        # `shift` rewrites every context value stamped AFTER each query time: a causal reader cannot see it.
        class Shifted(Series):
            def at(self, when, back=0):
                return super().at(when, back)
        daily = Shifted(times, closes); market = Shifted(times, [c * 2 for c in closes])
        regime = Shifted(times, ['above' if k % 3 else 'below' for k in range(len(times))])
        engine, got = run_stream('X', '5m', bars, Config(), context=dict(daily=daily, market=market, regime=regime))
        if shift:
            for t in got:
                cut = datetime.fromisoformat(t['signal_time'])
                k = daily.index(cut)
                future = Series(times[:k + 1] + times[k + 1:], closes[:k + 1] + [c * 9 for c in closes[k + 1:]])
                st = next(s for s in engine.s['structures'].values() if s.get('features') and engine.s['positions'].get(s['id'], {}).get('trigger_index') == t['trigger_index'])
                # Recompute at the trigger with the future rewritten; the features must not move.
                probe = ResearchEngine('X', '5m', Config(), context=dict(daily=future, market=market, regime=regime))
                for b in bars[:t['trigger_index'] + 1]:
                    probe.step(b)
                again = trigger_features(probe, probe.s['structures'][st['id']], t['trigger_index'])
                assert again['rs63'] == t['rs63'] and again['regime'] == t['regime'] and again['vol_ratio'] == t['vol_ratio']
                assert again['d_sma200'] == t['d_sma200'] and again['d_mom126'] == t['d_mom126'] and t['d_sma200'] is not None
        return got
    assert len(run(True)) >= 1
    s = Series([T0, T0 + timedelta(hours=1)], [1., 2.])
    assert s.at(T0 - timedelta(seconds=1)) is None and s.at(T0) == 1. and s.at(T0 + timedelta(minutes=59)) == 1. and s.at(T0 + timedelta(hours=1)) == 2.
    assert s.at(T0 + timedelta(hours=2), back=1) == 1. and s.at(T0, back=1) is None


def test_exit_rules_exit_when_and_only_when_they_say():
    capped = trades(exits=dict(target_r=1.0))
    hit = [t for t in capped if t['exit_reason'] == 'target']
    # MFE excludes the exit bar's own high, so it is the realised gross: 1R less exit friction.
    assert hit and all(t['mfe_r'] >= 0.95 and t['r'] < 1 for t in hit)
    assert all(0.9 < t['r'] < 1 and t['target_atr'] == pytest.approx(t['stop_atr']) for t in hit)
    timed = trades(exits=dict(time_stop_bars=5, time_stop_min_mfe_r=0.5))
    stopped = [t for t in timed if t['exit_reason'] == 'time_stop']
    assert stopped and all(t['holding_bars'] == 6 for t in stopped)
    two = trades(exits=dict(line_exit='two_closes'))
    assert any(t['exit_reason'] == 'line_two_closes' for t in two) and not any(t['exit_reason'] == 'line_invalidation' for t in two)
    buf = trades(exits=dict(line_exit='buffer', line_buffer_atr=0.25))
    assert any(t['exit_reason'] == 'line_buffer' for t in buf)
    # Looser line exits hold at least as long as the live rule on the same entries.
    live = {t['entry_index']: t for t in trades(seeds=[1])}
    for t in trades(seeds=[1], exits=dict(line_exit='two_closes')):
        if t['entry_index'] in live and live[t['entry_index']]['exit_reason'] == 'line_invalidation':
            assert t['holding_bars'] >= live[t['entry_index']]['holding_bars']
    part = trades(exits=dict(partial_at_r=1.0))
    banked = [t for t in part if t['partial_taken']]
    # Half is banked at +1R and the rest cannot give back more than breakeven (no gaps in this walk).
    assert banked and all(t['r'] > 0.25 and t['mfe_r'] >= 0.95 for t in banked)
    stopped = [t for t in banked if t['exit_reason'] in ('stop', 'breakeven_stop')]
    assert all(t['r'] < 0.5 for t in stopped)
    assert not any(t['partial_taken'] for t in trades(seeds=[1]))


def test_unknown_exit_option_is_refused():
    with pytest.raises(ValueError):
        ResearchEngine('X', '5m', Config(), exits=dict(trailing=1))
    with pytest.raises(ValueError):
        ResearchEngine('X', '5m', Config(), exits=dict(line_exit='sometimes'))


def test_flat_at_close_never_carries_a_position_past_a_marked_session_end():
    N = 20  # a short synthetic "session" so that live trades do cross its end
    live, flat = [], []
    for seed in range(8):
        bars = walk(seed)
        for k, b in enumerate(bars):
            b['session_last'] = k % N == N - 1
        live += run_stream('X', '5m', bars, Config())[1]
        flat += run_stream('X', '5m', bars, Config(), exits=dict(flat_at_close=True))[1]
    assert any(t['entry_index'] // N != t['exit_index'] // N for t in live)
    assert flat and all(t['entry_index'] // N == t['exit_index'] // N for t in flat)
    closed = [t for t in flat if t['exit_reason'] == 'session_close']
    assert closed and all(t['exit_index'] % N == N - 1 for t in closed)


def test_context_controls_are_drawn_only_from_the_trades_own_context():
    cfg = Config(); seen = 0
    for seed in range(6):
        bars = walk(seed)
        engine, got = run_stream('X', '5m', bars, cfg)
        assert len(engine.trend_at) == len(bars)
        cc = ContextControls(bars, engine)
        for t in got:
            picked = []
            class Spy(random.Random):
                def sample(self, population, k):
                    picked.extend(population); return super().sample(population, k)
            out = cc.draw(t, cfg, Spy(1), 20)
            seen += len(out)
            want_above = None if t['sma200'] is None else (t['sma200'] == (t['direction'] == 'long'))
            assert all(engine.trend_at[k] == t['direction'] and cc.above[k] == want_above and k != t['trigger_index'] for k in picked)
            # Every drawn twin's whole holding window is inside the segment.
            assert all(k + t['holding_bars'] <= len(bars) - 1 for k in picked)
    assert seen >= 20
    # The 200-bar side is the plain average of the last 200 closes, known at that bar.
    k = 777
    assert cc.above[k] == (bars[k]['close'] > sum(b['close'] for b in bars[k - 199:k + 1]) / 200) and cc.above[198] is None
