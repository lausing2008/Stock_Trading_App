"""Reproducible historical evaluation of LITTLE_RZY_INSPIRED_V1 in two steps.

1. export (inside the market-data container; read-only, rolls back):

    docker cp scripts/evaluate_little_rzy.py stockai-market-data-1:/tmp/
    docker exec -w /app stockai-market-data-1 nice -n 15 python /tmp/evaluate_little_rzy.py export /tmp/rzy-snapshot

   Writes bars.jsonl.gz (every contiguous regular-session segment, 5m native plus derived 15m/1h,
   and daily) and snapshot.json (coverage, row counts, sha256). Universe: every active,
   non-delisted US stock with stored bars. Segments under 60 bars are dropped.

2. evaluate (anywhere with the repository; no database):

    python scripts/evaluate_little_rzy.py evaluate <snapshot_dir> <out_dir> [--only baseline,...]

   Writes report.json, trades.csv and manifest.json (code sha256, snapshot sha256, config,
   seeds, assumptions). Inputs and code identity are enough to reproduce the run exactly.
"""
import argparse
import csv
import gzip
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

MIN_SEGMENT = 60
FIELDS = ('time', 'closed_at', 'open', 'high', 'low', 'close', 'volume', 'price_basis')
REPO = Path(__file__).resolve().parents[1]


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def export(out_dir):
    sys.path[:0] = [os.environ.get('RZY_EVAL_ROOT', '/app'), '/app/shared']
    from sqlalchemy import select
    from db import SessionLocal, Stock, Price, TimeFrame
    from common.market_calendar import session_bounds
    from common.price_basis import resolve_ohlc
    from src.services.rzy_data import prepare_bars, aggregate_completed_bars, break_indices
    os.makedirs(out_dir, exist_ok=True)
    now = datetime.now(timezone.utc); started = time.time()
    coverage = {}; path = os.path.join(out_dir, 'bars.jsonl.gz')
    def segments(bars, tf):
        cuts = [0] + break_indices(bars, tf, session_bounds) + [len(bars)]
        return [bars[a:b] for a, b in zip(cuts, cuts[1:]) if b - a >= MIN_SEGMENT]
    with gzip.open(path, 'wt') as out, SessionLocal() as s:
        def write(symbol, tf, segs):
            c = coverage.setdefault(tf, dict(symbols=0, segments=0, bars=0, first=None, last=None))
            if not segs: return
            c['symbols'] += 1; c['segments'] += len(segs); c['bars'] += sum(len(x) for x in segs)
            c['first'] = min(c['first'] or segs[0][0]['time'], segs[0][0]['time'])
            c['last'] = max(c['last'] or segs[-1][-1]['time'], segs[-1][-1]['time'])
            for seg in segs:
                out.write(json.dumps(dict(symbol=symbol, timeframe=tf, bars=[[b[k] for k in FIELDS] for b in seg])) + '\n')
        stocks = s.scalars(select(Stock).where(Stock.market == 'US', Stock.active.is_(True), Stock.delisted.is_(False)).order_by(Stock.symbol)).all()
        for stock in stocks:
            m5 = s.scalars(select(Price).where(Price.stock_id == stock.id, Price.timeframe == TimeFrame.M5).order_by(Price.ts)).all()
            if m5:
                bars5, ev5 = prepare_bars(m5, '5m', now, session_bounds=session_bounds, resolve_ohlc=resolve_ohlc)
                if bars5:
                    write(stock.symbol, '5m', segments(bars5, '5m'))
                    for tf in ('15m', '1h'):
                        agg, _ = aggregate_completed_bars(bars5, ev5, tf, now, session_bounds=session_bounds)
                        if agg: write(stock.symbol, tf, segments(agg, tf))
            d1 = s.scalars(select(Price).where(Price.stock_id == stock.id, Price.timeframe == TimeFrame.D1).order_by(Price.ts)).all()
            if d1:
                bars1, _ = prepare_bars(d1, '1d', now, session_bounds=session_bounds, resolve_ohlc=resolve_ohlc)
                if bars1: write(stock.symbol, '1d', segments(bars1, '1d'))
        s.rollback()
    meta = dict(exported_at=now.isoformat(), source='production stored prices, read-only', fields=FIELDS,
                min_segment_bars=MIN_SEGMENT, coverage=coverage, bars_sha256=sha256(path),
                source_revision=os.environ.get('RZY_SOURCE_REVISION', 'unknown'), seconds=round(time.time() - started))
    with open(os.path.join(out_dir, 'snapshot.json'), 'w') as f:
        json.dump(meta, f, indent=1)
    print(json.dumps(meta['coverage']), meta['bars_sha256'], flush=True)


