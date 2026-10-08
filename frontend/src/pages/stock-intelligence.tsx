/* Stock Intelligence — the research view for one issuer.
 *
 * WHY THIS SHIPS WITH NO RETURNS ON IT. MU's outcomes are currently
 * UNRESOLVED_ADJUSTMENT_UNVERIFIED: the platform cannot establish a corporate-action
 * adjustment basis for those windows, so there is no defensible return to show. A page that
 * waited for returns would withhold the evidence summary, the buckets, the frozen inputs and
 * the pending horizons — all of which are the research product and none of which depend on a
 * resolved figure. So the outcome column says precisely what is missing and who can close it,
 * and everything else renders.
 *
 * THREE RULES THIS PAGE DOES NOT BEND:
 *   1. UNKNOWN is not NEUTRAL. A bucket with nothing measured is a gap in OUR work, and is
 *      drawn as one — never beside a measured reading as though it were a finding.
 *   2. Support quality is not predictive confidence. How well evidenced a direction is says
 *      nothing about whether it is right, and the page says so where it shows either.
 *   3. Superseded outcomes are AUDIT RECORDS. They are reachable, visibly struck through, and
 *      never counted in any summary.
 *
 * Every label for a resolution state comes from the server's `reason_labels`. A copy kept here
 * is how four backend states once all rendered as the single thing they were added to stop
 * saying.
 */
import React, { useState } from 'react';
import Head from 'next/head';
import { useRouter } from 'next/router';
import useSWR from 'swr';
import { api, type StockOutcomes, type StockIntelligence, type IntelClaim,
         type OutcomeObservation } from '@/lib/api';

const PCT = (v: number | null | undefined) =>
  v === null || v === undefined ? '—' : `${(v * 100).toFixed(2)}%`;

const DIRECTION_TONE: Record<string, { fg: string; bg: string; bd: string }> = {
  STRONG_BULLISH: { fg: '#6ee7b7', bg: 'rgba(52,211,153,0.14)', bd: 'rgba(52,211,153,0.45)' },
  BULLISH:        { fg: '#6ee7b7', bg: 'rgba(52,211,153,0.10)', bd: 'rgba(52,211,153,0.32)' },
  NEUTRAL:        { fg: '#cbd5e1', bg: 'rgba(148,163,184,0.10)', bd: 'rgba(148,163,184,0.3)' },
  BEARISH:        { fg: '#fca5a5', bg: 'rgba(248,113,113,0.10)', bd: 'rgba(248,113,113,0.32)' },
  STRONG_BEARISH: { fg: '#fca5a5', bg: 'rgba(248,113,113,0.14)', bd: 'rgba(248,113,113,0.45)' },
};
/* UNKNOWN gets its OWN strong tone rather than the neutral grey: the whole point is that it
   must not read as "we looked and found nothing notable". */
const UNKNOWN_TONE = { fg: '#fde047', bg: 'rgba(234,179,8,0.12)', bd: 'rgba(234,179,8,0.45)' };
const tone = (d: string) => DIRECTION_TONE[d] ?? UNKNOWN_TONE;

/* A state is RESOLVED, PENDING (time will fix it) or BLOCKED (evidence must be collected).
   The distinction is what a reader needs: one of the three is nobody's action item. */
const stateKind = (s: string | undefined): 'resolved' | 'pending' | 'blocked' | 'none' => {
  if (!s) return 'none';
  if (s.startsWith('RESOLVED')) return 'resolved';
  if (s === 'UNRESOLVED_INSUFFICIENT_SESSIONS') return 'pending';
  return 'blocked';
};
const KIND_TONE = {
  resolved: { fg: '#6ee7b7', bg: 'rgba(52,211,153,0.10)', bd: 'rgba(52,211,153,0.35)' },
  pending:  { fg: '#7dd3fc', bg: 'rgba(56,189,248,0.10)', bd: 'rgba(56,189,248,0.32)' },
  blocked:  { fg: '#fdba74', bg: 'rgba(251,146,60,0.12)', bd: 'rgba(251,146,60,0.4)' },
  none:     { fg: '#94a3b8', bg: 'rgba(148,163,184,0.08)', bd: 'rgba(148,163,184,0.25)' },
};

