/* The research conclusion for one issuer, and what happened to it.
 *
 * ONE IMPLEMENTATION, TWO PLACES. This lives in Quality & Value, keyed to the symbol being
 * evaluated there, and is reached directly at /stock-intelligence for a single symbol. There is
 * no second copy of the rendering or of the API calls.
 *
 * CONCLUSIONS FIRST, and the order is the argument:
 *   1. direction and horizon  — what this says
 *   2. main factors           — why
 *   3. strongest counterevidence — what argues against it, from MEASURED buckets only
 *   4. triggers               — what would confirm or invalidate it
 *   5. outcome status         — what actually happened, or why that cannot be scored
 * Everything else — the thirteen buckets, the adjustment evidence, the superseded results —
 * is below and collapsed. The working is available, not in the way.
 *
 * Three rules this component does not bend:
 *   * UNKNOWN is not NEUTRAL. A bucket with nothing measured is a gap in OUR work and is drawn
 *     as one, never beside a measured reading as though it were a finding about the market.
 *   * Support quality is not predictive confidence.
 *   * Superseded outcomes are AUDIT RECORDS: reachable, struck through, counted nowhere.
 *
 * Every resolution-state label comes from the server. A copy kept here is how four backend
 * states once all rendered as the single thing they were added to stop saying.
 */
import React, { useState } from 'react';
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
/* UNKNOWN gets its OWN strong tone, not the neutral grey: the whole point is that it must not
   read as "we looked and found nothing notable". */
const UNKNOWN_TONE = { fg: '#fde047', bg: 'rgba(234,179,8,0.12)', bd: 'rgba(234,179,8,0.45)' };
const tone = (d: string) => DIRECTION_TONE[d] ?? UNKNOWN_TONE;

/* RESOLVED, PENDING (time will fix it) or BLOCKED (evidence must be collected). One of the
   three is nobody's action item, which is exactly what a reader needs to know. */
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

/* THE SECOND AND THIRD QUESTIONS, which the resolution state does not answer. A figure that
   could be CALCULATED is not thereby VERIFIED: a source returning no corporate actions
   establishes what it returned, not that none occurred. A provisional figure is a different
   population from a verified one, not a lower-quality version of it, so the two are shown in
   separate columns and the page never adds them together. */
const EVIDENCE_TONE: Record<string, { fg: string; bg: string; bd: string }> = {
  verified:    { fg: '#6ee7b7', bg: 'rgba(52,211,153,0.10)', bd: 'rgba(52,211,153,0.35)' },
  provisional: { fg: '#fde047', bg: 'rgba(234,179,8,0.10)',  bd: 'rgba(234,179,8,0.4)' },
  unverified:  { fg: '#fdba74', bg: 'rgba(251,146,60,0.10)', bd: 'rgba(251,146,60,0.38)' },
};

/* Replay and prospective are never pooled, and the summary counts them apart. */
const POOL_ORDER = ['prospective', 'replay'] as const;

const label: React.CSSProperties = {
  fontSize: 11, letterSpacing: '0.09em', textTransform: 'uppercase', color: '#94a3b8',
};
const box: React.CSSProperties = {
  background: 'rgba(30,41,59,0.45)', border: '1px solid rgba(148,163,184,0.18)',
  borderRadius: 12, padding: '14px 16px',
};

function Pill({ text, t }: { text: string; t: { fg: string; bg: string; bd: string } }) {
  return (
    <span style={{ background: t.bg, border: `1px solid ${t.bd}`, color: t.fg, borderRadius: 999,
                   padding: '2px 10px', fontSize: 12, fontWeight: 600, whiteSpace: 'nowrap' }}>
      {text}
    </span>
  );
}

