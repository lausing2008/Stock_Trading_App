/* Quality & Value — the SHADOW dashboard.
 *
 * WHY THIS PAGE LEADS WITH COVERAGE AND NOT A CANDIDATE LIST. Today every symbol evaluates to
 * `insufficient_evidence`, because the two gates that decide eligibility — competitive
 * durability and valuation — have no stored evidence at all (measured:
 * docs/audits/2026-10-06-quality-value-readiness-inventory.md). A table of 189 identical rows
 * would tell a reader nothing. The per-gate coverage underneath says exactly which inputs are
 * missing for how many companies, which is the decision this stage exists to inform.
 *
 * An empty eligible list is a valid result and is labelled one, not an error state.
 */
import { useMemo, useState } from 'react';
import Head from 'next/head';
import useSWR from 'swr';
import { api, type QualityValueReport, type QvEvaluation, type QvGateStatus } from '@/lib/api';
import { coverageRows, statusColumns, badgeLabel, badgeRemedy,
         type StatusCatalog } from '@/lib/qualityValueCoverage';

const GATE_TITLE: Record<string, string> = {
  business_quality: 'Business quality',
  competitive_durability: 'Competitive durability',
  valuation: 'Valuation',
  entry_condition: 'Entry condition',
  value_trap_risk: 'Value-trap risk',
  catalysts: 'Catalysts',
  portfolio_fit: 'Portfolio fit',
};

const STATE_TITLE: Record<string, string> = {
  entry_review_ready: 'Entry review',
  value_candidate: 'Value candidate',
  quality_watch: 'Quality watch',
  valuation_review: 'Valuation review',
  thesis_at_risk: 'Thesis at risk',
  entry_expired: 'Entry expired',
  insufficient_evidence: 'Insufficient evidence',
};

/* COLOUR ONLY. Labels and remedies come from the server's `status_catalog`, which is derived
   from the backend enum itself — the first version of this page kept its own five-entry map and
   defaulted everything else to "No evidence", so when the backend grew to nine states all four
   new ones silently rendered as the single thing they were added to stop saying. A palette can
   safely fall back to a neutral colour; a LABEL cannot. */
const STATUS_TONE: Record<string, { bg: string; bd: string; fg: string }> = {
  pass:            { bg: 'rgba(52,211,153,0.12)', bd: 'rgba(52,211,153,0.4)',  fg: '#6ee7b7' },
  fail:            { bg: 'rgba(248,113,113,0.1)', bd: 'rgba(248,113,113,0.35)', fg: '#fca5a5' },
  blocked:         { bg: 'rgba(244,63,94,0.14)',  bd: 'rgba(244,63,94,0.45)',  fg: '#fda4af' },
  not_applicable:  { bg: 'rgba(148,163,184,0.1)', bd: 'rgba(148,163,184,0.3)', fg: '#94a3b8' },
  not_assessed:    { bg: 'rgba(100,116,139,0.12)', bd: 'rgba(100,116,139,0.32)', fg: '#cbd5e1' },
  not_implemented: { bg: 'rgba(192,132,252,0.12)', bd: 'rgba(192,132,252,0.38)', fg: '#d8b4fe' },
  not_collected:   { bg: 'rgba(56,189,248,0.1)',  bd: 'rgba(56,189,248,0.32)', fg: '#7dd3fc' },
  stale:           { bg: 'rgba(251,146,60,0.12)', bd: 'rgba(251,146,60,0.38)', fg: '#fdba74' },
  conflicting:     { bg: 'rgba(232,121,249,0.12)', bd: 'rgba(232,121,249,0.38)', fg: '#f0abfc' },
  insufficient:    { bg: 'rgba(234,179,8,0.1)',   bd: 'rgba(234,179,8,0.32)',  fg: '#fde047' },
};
const UNKNOWN_TONE = { bg: 'rgba(239,68,68,0.18)', bd: '#ef4444', fg: '#fecaca' };

type Catalog = StatusCatalog;

