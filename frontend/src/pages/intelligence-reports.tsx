import { useCallback, useEffect, useMemo, useState } from 'react';
import Head from 'next/head';
import { api, type IntelField, type IntelReport, type IntelDiff } from '@/lib/api';

type Tab = 'market_outlook' | 'stock_outlook' | 'pre_earnings' | 'post_earnings';

const TABS: { key: Tab; label: string; needsSymbol: boolean; blurb: string }[] = [
  { key: 'market_outlook', label: 'Market Outlook', needsSymbol: false,
    blurb: 'Observed market condition and conditional direction by horizon.' },
  { key: 'stock_outlook', label: 'Stock Outlook', needsSymbol: true,
    blurb: 'Company context, structure and conditional outlook by horizon.' },
  { key: 'pre_earnings', label: 'Pre-Earnings', needsSymbol: true,
    blurb: 'The frozen baseline: what is expected, before the release.' },
  { key: 'post_earnings', label: 'Post-Earnings', needsSymbol: true,
    blurb: 'Actual versus the frozen baseline, and the observed reaction.' },
];

/* A field state is the point of this product, so it is colour-coded rather than rendered as an
   empty cell. UNAVAILABLE and UNKNOWN are deliberately NOT red: a dimension this platform
   cannot source is not an error, and showing it as one teaches the reader to ignore it. */
const STATE_STYLE: Record<string, { bg: string; bd: string; fg: string }> = {
  OK:             { bg: 'transparent',            bd: 'transparent',            fg: '#e2e8f0' },
  UNKNOWN:        { bg: 'rgba(148,163,184,0.10)', bd: 'rgba(148,163,184,0.30)', fg: '#cbd5e1' },
  UNAVAILABLE:    { bg: 'rgba(100,116,139,0.10)', bd: 'rgba(100,116,139,0.30)', fg: '#94a3b8' },
  STALE:          { bg: 'rgba(234,179,8,0.10)',   bd: 'rgba(234,179,8,0.35)',   fg: '#fde047' },
  CONFLICTING:    { bg: 'rgba(239,68,68,0.10)',   bd: 'rgba(239,68,68,0.35)',   fg: '#fca5a5' },
  NOT_APPLICABLE: { bg: 'rgba(100,116,139,0.08)', bd: 'rgba(100,116,139,0.25)', fg: '#94a3b8' },
};

const STATEMENT_LABEL: Record<string, string> = {
  observed_fact: 'observed',
  deterministic_calculation: 'calculated',
  interpretation: 'interpretation',
  conditional_scenario: 'conditional',
  model_forecast: 'model output',
};

function titleise(key: string) {
  return key.replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase());
}

function FieldValue({ f }: { f: IntelField }) {
  if (f.state !== 'OK') {
    const st = STATE_STYLE[f.state] ?? STATE_STYLE.UNKNOWN;
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
        <span style={{
          alignSelf: 'flex-start', padding: '2px 7px', borderRadius: '5px',
          background: st.bg, border: `1px solid ${st.bd}`, color: st.fg,
          fontSize: '10px', fontWeight: 700, letterSpacing: '0.05em',
        }}>{f.state}</span>
        <span style={{ fontSize: '12px', color: '#94a3b8', lineHeight: 1.45 }}>{f.reason}</span>
      </div>
    );
  }
  const v = f.value;
  if (v === null || v === undefined) return <span style={{ color: '#64748b' }}>—</span>;
  if (Array.isArray(v)) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        {v.map((item, i) => (
          <div key={i} style={{
            padding: '7px 9px', borderRadius: '7px', background: 'rgba(255,255,255,0.03)',
            border: '1px solid rgba(255,255,255,0.06)', fontSize: '12px', color: '#cbd5e1',
          }}>
            {typeof item === 'object' && item !== null
              ? Object.entries(item as Record<string, unknown>).map(([k, vv]) => (
                  <div key={k}><span style={{ color: '#64748b' }}>{titleise(k)}: </span>{String(vv)}</div>))
              : String(item)}
          </div>
        ))}
      </div>
    );
  }
  if (typeof v === 'object') {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', fontSize: '12px' }}>
        {Object.entries(v as Record<string, unknown>).map(([k, vv]) => (
          <div key={k}>
            <span style={{ color: '#64748b' }}>{titleise(k)}: </span>
            <span style={{ color: '#cbd5e1' }}>{vv === null ? '—' : String(vv)}</span>
          </div>
        ))}
      </div>
    );
  }
  return <span style={{ color: '#e2e8f0', fontSize: '13px' }}>{String(v)}{f.units ? ` ${f.units}` : ''}</span>;
}

