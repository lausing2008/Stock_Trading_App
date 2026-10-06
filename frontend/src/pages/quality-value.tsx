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
import { useState } from 'react';
import Head from 'next/head';
import useSWR from 'swr';
import { api, type QualityValueReport, type QvEvaluation, type QvGateStatus } from '@/lib/api';

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

const STATUS_STYLE: Record<QvGateStatus, { bg: string; bd: string; fg: string; label: string }> = {
  pass:           { bg: 'rgba(52,211,153,0.12)', bd: 'rgba(52,211,153,0.4)',  fg: '#6ee7b7', label: 'Pass' },
  fail:           { bg: 'rgba(248,113,113,0.1)', bd: 'rgba(248,113,113,0.35)', fg: '#fca5a5', label: 'Fail' },
  unknown:        { bg: 'rgba(234,179,8,0.1)',   bd: 'rgba(234,179,8,0.32)',  fg: '#fde047', label: 'No evidence' },
  blocked:        { bg: 'rgba(244,63,94,0.14)',  bd: 'rgba(244,63,94,0.45)',  fg: '#fda4af', label: 'Blocked' },
  not_applicable: { bg: 'rgba(148,163,184,0.1)', bd: 'rgba(148,163,184,0.3)', fg: '#94a3b8', label: 'N/A' },
};

function Pill({ s }: { s: QvGateStatus }) {
  const st = STATUS_STYLE[s] ?? STATUS_STYLE.unknown;
  return <span style={{ padding: '2px 7px', borderRadius: '5px', background: st.bg,
                        border: `1px solid ${st.bd}`, color: st.fg, fontSize: '10px',
                        fontWeight: 700, letterSpacing: '0.04em', whiteSpace: 'nowrap' }}>
    {st.label}</span>;
}

function Row({ e }: { e: QvEvaluation }) {
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
              <Pill s={g.status} /></span>
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
          {e.gates.filter(g => g.status !== 'pass').map(g => (
            <div key={g.gate} style={{ borderLeft: '2px solid rgba(234,179,8,0.35)',
                                       paddingLeft: '10px' }}>
              <div style={{ fontSize: '12px', color: '#e2e8f0', fontWeight: 600 }}>
                {GATE_TITLE[g.gate] ?? g.gate} <Pill s={g.status} /></div>
              {g.reasons.map((r, i) => (
                <div key={i} style={{ fontSize: '12px', color: '#cbd5e1', lineHeight: 1.5,
                                      marginTop: '3px' }}>{r}</div>
              ))}
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
          Shadow evaluation. Nothing on this page is stored, no alert type is registered and no
          email can be sent from it. A research state is not an order recommendation, and
          “entry review” names a point at which to research further — not a point at which to buy.
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
                    {['Gate', 'Pass', 'Fail', 'No evidence', 'Blocked', 'Required'].map(c => (
                      <th key={c} style={{ textAlign: c === 'Gate' ? 'left' : 'right',
                                           padding: '9px 12px', color: '#64748b',
                                           fontWeight: 600, whiteSpace: 'nowrap',
                                           borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
                        {c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(data.gate_coverage).map(([gate, c]) => (
                    <tr key={gate} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                      <td style={{ padding: '9px 12px', color: '#e2e8f0', fontWeight: 600 }}>
                        {GATE_TITLE[gate] ?? gate}</td>
                      {(['pass', 'fail', 'unknown', 'blocked'] as QvGateStatus[]).map(s => (
                        <td key={s} style={{ padding: '9px 12px', textAlign: 'right',
                                             color: c[s] ? STATUS_STYLE[s].fg : '#475569',
                                             fontVariantNumeric: 'tabular-nums' }}>{c[s] ?? 0}</td>
                      ))}
                      <td style={{ padding: '9px 12px', textAlign: 'right', color: '#94a3b8' }}>
                        {data.required_for_entry.includes(gate) ? 'yes' : '—'}</td>
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
                <strong style={{ color: '#fde047' }}>No company is eligible, and that is a
                result rather than a fault.</strong> Competitive durability and valuation are both
                required, and neither has stored evidence for any company in the universe. The
                coverage table above is what would have to change for this list to be non-empty.
              </div>
            )}

            <H>Companies</H>
            <div>{data.evaluations.map(e => <Row key={e.symbol} e={e} />)}</div>

            <div style={{ marginTop: '22px', display: 'flex', flexDirection: 'column',
                          gap: '6px' }}>
              {data.notes.map((n, i) => (
                <div key={i} style={{ fontSize: '11.5px', color: '#64748b',
                                      lineHeight: 1.5 }}>{n}</div>
              ))}
              <div style={{ fontSize: '11.5px', color: '#475569' }}>
                Evaluated at {new Date(data.as_of).toLocaleString()} · mode: {data.mode}
              </div>
            </div>
          </>
        )}
      </div>
    </>
  );
}
