"""Little RZY research runner: replay gate and exit variants over saved bars, in parallel.

Research only; reads files, never the database. Steps:

  prepare <bars_dir> <data_dir> [--market <1d.jsonl.gz> --market-symbol SPY]
      Split <bars_dir>/<tf>.jsonl.gz (or an evaluation snapshot's bars.jsonl.gz) into one file per
      symbol, derive 15m from 5m when 15m is absent, derive weekly bars from daily, and write
      market.json (the market proxy's daily closes).
  run <data_dir> <out_dir> --variants a,b --tfs 1d,1h [--workers 10]
      One row per closed trade and variant in <out_dir>/trades.csv, with its causal trigger
      features and the mean R of 20 random-timing twins; run.json records inputs and counts.

Variants are defined in VARIANTS below; `baseline` is the shipped rules.
"""
import argparse
import csv
import gzip
import hashlib
import json
import os
import pickle
import random
import sys
import time
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / 'services/market-data'), str(REPO / 'shared')]
FIELDS = ('time', 'closed_at', 'open', 'high', 'low', 'close', 'volume', 'price_basis')
INTRADAY = ('5m', '15m', '1h')
NY = ZoneInfo('America/New_York')
CONTROLS = 20
SEED = 7

# name -> dict(gates=..., exits=..., tfs=optional tuple). Thresholds are fixed here, before a
# confirmation run; see docs/2026-11-10/rzy-improvement-preregistration.md.
CONFIG_SWITCHES = ('sequence_filter', 'bollinger_context', 'no_chase_filter')
LT = dict(long_only=True, d_sma200=True)   # long, and the daily close above its 200-day average
OFF = dict(line_exit='off')                # exit only at the stop or the target
VARIANTS = {
    'baseline': {},
    # Daily "long with the trend" family, explored on the design set.
    'l_off': dict(gates=dict(long_only=True), exits=OFF),
    'lt_base': dict(gates=LT),
    'lt_off': dict(gates=LT, exits=OFF),
    'lt_off_t2': dict(gates=LT, exits=dict(OFF, target_r=2.0)),
    'lt_off_t3': dict(gates=LT, exits=dict(OFF, target_r=3.0)),
    'lt_buf100': dict(gates=LT, exits=dict(line_exit='buffer', line_buffer_atr=1.0)),
    'lt_buf200': dict(gates=LT, exits=dict(line_exit='buffer', line_buffer_atr=2.0)),
    'lt_off_be1': dict(gates=LT, exits=dict(OFF, breakeven_at_r=1.0)),
    'lt_off_part1': dict(gates=LT, exits=dict(OFF, partial_at_r=1.0)),
    'lt_off_time40': dict(gates=LT, exits=dict(OFF, time_stop_bars=40, time_stop_min_mfe_r=0.5)),
    'lt_off_seqall': dict(gates=LT, exits=OFF, config=dict(sequence_filter=False)),
    'lt_off_nobb': dict(gates=LT, exits=OFF, config=dict(bollinger_context=False)),
    'lt_off_mom': dict(gates=dict(LT, min_d_mom126=0.0), exits=OFF),
    'lt_off_regime': dict(gates=dict(LT, regime=True), exits=OFF),
    't_off': dict(gates=dict(d_sma200=True), exits=OFF),
    # The 2026-11-08 plan's Phase 1 filters (V1-V4), tested on independent data.
    'v1_htf': dict(gates=dict(htf_aligned=True)),
    'v2_htf_vol': dict(gates=dict(htf_aligned=True, max_vol_ratio=1.0)),
    'v3_htf_regime': dict(gates=dict(htf_aligned=True, regime=True)),
    'v4_cost': dict(gates=dict(max_cost_r=0.10, min_price=10.0)),
    # Exit rules explored on the design set (entries unchanged).
    'x_buffer25': dict(exits=dict(line_exit='buffer', line_buffer_atr=0.25)),
    'x_buffer50': dict(exits=dict(line_exit='buffer', line_buffer_atr=0.50)),
    'x_two_closes': dict(exits=dict(line_exit='two_closes')),
    'x_off': dict(exits=dict(line_exit='off')),
    'x_partial1': dict(exits=dict(partial_at_r=1.0)),
    'x_be1': dict(exits=dict(breakeven_at_r=1.0)),
    'x_time10': dict(exits=dict(time_stop_bars=10, time_stop_min_mfe_r=0.5)),
    'x_target2': dict(exits=dict(target_r=2.0)),
    'x_target1': dict(exits=dict(target_r=1.0)),
    'x_two_partial1': dict(exits=dict(line_exit='two_closes', partial_at_r=1.0)),
    'x_off_target2': dict(exits=dict(line_exit='off', target_r=2.0)),
    'x_off_target1': dict(exits=dict(line_exit='off', target_r=1.0)),
    # Intraday design exploration: overnight gaps and costs.
    'x_flat': dict(exits=dict(flat_at_close=True)),
    'x_flat_nolast': dict(exits=dict(flat_at_close=True), gates=dict(skip_tod=('15:00-16:00',))),
    'g_cost10': dict(gates=dict(max_cost_r=0.10)),
    'x_flat_nolast_cost10': dict(exits=dict(flat_at_close=True), gates=dict(skip_tod=('15:00-16:00',), max_cost_r=0.10)),
    'x_flat_off': dict(exits=dict(flat_at_close=True, line_exit='off')),
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _weekly(seg):
    weeks = {}
    for b in seg:
        weeks.setdefault(datetime.fromisoformat(b[0]).isocalendar()[:2], []).append(b)
    out = []
    keys = sorted(weeks)
    for k in keys:
        w = weeks[k]
        # The segment's last week is kept only when it ended on a Friday (otherwise incomplete).
        if k == keys[-1] and datetime.fromisoformat(w[-1][0]).weekday() != 4:
            continue
        out.append([w[0][0], w[-1][1], w[0][2], max(x[3] for x in w), min(x[4] for x in w), w[-1][5], sum(x[6] for x in w), w[0][7]])
    return out


def _fifteen(seg):
    from zoneinfo import ZoneInfo
    ny = ZoneInfo('America/New_York')
    days = {}
    for b in seg:
        days.setdefault(datetime.fromisoformat(b[0]).astimezone(ny).date(), []).append(b)
    out = []
    for day in sorted(days):
        d = days[day]
        for k in range(0, len(d) - len(d) % 3, 3):
            w = d[k:k + 3]
            if w[0][1] != w[1][0] or w[1][1] != w[2][0]:
                return []  # never bridge a gap; the fetcher only writes contiguous sessions
            out.append([w[0][0], w[2][1], w[0][2], max(x[3] for x in w), min(x[4] for x in w), w[2][5], sum(x[6] for x in w), w[0][7]])
    return out


def prepare(bars_dir, data_dir, market_file=None, market_symbol='SPY'):
    """Streams each input file (rows of one symbol and timeframe are adjacent), so memory stays
    small. One file per symbol and timeframe: symbols/<symbol>.<tf>.json.gz."""
    out_dir = os.path.join(data_dir, 'symbols'); os.makedirs(out_dir, exist_ok=True)
    inputs, counts, symbols, market = {}, {}, set(), None

    def flush(symbol, tf, segs):
        if not segs:
            return
        path = os.path.join(out_dir, f'{symbol}.{tf}.json.gz')
        if os.path.exists(path):
            raise SystemExit(f'{symbol} {tf} appears twice in the inputs')
        with gzip.open(path, 'wt') as f:
            json.dump(segs, f)
        c = counts.setdefault(tf, dict(symbols=0, segments=0, bars=0))
        c['symbols'] += 1; c['segments'] += len(segs); c['bars'] += sum(map(len, segs)); symbols.add(symbol)

    for path in sorted(Path(bars_dir).glob('*.jsonl.gz')):
        inputs[path.name] = sha256(path)
        has_15m = False
        with gzip.open(path, 'rt') as f:
            has_15m = any('"timeframe": "15m"' in line for line in f)
        key, segs = None, []
        def close(key, segs):
            if key is None:
                return
            symbol, tf = key
            flush(symbol, tf, segs)
            if tf == '5m' and not has_15m:
                flush(symbol, '15m', [x for x in (_fifteen(seg) for seg in segs) if len(x) >= 60])
            if tf == '1d':
                flush(symbol, '1w', [x for x in (_weekly(seg) for seg in segs) if len(x) >= 30])
        with gzip.open(path, 'rt') as f:
            for line in f:
                row = json.loads(line)
                if (row['symbol'], row['timeframe']) != key:
                    close(key, segs); key, segs = (row['symbol'], row['timeframe']), []
                segs.append(row['bars'])
                if not market_file and row['symbol'] == market_symbol and row['timeframe'] == '1d':
                    market = (market or []) + [[b[1], b[5]] for b in row['bars']]
        close(key, segs)
    if market_file:
        with gzip.open(market_file, 'rt') as f:
            for line in f:
                row = json.loads(line)
                if row['symbol'] == market_symbol and row['timeframe'] == '1d':
                    market = (market or []) + [[b[1], b[5]] for b in row['bars']]
        inputs['market:' + Path(market_file).name] = sha256(market_file)
    json.dump(dict(symbol=market_symbol, closes=market), open(os.path.join(data_dir, 'market.json'), 'w'))
    meta = dict(prepared_at=datetime.now(timezone.utc).isoformat(), inputs=inputs, counts=counts, symbols=len(symbols),
                market_symbol=market_symbol, market_bars=len(market or []))
    json.dump(meta, open(os.path.join(data_dir, 'prepared.json'), 'w'), indent=1)
    print(json.dumps(meta['counts']), 'market bars', meta['market_bars'])


_G = {}


def _init(data_dir, variant_names, tfs):
    from src.services.rzy_research import Series
    market = json.load(open(os.path.join(data_dir, 'market.json')))['closes'] or []
    times = [datetime.fromisoformat(t) for t, _ in market]; closes = [c for _, c in market]
    regime = [None if k < 49 else ('above' if closes[k] > sum(closes[k - 49:k + 1]) / 50 else 'below') for k in range(len(closes))]
    _G.update(data_dir=data_dir, variants=variant_names, tfs=tfs, market=Series(times, closes), regime=Series(times, regime))


def _bars(seg, intraday=False):
    from src.services.rzy_evaluation import BORROW_RATE
    out = [dict(zip(FIELDS, b), complete=True, provider='research_file', delay='unknown', session='REGULAR',
                freshness='historical_completed', execution_eligible=True, shortable=True, borrow_rate_annual=BORROW_RATE) for b in seg]
    if intraday:
        # Calendar fact used by the flat_at_close exit: the last bar of each New York session.
        days = [datetime.fromisoformat(b['time']).astimezone(NY).date() for b in out]
        for k, b in enumerate(out):
            b['session_last'] = k + 1 == len(out) or days[k + 1] != days[k]
    return out


for _name, _v in VARIANTS.items():
    assert set(_v.get('config', {})) <= set(CONFIG_SWITCHES), _name


def _symbol(symbol):
    from src.services.little_rzy import Config
    from src.services.rzy_evaluation import matched_controls
    from dataclasses import replace
    from src.services.rzy_research import run_stream, Series, ContextControls
    from src.services import rzy_evaluation_context as C
    cfg = Config()
    streams = {}
    for tf in sorted(set(_G['tfs']) | {'1d', '1w'}):
        path = os.path.join(_G['data_dir'], 'symbols', f'{symbol}.{tf}.json.gz')
        if os.path.exists(path):
            with gzip.open(path, 'rt') as f:
                streams[tf] = [_bars(seg, tf in INTRADAY) for seg in json.load(f)]
    C.GRACE.setdefault('1w', C.GRACE['1d'] * 2)
    # Speed only: trend timelines and indicators depend on bars and config alone, so they are
    # computed once per data set and reused by every variant (keyed by the config hash).
    cache_dir = os.path.join(_G['data_dir'], 'cache', cfg.hash); os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f'{symbol}.pkl')
    cache = pickle.load(open(cache_path, 'rb')) if os.path.exists(cache_path) else {}
    dirty = False
    if 'timelines' not in cache:
        cache['timelines'] = C.trend_timelines([(symbol, tf, streams[tf]) for tf in ('1d', '1w') if streams.get(tf)], cfg); dirty = True
    timelines = cache['timelines']
    daily = [b for seg in streams.get('1d', []) for b in seg]
    daily_series = Series([datetime.fromisoformat(b['closed_at']) for b in daily], [b['close'] for b in daily])
    rows, stats = [], {}
    for tf in _G['tfs']:
        context = dict(htf=timelines.get((symbol, '1w' if tf == '1d' else '1d')), regime=_G['regime'],
                       daily=daily_series, market=_G['market'])
        for k, seg in enumerate(streams.get(tf, [])):
            for name in _G['variants']:
                v = VARIANTS[name]
                if v.get('tfs') and tf not in v['tfs']:
                    continue
                # Config overrides are limited to rule switches: indicators (and their cache) are unchanged.
                vcfg = replace(cfg, **v['config']) if v.get('config') else cfg
                engine, trades = run_stream(symbol, tf, seg, vcfg, v.get('gates'), v.get('exits'), context, cache.get((tf, k)))
                if (tf, k) not in cache:
                    cache[(tf, k)] = engine.s['indicators']; dirty = True
                rng = random.Random(f'{SEED}:{name}:{symbol}:{tf}:{k}')
                inds = engine.s['indicators']
                matched = ContextControls(seg, engine) if trades else None
                for t in trades:
                    ctrl = matched_controls(t, seg, inds, vcfg, rng, CONTROLS)
                    ctx = matched.draw(t, vcfg, rng, CONTROLS)
                    t.update(variant=name, control_mean_r=mean(ctrl) if ctrl else None, controls=len(ctrl),
                             control_ctx_mean_r=mean(ctx) if ctx else None, controls_ctx=len(ctx))
                rows.extend(trades)
                s = stats.setdefault(f'{name}:{tf}', dict(bars=0, blocked={}))
                s['bars'] += len(seg)
                for reason, n in engine.blocked.items():
                    s['blocked'][reason] = s['blocked'].get(reason, 0) + n
    if dirty:
        with open(cache_path + '.tmp', 'wb') as f:
            pickle.dump(cache, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(cache_path + '.tmp', cache_path)
    return rows, stats


def _keep(rows, split_module, keep):
    """Drop trades outside one set of a split module's `assign` (so a design run never writes
    confirmation trades to disk)."""
    import importlib.util
    import pandas as pd
    spec = importlib.util.spec_from_file_location('rzy_split', split_module); mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    frame = pd.DataFrame(dict(symbol=[t['symbol'] for t in rows], timeframe=[t['timeframe'] for t in rows],
                              signal_time=pd.to_datetime([t['signal_time'] for t in rows], utc=True, format='ISO8601')))
    sets = mod.assign(frame).tolist()
    return [t for t, s in zip(rows, sets) if s == keep]


def run(data_dir, out_dir, variant_names, tfs, workers, symbols=None, split_module=None, keep=None):
    os.makedirs(out_dir, exist_ok=True)
    started = time.time()
    names = sorted({p.name.rsplit('.', 3)[0] for p in Path(data_dir, 'symbols').glob('*.json.gz')})
    if symbols:
        names = [n for n in names if n in symbols]
    rows, stats = [], {}
    with Pool(workers, initializer=_init, initargs=(data_dir, variant_names, tfs)) as pool:
        for k, (r, s) in enumerate(pool.imap_unordered(_symbol, names, chunksize=4)):
            rows.extend(r)
            for key, v in s.items():
                t = stats.setdefault(key, dict(bars=0, blocked={}))
                t['bars'] += v['bars']
                for reason, n in v['blocked'].items():
                    t['blocked'][reason] = t['blocked'].get(reason, 0) + n
            if k % 100 == 99:
                print(f'{k + 1}/{len(names)} symbols, {len(rows)} trades, {round(time.time() - started)}s', flush=True)
    if split_module and keep and rows:
        rows = _keep(rows, split_module, keep)
    rows.sort(key=lambda t: (t['variant'], t['timeframe'], t['symbol'], t['signal_time']))
    fields = []
    for t in rows:
        for key in t:
            if key not in fields:
                fields.append(key)
    path = os.path.join(out_dir, 'trades.csv')
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)
    code = {p: sha256(REPO / p) for p in ('services/market-data/src/services/little_rzy.py', 'services/market-data/src/services/rzy_evaluation.py',
                                          'services/market-data/src/services/rzy_research.py', 'scripts/rzy_research.py')}
    meta = dict(created_at=datetime.now(timezone.utc).isoformat(), data=json.load(open(os.path.join(data_dir, 'prepared.json'))),
                variants={n: VARIANTS[n] for n in variant_names}, tfs=tfs, symbols=len(names), trades=len(rows), stats=stats,
                controls=CONTROLS, seed=SEED, split_module=split_module, kept_set=keep, code_sha256=code, trades_sha256=sha256(path), seconds=round(time.time() - started),
                git_revision=os.popen(f'git -C {REPO} rev-parse HEAD').read().strip())
    json.dump(meta, open(os.path.join(out_dir, 'run.json'), 'w'), indent=1, default=str)
    print(f'{len(rows)} trades in {meta["seconds"]}s', flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('prepare'); p.add_argument('bars_dir'); p.add_argument('data_dir'); p.add_argument('--market'); p.add_argument('--market-symbol', default='SPY')
    r = sub.add_parser('run'); r.add_argument('data_dir'); r.add_argument('out_dir'); r.add_argument('--variants', default='baseline')
    r.add_argument('--tfs', default='1d'); r.add_argument('--workers', type=int, default=10); r.add_argument('--symbols')
    r.add_argument('--split'); r.add_argument('--keep')
    a = ap.parse_args()
    if a.cmd == 'prepare':
        prepare(a.bars_dir, a.data_dir, a.market, a.market_symbol)
    else:
        run(a.data_dir, a.out_dir, a.variants.split(','), a.tfs.split(','), a.workers, set(a.symbols.split(',')) if a.symbols else None, a.split, a.keep)