function Claims({ items, empty }: { items?: (IntelClaim | string)[]; empty: string }) {
  if (!items || items.length === 0)
    return <p style={{ color: '#64748b', fontSize: 13, margin: 0 }}>{empty}</p>;
  return (
    <ul style={{ margin: 0, paddingLeft: 18, display: 'grid', gap: 6 }}>
      {items.map((c, i) => {
        const o = typeof c === 'string' ? ({ claim: c } as IntelClaim) : c;
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

function Fold({ title, children, count }:
              { title: string; children: React.ReactNode; count?: number }) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ borderTop: '1px solid rgba(148,163,184,0.14)', paddingTop: 10, marginTop: 10 }}>
      <button onClick={() => setOpen(v => !v)}
              style={{ background: 'none', border: 'none', color: '#94a3b8', fontSize: 12,
                       cursor: 'pointer', padding: 0, letterSpacing: '0.05em' }}>
        {open ? '▾' : '▸'} {title}{count !== undefined ? ` (${count})` : ''}
      </button>
      {open && <div style={{ marginTop: 10 }}>{children}</div>}
    </div>
  );
}

function OutcomeRowCell({ row, labels }:
                        { row: OutcomeObservation; labels: Record<string, string> }) {
  const state = row.invalidated_reason ? 'INVALID_CAPTURE'
                                       : (row.outcome?.state ?? 'NOT_RESOLVED');
  const kind = row.invalidated_reason ? 'blocked' : stateKind(row.outcome?.state);
  return (
    <div style={{ display: 'grid', gap: 5 }}>
      <Pill text={labels[state] ?? state} t={KIND_TONE[kind]} />
      <p style={{ margin: 0, fontSize: 12, lineHeight: 1.45,
                  color: row.invalidated_reason ? '#fdba74' : '#94a3b8' }}>
        {row.invalidated_reason ?? row.outcome?.reason ?? ''}
      </p>
    </div>
  );
}

export default function StockIntelligencePanel({ symbol }: { symbol: string }) {
  const active = (symbol || '').trim().toUpperCase();
  const { data: outcomes, error: outErr, isLoading: outLoading } =
    useSWR<StockOutcomes>(active ? ['stock-outcomes', active] : null,
                          () => api.stockOutcomes(active));
  const { data: intel, error: intelErr, isLoading: intelLoading } =
    useSWR<StockIntelligence>(active ? ['stock-intel', active] : null,
                              () => api.stockIntelligence(active));

  if (!active) return null;
  const labels = outcomes?.reason_labels ?? {};

  if (outErr || intelErr) {
    return (
      <div style={{ ...box, borderColor: 'rgba(248,113,113,0.4)' }}>
        <p style={{ margin: 0, color: '#fca5a5', fontSize: 13 }}>
          Could not load intelligence for {active}. This is a failed request, not an empty
          result — the two are different and must not look the same.
        </p>
      </div>
    );
  }
  if ((outLoading || intelLoading) && !outcomes && !intel)
    return <p style={{ color: '#94a3b8', fontSize: 13 }}>Loading {active} intelligence…</p>;

  /* The MEDIUM horizon carries the fullest reading; fall back to whatever exists. */
  const obsWithSummary = (intel?.observations ?? []).filter(o => o.summary);
  const summary = (obsWithSummary.find(o => o.horizon_sessions === 20) ?? obsWithSummary[0])
                    ?.summary;
  const rows = outcomes?.observations ?? [];
  /* A NON-DIRECTIONAL reading has no confirmation or invalidation rule but still has
     something to say, so matching on the rules alone hid exactly the case that needed it. */
  const triggerRow = rows.find(r => r.confirmation_rule || r.invalidation_rule || r.triggers);
  const measured = (intel?.buckets ?? []).filter(b => b.direction !== 'UNKNOWN');
  const gaps = (intel?.buckets ?? []).filter(b => b.direction === 'UNKNOWN');
  const supersededCount = rows.reduce((n, r) => n + r.superseded.length, 0);
  const adjustment = rows.map(r => r.outcome).filter(Boolean);
  /* Distinct reasons a figure cannot be scored — the invalidated capture's own reason, and the
     resolution label for anything blocked on evidence rather than on elapsed time. */
  const blockedReasons = Array.from(new Set(rows.flatMap(r => {
    if (r.invalidated_reason)
      return [labels.INVALID_CAPTURE ?? 'Invalid capture — excluded from publication'];
    const st = r.outcome?.state;
    if (!st || stateKind(st) !== 'blocked') return [];
    return [labels[st] ?? st];
  })));

  return (
    <div style={{ display: 'grid', gap: 14 }}>
      {/* 1 — DIRECTION AND HORIZON */}
      <div style={box}>
        {summary ? (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <Pill text={summary.direction} t={tone(summary.direction)} />
              <strong style={{ color: '#f1f5f9', fontSize: 15 }}>
                over {summary.horizon ?? '—'}
                {summary.horizon_sessions ? ` (${summary.horizon_sessions} sessions)` : ''}
              </strong>
              <span style={{ color: '#94a3b8', fontSize: 13 }}>{summary.direction_basis}</span>
              {summary.support_quality && (
                <span style={label}>support {summary.support_quality}</span>
              )}
            </div>
            <p style={{ margin: '8px 0 0', fontSize: 12, color: '#fdba74', lineHeight: 1.5 }}>
              {summary.predictive_confidence_note
               ?? 'Support quality describes how well evidenced this reading is, not whether it is right.'}
            </p>
          </>
        ) : (
          <p style={{ margin: 0, color: '#94a3b8', fontSize: 13 }}>
            No stored reading for {active}. Nothing has been measured into a direction — that is
            a gap in coverage, not a neutral view of the market.
          </p>
        )}
      </div>

      {/* 2 and 3 — MAIN FACTORS, STRONGEST COUNTEREVIDENCE */}
      {summary && (
        <div style={{ display: 'grid', gap: 14,
                      gridTemplateColumns: 'repeat(auto-fit,minmax(280px,1fr))' }}>
          <div style={box}>
            <p style={{ ...label, margin: '0 0 8px' }}>Main factors</p>
            <Claims items={summary.three_factors}
                    empty="Nothing measured supports a direction." />
          </div>
          <div style={box}>
            <p style={{ ...label, margin: '0 0 8px' }}>Strongest counterevidence</p>
            <Claims items={summary.counterevidence}
                    empty="None from a measured bucket. An open question is a research limitation, not counterevidence." />
          </div>
        </div>
      )}

      {/* 4 — TRIGGERS, ORIENTED BY THE READING.
          A bearish view is confirmed by a break DOWN, not up. These were built from a fixed
          template and shown on a BEARISH GLD as "Confirms: close above 406.56" — the boundaries
          are symmetric facts about the range, and which one confirms depends entirely on what is
          being claimed. A non-directional reading has nothing to confirm, and the panel says so
          rather than attaching a thesis that was never formed. */}
      {triggerRow && (
        <div style={box}>
          <p style={{ ...label, margin: '0 0 8px' }}>Triggers</p>
          <div style={{ display: 'grid', gap: 6, fontSize: 13, color: '#e2e8f0' }}>
            {triggerRow.confirmation_rule && (
              <div><span style={{ color: '#6ee7b7' }}>Confirms: </span>
                {triggerRow.confirmation_rule}</div>
            )}
            {triggerRow.invalidation_rule && (
              <div><span style={{ color: '#fca5a5' }}>Invalidates: </span>
                {triggerRow.invalidation_rule}</div>
            )}
            {!triggerRow.confirmation_rule && !triggerRow.invalidation_rule && (
              <div style={{ color: '#94a3b8' }}>
                {(triggerRow.triggers?.establishes ?? []).length > 0 ? (
                  <>Neither boundary confirms or invalidates anything — no directional reading
                    was made. Either of these would establish one:{' '}
                    {(triggerRow.triggers?.establishes ?? []).join('; ')}.</>
                ) : 'No reading was formed, so no price level bears on one.'}
              </div>
            )}
            {triggerRow.triggers?.basis && (
              <div style={{ color: '#64748b', fontSize: 12, marginTop: 2 }}>
                {triggerRow.triggers.basis}
              </div>
            )}
          </div>
        </div>
      )}

      {/* 5 — OUTCOME STATUS: a COMPACT POOL SUMMARY by default, ledger folded underneath.
          The ledger is one row per observation per horizon and grows without bound; what a
          reader needs at a glance is how many figures are verified, how many are provisional,
          and how many captures were excluded. Replay and prospective are counted separately —
          a retrospective replay's rules were written with its outcomes already in existence. */}
      <div style={box}>
        <p style={{ ...label, margin: '0 0 8px' }}>Outcome status</p>
        {rows.length === 0 ? (
          <p style={{ margin: 0, color: '#64748b', fontSize: 13 }}>
            No observation has been recorded for {active} yet.
          </p>
        ) : (
          <>
            {POOL_ORDER.map(origin => {
              const c = outcomes?.coverage?.[origin];
              const pools = c?.pools ?? {};
              const n = (k: string) => pools[k]?.total ?? 0;
              const pending = rows.filter(
                r => r.origin === origin && !r.invalidated_reason
                     && (!r.outcome || r.outcome.state === 'UNRESOLVED_INSUFFICIENT_SESSIONS')
              ).length;
              const any = n('verified') + n('provisional') + n('ineligible')
                          + (c?.invalidated_captures ?? 0) + pending;
              if (!any) return null;
              return (
                <div key={origin} style={{ display: 'flex', gap: 8, flexWrap: 'wrap',
                                           alignItems: 'center', marginBottom: 8 }}>
                  <span style={{ ...label, fontSize: 10, minWidth: 78 }}>{origin}</span>
                  <Pill text={`${n('verified')} verified`} t={KIND_TONE.resolved} />
                  <Pill text={`${n('provisional')} provisional`} t={EVIDENCE_TONE.provisional} />
                  {pending > 0 && <Pill text={`${pending} pending`} t={KIND_TONE.pending} />}
                  {(c?.invalidated_captures ?? 0) > 0 && (
                    <Pill text={`${c?.invalidated_captures} invalidated`} t={KIND_TONE.blocked} />
                  )}
                </div>
              );
            })}
            {/* THE COUNTS ARE NOT THE REASONS. Folding the ledger must not fold away WHY a
                figure is not scoreable — that is the actionable part, and it is one line. */}
            {blockedReasons.length > 0 && (
              <div style={{ marginTop: 4, display: 'grid', gap: 3 }}>
                {blockedReasons.map(r => (
                  <p key={r} style={{ margin: 0, fontSize: 12, color: '#fdba74',
                                      lineHeight: 1.45 }}>{r}</p>
                ))}
              </div>
            )}
            {rows.some(r => r.outcome?.evidence_status === 'provisional') && (
              <>
                <p style={{ margin: '6px 0 0', fontSize: 12, color: '#fde047',
                            lineHeight: 1.5 }}>
                  {outcomes?.evidence_labels?.provisional
                   ?? 'Provisional — based on returned corporate actions; completeness unverified'}
                </p>
                {outcomes?.provisional_remedy && (
                  <p style={{ margin: '3px 0 0', fontSize: 12, color: '#94a3b8',
                              lineHeight: 1.5 }}>{outcomes.provisional_remedy}</p>
                )}
              </>
            )}
            <Fold title="Outcome ledger" count={rows.length}>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13,
                              minWidth: 720 }}>
                <thead>
                  <tr style={{ textAlign: 'left', color: '#94a3b8' }}>
                    {['Origin', 'Cutoff', 'Horizon', 'Calculation', 'Evidence', 'Eligibility',
                      'Descriptive', 'Excess', 'Basis'].map(h => (
                      <th key={h} style={{ padding: '7px 9px', fontWeight: 600, fontSize: 11,
                                           letterSpacing: '0.07em', textTransform: 'uppercase',
                                           borderBottom: '1px solid rgba(148,163,184,0.2)' }}>
                        {h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map(r => (
                    <tr key={r.observation_id}
                        style={{ borderBottom: '1px solid rgba(148,163,184,0.1)',
                                 opacity: r.invalidated_reason ? 0.6 : 1 }}>
                      <td style={{ padding: '9px', ...label, fontSize: 11 }}>{r.origin}</td>
                      <td style={{ padding: '9px', color: '#cbd5e1' }}>
                        {r.observed_at.slice(0, 10)}</td>
                      <td style={{ padding: '9px', color: '#cbd5e1' }}>
                        {r.horizon}<span style={{ color: '#64748b' }}> · {r.horizon_sessions}s</span>
                      </td>
                      <td style={{ padding: '9px', minWidth: 210 }}>
                        <OutcomeRowCell row={r} labels={labels} /></td>
                      <td style={{ padding: '9px', minWidth: 150 }}>
                        {r.outcome?.evidence_status ? (
                          <span title={r.outcome.evidence_label ?? ''}>
                            <Pill text={r.outcome.evidence_status}
                                  t={EVIDENCE_TONE[r.outcome.evidence_status]
                                     ?? EVIDENCE_TONE.unverified} />
                          </span>) : <span style={{ color: '#64748b' }}>—</span>}
                        {r.outcome?.evidence_status === 'provisional' && (
                          <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 3,
                                        lineHeight: 1.4 }}>
                            {outcomes?.evidence_labels?.provisional
                             ?? 'Provisional — based on returned corporate actions; completeness unverified'}
                          </div>)}
                      </td>
                      <td style={{ padding: '9px', color: '#94a3b8', fontSize: 12 }}>
                        {r.outcome?.performance_eligibility ?? '—'}</td>
                      <td style={{ padding: '9px', color: '#e2e8f0',
                                   fontVariantNumeric: 'tabular-nums' }}>
                        {PCT(r.outcome?.descriptive_return)}</td>
                      <td style={{ padding: '9px', color: '#e2e8f0',
                                   fontVariantNumeric: 'tabular-nums' }}>
                        {PCT(r.outcome?.excess_return)}</td>
                      <td style={{ padding: '9px', color: '#94a3b8', fontSize: 12 }}>
                        {r.outcome?.return_basis ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p style={{ margin: '10px 0 0', fontSize: 12, color: '#64748b', lineHeight: 1.5 }}>
              {outcomes?.note}
            </p>
            </Fold>
          </>
        )}
      </div>

      {/* EVERYTHING BELOW IS THE WORKING — available, not in the way. */}
      <div style={box}>
        <Fold title="Evidence buckets" count={intel?.buckets?.length}>
          <p style={{ margin: '0 0 10px', color: '#94a3b8', fontSize: 12 }}>
            {measured.length} of {intel?.buckets?.length ?? 0} buckets carry a measured reading.
            The rest are gaps in this platform&rsquo;s coverage — not findings that the market is
            neutral.
          </p>
          <div style={{ display: 'grid', gap: 10,
                        gridTemplateColumns: 'repeat(auto-fit,minmax(280px,1fr))' }}>
            {measured.map(b => (
              <div key={b.bucket} style={{ border: `1px solid ${tone(b.direction).bd}`,
                                           background: tone(b.direction).bg,
                                           borderRadius: 10, padding: 11 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8,
                              marginBottom: 7 }}>
                  <strong style={{ color: '#f1f5f9', fontSize: 13 }}>{b.bucket}</strong>
                  <Pill text={b.direction} t={tone(b.direction)} />
                </div>
                <Claims items={b.evidence} empty="No claims recorded." />
              </div>
            ))}
          </div>
          {gaps.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <p style={{ ...label, margin: '0 0 7px' }}>Not measured — {gaps.length}</p>
              <div style={{ display: 'grid', gap: 5 }}>
                {gaps.map(b => (
                  <div key={b.bucket} style={{ display: 'flex', gap: 9, flexWrap: 'wrap',
                                               alignItems: 'baseline' }}>
                    <Pill text={b.bucket} t={UNKNOWN_TONE} />
                    <span style={{ color: '#94a3b8', fontSize: 12 }}>
                      {b.evidence?.[0]?.claim ?? b.status}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </Fold>

        <Fold title="Adjustment evidence and return basis" count={adjustment.length}>
          <p style={{ margin: '0 0 10px', color: '#94a3b8', fontSize: 12, lineHeight: 1.5 }}>
            A return is only comparable across its window if both endpoints sit on one share
            basis. A split-adjusted price return excludes distributions by definition; a total
            return includes them. These are different measurements and are never pooled.
          </p>
          <div style={{ display: 'grid', gap: 8 }}>
            {rows.map(r => (
              <div key={r.observation_id} style={{ fontSize: 12, color: '#cbd5e1' }}>
                <strong>{r.horizon}</strong>{' · '}
                <span style={{ color: '#94a3b8' }}>
                  {r.outcome?.return_basis ?? 'no basis established'}
                </span>
                {r.outcome?.reason && (
                  <div style={{ color: '#64748b', marginTop: 2 }}>{r.outcome.reason}</div>
                )}
              </div>
            ))}
          </div>
        </Fold>

        {supersededCount > 0 && (
          <Fold title="Superseded results (audit records)" count={supersededCount}>
            <p style={{ margin: '0 0 9px', fontSize: 12, color: '#fdba74', lineHeight: 1.5 }}>
              Earlier versions of the same outcomes, retained unchanged after a resolver
              correction. Audit records only — excluded from every count above. They are not
              alternative results.
            </p>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead><tr style={{ textAlign: 'left', color: '#64748b' }}>
                {['Observation', 'Outcome', 'Resolver', 'Replaced by', 'State', 'Descriptive',
                  'Excess'].map(h => <th key={h} style={{ padding: '5px 9px' }}>{h}</th>)}
              </tr></thead>
              <tbody>
                {rows.flatMap(r => r.superseded.map(sv => (
                  <tr key={sv.id} style={{ color: '#64748b', textDecoration: 'line-through' }}>
                    <td style={{ padding: '5px 9px' }}>#{r.observation_id}</td>
                    <td style={{ padding: '5px 9px' }}>#{sv.id}</td>
                    <td style={{ padding: '5px 9px', fontFamily: 'monospace' }}>
                      {(sv.resolver ?? '').slice(0, 12)}</td>
                    <td style={{ padding: '5px 9px' }}>
                      {sv.superseded_by ? `#${sv.superseded_by}` : '—'}</td>
                    <td style={{ padding: '5px 9px' }}>{sv.state}</td>
                    <td style={{ padding: '5px 9px', fontVariantNumeric: 'tabular-nums' }}>
                      {PCT(sv.descriptive_return)}</td>
                    <td style={{ padding: '5px 9px', fontVariantNumeric: 'tabular-nums' }}>
                      {PCT(sv.excess_return)}</td>
                  </tr>
                )))}
              </tbody>
            </table>
          </Fold>
        )}
      </div>
    </div>
  );
}
