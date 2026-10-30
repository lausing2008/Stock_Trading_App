import React, { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import useSWR from 'swr';
import { api } from '@/lib/api';
import WatchlistPickerButton from '@/components/WatchlistPickerButton';
import { classificationLabel, selectValuationRows, valuationCsv, valuationPerformanceCsv,
  type ValuationFilter, type ValuationMetric, type ValuationRow } from '@/lib/valuationReport';

const panel: React.CSSProperties = { background: '#111827', border: '1px solid #253047', borderRadius: 12, padding: 20, minWidth: 0 };
const control: React.CSSProperties = { background: '#0b1220', color: '#e2e8f0', border: '1px solid #334155',
  borderRadius: 7, padding: '8px 12px', fontSize: 13 };
const muted: React.CSSProperties = { color: '#94a3b8', fontSize: 12, lineHeight: 1.7 };
const labels: [ValuationFilter, string][] = [['all', 'All companies'], ['undervalued', 'Undervalued research'],
  ['overvalued', 'Overvalued'], ['analyst_risk', 'Analyst risk flags'], ['reviewed', 'Reviewed candidates'], ['qualified', 'Qualified opportunities']];
const columns = [['symbol', 'Company'], ['price', 'Price'], ['fair_value', 'Model value'],
  ['discount_pct', 'Discount %'], ['trailing_pe', 'P/E (TTM)'], ['peg', 'PEG*'], ['rsi', 'RSI (14)']];

function num(value: number | null | undefined, digits = 1) {
  return value == null ? '—' : value.toLocaleString(undefined, { maximumFractionDigits: digits });
}
function when(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString() : 'Unknown';
}
function Metric({ metric, money = false }: { metric?: ValuationMetric; money?: boolean }) {
  const state = metric?.state || 'UNAVAILABLE';
  const provenance = [metric?.reason, metric?.source, metric?.as_of, metric?.basis].filter(Boolean).join(' · ');
  return <span title={provenance || undefined} style={{ position: 'relative', color: state === 'OK' ? '#e2e8f0' : '#fbbf24' }}>
    {metric?.value == null ? '—' : `${money ? '$' : ''}${num(metric.value, money ? 2 : 1)}`}
    {state === 'STALE' && <small> stale</small>}
    {metric?.value != null && metric.source?.startsWith('Unusual Whales') && <small style={{ color: '#94a3b8' }}> UW</small>}
    {metric?.value == null && <span style={{ position: 'absolute', left: 0, top: 0, width: 1, height: 1, padding: 0,
      overflow: 'hidden', clip: 'rect(0, 0, 0, 0)', whiteSpace: 'nowrap', border: 0 }}> {metric?.reason || 'Unavailable'}</span>}
  </span>;
}

function CompanyDetail({ row }: { row: ValuationRow }) {
  return <div style={{ padding: '18px 10px', fontSize: 13, lineHeight: 1.7, maxWidth: 'calc(100vw - 112px)' }}>
    <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'center', marginBottom: 14 }}>
      <strong>{row.name}</strong>
      {row.tracked && <><Link href={`/stock/${encodeURIComponent(row.symbol)}`} style={{ color: '#a5b4fc' }}>Stock research ↗</Link>
        <WatchlistPickerButton symbol={row.symbol} size="sm" /></>}
      {row.source_url && <a href={row.source_url} target="_blank" rel="noreferrer" style={{ color: '#a5b4fc' }}>{row.provider || 'Yahoo'} source ↗</a>}
      <Link href="/quality-value" style={{ color: '#a5b4fc' }}>Quality &amp; Value ↗</Link>
    </div>
    {row.candidate_review && <section style={{ ...panel, marginBottom: 20 }} aria-label="Issuer qualification review">
      <h4 style={{ marginTop: 0 }}>Issuer qualification review · {row.candidate_review.decision}</h4>
      <p style={muted}>Reviewed {row.candidate_review.reviewed_at} · v{row.candidate_review.version} · {row.candidate_review.author} · {row.candidate_review.status}</p>
      <p>{row.candidate_review.summary}</p>
      {!!Object.keys(row.candidate_review.gates || {}).length && <details>
        <summary>Issuer assessment findings</summary>
        {Object.entries(row.candidate_review.gates || {}).map(([name, gate]) => <div key={name}>
          <strong>{name.replaceAll('_', ' ')} · {gate.status}</strong>
          <p>{gate.rationale}</p><p style={muted}>Counterevidence: {gate.counterevidence}</p>
        </div>)}
      </details>}
      <ul>{row.candidate_review.thesis.map(t => <li key={t}>{t}</li>)}</ul>
      {row.candidate_review.findings.map(f => <div key={f.claim}>
        <p>{f.claim} {f.source_ids.map(id => {
          const source = row.candidate_review?.sources.find(s => s.id === id);
          return source ? <a key={id} href={source.url} target="_blank" rel="noreferrer" style={{ color: '#a5b4fc' }}>[{id}] </a> : null;
        })}</p><p style={muted}>Counterevidence: {f.counterevidence}</p>
      </div>)}
      {row.candidate_review.sensitivity && <>
        <h4>Owner-cash sensitivity</h4>
        <p style={muted}>{row.candidate_review.sensitivity.method} · fiscal period {row.candidate_review.sensitivity.period_end}. This is a separate stress test; the table’s provider model is unchanged.</p>
        <p>Bear / base / bull: {['bear', 'base', 'bull'].map(k => `$${num(row.candidate_review?.sensitivity?.scenarios[k]?.fair_value, 2)}`).join(' / ')}</p>
        <ul style={muted}>{row.candidate_review.sensitivity.limitations.map(t => <li key={t}>{t}</li>)}</ul>
      </>}
      <h4>What would invalidate the thesis</h4>
      <ul>{row.candidate_review.invalidation.map(t => <li key={t}>{t}</li>)}</ul>
      <h4>Next evidence to monitor</h4>
      <ul>{row.candidate_review.monitor.map(t => <li key={t}>{t}</li>)}</ul>
      <h4>Unresolved research</h4>
      <ul>{row.candidate_review.unresolved.map(t => <li key={t}>{t}</li>)}</ul>
      {row.candidate_review.financial_verification && <details>
        <summary>SEC annual financial verification · {row.candidate_review.financial_verification.state}</summary>
        <p style={muted}>Annual period {row.candidate_review.financial_verification.latest_period_end || 'unavailable'} · checked {when(row.candidate_review.financial_verification.checked_at)}</p>
        <p style={muted}>{row.candidate_review.financial_verification.reason || 'Annual facts retain their filing dates, concepts and units. Interim changes and full dilution still require review.'}</p>
        <ul>{row.candidate_review.reconciliation_flags?.map(f => <li key={f}>{f}</li>)}</ul>
        <dl>{Object.entries(row.candidate_review.financial_verification.latest_annual || {}).map(([name, fact]) => <div key={name}>
          <dt>{name.replaceAll('_', ' ')}: {num(fact.value, 2)} {fact.unit}</dt>
          <dd style={{ ...muted, marginLeft: 0, overflowWrap: 'anywhere' }}>{fact.concept} · period {fact.period_end} · filed {fact.filed}</dd>
        </div>)}</dl>
      </details>}
      {row.candidate_review.filing_verification && <details>
        <summary>SEC filing verification · {row.candidate_review.filing_verification.state}</summary>
        <p style={muted}>Checked {when(row.candidate_review.filing_verification.checked_at)} · since {row.candidate_review.filing_verification.since}</p>
        <p style={muted}>{row.candidate_review.filing_verification.reason || row.candidate_review.filing_verification.scope}</p>
        <p style={muted}>Finding a filing does not establish that its contents have been reviewed.</p>
        <ul>{row.candidate_review.filing_verification.filings?.slice(0, 20).map(f => <li key={f.accession}>
          <a href={f.url} target="_blank" rel="noreferrer" style={{ color: '#a5b4fc' }}>{f.form} · {f.filed}</a>
        </li>)}</ul>
        {(row.candidate_review.filing_verification.filings?.length || 0) > 20 && <p style={muted}>Showing the latest 20; the full inventory is included in CSV.</p>}
      </details>}
      <details><summary>Primary sources</summary><ul>{row.candidate_review.sources.map(s => <li key={s.id}>
        <a href={s.url} target="_blank" rel="noreferrer" style={{ color: '#a5b4fc' }}>{s.title}</a>
        <span style={muted}> · filed {s.published_at} · period {s.period_end}</span>
      </li>)}</ul></details>
    </section>}
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 20 }}>
      <div>
        <h4 style={{ margin: '0 0 8px', color: '#e2e8f0' }}>Investment case to investigate</h4>
        {row.thesis.length ? <ul>{row.thesis.map(t => <li key={t}>{t}</li>)}</ul>
          : <p>No supported investment thesis established by these inputs.</p>}
        <p style={muted}>{row.catalyst_note}</p>
        <h4>What needs checking</h4>
        <ul>{row.research_checks.map(t => <li key={t}>{t}</li>)}</ul>
        <h4>Opportunity eligibility</h4>
        <p style={{ color: row.qualified ? '#6ee7b7' : '#fbbf24' }}>{row.qualified ? 'Qualified by the report policy' : 'Research lead — further evidence required'}</p>
        <ul>{row.blockers.map(t => <li key={t}>{t.replaceAll('_', ' ')}</li>)}</ul>
        {row.quality.gates.map(g => <details key={g.gate} style={{ margin: '6px 0' }}>
          <summary>{g.label || g.gate.replaceAll('_', ' ')} · {g.status.replaceAll('_', ' ')}</summary>
          {g.reasons.map(r => <p key={r} style={muted}>{r}</p>)}
          <p style={muted}>{g.does_not_establish}</p>
        </details>)}
      </div>
      <div>
        <h4 style={{ margin: '0 0 8px' }}>Model fair value · {row.valuation.uncertainty || 'Unrated'} uncertainty</h4>
        {row.valuation.reason && <p style={{ color: '#fbbf24' }}>{row.valuation.reason}</p>}
        {row.valuation.diagnostics?.historical_cash_runway_months != null && <p style={muted}>Historical cash runway: {num(row.valuation.diagnostics.historical_cash_runway_months)} months at the last annual cash-burn rate. Financing needs may differ.</p>}
        {row.valuation.scenarios && <>
          <p style={muted}>{row.valuation.method} · annual period {row.valuation.period_end}</p>
          <table style={{ width: '100%', textAlign: 'left', fontSize: 12 }}>
            <thead><tr><th>Scenario</th><th>Value</th><th>Growth</th><th>Discount rate</th></tr></thead>
            <tbody>{Object.entries(row.valuation.scenarios).map(([name, s]) => <tr key={name}>
              <td>{name}</td><td>${num(s.fair_value, 2)}</td><td>{num(s.growth_pct)}%</td><td>{num(s.discount_rate_pct)}%</td>
            </tr>)}</tbody>
          </table>
          <p style={muted}>{row.valuation.scenarios.base?.multiple != null
            ? `Base FFO multiple: ${num(row.valuation.scenarios.base.multiple)}×. This sensitivity is not a calibrated fair value.`
            : `Base terminal-value share: ${num(row.valuation.scenarios.base?.terminal_value_share_pct)}%. Terminal growth: ${num(row.valuation.scenarios.base?.terminal_growth_pct)}%.`}</p>
          <details><summary>Inputs and limitations</summary>
            <dl>{Object.entries(row.valuation.inputs || {}).map(([key, value]) => <div key={key}>
              <dt style={muted}>{key.replaceAll('_', ' ')}</dt><dd style={{ marginLeft: 0 }}>{num(value, 2)}</dd></div>)}</dl>
            <ul>{row.valuation.limitations?.map(t => <li key={t}>{t}</li>)}</ul>
          </details>
        </>}
        <p>Upside to model value: <Metric metric={row.metrics.upside_pct} />%. Discount: <Metric metric={row.metrics.discount_pct} />%.</p>
        <p style={muted}>A price of $80 against $100 value is a 20% discount and 25% upside.</p>
        <p>Trailing P/E: <Metric metric={row.metrics.trailing_pe} /> · Industry median: {num(row.peer_pe.median)} ({row.peer_pe.count} peers)</p>
        <p>Forward P/E: <Metric metric={row.metrics.forward_pe} /></p>
        <p style={muted}>{row.peer_pe.note}</p>
        {row.eps_note && <p style={muted}>{row.eps_note}</p>}
        <h4>Momentum &amp; reported growth</h4>
        <p>RSI (14): <Metric metric={row.metrics.rsi} /></p>
        {row.metrics.rsi.source && <p style={muted}>{row.metrics.rsi.source} · {row.metrics.rsi.as_of}<br />{row.metrics.rsi.basis}</p>}
        <p>Quarterly EPS growth (YoY, %): <Metric metric={row.metrics.eps_yoy_growth_pct} /></p>
        <p style={muted}>{row.metrics.eps_yoy_growth_pct?.basis || 'Reported quarterly EPS growth unavailable.'}</p>
      </div>
      <div>
        <h4 style={{ margin: '0 0 8px' }}>Analyst view · {row.provider || 'Yahoo'}</h4>
        <p>Consensus: {row.analyst.consensus || 'Unavailable'} · {num(row.analyst.analyst_count, 0)} target contributors</p>
        <p>Mean target: {row.analyst.mean == null ? '—' : `$${num(row.analyst.mean, 2)}`} · Range: {num(row.analyst.low, 2)}–{num(row.analyst.high, 2)}</p>
        <p style={muted}>{row.analyst.note}</p>
        {row.analyst_actions.length > 0 ? <details><summary>Recent dated analyst actions ({row.analyst_actions.length})</summary>
          <ul>{row.analyst_actions.map((a, i) => <li key={`${a.date}-${a.firm}-${i}`}>
            {a.date} · {a.firm}: {a.rating} ({a.action}){a.target != null && ` · target $${num(a.target, 2)}`}
          </li>)}</ul>
        </details> : <p style={muted}>No dated analyst actions available in this snapshot.</p>}
        <h4>Risk context</h4>
        {row.risk_flags.length ? <ul>{row.risk_flags.map((r, i) => <li key={i}>{r.text} {r.date && <small>· {r.date}</small>}</li>)}</ul>
          : <p style={muted}>No flags found in available evidence; this does not establish low risk.</p>}
        {row.uw ? <p>UW short interest context · {row.uw.market_date || 'date unknown'}<br />Days to cover: {num(row.uw.days_to_cover)} · Borrow fee: {num(row.uw.fee_rate)}%<br />
          <span style={muted}>Cached source observation; reporting date and freshness require review.</span></p>
          : <p style={muted}>UW enrichment unavailable in the existing cache.</p>}
        <p style={muted}>Price session: {row.price_session || 'Unavailable'}<br />{row.provider || 'Yahoo'} retrieved: {when(row.fetched_at)}<br />{row.country || 'Domicile unknown'}{row.listing_basis && <><br />{row.listing_basis}</>}</p>
        {!!row.errors.length && <p style={{ color: '#fbbf24' }}>Source gaps: {row.errors.map(e => e.replaceAll('_', ' ')).join('; ')}</p>}
      </div>
    </div>
  </div>;
}

