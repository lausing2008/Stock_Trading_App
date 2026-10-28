import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/router';
import useSWR from 'swr';
import Link from 'next/link';
import { api, type RzyAdminOverview } from '@/lib/api';
import { getSession } from '@/lib/auth';
import { RZY_ALERT_STATE, RZY_CANCELLATION, RZY_EXCLUSION, RZY_STREAM_STATUS, cancellationParts, countReasons, groupExclusions, rzyReasonText } from '@/lib/rzyReasons';

const card = 'rounded-lg border border-slate-700 bg-slate-900/60 p-4';
const th = 'px-2 py-1 text-left text-[11px] uppercase tracking-wide text-slate-400';
const td = 'px-2 py-1 align-top';
const fmt = (v?: number | null, d = 2) => (v == null || !Number.isFinite(v) ? '—' : v.toFixed(d));
const when = (v?: string | null) => (v ? new Date(/(Z|[+-]\d\d:\d\d)$/.test(v) ? v : `${v}Z`).toLocaleString() : '—');

function Tile({ label, value, note, tone = 'text-slate-100' }: { label: string; value: React.ReactNode; note?: string; tone?: string }) {
  return <div className={card}><div className="text-[11px] uppercase tracking-wide text-slate-400">{label}</div>
    <div className={`mt-1 text-2xl font-semibold tabular-nums ${tone}`}>{value}</div>{note && <div className="mt-1 text-xs text-slate-400">{note}</div>}</div>;
}

function Section({ title, intro, children }: { title: string; intro?: React.ReactNode; children: React.ReactNode }) {
  return <section className="space-y-3"><h2 className="text-lg font-semibold text-slate-100">{title}</h2>
    {intro && <div className="max-w-4xl text-sm text-slate-400">{intro}</div>}{children}</section>;
}

// Every rule is counted in bars of the stream's own timeframe, so the same defaults cover very
// different clock spans. A US regular session is 78 × 5m, 26 × 15m or 7 × 1h bars (last one 30 min).
const TF_SCALE: { tf: string; role: string; pivot: string; armed: string; band: string; cost: string; evidence: string }[] = [
  { tf: '5m', role: 'Timing: the exact rejection candle', pivot: '35 min', armed: '100 min', band: '~1 h 40 min',
    cost: '0.117R (median risk 0.82% of price)', evidence: 'n=439, no edge' },
  { tf: '15m', role: 'Setup: the intraday A–B–C structure', pivot: '1 h 45 min', armed: '5 h', band: '5 h',
    cost: '0.069R', evidence: 'n=175, no edge' },
  { tf: '1h', role: 'Context: the swing over several sessions', pivot: '~1 session', armed: '~3 sessions', band: '~3 sessions',
    cost: 'not reported', evidence: 'n=28, too few' },
  { tf: '1d', role: 'Backdrop: the trend you trade with or against', pivot: '7 sessions', armed: '~4 weeks', band: '~1 month',
    cost: '0.011R', evidence: 'n=50, too few' },
];

