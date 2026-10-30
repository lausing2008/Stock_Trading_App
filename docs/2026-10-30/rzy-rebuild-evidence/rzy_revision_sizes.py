"""Read-only follow-up (2026-10-30): size, fields, providers and age of completed 5m bar revisions
and of the OHLC changes that triggered retained RZY rebuild archives. Prints JSON."""
import sys, json
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
sys.path[:0] = ['/app', '/app/shared']
from sqlalchemy import text
from db import SessionLocal
NY = ZoneInfo('America/New_York')
DAYS = ['2026-10-23', '2026-10-26', '2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30']
NOW = datetime.now(timezone.utc).replace(tzinfo=None)
def window(day):
    d = datetime.strptime(day, '%Y-%m-%d')
    a, b = (datetime(d.year, d.month, d.day, h, tzinfo=NY).astimezone(timezone.utc).replace(tzinfo=None) for h in (4, 20))
    return a, min(b, NOW)
def size(before, after):
    ab = max(abs(float(after[k]) - float(before[k])) for k in ('open', 'high', 'low', 'close'))
    rel = max(abs(float(after[k]) - float(before[k])) / max(abs(float(before[k])), 1e-9) for k in ('open', 'high', 'low', 'close'))
    return ab, rel
def abs_bucket(x):
    return '<=0.005' if x <= 0.005 + 1e-9 else '<=0.01' if x <= 0.01 + 1e-9 else '<=0.05' if x <= 0.05 + 1e-9 else '<=0.25' if x <= 0.25 else '>0.25'
def rel_bucket(r):
    return '<0.01%' if r < 1e-4 else '<0.05%' if r < 5e-4 else '<0.2%' if r < 2e-3 else '>=0.2%'
out = {}
with SessionLocal() as s:
    s.execute(text('SET TRANSACTION READ ONLY')); s.execute(text("SET LOCAL statement_timeout='120000'"))
    for day in DAYS:
        a, b = window(day); D = {}
        revs = s.execute(text("select provider,previous_provider,changed_fields,before,after,bar_age_minutes,fetched_at from bar_revisions where timeframe='5m' and fetched_at>=:a and fetched_at<:b and classification not like '%forming_bar'"), dict(a=a, b=b)).all()
        ab = Counter(); rb = Counter(); fs = Counter(); pp = Counter(); ages = Counter(); hours = Counter()
        for r in revs:
            x, y = size(r.before, r.after); ab[abs_bucket(x)] += 1; rb[rel_bucket(y)] += 1
            fs[','.join(f for f in r.changed_fields if f != 'volume') or 'none'] += 1
            pp[f'{r.previous_provider}->{r.provider}'] += 1
            m = r.bar_age_minutes
            ages['<15m' if m < 15 else '<60m' if m < 60 else '<6h' if m < 360 else '>=6h'] += 1
            hours[r.fetched_at.replace(tzinfo=timezone.utc).astimezone(NY).strftime('%H')] += 1
        D['bar_revisions_5m'] = dict(n=len(revs), max_abs_ohlc_change=dict(ab), max_rel_ohlc_change=dict(rb), ohlc_fields_changed=dict(fs.most_common(8)),
                                     provider_pairs=dict(pp.most_common(8)), bar_age_at_revision=dict(ages), by_fetch_hour_et=dict(sorted(hours.items())))
        arch = s.execute(text("select stream_key,archived_at,detail->'detail' as det from little_rzy_stream_generations where archived_at>=:a and archived_at<:b and detail->>'reason'='stored_completed_bar_changed'"), dict(a=a, b=b)).all()
        ab = Counter(); rb = Counter(); fs = Counter()
        for x in arch:
            bf, af = x.det['before'], x.det['after']
            m, r = size(bf, af); ab[abs_bucket(m)] += 1; rb[rel_bucket(r)] += 1
            fs[','.join(k for k in ('open', 'high', 'low', 'close') if abs(float(bf[k]) - float(af[k])) > 1e-6 * max(abs(float(bf[k])), abs(float(af[k])))) or 'none'] += 1
        D['rebuild_trigger_ohlc'] = dict(n=len(arch), max_abs_ohlc_change=dict(ab), max_rel_ohlc_change=dict(rb), fields_changed=dict(fs.most_common(8)))
        out[day] = D
    s.rollback()
print(json.dumps(out))