function Coverage({ r }: { r: IntelReport }) {
  const by = r.coverage?.by_state ?? {};
  const total = r.coverage?.total ?? 0;
  const ok = r.coverage?.ok ?? 0;
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', alignItems: 'center' }}>
      <span style={{ fontSize: '12px', color: '#94a3b8' }}>
        {ok} of {total} fields resolved
      </span>
      {Object.entries(by).filter(([k]) => k !== 'OK').map(([k, n]) => {
        const st = STATE_STYLE[k] ?? STATE_STYLE.UNKNOWN;
        return (
          <span key={k} style={{
            padding: '2px 7px', borderRadius: '5px', background: st.bg,
            border: `1px solid ${st.bd}`, color: st.fg, fontSize: '10px', fontWeight: 700,
          }}>{n} {k}</span>
        );
      })}
    </div>
  );
}

function Changes({ diff }: { diff: IntelDiff }) {
  if (diff.first_report) {
    return <div style={{ fontSize: '12px', color: '#64748b' }}>
      First report for this subject — nothing to compare against yet.
    </div>;
  }
  if (!diff.changed.length) {
    return <div style={{ fontSize: '12px', color: '#64748b' }}>
      No field changed between v{diff.from_version} and v{diff.to_version}.
    </div>;
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
      <div style={{ fontSize: '12px', color: '#94a3b8' }}>
        {diff.changed.length} change{diff.changed.length === 1 ? '' : 's'} from
        v{diff.from_version} to v{diff.to_version}
      </div>
      {diff.changed.slice(0, 40).map((c, i) => (
        <div key={i} style={{
          fontSize: '12px', padding: '6px 9px', borderRadius: '6px',
          background: 'rgba(99,102,241,0.07)', border: '1px solid rgba(99,102,241,0.18)',
          color: '#c7d2fe',
        }}>
          <strong>{titleise(c.field)}</strong> — {c.change}
          {c.change === 'state' && <> : {String(c.from)} → {String(c.to)}</>}
        </div>
      ))}
    </div>
  );
}

