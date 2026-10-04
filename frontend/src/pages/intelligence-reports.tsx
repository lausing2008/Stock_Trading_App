import { useCallback, useEffect, useMemo, useState } from 'react';
import Head from 'next/head';
import { api, type IntelField, type IntelReport, type IntelDiff,
         type IntelEvents } from '@/lib/api';
import { renderKind, tableColumns, formatScalar } from '@/lib/intelReportView';
import {
  groupFields, criticalLimitations, coverageBanner, fieldLabel, humaniseValue,
  SECTION_TITLE, TIMEFRAME_TITLE, type LayoutField,
} from '@/lib/intelReportLayout';

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

/* Renders a value of ANY shape, including the nested ones.
   The previous version called String() on each entry of an object, so a dict holding a list of
   objects — exactly what sector leadership is — rendered as "[object Object]". Nesting is
   handled by recursion, and a list of uniform objects becomes a real table rather than a pile
   of key: value lines. */
function Scalar({ v, units }: { v: unknown; units?: string | null }) {
  if (v === null || v === undefined) return <span style={{ color: '#64748b' }}>—</span>;
  return <span style={{ fontVariantNumeric: 'tabular-nums' }}>{formatScalar(v, units)}</span>;
}

function ObjectTable({ rows }: { rows: Record<string, unknown>[] }) {
  const cols = tableColumns(rows);
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ borderCollapse: 'collapse', fontSize: '12px', minWidth: '100%' }}>
        <thead>
          <tr>
            {cols.map(c => (
              <th key={c} style={{ textAlign: 'left', padding: '4px 10px 4px 0',
                                   color: '#64748b', fontWeight: 600,
                                   borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
                {titleise(c)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {cols.map(c => (
                <td key={c} style={{ padding: '4px 10px 4px 0', color: '#cbd5e1',
                                     verticalAlign: 'top' }}>
                  <AnyValue v={r[c]} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AnyValue({ v, units }: { v: unknown; units?: string | null }) {
  const kind = renderKind(v);
  if (kind === 'empty') return <span style={{ color: '#64748b' }}>—</span>;
  if (kind === 'table') return <ObjectTable rows={v as Record<string, unknown>[]} />;
  if (kind === 'list') return <span>{(v as unknown[]).map(i => String(i)).join(', ')}</span>;
  if (kind === 'object') {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
        {Object.entries(v as Record<string, unknown>).map(([k, vv]) => (
          <div key={k} style={{ display: 'flex', gap: '6px', alignItems: 'baseline',
                                flexWrap: 'wrap' }}>
            <span style={{ color: '#64748b', flexShrink: 0 }}>{titleise(k)}:</span>
            <AnyValue v={vv} />
          </div>
        ))}
      </div>
    );
  }
  return <Scalar v={v} units={units} />;
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
  return <div style={{ fontSize: '13px', color: '#e2e8f0' }}>
    <AnyValue v={f.value} units={f.units} />
  </div>;
}

function Coverage({ r }: { r: IntelReport }) {
  const by = r.coverage?.by_state ?? {};
  const total = r.coverage?.total ?? 0;
  const ok = r.coverage?.ok ?? 0;
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', alignItems: 'center' }}>
      {/* COMPLETENESS IS ONE AXIS. A single rising number reads as quality improving, so the
          other three are shown beside it and never summed into it. */}
      <span style={{ fontSize: '12px', color: '#94a3b8' }}
            title={r.coverage?.note ?? undefined}>
        {ok} of {total} fields resolved <span style={{ color: '#64748b' }}>(completeness)</span>
      </span>
      {typeof r.coverage?.sourced === 'number' && (
        <span style={{ fontSize: '11px', color: '#64748b' }}>
          · {r.coverage.sourced} cite evidence
        </span>
      )}
      {!!r.coverage?.comparability_unverified && (
        <span style={{ fontSize: '11px', color: '#fcd34d' }}>
          · {r.coverage.comparability_unverified} comparability unverified
        </span>
      )}
      {!!r.coverage?.conflicts && (
        <span style={{ fontSize: '11px', color: '#fda4af' }}>
          · {r.coverage.conflicts} with a recorded source conflict
        </span>
      )}
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
      {diff.note ?? `No field changed between v${diff.from_version} and v${diff.to_version}.`}
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
  const [events, setEvents] = useState<IntelEvents | null>(null);
  const [eventId, setEventId] = useState<number | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const spec = TABS.find(t => t.key === tab)!;

  useEffect(() => {
    setReport(null); setHistory([]); setMarkdown(null); setErr('');
    setEvents(null); setEventId(undefined);
  }, [tab]);

  /* The event list is loaded for the earnings tabs so a specific quarter can be chosen rather
     than silently taking the newest one on file. Built over KNOWN events, with the coverage
     limitation shown beside it. */
  const isEarnings = tab === 'pre_earnings' || tab === 'post_earnings';
  useEffect(() => {
    const sym = symbol.trim().toUpperCase();
    if (!isEarnings || sym.length < 1) { setEvents(null); return; }
    let cancelled = false;
    api.intelEvents(sym)
      .then(e => { if (!cancelled) setEvents(e); })
      .catch(() => { if (!cancelled) setEvents(null); });
    return () => { cancelled = true; };
  }, [symbol, isEarnings]);

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
        event_id: tab === 'post_earnings' ? eventId : undefined,
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

  /* Identity first, then conclusions, then the raw inputs they rest on — and resolved before
     unavailable, so a reader is not walked past five UNAVAILABLE rows to reach the answer.
     Plain alphabetical put the return windows in the order 1, 20, 5, 63 and interleaved
     identity with conclusions. */
  /* Grouping and reading order live in @/lib/intelReportLayout so they are testable without a
     DOM renderer. Sections first (summary, limitations, then detail), and within a section the
     evidence is separated by WHEN it describes — so June's results never sit beside today's
     price in one undifferentiated list. */
  const entries = useMemo(
    () => Object.entries(report?.payload?.fields ?? {}) as [string, IntelField][], [report]);
  const groups = useMemo(
    () => groupFields(entries as unknown as [string, LayoutField][]), [entries]);
  const limitations = useMemo(
    () => criticalLimitations(entries as unknown as [string, LayoutField][]), [entries]);
  const banner = useMemo(() => {
    const ec = report?.payload?.fields?.event_coverage as unknown as LayoutField | undefined;
    const ident = report?.payload?.fields?.event_identity?.value as
      { report_date?: string } | undefined;
    return coverageBanner(ec, ident?.report_date);
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
          {isEarnings && events && events.events.length > 0 && (
            <select value={eventId ?? ''} onChange={e => setEventId(
                       e.target.value ? Number(e.target.value) : undefined)}
              disabled={tab === 'pre_earnings'}
              title={tab === 'pre_earnings'
                ? 'A pre-earnings report always describes the next scheduled event'
                : events.coverage_note}
              style={{ padding: '9px 12px', fontSize: '13px', color: '#f1f5f9',
                       background: 'rgba(255,255,255,0.04)', borderRadius: '8px',
                       border: '1px solid rgba(148,163,184,0.15)',
                       opacity: tab === 'pre_earnings' ? 0.5 : 1 }}>
              <option value="">
                {tab === 'post_earnings' ? 'Newest released event' : 'Next scheduled event'}
              </option>
              {events.events.filter(e => tab === 'post_earnings' ? e.released : !e.released)
                .map(e => (
                  <option key={e.event_id} value={e.event_id}>
                    {e.report_date}{e.has_actuals ? '' : ' (no figures on file)'}
                  </option>
                ))}
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

        {isEarnings && events && (
          <div style={{ fontSize: '11px', color: '#64748b', marginBottom: '14px',
                        maxWidth: '760px', lineHeight: 1.5 }}>
            {events.events.length} event{events.events.length === 1 ? '' : 's'} on file.
            {' '}{events.coverage_note}
          </div>
        )}

        {report && report.superseded_by && report.superseded_by.length > 0 && (
          <div style={{ padding: '12px 15px', borderRadius: '10px', marginBottom: '12px',
                        background: 'rgba(99,102,241,0.10)',
                        border: '1px solid rgba(99,102,241,0.35)', color: '#c7d2fe',
                        fontSize: '13px' }}>
            A later version of this report exists. This snapshot is preserved exactly as issued.
            {' '}
            {report.superseded_by.map(v => (
              <button key={v.report_id} onClick={() => openVersion(v.report_id)} style={{
                marginLeft: '6px', padding: '3px 9px', borderRadius: '6px', cursor: 'pointer',
                fontSize: '12px', background: 'rgba(99,102,241,0.18)', color: '#a5b4fc',
                border: '1px solid rgba(99,102,241,0.4)' }}>
                Open v{v.version}
              </button>
            ))}
          </div>
        )}

        {/* A CROSS-SUBJECT correction. Louder than the version banner on purpose: a later
            VERSION refines the same event, whereas this says the report in front of you is
            about the wrong event entirely. */}
        {report && report.corrected_by && (() => {
          const coverage = report.corrected_by.kind === 'coverage';
          const tone = coverage
            ? { bg: 'rgba(251,191,36,0.10)', bd: 'rgba(251,191,36,0.40)', fg: '#fde68a',
                btnBg: 'rgba(251,191,36,0.18)', btnFg: '#fcd34d' }
            : { bg: 'rgba(244,63,94,0.10)', bd: 'rgba(244,63,94,0.40)', fg: '#fecdd3',
                btnBg: 'rgba(244,63,94,0.18)', btnFg: '#fda4af' };
          return (
          <div style={{ padding: '13px 16px', borderRadius: '10px', marginBottom: '12px',
                        background: tone.bg, border: `1px solid ${tone.bd}`, color: tone.fg,
                        fontSize: '13px', lineHeight: 1.55 }}>
            <strong>{report.corrected_by.note
              ?? 'A later report supersedes this one in scope.'}</strong>{' '}
            {report.corrected_by.correction?.reason}
            <button onClick={() => openVersion(report.corrected_by!.report_id)} style={{
              marginLeft: '8px', padding: '3px 9px', borderRadius: '6px', cursor: 'pointer',
              fontSize: '12px', background: tone.btnBg, color: tone.btnFg,
              border: `1px solid ${tone.bd}` }}>
              {coverage ? 'Open the later report' : 'Open the corrected report'} #{report.corrected_by.report_id}
            </button>
            {report.corrected_by.correction?.actor && (
              <div style={{ marginTop: '6px', fontSize: '11px', color: tone.btnFg }}>
                Recorded by {report.corrected_by.correction.actor}
                {report.corrected_by.correction.recorded_at
                  ? ` on ${report.corrected_by.correction.recorded_at.slice(0, 10)}` : ''}
              </div>
            )}
          </div>
          );
        })()}

        {report && report.contract_is_current === false && report.contract_note && (
          <div style={{ padding: '12px 15px', borderRadius: '10px', marginBottom: '12px',
                        background: 'rgba(100,116,139,0.12)',
                        border: '1px solid rgba(148,163,184,0.3)', color: '#cbd5e1',
                        fontSize: '12px', lineHeight: 1.5 }}>
            {report.contract_note}
          </div>
        )}

        {report && banner && (
          <div style={{
            padding: '13px 16px', borderRadius: '10px', marginBottom: '14px',
            background: banner.tone === 'error' ? 'rgba(239,68,68,0.10)' : 'rgba(234,179,8,0.10)',
            border: `1px solid ${banner.tone === 'error' ? 'rgba(239,68,68,0.4)' : 'rgba(234,179,8,0.4)'}`,
            color: banner.tone === 'error' ? '#fca5a5' : '#fde047',
            fontSize: '13px', fontWeight: 600,
          }}>{banner.text}</div>
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
                {/* "Version 2" and "contract v2" are different numbers that happened to
                    coincide, and printing both as "v2" made them indistinguishable — a reader
                    comparing a screenshot against the database cannot tell which is which. */}
                <span style={{ fontSize: '12px', color: '#64748b' }}>Version {report.version}
                  {report.supersedes_id ? ` · supersedes report #${report.supersedes_id}` : ''}</span>
                {report.created === false && (
                  <span style={{ fontSize: '11px', color: '#818cf8' }}>
                    inputs unchanged — existing snapshot reused
                  </span>
                )}
              </div>
              {/* FOUR DIFFERENT TIMES, NOT ONE. A reused snapshot shows the time it was
                  GENERATED, which a reader reads as "when I asked" — over a weekend those are
                  days apart, and the page looked stale when it was simply unchanged. */}
              <div style={{ fontSize: '12px', color: '#64748b', display: 'flex',
                            flexWrap: 'wrap', gap: '4px 18px', marginBottom: '6px' }}>
                <span>Report generated {report.generated_at}</span>
                {report.latest_input_session && (
                  <span>Latest underlying session {report.latest_input_session}</span>
                )}
                {report.checked_at && <span>Last checked {report.checked_at}</span>}
                <span>Information available through {report.cutoff_at}</span>
                <span>Report contract v{report.contract_version} · generation policy {report.policy_version}</span>
              </div>
              {report.reuse_note && (
                <div style={{ fontSize: '11px', color: '#64748b', marginBottom: '10px',
                              maxWidth: '820px', lineHeight: 1.5 }}>
                  Result: {report.reuse_note}.{report.freshness_note ? ` ${report.freshness_note}` : ''}
                </div>
              )}
              <Coverage r={report} />
            </div>

            {limitations.length > 0 && (
              <div style={{ padding: '14px 16px', borderRadius: '11px',
                            background: 'rgba(234,179,8,0.06)',
                            border: '1px solid rgba(234,179,8,0.22)' }}>
                <div style={{ fontSize: '11px', fontWeight: 700, color: '#fde047',
                              textTransform: 'uppercase', letterSpacing: '0.07em',
                              marginBottom: '9px' }}>
                  Analysis limitations — {limitations.length} input{limitations.length === 1 ? '' : 's'} that
                  {' '}constrain what this report can conclude
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '7px' }}>
                  {limitations.map(([k, lf]) => (
                    <div key={k} style={{ fontSize: '12px', color: '#cbd5e1' }}>
                      <strong>{fieldLabel(k, lf)}</strong>
                      <span style={{ color: '#94a3b8' }}> — {lf.reason}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Collapsed by default: a 13-row change list dominated the first screen and
                pushed the report itself below the fold. */}
            {report.changes_since_previous && (
              <details style={{ padding: '12px 16px', borderRadius: '11px',
                                background: 'rgba(255,255,255,0.025)',
                                border: '1px solid rgba(255,255,255,0.07)' }}>
                <summary style={{ fontSize: '11px', fontWeight: 700, color: '#64748b',
                                  textTransform: 'uppercase', letterSpacing: '0.07em',
                                  cursor: 'pointer' }}>
                  What changed since the previous report
                  {report.changes_since_previous.changed?.length
                    ? ` — ${report.changes_since_previous.changed.length} change(s)`
                    : report.changes_since_previous.first_report ? ' — first report' : ' — none'}
                </summary>
                <div style={{ marginTop: '10px' }}>
                  <Changes diff={report.changes_since_previous} />
                </div>
              </details>
            )}

            {/* THE SECTION HEADING PRINTS ONCE PER SECTION, not once per timeframe group.
                A section with four timeframes repeated "ANALYSIS LIMITATIONS" four times down
                the page, which reads as four separate sections of the same name. */}
            {groups.map((g, i) => (
              <div key={`${g.section}|${g.timeframe}`} style={{ display: 'flex',
                   flexDirection: 'column', gap: '6px' }}>
                {(i === 0 || groups[i - 1].section !== g.section) && (
                  <div style={{ fontSize: '11px', fontWeight: 700, color: '#64748b',
                                textTransform: 'uppercase', letterSpacing: '0.07em' }}>
                    {SECTION_TITLE[g.section]}
                  </div>
                )}
                {TIMEFRAME_TITLE[g.timeframe] && (
                  <div style={{ fontSize: '12px', color: g.timeframe === 'current'
                                  ? '#fbbf24' : '#94a3b8', marginBottom: '2px' }}>
                    {TIMEFRAME_TITLE[g.timeframe]}
                  </div>
                )}
                <div style={{ borderRadius: '11px', overflowX: 'auto',
                              border: '1px solid rgba(255,255,255,0.07)' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse',
                                  tableLayout: 'fixed' }}>
                    <tbody>
                      {g.keys.map(key => {
                        const f = report.payload!.fields[key];
                        const humanised = humaniseValue(key, f.value);
                        return (
                          <tr key={key} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                            {/* Proportional, not a fixed 230px: at a 390px viewport a fixed
                                label column plus the value column cannot fit, and the page
                                scrolled sideways. */}
                            <td style={{ padding: '11px 14px', width: '34%', minWidth: '110px',
                                         verticalAlign: 'top', fontSize: '13px',
                                         color: '#cbd5e1', fontWeight: 600,
                                         overflowWrap: 'anywhere' }}>
                              {fieldLabel(key, f as unknown as LayoutField)}
                              {f.state === 'OK' && (
                                <div style={{ fontSize: '10px', color: '#475569',
                                              fontWeight: 400, marginTop: '2px' }}>
                                  {STATEMENT_LABEL[f.statement] ?? f.statement}
                                </div>
                              )}
                            </td>
                            <td style={{ padding: '11px 14px', verticalAlign: 'top',
                                         overflowWrap: 'anywhere' }}>
                              {humanised
                                ? <span style={{ fontSize: '13px', color: '#e2e8f0' }}>{humanised}</span>
                                : <FieldValue f={f} />}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            ))}

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
