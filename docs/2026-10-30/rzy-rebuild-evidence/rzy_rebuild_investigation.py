"""Read-only RZY rebuild investigation (2026-10-30). Run inside market-data:
    ssh ... 'docker exec -i -w /app stockai-market-data-1 python -' < rzy_rebuild_investigation.py > out.json
Writes nothing: one READ ONLY transaction. Prints one JSON document (summary + samples).
Change classes follow rzy_workflow._ohlc_revised (what triggers a rebuild) and
bar_provenance.revision_magnitude (rounding vs material)."""
import sys, json, hashlib
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
sys.path[:0] = ['/app', '/app/shared']
from sqlalchemy import select, text
from db import SessionLocal, Price, Stock, TimeFrame
from src.services.bar_provenance import revision_magnitude

NY = ZoneInfo('America/New_York')
DAYS = ['2026-10-23', '2026-10-26', '2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30']
STEP = {'5m': 5, '15m': 15, '1h': 60}
TF = {'5m': TimeFrame.M5, '15m': TimeFrame.M15, '1h': TimeFrame.H1}
NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def window(day):
    d = datetime.strptime(day, '%Y-%m-%d')
    a, b = (datetime(d.year, d.month, d.day, h, tzinfo=NY).astimezone(timezone.utc).replace(tzinfo=None) for h in (4, 20))
    return a, min(b, NOW)


def naive(iso):
    v = datetime.fromisoformat(iso)
    return v.astimezone(timezone.utc).replace(tzinfo=None) if v.tzinfo else v


def trigger_class(reason, det):
    """Class of what made the stored checkpoint disagree with current history."""
    if reason != 'stored_completed_bar_changed':
        return reason
    b, a = det.get('before') or {}, det.get('after') or {}
    if b.get('closed_at') != a.get('closed_at'):
        return 'closed_at_changed'
    if b.get('price_basis') != a.get('price_basis'):
        return 'price_basis_changed'
    try:
        return 'ohlc_' + revision_magnitude(b, a)
    except Exception:
        return 'ohlc_unknown'


def dist(counts):
    c = Counter('1' if n == 1 else '2-5' if n <= 5 else '6+' for n in counts.values())
    return {k: c.get(k, 0) for k in ('1', '2-5', '6+')}


def derived_from_5m(s, stock_id, t0, minutes):
    rows = s.execute(select(Price.ts, Price.open, Price.high, Price.low, Price.close).where(
        Price.stock_id == stock_id, Price.timeframe == TimeFrame.M5, Price.ts >= t0,
        Price.ts < t0 + timedelta(minutes=minutes)).order_by(Price.ts)).all()
    if not rows:
        return None
    return dict(open=float(rows[0].open), high=max(float(r.high) for r in rows), low=min(float(r.low) for r in rows),
                close=float(rows[-1].close), n=len(rows))


def same(x, y):
    return x is not None and y is not None and all(abs(float(x[k]) - float(y[k])) <= max(0.005, 2e-5 * abs(float(x[k]))) for k in ('open', 'high', 'low', 'close'))


