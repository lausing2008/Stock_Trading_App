"""Oct 21 OD-01/OD-02 analysis from the preserved capture (read-only, local CSVs)."""
import json, sys
import pandas as pd
sys.path.insert(0, '/home/lausing/Documents/MyProjects/Stock_Trading_App/services/market-data')
D = '/home/lausing/Documents/MyProjects/rzy-session-evidence/2026-10-21/'
rev = pd.read_csv(D + 'bar_revisions.csv', parse_dates=['ts', 'fetched_at'])
reb = pd.read_csv(D + 'rebuilds.csv', parse_dates=['rebuilt_at'])
occ = pd.read_csv(D + 'occurrences.csv', parse_dates=['signal_time', 'first_cutoff', 'first_seen_at', 'queued_at'])
scans = pd.read_csv(D + 'scan_runs.csv', parse_dates=['scan_time'])
out = {}
done = rev[~rev.classification.str.endswith('_forming_bar')]
out['revisions'] = {'completed': len(done), 'by_magnitude': done.magnitude.value_counts().to_dict(),
    'magnitude_x_class': done.groupby(['magnitude', 'classification']).size().unstack(fill_value=0).to_dict()}
# Rounding vs material by provider pair
done = done.assign(pair=done.previous_provider.fillna('unknown') + '->' + done.provider)
out['revisions']['pair_x_magnitude'] = done.groupby(['pair', 'magnitude']).size().unstack(fill_value=0).to_dict('index')
# Repeated rewrites of the same bar (flip-flop)
per_bar = done.groupby(['symbol', 'ts']).size()
out['revisions']['bars_revised'] = int(len(per_bar)); out['revisions']['revisions_per_bar'] = per_bar.describe().round(2).to_dict()
# ── Link each rebuild to the revisions that preceded it on the same symbol ──
reb['symbol'] = reb.stream_key.str.split(':').str[0]; reb['tf'] = reb.stream_key.str.split(':').str[1]
reb = reb.sort_values('rebuilt_at')
reb['prev'] = reb.groupby('stream_key').rebuilt_at.shift(1)
rows = []
mat_by_sym = {s: g.sort_values('fetched_at') for s, g in done.groupby('symbol')}
for r in reb.itertuples():
    if r.reason != 'stored_completed_bar_changed':
        rows.append('gap_after_checkpoint'); continue
    lo = r.prev if pd.notna(r.prev) else r.rebuilt_at - pd.Timedelta(hours=24)
    g = mat_by_sym.get(r.symbol)
    w = g[(g.fetched_at > lo) & (g.fetched_at <= r.rebuilt_at)] if g is not None else pd.DataFrame()
    if w.empty: rows.append('unresolved_no_traced_revision')
    elif (w.magnitude == 'material').any():
        rows.append('material_cross_provider' if (w[w.magnitude == 'material'].classification == 'cross_provider').any() else 'material_other')
    else: rows.append('rounding_only')
reb['driver'] = rows
out['rebuilds'] = {'total': len(reb), 'by_driver': reb.driver.value_counts().to_dict(),
                   'by_tf_driver': reb.groupby(['tf', 'driver']).size().unstack(fill_value=0).to_dict('index')}
# ── Signals delayed by restatement: today's occurrences first seen only in a rebuild ──
emailable = {'TRIGGERED', 'REVERSAL_WATCH', 'EXHAUSTION_WATCH', 'EXIT_PENDING', 'PAPER_EXIT', 'ENTRY_CANCELLED'}
today = occ[(occ.signal_time >= '2026-10-21 13:30') & (occ.signal_time <= '2026-10-21 20:05')]
today = today.assign(delay_min=(today.first_seen_at - today.signal_time).dt.total_seconds() / 60,
                     emailable=today.event_type.isin(emailable))
def lat(x): return x.describe(percentiles=[.5, .9, .95]).round(2).to_dict() if len(x) else {}
out['todays_signals'] = {
    'occurrences': len(today), 'by_observed_in': today.observed_in.value_counts().to_dict(),
    'emailable_by_observed_in': today[today.emailable].observed_in.value_counts().to_dict(),
    'emailable_delay_minutes_incremental': lat(today[today.emailable & (today.observed_in == 'incremental')].delay_min),
    'emailable_delay_minutes_rebuild': lat(today[today.emailable & (today.observed_in == 'rebuild')].delay_min),
    'emailable_rebuild_by_tf': today[today.emailable & (today.observed_in == 'rebuild')].timeframe.value_counts().to_dict(),
    'emailable_incremental_by_tf': today[today.emailable & (today.observed_in == 'incremental')].timeframe.value_counts().to_dict(),
}
# ── Scan duration vs mailing window ──
sess = scans[scans['mode'] == 'session'].copy()
sess['paused'] = sess.statuses.apply(lambda s: json.loads(s).get('restatement_pending', 0))
sess['streams'] = sess.streams
out['scans'] = {'session_runs': len(sess), 'duration_s': lat(sess.duration_seconds),
                'runs_over_8min': int((sess.duration_seconds > 480).sum()),
                'corr_duration_paused': round(sess.duration_seconds.corr(sess.paused), 3),
                'streams_per_scan': lat(sess.streams)}
print(json.dumps(out, indent=1, default=str))
reb.to_csv('rebuild_drivers.csv', index=False)