def load_snapshot(snapshot_dir):
    meta = json.load(open(os.path.join(snapshot_dir, 'snapshot.json')))
    path = os.path.join(snapshot_dir, 'bars.jsonl.gz')
    if sha256(path) != meta['bars_sha256']:
        raise SystemExit('Snapshot hash mismatch; refusing to evaluate altered inputs')
    streams = {}
    with gzip.open(path, 'rt') as f:
        for line in f:
            row = json.loads(line)
            bars = [dict(zip(meta['fields'], b), complete=True, provider='stored_prices_upstream_not_retained', delay='unknown',
                         session='REGULAR', freshness='historical_completed') for b in row['bars']]
            streams.setdefault((row['symbol'], row['timeframe']), []).append(bars)
    return meta, [(sym, tf, segs) for (sym, tf), segs in streams.items()]


def evaluate(snapshot_dir, out_dir, only=None):
    sys.path[:0] = [str(REPO / 'services/market-data'), str(REPO / 'shared')]
    from src.services.rzy_evaluation import (research_bars, evaluate_streams, report, chronological_cut,
                                             ASSUMPTIONS, HELD_OUT_FRACTION)
    from src.services.little_rzy import Config, VERSION
    os.makedirs(out_dir, exist_ok=True); started = time.time()
    meta, streams = load_snapshot(snapshot_dir)
    streams = [(sym, tf, [research_bars(seg) for seg in segs]) for sym, tf, segs in streams]
    results = evaluate_streams(streams, controls=20, only=only)
    cuts = chronological_cut(meta['coverage'])
    rep = dict(strategy_version=VERSION, config_hash=Config().hash, snapshot=meta, assumptions=ASSUMPTIONS,
               chronological_cut=cuts, held_out_fraction=HELD_OUT_FRACTION,
               method=dict(unit='R = net P&L per share / initial risk (fill to frozen stop)',
                           control='20 random-timing twins per trade inside the same segment, full holding window observable: same direction, stop/target in ATR, costs and borrow; borrow accrues only until the actual exit',
                           intervals='edge 95% CI bootstrapped by symbol (verdict); by-trade CI and minimum detectable edge reported alongside',
                           periods='per timeframe, the last 30% of the covered span is held out; nothing is fitted on either period',
                           fills='next bar open with half-spread + slippage; stop before target; gaps fill at the worse open',
                           tuning='none; all variants reported; research_stop_target_only is NOT a live default'),
               runtime_seconds=None, variants=report(results, cuts))
    rep['runtime_seconds'] = round(time.time() - started)
    with open(os.path.join(out_dir, 'report.json'), 'w') as f:
        json.dump(rep, f, indent=1, default=str)
    fields = ['variant', 'symbol', 'timeframe', 'direction', 'sequence', 'sequence_confidence', 'entry_time', 'exit_time',
              'holding_bars', 'fill', 'stop', 'target', 'r', 'ret', 'cost_r', 'control_mean_r', 'controls', 'exit_reason']
    with open(os.path.join(out_dir, 'trades.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore'); w.writeheader()
        for res in results.values(): w.writerows(res['trades'])
    code = {p: sha256(REPO / p) for p in ('services/market-data/src/services/little_rzy.py', 'services/market-data/src/services/rzy_data.py',
                                          'services/market-data/src/services/rzy_evaluation.py', 'scripts/evaluate_little_rzy.py')}
    manifest = dict(created_at=datetime.now(timezone.utc).isoformat(), git_revision=os.popen(f'git -C {REPO} rev-parse HEAD').read().strip(),
                    git_dirty=bool(os.popen(f'git -C {REPO} status --porcelain -- services/market-data/src scripts/evaluate_little_rzy.py').read().strip()),
                    code_sha256=code, snapshot_bars_sha256=meta['bars_sha256'], snapshot_exported_at=meta['exported_at'],
                    variants=list(results), controls=20, seeds=dict(evaluate=7, report=11), assumptions=ASSUMPTIONS,
                    outputs_sha256={n: sha256(os.path.join(out_dir, n)) for n in ('report.json', 'trades.csv')})
    with open(os.path.join(out_dir, 'manifest.json'), 'w') as f:
        json.dump(manifest, f, indent=1)
    print('done in %ds' % rep['runtime_seconds'], flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    e = sub.add_parser('export'); e.add_argument('out')
    v = sub.add_parser('evaluate'); v.add_argument('snapshot'); v.add_argument('out'); v.add_argument('--only')
    a = ap.parse_args()
    if a.cmd == 'export': export(a.out)
    else: evaluate(a.snapshot, a.out, set(a.only.split(',')) if a.only else None)