function TimeframeGuide() {
  return <details className={card}>
    <summary className="cursor-pointer font-semibold text-teal-400">Reading the timeframes: what 5m, 15m, 1h and daily tell you, and how to use them for entries</summary>
    <div className="mt-3 space-y-4 text-sm text-slate-300">
      <p>Each symbol and timeframe is its own stream: the scanner runs the same rules separately on 5m, 15m, 1h and daily bars and never compares them. 15m and 1h bars are built from completed 5m bars; an incomplete bin is withheld, not filled in. Because every rule is counted in <i>bars</i>, the same settings mean very different clock time:</p>
      <div className="overflow-x-auto"><table className="w-full">
        <thead><tr>{['TF', 'What it is good for', 'Pivot confirms after (7 bars)', 'Armed setup expires (20 bars)', 'Bollinger window (20 bars)', 'Cost per trade', 'Evidence (Jul–Oct 2026)'].map(h => <th key={h} className={th}>{h}</th>)}</tr></thead>
        <tbody>{TF_SCALE.map(r => <tr key={r.tf} className="border-t border-slate-800"><td className={`${td} font-mono`}>{r.tf}</td><td className={td}>{r.role}</td>
          <td className={td}>{r.pivot}</td><td className={td}>{r.armed}</td><td className={td}>{r.band}</td><td className={td}>{r.cost}</td><td className={td}>{r.evidence}</td></tr>)}</tbody>
      </table></div>
      <ul className="list-disc space-y-1 pl-5 text-slate-400">
        <li><b className="text-slate-200">Lower timeframe = earlier but noisier.</b> A 5m pivot is known 35 minutes after it forms, a daily pivot only after 7 sessions. 5m structures are small, so the stop is tight and costs take a large share of the risk: about 0.12R per trade, which on its own turned a break-even 5m result into a loss.</li>
        <li><b className="text-slate-200">Higher timeframe = slower but more meaningful.</b> A daily or hourly trend (two higher highs and lows, or two lower) describes the move the intraday setups sit inside. Its stops are wider, so costs matter far less, but it produces few setups.</li>
        <li><b className="text-slate-200">15m is the middle ground.</b> It is where costs are smallest relative to risk among the intraday streams, which is why the evaluation suggested testing it first.</li>
        <li><b className="text-slate-200">Targets are rarely reached.</b> Planned targets average about 3R, but the median trade lasted 4 bars and most exits were a close back through the line. On 5m that is 20 minutes; on 1h it is most of a session.</li>
      </ul>
      <h3 className="font-semibold text-slate-100">Using them together to choose an entry</h3>
      <p className="text-amber-300">This is a reading practice, not something the scanner does. Combining timeframes has not been implemented or tested here, and no single timeframe has shown an edge over random entry timing. Treat it as a way to filter and time setups, not as a validated method.</p>
      <ol className="list-decimal space-y-1 pl-5 text-slate-400">
        <li><b className="text-slate-200">Direction from the top (daily, then 1h).</b> Note whether the higher timeframe is trending up, trending down or unclear. Prefer lower-timeframe setups in the same direction. A sequence of 3 or more, or an exhaustion warning, means that trend is late: be more selective, not more aggressive.</li>
        <li><b className="text-slate-200">Setup on 15m.</b> Look for an armed A–B–C structure in that direction with planned reward/risk of at least 1.5, where C pulled back to near the middle band. The line through A and C is where the entry should happen; D is the measured target.</li>
        <li><b className="text-slate-200">Timing on 5m.</b> As price comes back to the 15m line, wait for a <i>completed</i> 5m candle that touches the line and closes back in the trend direction. Do not buy the forming candle or a close already beyond the outer Bollinger band (the no-chase rule).</li>
        <li><b className="text-slate-200">Keep the stop where the setup is proven wrong.</b> The stop belongs beyond the structure you are trading (the 15m C or line, plus a buffer), not just beyond the 5m candle. Shrinking the stop to the 5m candle makes reward/risk look better on paper, but it raises cost drag and the chance of being stopped by noise.</li>
        <li><b className="text-slate-200">Recheck at the next open, and stand aside on conflict.</b> Reward/risk must still be at least 1.5 at the actual next-bar open. If the timeframes disagree (for example a 5m long trigger inside a daily downtrend), treat it as counter-trend and skip it, or treat it only as an exhaustion/reversal watch.</li>
        <li><b className="text-slate-200">Check the data first.</b> Use only streams shown as usable in Data health below. 15m and 1h are built from 5m, so a 5m restatement or missing bin affects all three.</li>
      </ol>
      <p className="text-xs text-slate-500">Sources: rule defaults in docs/2026-10-19/little-rzy-parameters.md; evaluation figures from docs/2026-10-19/little-rzy-evaluation-results.md and docs/2026-10-20/little-rzy-evaluation-rerun.md (5m/15m/1h Jul 21 – Oct 20 2026; daily Dec 2023 – Oct 2026; costs as modelled there).</p>
    </div>
  </details>;
}

