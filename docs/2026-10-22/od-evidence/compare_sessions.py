"""OD-01/OD-02: compare two captured sessions with the same metrics (read-only, local CSVs).

Usage: python compare_sessions.py 2026-10-21 2026-10-22 > comparison.json
Every metric is computed twice: the capture window (04:00-24:00 ET) and the REGULAR SESSION only
(bars/signals between 09:30 and 16:00 ET), so after-hours activity cannot move the comparison.
"""
import json, sys
import pandas as pd

ROOT = '/home/lausing/Documents/MyProjects/rzy-session-evidence/'
EMAILABLE = {'TRIGGERED', 'REVERSAL_WATCH', 'EXHAUSTION_WATCH', 'EXIT_PENDING', 'PAPER_EXIT', 'ENTRY_CANCELLED'}


def pct(x):
    x = pd.Series(x).dropna()
    return {'n': int(len(x))} if x.empty else {'n': int(len(x)), 'median': round(float(x.median()), 2),
            'p90': round(float(x.quantile(.9)), 2), 'max': round(float(x.max()), 2)}


def session(day):
    D = ROOT + day + '/'
    open_, close = pd.Timestamp(day + ' 13:30'), pd.Timestamp(day + ' 20:00')   # EDT both days
    rev = pd.read_csv(D + 'bar_revisions.csv', parse_dates=['ts', 'fetched_at'])
    reb = pd.read_csv(D + 'rebuilds.csv', parse_dates=['rebuilt_at'])
    occ = pd.read_csv(D + 'occurrences.csv', parse_dates=['signal_time', 'first_seen_at'])
    scans = pd.read_csv(D + 'scan_runs.csv', parse_dates=['scan_time'])
    path = pd.read_csv(D + 'rzy_alert_path.csv')
    out = {'release': json.load(open(D + 'manifest.json')).get('release'),
           'config_hash': json.load(open(D + 'manifest.json')).get('config_hash')}
    done = rev[~rev.classification.str.endswith('_forming_bar')].copy()
    for label, sub in (('window', done), ('regular_session_bars', done[(done.ts >= open_) & (done.ts < close)])):
        out[f'completed_revisions_{label}'] = {
            'total': int(len(sub)), 'by_class': sub.classification.value_counts().to_dict(),
            'by_magnitude': sub.magnitude.value_counts().to_dict(),
            'material_cross_provider': int(((sub.magnitude == 'material') & (sub.classification == 'cross_provider')).sum()),
            'by_timeframe': sub.timeframe.value_counts().to_dict()}
    reb['symbol'] = reb.stream_key.str.split(':').str[0]; reb['tf'] = reb.stream_key.str.split(':').str[1]
    reb = reb.sort_values('rebuilt_at'); reb['prev'] = reb.groupby('stream_key').rebuilt_at.shift(1)
    mat = {s: g for s, g in done.groupby('symbol')}
    drivers = []
    for r in reb.itertuples():
        if r.reason != 'stored_completed_bar_changed':
            drivers.append(r.reason); continue
        lo = r.prev if pd.notna(r.prev) else r.rebuilt_at - pd.Timedelta(hours=24)
        g = mat.get(r.symbol); w = g[(g.fetched_at > lo) & (g.fetched_at <= r.rebuilt_at)] if g is not None else pd.DataFrame()
        drivers.append('unresolved_no_traced_revision' if w.empty else
                       ('material_cross_provider' if ((w.magnitude == 'material') & (w.classification == 'cross_provider')).any()
                        else 'material_same_or_unknown' if (w.magnitude == 'material').any() else 'rounding_only'))
    reb['driver'] = drivers
    out['rebuilds'] = {'total': int(len(reb)), 'by_driver': reb.driver.value_counts().to_dict(),
                       'during_regular_session': int(((reb.rebuilt_at >= open_) & (reb.rebuilt_at < close)).sum())}
    s = scans[(scans['mode'] == 'session') & (scans.scan_time >= open_) & (scans.scan_time < close)].copy()
    s['paused'] = s.statuses.apply(lambda v: json.loads(v).get('restatement_pending', 0))
    out['scans_regular_session'] = {'runs': int(len(s)), 'duration_s': pct(s.duration_seconds),
        'minutes_between': pct(s.scan_time.diff().dt.total_seconds() / 60), 'stream_scans': int(s.streams.sum()),
        'paused_stream_scans': int(s.paused.sum()), 'paused_share': round(s.paused.sum() / max(s.streams.sum(), 1), 4),
        'too_late_to_mail': int(s.too_late_to_mail.sum()), 'queued': int(s.queued.sum())}
    try:
        ph = pd.read_csv(D + 'scan_phases.csv', parse_dates=['scan_time'])
        ph = ph[(ph.scan_time >= open_) & (ph.scan_time < close)]
        out['scan_phases_regular_session_s'] = {c: pct(ph[c]) for c in ph.columns if c not in ('scan_time', 'mode')}
    except FileNotFoundError:
        out['scan_phases_regular_session_s'] = 'not captured (release predates phase timing)'
    today = occ[(occ.signal_time >= open_) & (occ.signal_time <= close + pd.Timedelta(minutes=5))].copy()
    today['delay'] = (today.first_seen_at - today.signal_time).dt.total_seconds() / 60
    em = today[today.event_type.isin(EMAILABLE)]
    out['signals_regular_session'] = {'occurrences': int(len(today)), 'by_observed_in': today.observed_in.value_counts().to_dict(),
        'emailable': int(len(em)), 'emailable_by_observed_in': em.observed_in.value_counts().to_dict(),
        'emailable_close_to_recorded_min_incremental': pct(em[em.observed_in == 'incremental'].delay),
        'emailable_close_to_recorded_min_rebuild': pct(em[em.observed_in == 'rebuild'].delay)}
    out['alert_path'] = {tf: {'signals': int((path.timeframe == tf).sum()) if 'timeframe' in path else 0,
                              'outcomes': path[path.timeframe == tf].outcome.value_counts().to_dict() if 'outcome' in path else {},
                              'close_to_accepted_min': pct(path[path.timeframe == tf].close_to_accepted_min) if 'close_to_accepted_min' in path else {}}
                         for tf in ('5m', '15m', '1h')}
    rep = json.load(open(D + 'report.json'))
    out['outbox_states'] = rep.get('outbox_states')
    out['bar_session_authority'] = rep.get('bar_session_authority', 'not captured (release predates bar authority)')
    return out


if __name__ == '__main__':
    print(json.dumps({d: session(d) for d in sys.argv[1:]}, indent=1, default=str))
