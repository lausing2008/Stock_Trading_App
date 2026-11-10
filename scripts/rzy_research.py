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
import random
import sys
import time
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path
from statistics import mean

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / 'services/market-data'), str(REPO / 'shared')]
FIELDS = ('time', 'closed_at', 'open', 'high', 'low', 'close', 'volume', 'price_basis')
INTRADAY = ('5m', '15m', '1h')
CONTROLS = 20
SEED = 7

# name -> dict(gates=..., exits=..., tfs=optional tuple). Thresholds are fixed here, before a
# confirmation run; see docs/2026-11-10/rzy-improvement-preregistration.md.
VARIANTS = {
    'baseline': {},
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


def _bars(seg):
    from src.services.rzy_evaluation import BORROW_RATE
    return [dict(zip(FIELDS, b), complete=True, provider='research_file', delay='unknown', session='REGULAR',
                 freshness='historical_completed', execution_eligible=True, shortable=True, borrow_rate_annual=BORROW_RATE) for b in seg]


def _symbol(symbol):
    from src.services.little_rzy import Config
    from src.services.rzy_evaluation import matched_controls
    from src.services.rzy_research import run_stream, Series
    from src.services import rzy_evaluation_context as C
    cfg = Config()
    streams = {}
    for tf in sorted(set(_G['tfs']) | {'1d', '1w'}):
        path = os.path.join(_G['data_dir'], 'symbols', f'{symbol}.{tf}.json.gz')
        if os.path.exists(path):
            with gzip.open(path, 'rt') as f:
                streams[tf] = [_bars(seg) for seg in json.load(f)]
    C.GRACE.setdefault('1w', C.GRACE['1d'] * 2)
    timelines = C.trend_timelines([(symbol, tf, streams[tf]) for tf in ('1d', '1w') if streams.get(tf)], cfg)
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
                engine, trades = run_stream(symbol, tf, seg, cfg, v.get('gates'), v.get('exits'), context)
                rng = random.Random(f'{SEED}:{name}:{symbol}:{tf}:{k}')
                inds = engine.s['indicators']
                for t in trades:
                    ctrl = matched_controls(t, seg, inds, cfg, rng, CONTROLS)
                    t.update(variant=name, control_mean_r=mean(ctrl) if ctrl else None, controls=len(ctrl))
                rows.extend(trades)
                s = stats.setdefault(f'{name}:{tf}', dict(bars=0, blocked={}))
                s['bars'] += len(seg)
                for reason, n in engine.blocked.items():
                    s['blocked'][reason] = s['blocked'].get(reason, 0) + n
    return rows, stats


def run(data_dir, out_dir, variant_names, tfs, workers, symbols=None):
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
                controls=CONTROLS, seed=SEED, code_sha256=code, trades_sha256=sha256(path), seconds=round(time.time() - started),
                git_revision=os.popen(f'git -C {REPO} rev-parse HEAD').read().strip())
    json.dump(meta, open(os.path.join(out_dir, 'run.json'), 'w'), indent=1, default=str)
    print(f'{len(rows)} trades in {meta["seconds"]}s', flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('prepare'); p.add_argument('bars_dir'); p.add_argument('data_dir'); p.add_argument('--market'); p.add_argument('--market-symbol', default='SPY')
    r = sub.add_parser('run'); r.add_argument('data_dir'); r.add_argument('out_dir'); r.add_argument('--variants', default='baseline')
    r.add_argument('--tfs', default='1d'); r.add_argument('--workers', type=int, default=10); r.add_argument('--symbols')
    a = ap.parse_args()
    if a.cmd == 'prepare':
        prepare(a.bars_dir, a.data_dir, a.market, a.market_symbol)
    else:
        run(a.data_dir, a.out_dir, a.variants.split(','), a.tfs.split(','), a.workers, set(a.symbols.split(',')) if a.symbols else None)