export default function IntelligenceReportsPage() {
  const [tab, setTab] = useState<Tab>('market_outlook');
  const [symbol, setSymbol] = useState('');
  const [market, setMarket] = useState('US');
  const [report, setReport] = useState<IntelReport | null>(null);
  const [history, setHistory] = useState<IntelReport[]>([]);
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const spec = TABS.find(t => t.key === tab)!;

  useEffect(() => { setReport(null); setHistory([]); setMarkdown(null); setErr(''); }, [tab]);

  const loadHistory = useCallback(async (subjectKey: string) => {
    try { setHistory((await api.intelHistory(subjectKey)).reports); }
    catch { /* history is supplementary; a failure here must not hide the report */ }
  }, []);

  async function generate() {
    setBusy(true); setErr(''); setMarkdown(null);
    try {
      const r = await api.generateIntelReport({
        report_type: tab,
        symbol: spec.needsSymbol ? symbol.trim().toUpperCase() : undefined,
        market: tab === 'market_outlook' ? market : undefined,
      });
      setReport(r);
      await loadHistory(r.subject_key);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setErr(msg.includes('404')
        ? `Nothing to report on: ${msg}`
        : msg.includes('401') ? 'Session expired — please log in again.'
        : `Could not generate the report. ${msg}`);
    } finally { setBusy(false); }
  }

  async function openVersion(id: number) {
    setBusy(true); setMarkdown(null);
    try { setReport(await api.getIntelReport(id)); }
    catch (e: unknown) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  async function showMarkdown() {
    if (!report) return;
    try { setMarkdown((await api.getIntelMarkdown(report.id)).markdown); }
    catch (e: unknown) { setErr(e instanceof Error ? e.message : String(e)); }
  }

  const fields = useMemo(() => {
    const f = report?.payload?.fields ?? {};
    // Resolved fields first, then everything the report could not source — a reader scanning
    // for the answer should not have to walk past five UNAVAILABLE rows to reach it.
    return Object.entries(f).sort(([ak, av], [bk, bv]) => {
      if ((av.state === 'OK') !== (bv.state === 'OK')) return av.state === 'OK' ? -1 : 1;
      return ak.localeCompare(bk);
    });
  }, [report]);

  return (
    <>
      <Head><title>Intelligence Reports · StockAI</title></Head>
      <div style={{ padding: '20px', maxWidth: '1180px', margin: '0 auto' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 700, color: '#f1f5f9', margin: '0 0 4px' }}>
          Intelligence Reports
        </h1>
        <p style={{ fontSize: '13px', color: '#64748b', margin: '0 0 18px', maxWidth: '760px' }}>
          Generated, versioned snapshots. Every field says what it is — observed, calculated,
          interpretation, conditional or model output — and an empty field says why it is empty.
          Read-only research: a constructive report is not an order authorisation.
        </p>

        <div style={{ display: 'flex', gap: '2px', borderBottom: '1px solid rgba(255,255,255,0.08)',
                      marginBottom: '16px', flexWrap: 'wrap' }}>
          {TABS.map(t => (
            <button key={t.key} onClick={() => setTab(t.key)} style={{
              padding: '9px 14px', border: 'none', background: 'transparent', cursor: 'pointer',
              fontSize: '13px', fontWeight: tab === t.key ? 700 : 500,
              color: tab === t.key ? '#f9fafb' : '#6b7280',
              borderBottom: tab === t.key ? '2px solid #6d28d9' : '2px solid transparent',
            }}>{t.label}</button>
          ))}
        </div>

        <div style={{ fontSize: '12px', color: '#64748b', marginBottom: '12px' }}>{spec.blurb}</div>

        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center',
                      marginBottom: '18px' }}>
          {spec.needsSymbol ? (
            <input value={symbol} onChange={e => setSymbol(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && symbol.trim()) generate(); }}
              placeholder="Ticker, e.g. AAPL"
              style={{ padding: '9px 12px', fontSize: '13px', color: '#f1f5f9', width: '190px',
                       background: 'rgba(255,255,255,0.04)', borderRadius: '8px',
                       border: '1px solid rgba(148,163,184,0.15)', outline: 'none' }} />
          ) : (
            <select value={market} onChange={e => setMarket(e.target.value)}
              style={{ padding: '9px 12px', fontSize: '13px', color: '#f1f5f9',
                       background: 'rgba(255,255,255,0.04)', borderRadius: '8px',
                       border: '1px solid rgba(148,163,184,0.15)' }}>
              <option value="US">US</option>
              <option value="HK">HK</option>
            </select>
          )}
          <button onClick={generate} disabled={busy || (spec.needsSymbol && !symbol.trim())}
            style={{ padding: '9px 18px', borderRadius: '8px', border: 'none',
                     cursor: busy || (spec.needsSymbol && !symbol.trim()) ? 'not-allowed' : 'pointer',
                     fontSize: '13px', fontWeight: 700, color: '#fff',
                     opacity: busy || (spec.needsSymbol && !symbol.trim()) ? 0.5 : 1,
                     background: 'linear-gradient(135deg, #4f46e5, #6366f1)' }}>
            {busy ? 'Generating…' : 'Generate report'}
          </button>
          {report && (
            <button onClick={showMarkdown} style={{
              padding: '9px 14px', borderRadius: '8px', fontSize: '13px', cursor: 'pointer',
              background: 'rgba(255,255,255,0.04)', color: '#94a3b8',
              border: '1px solid rgba(148,163,184,0.15)' }}>Markdown</button>
          )}
        </div>

        {err && (
          <div style={{ padding: '11px 14px', borderRadius: '9px', marginBottom: '16px',
                        background: 'rgba(239,68,68,0.07)', border: '1px solid rgba(239,68,68,0.2)',
                        color: '#fca5a5', fontSize: '13px' }}>{err}</div>
        )}

        {report && (
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr)', gap: '16px' }}>
            <div style={{ padding: '14px 16px', borderRadius: '11px',
                          background: 'rgba(255,255,255,0.025)',
                          border: '1px solid rgba(255,255,255,0.07)' }}>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px 18px',
                            alignItems: 'center', marginBottom: '10px' }}>
                <strong style={{ color: '#f1f5f9', fontSize: '14px' }}>{report.subject_key}</strong>
                <span style={{ fontSize: '11px', padding: '2px 8px', borderRadius: '5px',
                               background: report.status === 'complete' ? 'rgba(34,197,94,0.12)'
                                         : 'rgba(234,179,8,0.12)',
                               color: report.status === 'complete' ? '#86efac' : '#fde047',
                               fontWeight: 700 }}>{report.status}</span>
                <span style={{ fontSize: '12px', color: '#64748b' }}>v{report.version}
                  {report.supersedes_id ? ` · supersedes #${report.supersedes_id}` : ''}</span>
                {report.created === false && (
                  <span style={{ fontSize: '11px', color: '#818cf8' }}>
                    inputs unchanged — existing report reused
                  </span>
                )}
              </div>
              <div style={{ fontSize: '12px', color: '#64748b', display: 'flex',
                            flexWrap: 'wrap', gap: '4px 18px', marginBottom: '10px' }}>
                <span>Generated {report.generated_at}</span>
                <span>Information available through {report.cutoff_at}</span>
                <span>Contract v{report.contract_version} · policy {report.policy_version}</span>
              </div>
              <Coverage r={report} />
            </div>

            {report.changes_since_previous && (
              <div style={{ padding: '14px 16px', borderRadius: '11px',
                            background: 'rgba(255,255,255,0.025)',
                            border: '1px solid rgba(255,255,255,0.07)' }}>
                <div style={{ fontSize: '11px', fontWeight: 700, color: '#64748b',
                              textTransform: 'uppercase', letterSpacing: '0.07em',
                              marginBottom: '9px' }}>What changed since the previous report</div>
                <Changes diff={report.changes_since_previous} />
              </div>
            )}

            <div style={{ borderRadius: '11px', overflow: 'hidden',
                          border: '1px solid rgba(255,255,255,0.07)' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <tbody>
                  {fields.map(([key, f]) => (
                    <tr key={key} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                      <td style={{ padding: '11px 14px', width: '230px', verticalAlign: 'top',
                                   fontSize: '13px', color: '#cbd5e1', fontWeight: 600 }}>
                        {titleise(key)}
                        {/* Only a field that HAS a value makes a claim. Showing the default
                            class beside UNAVAILABLE would label an absence an observed fact. */}
                        {f.state === 'OK' && (
                          <div style={{ fontSize: '10px', color: '#475569', fontWeight: 400,
                                        marginTop: '2px' }}>
                            {STATEMENT_LABEL[f.statement] ?? f.statement}
                          </div>
                        )}
                      </td>
                      <td style={{ padding: '11px 14px', verticalAlign: 'top' }}>
                        <FieldValue f={f} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {history.length > 1 && (
              <div style={{ padding: '14px 16px', borderRadius: '11px',
                            background: 'rgba(255,255,255,0.025)',
                            border: '1px solid rgba(255,255,255,0.07)' }}>
                <div style={{ fontSize: '11px', fontWeight: 700, color: '#64748b',
                              textTransform: 'uppercase', letterSpacing: '0.07em',
                              marginBottom: '9px' }}>Report history</div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                  {history.map(h => (
                    <button key={h.id} onClick={() => openVersion(h.id)} style={{
                      padding: '6px 11px', borderRadius: '7px', cursor: 'pointer',
                      fontSize: '12px',
                      background: h.id === report.id ? 'rgba(99,102,241,0.14)' : 'rgba(255,255,255,0.03)',
                      border: `1px solid ${h.id === report.id ? 'rgba(99,102,241,0.4)' : 'rgba(255,255,255,0.08)'}`,
                      color: h.id === report.id ? '#a5b4fc' : '#94a3b8',
                    }}>v{h.version} · {h.generated_at?.slice(0, 16).replace('T', ' ')}</button>
                  ))}
                </div>
              </div>
            )}

            {markdown && (
              <div style={{ padding: '14px 16px', borderRadius: '11px',
                            background: '#0b1020', border: '1px solid rgba(255,255,255,0.07)' }}>
                <div style={{ fontSize: '11px', fontWeight: 700, color: '#64748b',
                              textTransform: 'uppercase', letterSpacing: '0.07em',
                              marginBottom: '9px' }}>Markdown export</div>
                <pre style={{ margin: 0, fontSize: '11px', color: '#94a3b8', overflowX: 'auto',
                              whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>{markdown}</pre>
              </div>
            )}
          </div>
        )}

        {!report && !busy && !err && (
          <div style={{ padding: '40px 20px', textAlign: 'center', color: '#475569',
                        fontSize: '13px' }}>
            No report yet. {spec.needsSymbol ? 'Enter a ticker and generate one.' : 'Generate one to begin.'}
          </div>
        )}
      </div>
    </>
  );
}