function Pill({ s, catalog }: { s: string; catalog?: Catalog }) {
  const tone = STATUS_TONE[s] ?? UNKNOWN_TONE;
  // A STATE THIS BUILD DOES NOT KNOW IS AN ERROR, NOT A DEFAULT. Falling back to a neutral
  // label is how four distinct states all read as "No evidence" for a day.
  const label = badgeLabel(s, catalog);
  const entry = catalog?.[s];
  return <span title={entry?.remedy || undefined}
    style={{ padding: '2px 7px', borderRadius: '5px', background: tone.bg,
             border: `1px solid ${tone.bd}`, color: tone.fg, fontSize: '10px',
             fontWeight: 700, letterSpacing: '0.04em', whiteSpace: 'nowrap' }}>
    {label}</span>;
}

function Remedy({ s, catalog }: { s: string; catalog?: Catalog }) {
  const r = badgeRemedy(s, catalog);
  if (!r) return null;
  return <div style={{ fontSize: '11.5px', color: '#7dd3fc', marginTop: '3px' }}>
    What would close it: {r}</div>;
}

function Row({ e, catalog }: { e: QvEvaluation; catalog?: Catalog }) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ borderBottom: '1px solid rgba(255,255,255,0.06)', padding: '11px 0' }}>
      <div style={{ display: 'flex', gap: '10px', alignItems: 'baseline', flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 700, color: '#f1f5f9', fontSize: '13.5px',
                       minWidth: '72px' }}>{e.symbol}</span>
        <span style={{ color: '#94a3b8', fontSize: '12px', flex: '1 1 160px',
                       overflowWrap: 'anywhere' }}>{e.name ?? '—'}</span>
        <div style={{ display: 'flex', gap: '5px', flexWrap: 'wrap' }}>
          {e.gates.map(g => (
            <span key={g.gate} title={`${GATE_TITLE[g.gate] ?? g.gate}: ${g.status}`}>
              <Pill s={g.status} catalog={catalog} /></span>
          ))}
        </div>
        <button onClick={() => setOpen(o => !o)} style={{
          padding: '2px 9px', borderRadius: '6px', cursor: 'pointer', fontSize: '11px',
          background: 'rgba(99,102,241,0.14)', color: '#a5b4fc',
          border: '1px solid rgba(99,102,241,0.3)' }}>
          {open ? 'Hide' : 'Why not eligible'}
        </button>
      </div>
      {open && (
        <div style={{ marginTop: '9px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {/* PASSING GATES ARE SHOWN TOO. A reader who only sees the failures takes the
              passes as endorsements, which is exactly what the labels exist to prevent. */}
          {e.gates.filter(g => g.status === 'pass').map(g => (
            <div key={g.gate} style={{ borderLeft: '2px solid rgba(52,211,153,0.35)',
                                       paddingLeft: '10px' }}>
              <div style={{ fontSize: '12px', color: '#e2e8f0', fontWeight: 600 }}>
                {g.label ?? GATE_TITLE[g.gate] ?? g.gate} <Pill s={g.status} catalog={catalog} /></div>
              {g.establishes && (
                <div style={{ fontSize: '12px', color: '#cbd5e1', lineHeight: 1.5,
                              marginTop: '3px' }}>Establishes: {g.establishes}</div>
              )}
              {g.does_not_establish && (
                <div style={{ fontSize: '11.5px', color: '#fcd34d', lineHeight: 1.5,
                              marginTop: '4px' }}>
                  Does not establish: {g.does_not_establish}
                </div>
              )}
            </div>
          ))}
          {e.gates.filter(g => g.status !== 'pass').map(g => (
            <div key={g.gate} style={{ borderLeft: '2px solid rgba(234,179,8,0.35)',
                                       paddingLeft: '10px' }}>
              <div style={{ fontSize: '12px', color: '#e2e8f0', fontWeight: 600 }}>
                {g.label ?? GATE_TITLE[g.gate] ?? g.gate} <Pill s={g.status} catalog={catalog} /></div>
              {g.reasons.map((r, i) => (
                <div key={i} style={{ fontSize: '12px', color: '#cbd5e1', lineHeight: 1.5,
                                      marginTop: '3px' }}>{r}</div>
              ))}
              {g.does_not_establish && (
                <div style={{ fontSize: '11.5px', color: '#fcd34d', lineHeight: 1.5,
                              marginTop: '4px' }}>
                  Does not establish: {g.does_not_establish}
                </div>
              )}
              <Remedy s={g.status} catalog={catalog} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function QualityValuePage() {
  const [symbols, setSymbols] = useState('');
  const [query, setQuery] = useState('');
  const { data, error, isLoading } = useSWR<QualityValueReport>(
    ['quality-value', query], () => api.qualityValue(query || undefined),
    { revalidateOnFocus: false });
  const err = error ? (error instanceof Error ? error.message : String(error)) : null;

  // COLUMNS FROM THE DATA, not a hardcoded list: every state any gate actually reports, in the
  // server's catalog order, plus any state the catalog does not know (which must still appear,
  // loudly, rather than vanish from the sums).
  const statusCols = useMemo(() => (data ? statusColumns(data) : []), [data]);
  const rows = useMemo(() => (data ? coverageRows(data) : []), [data]);

  const H = ({ children }: { children: React.ReactNode }) => (
    <div style={{ fontSize: '11px', fontWeight: 700, color: '#64748b', letterSpacing: '0.07em',
                  textTransform: 'uppercase', margin: '22px 0 8px' }}>{children}</div>);

  return (
    <>
      <Head><title>Quality &amp; Value · StockAI</title></Head>
      <div style={{ padding: '20px', maxWidth: '1180px', margin: '0 auto' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 700, color: '#f1f5f9', margin: '0 0 4px' }}>
          Quality &amp; Value Opportunities
        </h1>
        <p style={{ fontSize: '13px', color: '#64748b', margin: '0 0 6px', maxWidth: '820px',
                    lineHeight: 1.55 }}>
          Shadow evaluation. Every verdict IS stored, immutably and under a fingerprint of the
          rules that produced it, so the screen can later be measured — but no alert type is
          registered and no email can be sent from here. A research state is not an order
          recommendation, and “entry review” names a point at which to research further — not a
          point at which to buy.
        </p>
        <p style={{ fontSize: '13px', color: '#fde047', margin: '0 0 18px', maxWidth: '820px',
                    lineHeight: 1.55 }}>
          There is no combined score. Every required gate must pass on its own evidence; a strong
          reading on one cannot make up for an absent one on another.
        </p>

        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '14px' }}>
          <input value={symbols} onChange={e => setSymbols(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') setQuery(symbols.trim()); }}
            placeholder="Symbols, comma separated — blank for the active universe"
            style={{ padding: '9px 12px', fontSize: '13px', color: '#f1f5f9', flex: '1 1 320px',
                     background: 'rgba(255,255,255,0.04)', borderRadius: '8px',
                     border: '1px solid rgba(148,163,184,0.15)', outline: 'none' }} />
          <button onClick={() => setQuery(symbols.trim())} style={{
            padding: '9px 18px', borderRadius: '8px', border: 'none', cursor: 'pointer',
            fontSize: '13px', fontWeight: 700, color: '#fff',
            background: 'linear-gradient(135deg, #4f46e5, #6366f1)' }}>Evaluate</button>
        </div>

        {err && (
          <div style={{ padding: '13px 15px', borderRadius: '10px', fontSize: '13px',
                        background: 'rgba(248,113,113,0.08)', color: '#fca5a5',
                        border: '1px solid rgba(248,113,113,0.3)' }}>
            Could not evaluate: {err}
          </div>
        )}
        {!err && isLoading && (
          <div style={{ fontSize: '13px', color: '#64748b' }}>Evaluating…</div>)}

        {data && (
          <>
            <H>Evidence coverage — which gates the stored data can actually decide</H>
            <div style={{ borderRadius: '11px', border: '1px solid rgba(255,255,255,0.07)',
                          overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12.5px',
                              minWidth: '520px' }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: 'left', padding: '9px 12px', color: '#64748b',
                                 fontWeight: 600, whiteSpace: 'nowrap',
                                 borderBottom: '1px solid rgba(255,255,255,0.08)' }}>Gate</th>
                    {statusCols.map(c => (
                      <th key={c} style={{ textAlign: 'right', padding: '9px 12px',
                                           color: '#64748b', fontWeight: 600,
                                           whiteSpace: 'nowrap',
                                           borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
                        {data.status_catalog?.[c]?.label ?? c}</th>
                    ))}
                    {['Total', 'Required'].map(c => (
                      <th key={c} style={{ textAlign: 'right', padding: '9px 12px',
                                           color: '#64748b', fontWeight: 600,
                                           whiteSpace: 'nowrap',
                                           borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
                        {c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map(r => (
                    <tr key={r.gate} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                      <td style={{ padding: '9px 12px', color: '#e2e8f0', fontWeight: 600 }}>
                        {r.label !== r.gate ? r.label
                          : (GATE_TITLE[r.gate] ?? r.gate)}</td>
                      {statusCols.map(s => (
                        <td key={s} style={{ padding: '9px 12px', textAlign: 'right',
                                             color: r.counts[s] ? (STATUS_TONE[s]?.fg ?? '#fecaca')
                                                                : '#475569',
                                             fontVariantNumeric: 'tabular-nums' }}>
                          {r.counts[s] ?? 0}</td>
                      ))}
                      <td title={r.reconciles ? undefined
                                 : `does not reconcile: ${r.total} of ${data.evaluated}`}
                          style={{ padding: '9px 12px', textAlign: 'right',
                                   fontVariantNumeric: 'tabular-nums',
                                   color: r.reconciles ? '#94a3b8' : '#fecaca',
                                   fontWeight: r.reconciles ? 400 : 700 }}>
                        {r.total}{r.reconciles ? '' : ` ≠ ${data.evaluated}`}</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right', color: '#94a3b8' }}>
                        {r.required ? 'yes' : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <H>States across {data.evaluated} evaluated {data.evaluated === 1 ? 'company' : 'companies'}</H>
            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
              {Object.entries(data.states).filter(([, n]) => n > 0).map(([s, n]) => (
                <div key={s} style={{ padding: '8px 13px', borderRadius: '9px',
                                      background: 'rgba(255,255,255,0.035)',
                                      border: '1px solid rgba(255,255,255,0.07)' }}>
                  <div style={{ fontSize: '17px', fontWeight: 700, color: '#f1f5f9',
                                fontVariantNumeric: 'tabular-nums' }}>{n}</div>
                  <div style={{ fontSize: '11px', color: '#94a3b8' }}>
                    {STATE_TITLE[s] ?? s}</div>
                </div>
              ))}
            </div>

            {(data.states.entry_review_ready ?? 0) === 0 && (
              <div style={{ marginTop: '12px', padding: '13px 15px', borderRadius: '10px',
                            fontSize: '12.5px', color: '#cbd5e1', lineHeight: 1.6,
                            background: 'rgba(234,179,8,0.06)',
                            border: '1px solid rgba(234,179,8,0.22)' }}>
                <strong style={{ color: '#fde047' }}>No entry-review candidates yet. Durability,
                valuation and risk assessments are not connected to this evaluator.</strong>{' '}
                All three are required, and none of them reads a stored assessment today — so
                the empty list reflects unfinished work here, not a judgement about any company.
                The coverage table above says which gate is in which state.
              </div>
            )}

            <H>Companies</H>
            <div>{data.evaluations.map(e => <Row key={e.symbol} e={e} catalog={data.status_catalog} />)}</div>

            <div style={{ marginTop: '22px', display: 'flex', flexDirection: 'column',
                          gap: '6px' }}>
              {data.notes.map((n, i) => (
                <div key={i} style={{ fontSize: '11.5px', color: '#64748b',
                                      lineHeight: 1.5 }}>{n}</div>
              ))}
              <div style={{ fontSize: '11.5px', color: '#475569' }}>
                Cutoff {data.cutoff ? new Date(data.cutoff).toLocaleString() : '—'} · mode{' '}
                {data.mode} · policy {data.policy_version} ({data.policy_fingerprint})
                {typeof data.stored_new === 'number' && <> · {data.stored_new} new evaluation(s)
                  stored</>}
              </div>
              {!!data.persist_errors?.length && (
                <div style={{ fontSize: '11.5px', color: '#fca5a5' }}>
                  {data.persist_errors.length} evaluation(s) could not be stored — this run is
                  not a complete record: {data.persist_errors.slice(0, 5).join('; ')}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </>
  );
}