const card: React.CSSProperties = {
  background: 'rgba(30,41,59,0.55)', border: '1px solid rgba(148,163,184,0.18)',
  borderRadius: 12, padding: '16px 18px',
};
const label: React.CSSProperties = {
  fontSize: 11, letterSpacing: '0.09em', textTransform: 'uppercase', color: '#94a3b8',
};

function Pill({ text, t }: { text: string; t: { fg: string; bg: string; bd: string } }) {
  return (
    <span style={{ background: t.bg, border: `1px solid ${t.bd}`, color: t.fg, borderRadius: 999,
                   padding: '2px 10px', fontSize: 12, fontWeight: 600, whiteSpace: 'nowrap' }}>
      {text}
    </span>
  );
}

function ClaimList({ items, empty }: { items?: (IntelClaim | string)[]; empty: string }) {
  if (!items || items.length === 0) return <p style={{ color: '#64748b', fontSize: 13 }}>{empty}</p>;
  return (
    <ul style={{ margin: 0, paddingLeft: 18, display: 'grid', gap: 6 }}>
      {items.map((c, i) => {
        const o = typeof c === 'string' ? { claim: c } as IntelClaim : c;
        return (
          <li key={i} style={{ fontSize: 13, color: '#e2e8f0', lineHeight: 1.5 }}>
            {o.claim}
            {(o.source || o.as_of) && (
              <span style={{ color: '#64748b', fontSize: 12 }}>
                {' — '}{o.source}{o.source_ref ? ` (${o.source_ref})` : ''}
                {o.as_of ? `, as of ${o.as_of}` : ''}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function OutcomeCell({ row, labels }: { row: OutcomeObservation; labels: Record<string, string> }) {
  const state = row.invalidated_reason ? 'INVALID_CAPTURE' : (row.outcome?.state ?? 'NOT_RESOLVED');
  const kind = row.invalidated_reason ? 'blocked' : stateKind(row.outcome?.state);
  return (
    <div style={{ display: 'grid', gap: 6 }}>
      <Pill text={labels[state] ?? state} t={KIND_TONE[kind]} />
      {row.invalidated_reason && (
        <p style={{ margin: 0, fontSize: 12, color: '#fdba74', lineHeight: 1.45 }}>
          {row.invalidated_reason}
        </p>
      )}
      {!row.invalidated_reason && row.outcome?.reason && (
        <p style={{ margin: 0, fontSize: 12, color: '#94a3b8', lineHeight: 1.45 }}>
          {row.outcome.reason}
        </p>
      )}
    </div>
  );
}

export default function StockIntelligencePage() {
  const router = useRouter();
  const qs = typeof router.query.symbol === 'string' ? router.query.symbol : '';
  const [symbol, setSymbol] = useState('MU');
  const active = (qs || symbol).toUpperCase();
  const [showAudit, setShowAudit] = useState(false);

  const { data: outcomes, error: outErr, isLoading: outLoading } =
    useSWR<StockOutcomes>(active ? ['stock-outcomes', active] : null,
                          () => api.stockOutcomes(active));
  const { data: intel, error: intelErr, isLoading: intelLoading } =
    useSWR<StockIntelligence>(active ? ['stock-intel', active] : null,
                              () => api.stockIntelligence(active));

  const labels = outcomes?.reason_labels ?? {};
  const summary = intel?.observations?.[1]?.summary ?? intel?.observations?.[0]?.summary;
  const measured = (intel?.buckets ?? []).filter(b => b.direction !== 'UNKNOWN');
  const gaps = (intel?.buckets ?? []).filter(b => b.direction === 'UNKNOWN');

  return (
    <>
      <Head><title>Stock Intelligence</title></Head>
      <div style={{ maxWidth: 1180, margin: '0 auto', padding: '24px 16px 72px' }}>
        <h1 style={{ margin: '0 0 4px', fontSize: 26, color: '#f1f5f9' }}>Stock Intelligence</h1>
        <p style={{ margin: '0 0 20px', color: '#94a3b8', fontSize: 14, maxWidth: 820 }}>
          What the platform has measured for one issuer, what it has not, and what happened to
          each recorded observation. A direction here is a reading of stored evidence — it is
          not a forecast, and no part of this page establishes predictive skill.
        </p>

        <form onSubmit={e => { e.preventDefault();
                               router.push(`/stock-intelligence?symbol=${symbol.toUpperCase()}`); }}
              style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
          <input value={symbol} onChange={e => setSymbol(e.target.value)} placeholder="Symbol"
                 style={{ background: 'rgba(15,23,42,0.8)', border: '1px solid rgba(148,163,184,0.3)',
                          borderRadius: 8, padding: '8px 12px', color: '#e2e8f0', width: 160 }} />
          <button type="submit"
                  style={{ background: 'rgba(56,189,248,0.15)', border: '1px solid rgba(56,189,248,0.4)',
                           borderRadius: 8, padding: '8px 16px', color: '#7dd3fc', cursor: 'pointer' }}>
            Load
          </button>
        </form>

        {(outErr || intelErr) && (
          <div style={{ ...card, borderColor: 'rgba(248,113,113,0.4)', marginBottom: 20 }}>
            <p style={{ margin: 0, color: '#fca5a5', fontSize: 14 }}>
              Could not load intelligence for {active}. This is a failed request, not an empty
              result — the two are different and must not be shown as the same thing.
            </p>
          </div>
        )}

        {(outLoading || intelLoading) && !outcomes && !intel && (
          <p style={{ color: '#94a3b8' }}>Loading…</p>
        )}

        {/* ── SUMMARY ────────────────────────────────────────────────────────────────── */}
        {summary && (
          <section style={{ ...card, marginBottom: 18 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
                          marginBottom: 12 }}>
              <Pill text={summary.direction} t={tone(summary.direction)} />
              <span style={{ color: '#94a3b8', fontSize: 13 }}>{summary.direction_basis}</span>
              {summary.support_quality && (
                <span style={{ ...label }}>support {summary.support_quality}</span>
              )}
            </div>
            <p style={{ margin: '0 0 14px', fontSize: 12, color: '#fdba74', lineHeight: 1.5 }}>
              {summary.predictive_confidence_note
               ?? 'Support quality describes how well evidenced this reading is, not whether it is right.'}
            </p>
            <div style={{ display: 'grid', gap: 16,
                          gridTemplateColumns: 'repeat(auto-fit,minmax(260px,1fr))' }}>
              <div>
                <p style={{ ...label, margin: '0 0 6px' }}>What supports it</p>
                <ClaimList items={summary.three_factors} empty="Nothing measured supports a direction." />
              </div>
              <div>
                <p style={{ ...label, margin: '0 0 6px' }}>What argues against it</p>
                <ClaimList items={summary.counterevidence}
                           empty="No counterevidence from a measured bucket." />
              </div>
              <div>
                <p style={{ ...label, margin: '0 0 6px' }}>Open questions</p>
                <ClaimList items={summary.open_questions as IntelClaim[]}
                           empty="None recorded. An open question is a research limitation, not counterevidence." />
              </div>
            </div>
          </section>
        )}

        {/* ── OUTCOMES ───────────────────────────────────────────────────────────────── */}
        <section style={{ ...card, marginBottom: 18 }}>
          <h2 style={{ margin: '0 0 4px', fontSize: 17, color: '#f1f5f9' }}>
            Recorded observations and their outcomes
          </h2>
          <p style={{ margin: '0 0 14px', color: '#94a3b8', fontSize: 13, maxWidth: 820 }}>
            {outcomes?.note ?? 'Replay and prospective origins are never pooled.'}
          </p>
          {outcomes && Object.keys(outcomes.state_counts).length > 0 && (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>
              {Object.entries(outcomes.state_counts).map(([s, n]) => (
                <Pill key={s} text={`${labels[s] ?? s}: ${n}`}
                      t={KIND_TONE[s === 'INVALID_CAPTURE' ? 'blocked' : stateKind(s)]} />
              ))}
            </div>
          )}
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, minWidth: 820 }}>
              <thead>
                <tr style={{ textAlign: 'left', color: '#94a3b8' }}>
                  {['Origin', 'Cutoff', 'Horizon', 'Direction', 'Reference', 'Outcome',
                    'Descriptive', 'Excess', 'Basis'].map(h => (
                    <th key={h} style={{ padding: '8px 10px', borderBottom: '1px solid rgba(148,163,184,0.2)',
                                         fontWeight: 600, fontSize: 11, letterSpacing: '0.07em',
                                         textTransform: 'uppercase' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(outcomes?.observations ?? []).map(r => (
                  <tr key={r.observation_id}
                      style={{ borderBottom: '1px solid rgba(148,163,184,0.1)',
                               opacity: r.invalidated_reason ? 0.6 : 1 }}>
                    <td style={{ padding: '10px' }}>
                      <span style={{ ...label, fontSize: 11 }}>{r.origin}</span>
                    </td>
                    <td style={{ padding: '10px', color: '#cbd5e1' }}>{r.observed_at.slice(0, 10)}</td>
                    <td style={{ padding: '10px', color: '#cbd5e1' }}>
                      {r.horizon}<span style={{ color: '#64748b' }}> · {r.horizon_sessions}s</span>
                    </td>
                    <td style={{ padding: '10px' }}>
                      <Pill text={r.direction} t={tone(r.direction)} />
                    </td>
                    <td style={{ padding: '10px', color: '#cbd5e1', fontVariantNumeric: 'tabular-nums' }}>
                      {r.reference_price?.toFixed(2) ?? '—'}
                      <span style={{ color: '#64748b', fontSize: 11, display: 'block' }}>
                        {r.reference_price_as_of?.slice(0, 10) ?? ''}
                      </span>
                    </td>
                    <td style={{ padding: '10px', minWidth: 230 }}>
                      <OutcomeCell row={r} labels={labels} />
                    </td>
                    <td style={{ padding: '10px', fontVariantNumeric: 'tabular-nums', color: '#e2e8f0' }}>
                      {PCT(r.outcome?.descriptive_return)}
                    </td>
                    <td style={{ padding: '10px', fontVariantNumeric: 'tabular-nums', color: '#e2e8f0' }}>
                      {PCT(r.outcome?.excess_return)}
                    </td>
                    <td style={{ padding: '10px', color: '#94a3b8', fontSize: 12 }}>
                      {r.outcome?.return_basis ?? '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {(outcomes?.observations ?? []).some(r => r.superseded.length > 0) && (
            <div style={{ marginTop: 14 }}>
              <button onClick={() => setShowAudit(v => !v)}
                      style={{ background: 'none', border: '1px solid rgba(148,163,184,0.3)',
                               borderRadius: 8, padding: '6px 12px', color: '#94a3b8',
                               fontSize: 12, cursor: 'pointer' }}>
                {showAudit ? 'Hide' : 'Show'} superseded audit records
              </button>
              {showAudit && (
                <div style={{ marginTop: 12 }}>
                  <p style={{ margin: '0 0 8px', fontSize: 12, color: '#fdba74', maxWidth: 820 }}>
                    These are earlier versions of the same outcomes, retained unchanged after a
                    resolver correction. They are audit records only and are excluded from every
                    count and summary above — they are not alternative results.
                  </p>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                    <thead><tr style={{ textAlign: 'left', color: '#64748b' }}>
                      {['Observation', 'Outcome', 'Resolver', 'Superseded by', 'State',
                        'Descriptive', 'Excess'].map(h => (
                        <th key={h} style={{ padding: '6px 10px' }}>{h}</th>))}
                    </tr></thead>
                    <tbody>
                      {(outcomes?.observations ?? []).flatMap(r => r.superseded.map(sv => (
                        <tr key={sv.id} style={{ color: '#64748b',
                                                 textDecoration: 'line-through' }}>
                          <td style={{ padding: '6px 10px' }}>#{r.observation_id}</td>
                          <td style={{ padding: '6px 10px' }}>#{sv.id}</td>
                          <td style={{ padding: '6px 10px', fontFamily: 'monospace' }}>
                            {(sv.resolver ?? '').slice(0, 12)}
                          </td>
                          <td style={{ padding: '6px 10px' }}>
                            {sv.superseded_by ? `#${sv.superseded_by}` : '—'}
                          </td>
                          <td style={{ padding: '6px 10px' }}>{sv.state}</td>
                          <td style={{ padding: '6px 10px', fontVariantNumeric: 'tabular-nums' }}>
                            {PCT(sv.descriptive_return)}
                          </td>
                          <td style={{ padding: '6px 10px', fontVariantNumeric: 'tabular-nums' }}>
                            {PCT(sv.excess_return)}
                          </td>
                        </tr>
                      )))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </section>

        {/* ── EVIDENCE BUCKETS ───────────────────────────────────────────────────────── */}
        {intel && (
          <section style={card}>
            <h2 style={{ margin: '0 0 4px', fontSize: 17, color: '#f1f5f9' }}>Evidence buckets</h2>
            <p style={{ margin: '0 0 14px', color: '#94a3b8', fontSize: 13, maxWidth: 820 }}>
              {measured.length} of {intel.buckets.length} buckets carry a measured reading. The
              rest are gaps in this platform&rsquo;s coverage — not findings that the market is
              neutral.
            </p>
            <div style={{ display: 'grid', gap: 12,
                          gridTemplateColumns: 'repeat(auto-fit,minmax(300px,1fr))' }}>
              {measured.map(b => (
                <div key={b.bucket} style={{ border: `1px solid ${tone(b.direction).bd}`,
                                             borderRadius: 10, padding: 12,
                                             background: tone(b.direction).bg }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8,
                                marginBottom: 8 }}>
                    <strong style={{ color: '#f1f5f9', fontSize: 14 }}>{b.bucket}</strong>
                    <Pill text={b.direction} t={tone(b.direction)} />
                  </div>
                  <ClaimList items={b.evidence} empty="No claims recorded." />
                  {b.limitations && b.limitations.length > 0 && (
                    <div style={{ marginTop: 8 }}>
                      <p style={{ ...label, margin: '0 0 4px' }}>Limitations</p>
                      <ClaimList items={b.limitations} empty="" />
                    </div>
                  )}
                </div>
              ))}
            </div>
            {gaps.length > 0 && (
              <div style={{ marginTop: 16 }}>
                <p style={{ ...label, margin: '0 0 8px' }}>
                  Not measured — {gaps.length} bucket{gaps.length === 1 ? '' : 's'}
                </p>
                <div style={{ display: 'grid', gap: 6 }}>
                  {gaps.map(b => (
                    <div key={b.bucket} style={{ display: 'flex', gap: 10, alignItems: 'baseline',
                                                 flexWrap: 'wrap' }}>
                      <Pill text={b.bucket} t={UNKNOWN_TONE} />
                      <span style={{ color: '#94a3b8', fontSize: 12 }}>
                        {b.evidence?.[0]?.claim ?? b.status}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </section>
        )}
      </div>
    </>
  );
}