out = dict(generated_at=NOW.isoformat(), sessions={}, samples=defaultdict(list), notes=[])
with SessionLocal() as s:
    s.execute(text('SET TRANSACTION READ ONLY'))
    s.execute(text("SET LOCAL statement_timeout='120000'"))
    sid = dict(s.execute(text('select symbol,id from stocks')).all())
    streams_by_tf = dict(s.execute(text("select timeframe,count(*) from little_rzy_streams group by 1")).all())
    out['streams_table_by_timeframe'] = streams_by_tf
    for day in DAYS:
        a, b = window(day)
        S = dict(window_utc=[a.isoformat(), b.isoformat()], partial=(day == DAYS[-1]))
        # 1. Rebuilds per evaluated stream
        scans = s.execute(text("select mode,count(*),sum(streams),max(streams) from little_rzy_scan_runs where scan_time>=:a and scan_time<:b group by 1"), dict(a=a, b=b)).all()
        S['scan_runs'] = {m: dict(runs=n, stream_evaluations=int(se or 0), max_streams_per_run=mx) for m, n, se, mx in scans}
        reb = s.execute(text("select stream_key,symbol,timeframe,generation,reason,rebuilt_at from little_rzy_rebuilds where rebuilt_at>=:a and rebuilt_at<:b"), dict(a=a, b=b)).all()
        S['rebuilds_total'] = len(reb)
        per_tf = {}
        for tf in ('5m', '15m', '1h'):
            r = [x for x in reb if x.timeframe == tf]
            per_stream = Counter(x.stream_key for x in r)
            per_tf[tf] = dict(rebuilds=len(r), streams_in_table=streams_by_tf.get(tf), rebuilt_streams=len(per_stream),
                              rebuilds_per_stream_in_table=round(len(r) / streams_by_tf[tf], 2) if streams_by_tf.get(tf) else None,
                              rebuilds_per_rebuilt_stream=round(len(r) / len(per_stream), 2) if per_stream else None,
                              rebuilt_streams_by_count=dist(per_stream), by_reason=dict(Counter(x.reason for x in r)))
        S['by_timeframe'] = per_tf
        S['affected_symbols'] = len({x.symbol for x in reb})
        S['rebuilds_by_et_hour'] = dict(sorted(Counter(x.rebuilt_at.replace(tzinfo=timezone.utc).astimezone(NY).strftime('%H') for x in reb).items()))
        se = sum(v['stream_evaluations'] for k, v in S['scan_runs'].items() if k == 'session')
        S['rebuilds_per_session_stream_evaluation'] = dict(numerator=len(reb), denominator=se, rate=round(len(reb) / se, 4) if se else None)
        # Cascade: timeframes rebuilt together for one symbol in one scan (rebuilt_at to the minute)
        grp = defaultdict(set)
        for x in reb:
            grp[(x.symbol, x.rebuilt_at.replace(second=0, microsecond=0))].add(x.timeframe)
        S['cascade_symbol_scans'] = dict(Counter('+'.join(sorted(v, key=lambda t: STEP[t])) for v in grp.values()))
        # 2/3. Archives retained for this window's rebuilds
        arch = s.execute(text("select stream_key,generation,archived_at,detail->>'reason' as reason,detail->'detail' as det from little_rzy_stream_generations where archived_at>=:a and archived_at<:b"), dict(a=a, b=b)).all()
        S['archives_retained'] = dict(numerator=len(arch), denominator=len(reb), share=round(len(arch) / len(reb), 3) if reb else None)
        prev_start = {(x.stream_key, x.generation): x.rebuilt_at for x in s.execute(text("select stream_key,generation,rebuilt_at from little_rzy_rebuilds where rebuilt_at<:b and rebuilt_at>=:a0"), dict(b=b, a0=a - timedelta(days=10))).all()}
        classes = Counter(); classes_tf = Counter(); trig_bars = set(); age = Counter(); attrib = Counter(); attrib_tf = Counter()
        for x in arch:
            sym, tf = x.stream_key.split(':')[0], x.stream_key.split(':')[1]
            det = x.det or {}
            cls = trigger_class(x.reason, det)
            classes[cls] += 1; classes_tf[f'{tf}:{cls}'] += 1
            t = det.get('time') if isinstance(det, dict) else None
            if t:
                trig_bars.add((sym, tf, t))
                tt = naive(t)
                # Age of the triggering bar at rebuild: same ET session as the rebuild, or an earlier one
                same_day = tt.replace(tzinfo=timezone.utc).astimezone(NY).date() == x.archived_at.replace(tzinfo=timezone.utc).astimezone(NY).date()
                age['same_session' if same_day else 'earlier_session'] += 1
            row = dict(day=day, symbol=sym, timeframe=tf, rebuilt_at=x.archived_at.isoformat(), generation_archived=x.generation, cls=cls, bar_time=t,
                       before=json.dumps((det or {}).get('before')) if isinstance(det, dict) else None, after=json.dumps((det or {}).get('after')) if isinstance(det, dict) else None)
            # 4. Attribution for derived timeframes
            if tf in ('15m', '1h') and x.reason == 'stored_completed_bar_changed' and t:
                start = prev_start.get((x.stream_key, x.generation)) or (x.archived_at - timedelta(days=7))
                tt = naive(t)
                revs = s.execute(text("select provider,previous_provider,classification,before,after,fetched_at from bar_revisions where symbol=:s and timeframe='5m' and ts>=:t0 and ts<:t1 and fetched_at>:f0 and fetched_at<=:f1"),
                                 dict(s=sym, t0=tt, t1=tt + timedelta(minutes=STEP[tf]), f0=start, f1=x.archived_at)).all()
                if revs:
                    res = 'explained_by_5m_revision'
                    row['providers'] = ';'.join(sorted({f'{r.previous_provider}->{r.provider}' for r in revs}))
                else:
                    nat_rev = s.execute(text("select count(*) from bar_revisions where symbol=:s and timeframe=:tf and ts=:t and fetched_at>:f0 and fetched_at<=:f1"),
                                        dict(s=sym, tf=tf, t=tt, f0=start, f1=x.archived_at)).scalar()
                    nat = s.execute(select(Price.open, Price.high, Price.low, Price.close).where(Price.stock_id == sid.get(sym), Price.timeframe == TF[tf], Price.ts == tt)).first()
                    nat = dict(open=nat.open, high=nat.high, low=nat.low, close=nat.close) if nat else None
                    der = derived_from_5m(s, sid.get(sym), tt, STEP[tf])
                    bef, aft = (det.get('before') or {}), (det.get('after') or {})
                    if nat_rev:
                        res = 'explained_by_native_bar_revision'
                    elif nat and der and ((same(bef, nat) and same(aft, der)) or (same(bef, der) and same(aft, nat))):
                        res = 'source_switch_native_vs_derived'
                    elif nat is None and der is not None and same(aft, der):
                        res = 'after_matches_derived_no_native_row'
                    else:
                        res = 'unexplained'
                    row['native_now'] = json.dumps(nat); row['derived_now'] = json.dumps(der)
                attrib[res] += 1; attrib_tf[f'{tf}:{res}'] += 1
                row['attribution'] = res
                if len([q for q in out['samples']['attribution:' + res] if q['day'] == day]) < 3:
                    out['samples']['attribution:' + res].append(row)
            if len([q for q in out['samples'][cls] if q['day'] == day]) < 2 and len(out['samples'][cls]) < 10:
                out['samples'][cls].append(row)
        S['archive_trigger_classes'] = dict(classes); S['archive_trigger_classes_by_tf'] = dict(sorted(classes_tf.items()))
        S['distinct_triggering_bars_in_retained_archives'] = len(trig_bars)
        S['trigger_bar_age'] = dict(age)
        S['derived_tf_attribution'] = dict(attrib); S['derived_tf_attribution_by_tf'] = dict(sorted(attrib_tf.items()))
        # bar_revisions in the window (completed bars only)
        revs = s.execute(text("select symbol,timeframe,ts,provider,previous_provider,classification,changed_fields,before,after from bar_revisions where fetched_at>=:a and fetched_at<:b and classification not like '%forming_bar'"), dict(a=a, b=b)).all()
        rc = Counter(); rbars = defaultdict(set)
        for r in revs:
            cf = r.changed_fields or []
            if cf == ['volume']:
                k = 'volume_only'
            else:
                try:
                    k = 'ohlc_' + revision_magnitude(r.before, r.after)
                except Exception:
                    k = 'ohlc_unknown'
                if 'volume' in cf:
                    k += '+volume'
            rc[(r.timeframe, k, r.classification)] += 1
            rbars[r.timeframe].add((r.symbol, r.ts))
        S['bar_revisions_completed'] = dict(total=len(revs), distinct_bars_by_tf={k: len(v) for k, v in rbars.items()},
                                           symbols=len({r.symbol for r in revs}),
                                           by_tf_class_provider={f'{a1}|{b1}|{c1}': n for (a1, b1, c1), n in sorted(rc.items())})
        vo = s.execute(text("select bp.timeframe,sum(bp.volume_only_revisions),sum(bp.ohlc_revisions),sum(bp.provider_switches_same_values),count(*) from bar_provenance bp where bp.ts>=:a and bp.ts<:b group by 1"), dict(a=a, b=b)).all()
        S['bar_provenance_counters_for_bars_in_window'] = {t: dict(volume_only_revisions=int(v or 0), ohlc_revisions=int(o or 0), provider_switch_same_values=int(p or 0), bars=n) for t, v, o, p, n in vo}
        out['sessions'][day] = S
    s.rollback()
print(json.dumps(out, default=str))