export default function ValuationReportTab({ market }: { market: 'US' | 'HK' }) {
  const enabled = market === 'US';
  const { data: latest, error: latestError, mutate: reloadLatest } = useSWR(enabled ? 'valuation-latest' : null, api.valuationLatest, { revalidateOnFocus: false });
  const { data: history, mutate: reloadHistory } = useSWR(enabled ? 'valuation-history' : null, api.valuationHistory);
  const { data: automation } = useSWR(enabled ? 'valuation-status' : null, api.valuationStatus, { refreshInterval: 60000 });
  const [selected, setSelected] = useState<number | null>(null);
  const { data: archived, error: archiveError } = useSWR(enabled && selected ? ['valuation-report', selected] : null, () => api.valuationReport(selected!));
  const [jobId, setJobId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const { data: job, error: jobError } = useSWR(jobId ? ['valuation-job', jobId] : null, () => api.valuationJob(jobId!),
    { refreshInterval: data => data && !['queued', 'running'].includes(data.state) ? 0 : 3000, shouldRetryOnError: false });
  const [filter, setFilter] = useState<ValuationFilter>('all');
  const [search, setSearch] = useState('');
  const [sector, setSector] = useState('');
  const [sort, setSort] = useState('discount_pct');
  const [ascending, setAscending] = useState(false);
  const [page, setPage] = useState(0);
  const [expanded, setExpanded] = useState<string | null>(null);
  useEffect(() => { setJobId(sessionStorage.getItem('valuation-job')); }, []);
  useEffect(() => {
    if (!job || !['complete', 'failed'].includes(job.state)) return;
    if (job.state === 'complete') {
      setSelected(null); void reloadLatest(); void reloadHistory();
      setMessage(job.created === false ? 'Inputs unchanged; existing report reused.' : 'Report generated from frozen inputs.');
    } else setMessage(job.error || 'Generation failed.');
    sessionStorage.removeItem('valuation-job'); setJobId(null);
  }, [job, reloadLatest, reloadHistory]);
  useEffect(() => {
    if (jobError) { setMessage(jobError.message); setJobId(null); sessionStorage.removeItem('valuation-job'); }
  }, [jobError]);
  const report = selected ? archived : latest?.report;
  const data = report?.valuation;
  const { data: measured, error: performanceError } = useSWR(report ? ['valuation-performance', report.id] : null,
    () => api.valuationPerformance(report!.id), { refreshInterval: d => !d?.evaluation ? 10000
      : d.evaluation.horizons.some(h => Object.values(h.groups).some(g => ['PENDING', 'UNAVAILABLE'].includes(g.state))) ? 60000 : 0,
      revalidateOnFocus: false });
  const evaluation = measured?.evaluation;
  const rows = useMemo(() => selectValuationRows(data?.rows || [], { filter, search, sector, sort, ascending }),
    [data, filter, search, sector, sort, ascending]);
  useEffect(() => { setPage(0); }, [filter, search, sector, sort, ascending, report?.id]);
  const busy = starting || !!jobId;
  async function generate() {
    setStarting(true); setMessage(null);
    try {
      const result = await api.valuationGenerate();
      setJobId(result.job_id); sessionStorage.setItem('valuation-job', result.job_id);
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Generation failed'); }
    finally { setStarting(false); }
  }
  function downloadCsv(csv: string, name: string) {
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a'); link.href = url; link.download = name;
    link.click(); URL.revokeObjectURL(url);
  }
  function exportCsv() {
    if (report) downloadCsv(valuationCsv(report), `us-valuation-${report.id}-v${report.version}.csv`);
  }
  if (!enabled) return <div style={panel}>Valuation &amp; Opportunities currently covers US-listed companies. Select US to view the report.</div>;
  return <div style={{ display: 'grid', gap: 18, minWidth: 0 }}>
    <section style={{ ...panel, background: 'linear-gradient(120deg, #17213b, #111827)' }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', gap: 16 }}>
        <div><p style={{ ...muted, color: '#a5b4fc', margin: '0 0 5px', letterSpacing: 1 }}>US EQUITY RESEARCH</p>
          <h2 style={{ margin: '0 0 8px', fontSize: 24 }}>Valuation &amp; Opportunities</h2>
          <p style={{ ...muted, margin: 0 }}>Find potential value, inspect expensive names, and follow dated analyst risk flags.</p></div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <button onClick={generate} disabled={busy} style={{ ...control, background: '#4f46e5', cursor: busy ? 'wait' : 'pointer' }}>
            {busy ? 'Generating…' : report ? 'Refresh report' : 'Generate first report'}</button>
          <button onClick={exportCsv} disabled={!report} style={control}>Export full CSV</button>
        </div>
      </div>
      <div role="status" aria-live="polite" style={{ ...muted, marginTop: 12 }}>
        {busy ? (job?.stage || 'Starting background refresh…') + (job?.retry_at ? ` Next attempt: ${when(job.retry_at)}.` : '') + ' You can leave this page and return.' : message}
      </div>
      {report && <p style={{ ...muted, marginBottom: 0 }}>Price session {data?.price_session} · Generated {when(report.generated_at)} · v{report.version} · {report.status} coverage</p>}
    </section>
    {(latestError || archiveError) && <div role="alert" style={panel}>Unable to load {selected ? 'selected' : 'latest'} report: {(archiveError || latestError).message}
      <button style={{ ...control, marginLeft: 12 }} onClick={() => { setSelected(null); void reloadLatest(); }}>Retry latest</button></div>}
    {selected && !archived && !archiveError && <div style={panel}>Loading saved snapshot…</div>}
    {!selected && !latest && !latestError && <div style={panel} role="status">Loading valuation reports…</div>}
    {latest && !report && !selected && <section style={panel}>
      <h3>Discover beyond your watchlist</h3><p style={muted}>Generate a report to screen a bounded sample of US companies using Yahoo or available UW fundamentals and conditional valuation scenarios. The first run can take several minutes.</p>
      <p style={muted}>No Morningstar subscription required. Available UW cache data adds risk context.</p>
    </section>}
    {report && data && <>
      <section style={panel}>
        <h3 style={{ marginTop: 0 }}>Frozen cohort outcomes</h3>
        <p style={muted}>Returns begin at the first session close after publication and the information cutoff. Membership stays fixed. Missing or delisted names remain in the coverage denominator.</p>
        {performanceError ? <p style={muted}>Outcome observations could not be loaded.</p> : !evaluation ? <p style={muted}>{measured?.note || 'Loading outcome observations…'}</p> : <>
          <p style={muted}>Published {when(evaluation.published_at)} · evaluated {when(evaluation.evaluated_at)} · benchmark {evaluation.benchmark}</p>
          {measured?.status === 'retrying' && <p style={{ ...muted, color: '#fbbf24' }}>{measured.note}</p>}
          <div style={{ overflowX: 'auto' }}><table style={{ width: '100%', minWidth: 650, textAlign: 'left', fontSize: 12 }}>
            <thead><tr><th>Cohort</th><th>Sessions</th><th>Status</th><th>Observed</th><th>Return</th><th>SPY</th><th>Excess (pp)</th></tr></thead>
            <tbody>{evaluation.horizons.flatMap(h => Object.entries(h.groups).map(([name, group]) => <tr key={`${h.sessions}-${name}`} title={`Entry ${h.entry_session} · end ${h.end_session}`}>
              <td>{name === 'qualified' ? 'Qualified opportunities' : 'Undervalued research'}</td><td>{h.sessions}</td>
              <td>{group.state === 'NO_COHORT' ? 'None in snapshot' : group.state === 'PENDING' ? 'Awaiting sessions' : group.state === 'UNAVAILABLE' ? 'Missing observations' : 'Measured'}</td>
              <td>{group.observed}/{group.expected}</td><td>{num(group.return_pct, 2)}%</td><td>{num(h.benchmark_return_pct, 2)}%</td><td>{num(group.excess_percentage_points, 2)}</td>
            </tr>))}</tbody>
          </table></div>
          <details style={{ margin: '12px 0' }}><summary>Measurement rules</summary><ul style={muted}>{evaluation.notes.map(note => <li key={note}>{note}</li>)}</ul></details>
          <button style={control} onClick={() => downloadCsv(valuationPerformanceCsv(evaluation), `us-valuation-${report.id}-outcomes.csv`)}>Export outcomes CSV</button>
        </>}
      </section>
      {(Date.now() - Date.parse(report.generated_at) > 36 * 3600_000 || !!data.queries_failed.length) && <div role="status" style={{ ...panel, color: '#fbbf24' }}>
        {Date.now() - Date.parse(report.generated_at) > 36 * 3600_000 && 'This is an older snapshot. Refresh to check current inputs. '}
        {!!data.queries_failed.length && `Yahoo screens unavailable: ${data.queries_failed.join(', ')}. ${data.uw_discovery ? 'UW discovery supplied fallback coverage.' : 'Coverage is partial.'}`}
      </div>}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12 }}>
        {([['undervalued', 'Below model value'], ['overvalued', 'Above model value'], ['analyst_risk', 'Analyst risk flags'], ['reviewed', 'Reviewed candidates'], ['qualified', 'Qualified opportunities']] as const).map(([key, label]) => <div key={key} style={panel}>
          <div style={muted}>{label}</div><strong style={{ fontSize: 27 }}>{data.counts[key] || 0}</strong></div>)}
      </div>
      <section style={panel}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14, justifyContent: 'space-between' }}>
          <div><strong>Coverage &amp; methodology</strong><p style={muted}>{data.sampled} sampled / {data.discovered} discovered or covered symbols · {data.counts.insufficient_evidence} insufficient evidence · sample limit {data.sample_limit}<br />{data.universe}</p></div>
          <label style={muted}>Snapshot <select aria-label="Report snapshot" value={selected || ''} style={control} onChange={e => setSelected(e.target.value ? Number(e.target.value) : null)}>
            <option value="">Latest</option>{history?.reports.map(h => <option key={h.id} value={h.id}>v{h.version} · {h.price_session}</option>)}
          </select></label>
        </div>
        {automation && <p style={muted}>Automatic refresh: {automation.automatic_refresh} · {automation.schedule}</p>}
        {data.catalog && <p style={muted}>
          US listing catalog: {num(data.catalog.eligible_count, 0)} candidates · {data.catalog.state.toLowerCase()} · {data.catalog.selected_count || 0} directory selections in this batch.<br />
          {data.catalog.scope || data.catalog.reason} The catalog count does not mean that every company has current ratings.<br />
          {Object.entries(data.catalog.source_dates || {}).map(([name, day]) => `${name}: ${day}`).join(' · ')}
          {data.catalog.rotation_persisted === false && ' · Rotation progress could not be saved.'}
        </p>}
        <details><summary style={{ cursor: 'pointer', fontSize: 13 }}>Read assumptions and source limits</summary>
          <p style={muted}>{data.horizon} · Policy {data.policy_version}</p><ul style={muted}>{data.notes.map(n => <li key={n}>{n}</li>)}</ul>
        </details>
        <details style={{ marginTop: 12 }}><summary style={{ cursor: 'pointer', fontSize: 13 }}>Changes since previous snapshot ({data.changes.length})</summary>
          <p style={muted}>{data.comparison_note}</p>{data.changes.length ? <ul style={muted}>{data.changes.map(c => <li key={c.symbol}>{c.symbol}: {c.kind === 'new_coverage' ? 'new coverage' : `${classificationLabel(c.before || '')} → ${classificationLabel(c.after)}${c.kind === 'methodology_changed' ? ' (methodology changed)' : ''}`}</li>)}</ul> : <p style={muted}>No classification changes recorded.</p>}
        </details>
      </section>
      <section style={panel}>
        <div role="group" aria-label="Valuation categories" style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
          {labels.map(([key, label]) => <button key={key} aria-pressed={filter === key} onClick={() => setFilter(key)} style={{ ...control, borderColor: filter === key ? '#818cf8' : '#334155', cursor: 'pointer' }}>{label}</button>)}
        </div>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
          <input aria-label="Search companies" placeholder="Search symbol or company" value={search} onChange={e => setSearch(e.target.value)} style={{ ...control, minWidth: 230 }} />
          <select aria-label="Filter sector" value={sector} onChange={e => setSector(e.target.value)} style={control}>
            <option value="">All sectors</option>{Array.from(new Set(data.rows.map(r => r.sector))).sort().map(s => <option key={s}>{s}</option>)}
          </select>
          <span style={muted}>{rows.length} matching companies</span>
        </div>
        {rows.length === 0 ? <p style={muted}>{filter === 'qualified'
          ? 'No companies meet all opportunity requirements. Inspect undervalued research leads and their missing evidence.'
          : 'No companies match these filters. Try another category, sector or search.'}</p> : <>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead><tr>{columns.map(([key, title]) => <th key={key} scope="col" aria-sort={sort === key ? ascending ? 'ascending' : 'descending' : 'none'} style={{ textAlign: key === 'symbol' ? 'left' : 'right', padding: '8px 10px', whiteSpace: 'nowrap' }}>
                <button onClick={() => { if (sort === key) setAscending(!ascending); else { setSort(key); setAscending(key === 'symbol'); } }} style={{ background: 'none', border: 0, color: '#94a3b8', cursor: 'pointer' }}>{title} {sort === key ? ascending ? '↑' : '↓' : ''}</button>
              </th>)}<th scope="col" style={{ textAlign: 'left', padding: 10 }}>Assessment</th></tr></thead>
              <tbody>{rows.slice(page * 20, (page + 1) * 20).map(row => <CompanyRows key={row.symbol} row={row} open={expanded === row.symbol} toggle={() => setExpanded(expanded === row.symbol ? null : row.symbol)} />)}</tbody>
            </table>
          </div>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 14 }}>
            <button style={control} disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
            <span style={muted}>Page {page + 1} of {Math.ceil(rows.length / 20)}</span>
            <button style={control} disabled={(page + 1) * 20 >= rows.length} onClick={() => setPage(page + 1)}>Next</button>
          </div>
        </>}
        <p style={{ ...muted, marginBottom: 0 }}>* PEG is provider-reported; its growth horizon is unverified. UW beside RSI identifies a dated provider reading; its price-adjustment basis is unverified. Expand a company for sources and assumptions. RSI describes momentum.</p>
      </section>
    </>}
  </div>;
}

