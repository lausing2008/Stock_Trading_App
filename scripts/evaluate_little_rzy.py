"""Read-only historical evaluation of LITTLE_RZY_INSPIRED_V1 on stored production bars.

Runs inside the market-data container (needs /app on sys.path and the service's DB settings):

    docker cp scripts/evaluate_little_rzy.py stockai-market-data-1:/tmp/
    docker exec -w /app stockai-market-data-1 nice -n 15 python /tmp/evaluate_little_rzy.py /tmp/rzy-eval

Writes <out>/report.json and <out>/trades.csv. Never writes to the database (rolls back).
Universe: every active, non-delisted US stock with stored bars (no hand-picked list).
Bars: regular-session completed bars from the production adapter; 15m/1h derived from 5m;
each contiguous segment is replayed by a fresh engine. Segments under 60 bars are skipped.
"""
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path[:0] = [os.environ.get('RZY_EVAL_ROOT', '/app'), '/app/shared']
from sqlalchemy import select  # noqa: E402
from db import SessionLocal, Stock, Price, TimeFrame  # noqa: E402
from common.market_calendar import session_bounds  # noqa: E402
from common.price_basis import resolve_ohlc  # noqa: E402
from src.services.rzy_data import prepare_bars, aggregate_completed_bars, break_indices  # noqa: E402
from src.services.rzy_evaluation import research_bars, evaluate_streams, report, ASSUMPTIONS  # noqa: E402
from src.services.little_rzy import Config, VERSION  # noqa: E402

MIN_SEGMENT = 60


def segments(bars, timeframe):
    cuts = [0] + break_indices(bars, timeframe, session_bounds) + [len(bars)]
    return [research_bars(bars[a:b]) for a, b in zip(cuts, cuts[1:]) if b - a >= MIN_SEGMENT]


def main(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    now = datetime.now(timezone.utc)
    started = time.time()
    streams = []
    coverage = {}
    with SessionLocal() as s:
        stocks = s.scalars(select(Stock).where(Stock.market == 'US', Stock.active.is_(True), Stock.delisted.is_(False))).all()
        for stock in stocks:
            m5 = s.scalars(select(Price).where(Price.stock_id == stock.id, Price.timeframe == TimeFrame.M5).order_by(Price.ts)).all()
            d1 = s.scalars(select(Price).where(Price.stock_id == stock.id, Price.timeframe == TimeFrame.D1).order_by(Price.ts)).all()
            if m5:
                bars5, ev5 = prepare_bars(m5, '5m', now, session_bounds=session_bounds, resolve_ohlc=resolve_ohlc)
                if bars5:
                    streams.append((stock.symbol, '5m', segments(bars5, '5m')))
                    for tf in ('15m', '1h'):
                        agg, _ = aggregate_completed_bars(bars5, ev5, tf, now, session_bounds=session_bounds)
                        if agg: streams.append((stock.symbol, tf, segments(agg, tf)))
            if d1:
                bars1, _ = prepare_bars(d1, '1d', now, session_bounds=session_bounds, resolve_ohlc=resolve_ohlc)
                if bars1: streams.append((stock.symbol, '1d', segments(bars1, '1d')))
        s.rollback()
    for sym, tf, segs in streams:
        c = coverage.setdefault(tf, dict(symbols=0, segments=0, bars=0, first=None, last=None))
        if segs:
            c['symbols'] += 1; c['segments'] += len(segs); c['bars'] += sum(len(x) for x in segs)
            first, last = segs[0][0]['time'], segs[-1][-1]['time']
            c['first'] = min(c['first'] or first, first); c['last'] = max(c['last'] or last, last)
    print('loaded', {k: v['bars'] for k, v in coverage.items()}, 'in %.0fs' % (time.time() - started), flush=True)
    results = evaluate_streams([x for x in streams if x[2]], controls=20)
    rep = dict(strategy_version=VERSION, config_hash=Config().hash, generated_at=now.isoformat(),
               runtime_seconds=round(time.time() - started), coverage=coverage, assumptions=ASSUMPTIONS,
               method=dict(unit='R = net P&L per share / initial risk (fill to frozen stop)',
                           control='20 random-timing twins per trade on the same stream: same direction, same stop/target in ATR, same costs, time exit after the real holding length',
                           fills='next bar open with half-spread + slippage; stop before target; gaps fill at the worse open',
                           tuning='none; all variants reported', min_segment_bars=MIN_SEGMENT),
               variants=report(results))
    with open(os.path.join(out_dir, 'report.json'), 'w') as f:
        json.dump(rep, f, indent=1, default=str)
    fields = ['variant', 'symbol', 'timeframe', 'direction', 'sequence', 'sequence_confidence', 'entry_time', 'exit_time',
              'holding_bars', 'fill', 'stop', 'target', 'r', 'ret', 'control_mean_r', 'exit_reason']
    with open(os.path.join(out_dir, 'trades.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore'); w.writeheader()
        for res in results.values():
            w.writerows(res['trades'])
    print('done in %.0fs' % (time.time() - started), flush=True)


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else '/tmp/rzy-eval')