export default function LittleRzyAdminPage() {
  const router = useRouter();
  const [allowed, setAllowed] = useState(false);
  useEffect(() => {
    const s = getSession();
    if (!s || s.role !== 'admin') { router.replace('/'); return; }
    setAllowed(true);
  }, [router]);
  const { data: overview, error: overviewError } = useSWR<RzyAdminOverview>(allowed ? 'rzy-admin-overview' : null, api.rzyAdminOverview, { refreshInterval: 60_000 });
  const { data: digest, error: digestError } = useSWR(allowed ? 'rzy-digest-snapshot' : null, api.rzyDigest, { refreshInterval: 60_000 });
  const { data: paper, error: paperError } = useSWR(allowed ? 'rzy-paper' : null, api.rzyPaper, { refreshInterval: 60_000 });
  if (!allowed) return null;

  const status = overview?.streams.by_status ?? {};
  const usable = status.research_only ?? 0;
  const paused = (status.restatement_pending ?? 0) + (status.rebuild_pending ?? 0);
  const positions = paper?.positions ?? [];
  const cancelled = positions.filter(p => p.cancelled);
  const reasonCounts = countReasons(cancelled);

  return <div className="mx-auto max-w-6xl space-y-8 px-4 py-6 text-sm text-slate-300">
    <header className="space-y-2">
      <h1 className="text-2xl font-bold text-slate-100">Little RZY</h1>
      <p className="max-w-4xl text-slate-400">Operations and research view for the Little RZY-inspired strategy: what the scanner is doing, the morning digest preview, research alerts, data health and the research paper ledger.</p>
      <p className="inline-block rounded border border-amber-500/50 bg-amber-500/10 px-2 py-1 text-xs text-amber-300">
        {overview?.positioning ?? 'Research overlay and watch alert. Not a validated trade recommendation; paper execution is disabled.'}
      </p>
      <p className="text-xs text-slate-500">Strategy {overview?.strategy_version ?? '…'} · config {overview?.config_hash ?? '…'} · email preferences live in <Link className="text-teal-400 underline" href="/settings">Settings</Link>.</p>
    </header>

    <TimeframeGuide />

    {overviewError && <p role="alert" className="text-red-400">Overview unavailable: {String(overviewError.message ?? overviewError)}</p>}
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      <Tile label="Streams usable now" value={overview ? `${usable} / ${overview.streams.total}` : '…'} note={`research_only in a current snapshot${overview?.streams.expired ? ` · ${overview.streams.expired} expired snapshot(s) not counted` : ''}`} tone="text-teal-300" />
      <Tile label="Paused or rebuilding" value={overview ? paused : '…'} note="bars restated by the data source" tone={paused ? 'text-amber-300' : 'text-slate-100'} />
      <Tile label="Scan time (median / p95)" value={overview ? `${fmt(overview.scans.duration_seconds.median, 0)}s / ${fmt(overview.scans.duration_seconds.p95, 0)}s` : '…'} note={overview ? `${overview.scans.recent_session_scans} session scans in the last 20 h` : undefined} />
      <Tile label="Signals too late to email" value={overview ? overview.scans.too_late_to_mail : '…'} note={overview ? `${overview.scans.queued} queued in the last 20 h` : undefined} tone={overview?.scans.too_late_to_mail ? 'text-red-300' : 'text-slate-100'} />
    </div>

    {overview?.monitoring && <Section title="Alert timing and readiness" intro={overview.monitoring.note}>
      <p className="text-amber-300">Research only · execution is not ready.</p>
      <ul className="list-disc pl-5 text-xs text-slate-400">{overview.monitoring.blockers.map(b => <li key={b}>{b}</li>)}</ul>
      <div className={`${card} overflow-x-auto`}><table className="w-full tabular-nums"><thead><tr>{['TF', 'Trigger occurrences', 'Stage', 'Samples', 'Median', 'p95', 'Maximum'].map(h => <th className={th} key={h}>{h}</th>)}</tr></thead>
        <tbody>{Object.entries(overview.monitoring.by_timeframe).flatMap(([tf, row]) => Object.entries({ ...row.seconds_from_close, ...row.segments_seconds }).map(([stage, values]) => <tr key={`${tf}:${stage}`}>
          <td className={td}>{tf}</td><td className={td}>{row.trigger_occurrences}</td><td className={td}>{stage.replaceAll('_', ' ')}</td><td className={td}>{values.samples}</td>
          <td className={td}>{fmt(values.median, 0)}s</td><td className={td}>{fmt(values.p95, 0)}s</td><td className={td}>{fmt(values.max, 0)}s</td>
        </tr>))}</tbody></table>
        {!Object.keys(overview.monitoring.by_timeframe).length && <p>No incremental trigger observations in the last 20 hours.</p>}
        {Object.entries(overview.monitoring.by_timeframe).map(([tf, row]) => <p className="mt-2 text-xs text-slate-400" key={tf}>{tf}: {row.too_late_to_queue} detected past the queue limit; {row.invalid_timestamps} invalid timestamps. Outcomes: {Object.entries(row.receipt_outcomes).map(([name, n]) => `${name} ${n}`).join(' · ') || 'no receipts'}.</p>)}
        {Object.entries(overview.monitoring.by_timeframe).filter(([tf]) => tf === '5m').map(([tf, row]) => <p className="mt-2 text-xs text-slate-400" key={`writes:${tf}`}>Post-close write evidence: {row.post_close_write_missing ?? 'unknown'} missing, {row.post_close_write_timestamp_excluded ?? 'unknown'} excluded timestamps. Unknown observations are not zero-delay samples.</p>)}
      </div>
      <details className={card}><summary className="cursor-pointer text-teal-400">Stream freshness and execution blockers</summary>
        <div className="mt-2 overflow-x-auto"><table className="w-full"><thead><tr>{['Stream', 'Status', 'Latest data close', 'Expected close', 'Latest bin present', 'Paper blockers'].map(h => <th className={th} key={h}>{h}</th>)}</tr></thead>
          <tbody>{overview.monitoring.streams.map(s => <tr key={`${s.symbol}:${s.timeframe}`}><td className={td}>{s.symbol} {s.timeframe}</td><td className={td}>{s.status}</td>
            <td className={td}>{when(s.data_as_of)}</td><td className={td}>{when(s.expected_latest_close)}</td><td className={td}>{s.latest_completed_bin_present == null ? 'unknown' : s.latest_completed_bin_present ? 'yes' : 'missing'}</td>
            <td className={td}>{s.paper_blockers?.join(', ') || 'unknown'}</td></tr>)}</tbody></table></div>
      </details>
      {overview.ingest_observations && <details className={card}><summary className="cursor-pointer text-teal-400">Received, rejected and written bars (last 20 hours)</summary>
        <p className="mt-2 text-xs text-slate-400">Received counts start after OHLCV validation. Duplicate, off-grid and authority filters partition these rows. Written counts committed insert/update operations, including repeated writes of existing bars, rather than unique candles. Empty evidence does not prove that ingestion was inactive or complete.</p>
        <div className="mt-2 overflow-x-auto"><table className="w-full tabular-nums"><thead><tr>{['Session', 'Market / type', 'TF / provider', 'Received', 'Duplicates', 'Off-grid', 'Authority', 'Written'].map(h => <th key={h} className={th}>{h}</th>)}</tr></thead>
          <tbody>{overview.ingest_observations.map((r, i) => <tr key={i}><td className={td}>{r.session_day}</td><td className={td}>{r.market} / {r.session_kind}</td><td className={td}>{r.timeframe} / {r.provider}</td>
            <td className={td}>{r.received}</td><td className={td}>{r.duplicate_dropped}</td><td className={td}>{r.off_grid_rejected}</td><td className={td}>{r.authority_rejected}</td><td className={td}>{r.written}</td></tr>)}</tbody></table></div>
        {!overview.ingest_observations.length && <p className="mt-2 text-xs text-slate-400">No ingest observations recorded in this window.</p>}
      </details>}
    </Section>}

    <Section title="Morning digest preview" intro={<>Read from the scanner&apos;s saved results; nothing is recomputed when you open this page. Candidates are armed setups with planned reward/risk of at least 1.5. Entry and stop are provisional references: wait for an intraday trigger candle and recheck the open.</>}>
      {digestError && <p role="alert" className="text-red-400">Preview unavailable.</p>}
      {digest && digest.available === false && <p role="status" className={card}>{digest.message ?? 'Preview not available yet.'}</p>}
      {digest && digest.available !== false && <>
        <p role="status" className="text-xs text-slate-400">Snapshot: oldest stream checked {when(digest.snapshot_from)} ({digest.snapshot_age_minutes} min ago), newest {when(digest.snapshot_to)} · {digest.streams_with_snapshot} of {digest.streams_expected} streams have a snapshot{digest.streams_expired ? `, ${digest.streams_expired} expired (not used)` : ''} · {digest.total_candidates ?? digest.candidates.length} candidate(s), showing up to 10.</p>
        {digest.data_cutoff_from && <p className="text-xs text-slate-400">Input data cutoffs: {when(digest.data_cutoff_from)} to {when(digest.data_cutoff_to)}. Check times above show when processing finished; they do not make older inputs current.</p>}
        {!!digest.streams_unknown_cutoff && <p className="text-xs text-amber-300">{digest.streams_unknown_cutoff} snapshot(s) have an unknown data cutoff and are excluded.</p>}
        <div className={`${card} overflow-x-auto`}>
          {digest.candidates.length ? <table className="w-full tabular-nums"><thead><tr>{['Symbol', 'Direction', 'TF', 'Entry ref', 'Provisional stop', 'Target', 'Planned R/R'].map(h => <th key={h} className={th}>{h}</th>)}</tr></thead>
            <tbody>{digest.candidates.map(c => <tr key={`${c.symbol}:${c.timeframe}:${c.direction}`} className="border-t border-slate-800">
              <td className={td}>{c.symbol}</td><td className={`${td} ${c.direction === 'long' ? 'text-teal-300' : 'text-rose-300'}`}>{c.direction}</td><td className={td}>{c.timeframe}</td>
              <td className={td}>{fmt(c.entry_reference)}</td><td className={td}>{fmt(c.provisional_stop)}</td><td className={td}>{fmt(c.target)}</td><td className={td}>{fmt(c.planned_reward_risk)}</td></tr>)}</tbody></table>
            : <p>No qualifying candidates. Do not force a trade.</p>}
        </div>
        <div className={card}><h3 className="mb-2 font-semibold text-slate-200">Why streams are excluded ({digest.exclusions.length})</h3>
          <ul className="space-y-2">{groupExclusions(digest.exclusions).map(([reason, items]) => <li key={reason}>
            <span className="font-mono text-xs text-slate-200">{reason}</span> <span className="text-slate-500">· {items.length}</span>
            <div className="text-xs text-slate-400">{rzyReasonText(RZY_EXCLUSION, reason)}</div>
            <details><summary className="cursor-pointer text-xs text-teal-400">Show streams</summary><div className="mt-1 text-xs text-slate-400">{items.join(', ')}</div></details></li>)}</ul>
        </div>
      </>}
    </Section>

    <Section title="Research alerts" intro="Emails sent by Little RZY (pattern alerts and the morning digest). Recipients are not shown. Accepted means the email provider took the message; it is not proof of inbox delivery.">
      {overview && <div className={`${card} overflow-x-auto`}>
        <div className="mb-2 flex flex-wrap gap-2 text-xs">{Object.entries(overview.alerts.states).map(([k, v]) => <span key={k} className="rounded bg-slate-800 px-2 py-0.5 font-mono">{k}: {v}</span>)}</div>
        <table className="w-full"><thead><tr>{['Queued', 'Subject', 'Outcome', 'Accepted'].map(h => <th key={h} className={th}>{h}</th>)}</tr></thead>
          <tbody>{overview.alerts.recent.map((m, i) => <tr key={i} className="border-t border-slate-800">
            <td className={`${td} whitespace-nowrap`}>{when(m.created_at)}</td><td className={td}>{m.subject}</td>
            <td className={td}><span className="font-mono text-xs">{m.state}</span><div className="text-xs text-slate-500">{RZY_ALERT_STATE[m.state] ?? ''}</div></td>
            <td className={`${td} whitespace-nowrap`}>{when(m.accepted_at)}</td></tr>)}</tbody></table>
      </div>}
    </Section>

    <Section title="Data health" intro="Status of every watchlist stream from its latest scan, rebuilds caused by restated bars, and the bar-revision trace (which provider rewrote completed bars).">
      {overview && <div className="grid gap-3 md:grid-cols-2">
        <div className={`${card} overflow-x-auto`}><h3 className="mb-2 font-semibold text-slate-200">Streams by timeframe and status</h3>
          <table className="w-full tabular-nums"><thead><tr><th className={th}>TF</th>{Object.keys(status).map(k => <th key={k} className={th}>{k}</th>)}</tr></thead>
            <tbody>{Object.entries(overview.streams.by_timeframe_status).map(([tf, row]) => <tr key={tf} className="border-t border-slate-800"><td className={td}>{tf}</td>
              {Object.keys(status).map(k => <td key={k} className={td}>{row[k] ?? 0}</td>)}</tr>)}</tbody></table>
          <ul className="mt-3 space-y-1 text-xs text-slate-400">{Object.keys(status).map(k => <li key={k}><span className="font-mono text-slate-300">{k}</span>: {RZY_STREAM_STATUS[k] ?? RZY_EXCLUSION[k] ?? ''}</li>)}</ul>
          <p className="mt-2 text-xs text-slate-500">Snapshots from {when(overview.streams.oldest_snapshot)} to {when(overview.streams.newest_snapshot)}.</p>
        </div>
        <div className={card}><h3 className="mb-2 font-semibold text-slate-200">Restated bars (last 20 h)</h3>
          <p>Rebuilds: <span className="tabular-nums">{overview.rebuilds_last_20h.total}</span></p>
          <ul className="mb-3 text-xs text-slate-400">{Object.entries(overview.rebuilds_last_20h.by_reason).map(([k, v]) => <li key={k}><span className="font-mono">{k}</span>: {v}</li>)}</ul>
          {overview.occurrences_last_20h && <p className="mb-3 text-xs text-slate-400">New signal occurrences: {overview.occurrences_last_20h.total} ({Object.entries(overview.occurrences_last_20h.by_observation).map(([k, v]) => `${k} ${v}`).join(', ') || 'none'}; incremental = seen on a newly completed bar, rebuild/bootstrap = found while replaying stored history), {overview.occurrences_last_20h.queued} queued for email.</p>}
          <p>Completed-bar revisions by source: <span className="tabular-nums">{Object.values(overview.bar_revisions_last_20h.completed).reduce((a, b) => a + b, 0)}</span></p>
          <ul className="text-xs text-slate-400">{Object.entries(overview.bar_revisions_last_20h.completed).map(([k, v]) => <li key={k}><span className="font-mono">{k}</span>: {v}</li>)}</ul>
          <p className="mt-2 text-xs text-slate-500">cross_provider: a different data provider rewrote the bar. same_provider: the same provider corrected it. previous_provider_unknown: the bar predates the trace and counts toward neither. Forming-bar updates ({Object.values(overview.bar_revisions_last_20h.forming).reduce((a, b) => a + b, 0)}) are normal and listed separately.</p>
        </div>
      </div>}
    </Section>

    <Section title="Scanner runs" intro="Each run evaluates every watchlist stream. The scan is scheduled every minute; a run that takes longer delays the next one, which is how signals become too late to email.">
      {overview && <div className={`${card} overflow-x-auto`}><table className="w-full tabular-nums"><thead><tr>{['Time', 'Mode', 'Duration', 'Streams', 'New emailable', 'Too late', 'Queued', 'Statuses'].map(h => <th key={h} className={th}>{h}</th>)}</tr></thead>
        <tbody>{overview.scans.latest.map((r, i) => <tr key={i} className="border-t border-slate-800">
          <td className={`${td} whitespace-nowrap`}>{when(r.scan_time)}</td><td className={td}>{r.mode}</td><td className={td}>{fmt(r.duration_seconds, 1)}s</td><td className={td}>{r.streams}</td>
          <td className={td}>{r.new_emailable_events}</td><td className={td}>{r.too_late_to_mail}</td><td className={td}>{r.queued}</td>
          <td className={`${td} text-xs text-slate-400`}>{Object.entries(r.statuses).map(([k, v]) => `${k} ${v}`).join(' · ')}</td></tr>)}</tbody></table>
        {!overview.scans.latest.length && <p>No scan has been recorded yet. Scans run from 50 minutes before the US open until 5 minutes after the close.</p>}
      </div>}
    </Section>

    <Section title="Research paper ledger" intro="A separate, simulated ledger in one-share units. It never touches your real or paper portfolios and never sends broker orders.">
      {paper?.performance && <div className="space-y-3">
        <h3 className="font-semibold text-slate-100">Forward simulator performance</h3>
        <p className="text-xs text-slate-400">{paper.performance.coverage} First observation in this window: {when(paper.performance.recording_since)}.</p>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Tile label="Scored forward trades" value={paper.performance.summary.scored_trades} note={`${paper.performance.summary.cancelled} cancelled · ${paper.performance.summary.unresolved} unresolved`} />
          <Tile label="Forward win rate" value={paper.performance.summary.win_rate == null ? '—' : `${fmt(paper.performance.summary.win_rate * 100, 1)}%`} />
          <Tile label="Net expectancy" value={`${fmt(paper.performance.summary.net_expectancy_r, 3)} R`} />
          <Tile label="Realised drawdown" value={`${fmt(paper.performance.summary.realized_drawdown_r, 2)} R`} note="closed-trade exit order" />
        </div>
        <p className="text-xs text-slate-400">{paper.performance.units}</p>
        <p className="text-xs text-slate-400">Average winning trade: {fmt(paper.performance.summary.average_win_r, 3)} R · average losing or flat trade: {fmt(paper.performance.summary.average_loss_r, 3)} R.</p>
        <p className="text-xs text-amber-300">{paper.performance.summary.conclusion}</p>
        <p className="text-xs text-slate-400">Excluded: {paper.performance.summary.replay_or_imported} replay/imported observations, {paper.performance.summary.superseded} superseded generations, {paper.performance.summary.unscorable_closed} closed trades without valid risk. Categories can overlap.</p>
        <details className={card}><summary className="cursor-pointer text-teal-400">Performance by timeframe and direction</summary>
          <table className="mt-2 w-full tabular-nums"><thead><tr>{['Group', 'Scored trades', 'Win rate', 'Net expectancy (R)'].map(h => <th className={th} key={h}>{h}</th>)}</tr></thead>
            <tbody>{Object.entries(paper.performance.groups).flatMap(([dimension, groups]) => Object.entries(groups).map(([name, s]) => <tr key={`${dimension}:${name}`}>
              <td className={td}>{dimension}: {name}</td><td className={td}>{s.scored_trades}</td><td className={td}>{s.win_rate == null ? '—' : `${fmt(s.win_rate * 100, 1)}%`}</td><td className={td}>{fmt(s.net_expectancy_r, 3)} R</td>
            </tr>))}</tbody></table>
        </details>
        <details className={card}><summary className="cursor-pointer text-teal-400">Durable lifecycle journal ({paper.performance.journal_total}; latest 200 shown)</summary>
          <div className="mt-2 overflow-x-auto"><table className="w-full"><thead><tr>{['Stream', 'Generation', 'Cohort', 'State', 'Fill', 'Net / share', 'Lifecycle'].map(h => <th key={h} className={th}>{h}</th>)}</tr></thead>
            <tbody>{paper.performance.journal.map(p => <tr key={p.journal_id}><td className={td}>{p.symbol} {p.timeframe}</td><td className={td}>{p.generation}{p.superseded ? ' (superseded)' : ''}</td>
              <td className={td}>{p.observed_in}</td><td className={td}>{p.state}{p.cancelled ? ' (cancelled)' : ''}</td><td className={td}>{fmt(p.fill)}</td><td className={td}>{fmt(p.net, 4)}</td>
              <td className={td}>{p.lifecycle.map(e => <div key={e.event_id} className="text-xs">{e.event_type} · {when(e.signal_time)}</div>)}</td></tr>)}</tbody></table></div>
        </details>
      </div>}
      <div className="rounded-lg border border-sky-600/40 bg-sky-500/10 p-4 text-sm text-sky-100">
        <h3 className="mb-1 font-semibold">What &ldquo;CLOSED (cancelled)&rdquo; means</h3>
        <p>When a setup triggers, the strategy plans a paper entry at the next bar&apos;s open and re-checks it then. If any check fails, the paper trade is <b>cancelled before it opens</b>: no position, no profit or loss. It is recorded as CLOSED with the reasons that failed. A cancelled row is not a losing trade, and the trigger stays visible on the chart as research history.</p>
        <p className="mt-2">Today every row is cancelled because stored prices carry no verified provider timing (<span className="font-mono">data_basis_or_freshness_unverified</span>), and shorts also lack borrow data. That is deliberate until execution data is verified.</p>
      </div>
      {paperError && <p role="alert" className="text-red-400">Ledger unavailable.</p>}
      {paper && <>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Tile label="Paper positions" value={positions.length} note="current configuration" />
          <Tile label="Cancelled before opening" value={cancelled.length} />
          <Tile label="Opened" value={positions.filter(p => p.fill != null).length} />
          <Tile label="Still open or pending" value={positions.filter(p => p.state !== 'CLOSED').length} />
        </div>
        <div className={card}><h3 className="mb-2 font-semibold text-slate-200">Why entries were cancelled</h3>
          <ul className="space-y-2">{reasonCounts.map(([reason, n]) => <li key={reason}><span className="font-mono text-xs text-slate-200">{reason}</span> <span className="text-slate-500">· {n}</span>
            <div className="text-xs text-slate-400">{rzyReasonText(RZY_CANCELLATION, reason)}</div></li>)}</ul>
        </div>
        <div className={`${card} overflow-x-auto`}><table className="w-full"><thead><tr>{['Symbol', 'TF', 'State', 'Generation', 'Fill', 'Net / share', 'Reasons'].map(h => <th key={h} className={th}>{h}</th>)}</tr></thead>
          <tbody>{positions.map((p, i) => <tr key={i} className="border-t border-slate-800">
            <td className={td}>{p.symbol}</td><td className={td}>{p.timeframe}</td>
            <td className={td}>{p.state}{p.cancelled ? ' (cancelled)' : ''}</td><td className={`${td} tabular-nums`}>{p.generation ?? '—'}</td>
            <td className={`${td} tabular-nums`}>{fmt(p.fill)}</td><td className={`${td} tabular-nums`}>{fmt(p.net, 4)}</td>
            <td className={td}><div className="flex flex-wrap gap-1">{cancellationParts(p.reason).map(r => <span key={r} title={rzyReasonText(RZY_CANCELLATION, r)}
              className={`rounded px-1.5 py-0.5 font-mono text-[11px] ${r === 'data_basis_or_freshness_unverified' || r === 'shortability_or_borrow_cost_unverified' ? 'bg-slate-800 text-slate-400' : 'bg-amber-500/15 text-amber-300'}`}>{r}</span>)}</div></td></tr>)}</tbody></table>
          {!positions.length && <p>No research paper positions yet.</p>}
          {paper.provenance && <p className="mt-2 text-xs text-slate-500">{paper.provenance}</p>}
        </div>
      </>}
    </Section>
  </div>;
}