function CompanyRows({ row, open, toggle }: { row: ValuationRow; open: boolean; toggle: () => void }) {
  return <>
    <tr style={{ borderTop: '1px solid #253047' }}>
      <td style={{ padding: 10, minWidth: 180 }}><button aria-expanded={open} aria-controls={`valuation-${row.symbol}`} onClick={toggle}
        style={{ color: '#a5b4fc', background: 'none', border: 0, padding: 0, fontWeight: 700, cursor: 'pointer' }}>{open ? '−' : '+'} {row.symbol}</button>
        <div style={muted}>{row.name}</div><small style={{ color: '#64748b' }}>{row.sector}{!row.tracked && ' · Discovered beyond tracked stocks'}</small></td>
      {columns.slice(1).map(([key]) => <td key={key} style={{ textAlign: 'right', padding: 10, whiteSpace: 'nowrap' }}><Metric metric={row.metrics[key]} money={key === 'price' || key === 'fair_value'} /></td>)}
      <td style={{ padding: 10, minWidth: 155 }}><span style={{ color: row.classification === 'undervalued' ? '#6ee7b7' : row.classification === 'overvalued' ? '#fda4af' : '#94a3b8' }}>{classificationLabel(row.classification)}</span>
        {row.risk_flags.some(f => f.kind === 'analyst') && <div style={{ color: '#fbbf24', fontSize: 11 }}>Analyst risk flag</div>}
        {row.qualified && <div style={{ color: '#6ee7b7', fontSize: 11 }}>Qualified opportunity</div>}</td>
    </tr>
    {open && <tr id={`valuation-${row.symbol}`}><td colSpan={8} style={{ background: '#0c1424', padding: '0 12px' }}><CompanyDetail row={row} /></td></tr>}
  </>;
}
