// Call Finder (docs/features/2026-11-10-options-tab-call-finder-design.md): one near-term call and
// one LEAPS call for the reader's view, each with the six figures asked for and a reason for each.
// Research candidates ranked against the reader's own target, not recommendations.
import React, { useState } from 'react';
import useSWR from 'swr';
import { api, type CallFinderContract, type CallFinderPick, type CallFinderResponse } from '@/lib/api';

const CARD: React.CSSProperties = { background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '14px 16px' };
const pct = (v: number | null | undefined, digits = 0) => (typeof v === 'number' && Number.isFinite(v) ? `${v >= 0 ? '+' : ''}${(v * 100).toFixed(digits)}%` : '—');
const usd = (v: number) => `$${v.toFixed(2)}`;
const tone = (v: number | null | undefined) => (typeof v !== 'number' ? '#94a3b8' : v >= 0 ? '#4ade80' : '#f87171');

const ROWS: [keyof CallFinderContract['reasons'], string, (c: CallFinderContract) => string][] = [
  ['expiration', 'Expiration', c => `${c.expiry} (${c.dte}d)`],
  ['strike', 'Strike', c => usd(c.strike)],
  ['delta', 'Delta', c => `${c.delta.toFixed(2)}${c.greeks_estimated ? ' est.' : ''}`],
  ['gamma', 'Gamma', c => (c.gamma != null ? `${c.gamma.toFixed(3)}${c.greeks_estimated ? ' est.' : ''}` : '—')],
  ['open_interest', 'Open interest', c => c.oi.toLocaleString()],
  ['iv', 'IV', c => `${c.iv_pct.toFixed(1)}%`],
];

export function CallFinderContractView({ c, label }: { c: CallFinderContract; label: string }) {
  const s = c.scenarios;
  return (
    <div data-testid={`call-finder-${label}`}>
      <div style={{ fontSize: 14, fontWeight: 700, color: '#e2e8f0' }}>
        {usd(c.strike)} call · {c.expiry} · ask {usd(c.ask)} <span style={{ color: '#64748b', fontWeight: 400 }}>({usd(c.cost_per_contract)} per contract)</span>
      </div>
      <div style={{ fontSize: 12, color: '#94a3b8', margin: '2px 0 8px' }}>
        Break-even {usd(c.breakeven)} ({c.breakeven_move_pct >= 0 ? '+' : ''}{c.breakeven_move_pct.toFixed(1)}% from here) · spread {c.spread_pct.toFixed(1)}%
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <tbody>
          {ROWS.map(([key, name, value]) => (
            <tr key={key} style={{ borderTop: '1px solid #1e293b' }}>
              <th scope="row" style={{ textAlign: 'left', padding: '5px 8px 5px 0', color: '#64748b', fontWeight: 500, whiteSpace: 'nowrap', verticalAlign: 'top' }}>{name}</th>
              <td style={{ padding: '5px 8px', color: '#e2e8f0', fontWeight: 700, whiteSpace: 'nowrap', verticalAlign: 'top' }}>{value(c)}</td>
              <td style={{ padding: '5px 0', color: '#94a3b8' }}>{c.reasons[key]}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ fontSize: 11, color: '#64748b', marginTop: 10 }}>If you&apos;re right / if you&apos;re wrong (return on the premium):</div>
      <div aria-label={`${label} scenarios`} style={{ display: 'flex', flexWrap: 'wrap', gap: 12, fontSize: 12, marginTop: 4 }}>
        {([['At target', s.target], ['Half the move', s.half_move], ['Flat', s.flat], ['One σ down', s.down_one_sigma],
          [`Target, twice as late (${s.late_days}d)`, s.target_twice_as_late]] as [string, number | null][]).map(([name, v]) => (
          <span key={name}><span style={{ color: '#64748b' }}>{name}: </span><strong style={{ color: tone(v) }}>{pct(v)}</strong></span>
        ))}
      </div>
      {c.model_prob_above_breakeven != null && (
        <div style={{ fontSize: 11, color: '#64748b', marginTop: 6 }}>
          Model chance of finishing above break-even at expiry: {(c.model_prob_above_breakeven * 100).toFixed(0)}% (the market&apos;s pricing under the model, not a prediction).
        </div>
      )}
    </div>
  );
}

function PickColumn({ title, p }: { title: string; p?: CallFinderPick }) {
  const [showAlt, setShowAlt] = useState(false);
  if (!p) return null;
  return (
    <section aria-label={title} style={{ ...CARD, flex: '1 1 340px', minWidth: 0 }}>
      <h4 style={{ margin: '0 0 6px', fontSize: 13, color: '#38bdf8' }}>{title}</h4>
      {!p.available || !p.pick ? (
        <p style={{ fontSize: 12, color: '#94a3b8', margin: 0 }}>No pick: {p.reason}.</p>
      ) : (
        <>
          <p style={{ fontSize: 11, color: '#64748b', margin: '0 0 8px' }}>
            Expiry: {p.expiry_reason}. Strike: {p.rule}. Target {usd(p.target!)} ({p.target_basis}); {p.hold_days}-day hold; {p.eligible_count} contracts passed the checks.
          </p>
          {p.kind === 'near' && (
            <p data-testid="call-finder-replay-note" style={{ fontSize: 11, color: '#94a3b8', margin: '0 0 8px' }}>
              What the replay found (353,000 archived trades, Nov 2026): this pick is lower leverage than the aggressive
              alternative, so it trailed in months the stock rose and held up better in flat or falling months. Its one
              consistent edge was a narrower spread on 7-day holds. It does not forecast which contract will earn most.
            </p>
          )}
          {p.warnings && p.warnings.length > 0 && (
            <ul aria-label={`${title} warnings`} style={{ margin: '0 0 8px', paddingLeft: 18, fontSize: 11, color: '#f59e0b' }}>
              {p.warnings.map(w => <li key={w}>{w}</li>)}
            </ul>
          )}
          <CallFinderContractView c={p.pick} label={p.kind} />
          {p.aggressive_alternative && (
            <div style={{ marginTop: 10 }}>
              <button type="button" onClick={() => setShowAlt(v => !v)} style={{ fontSize: 11, background: 'transparent', border: '1px solid #334155', color: '#94a3b8', borderRadius: 5, padding: '3px 8px', cursor: 'pointer' }}>
                {showAlt ? 'Hide' : 'Show'} aggressive alternative (highest return only if the whole move arrives on time)
              </button>
              {showAlt && <div style={{ marginTop: 8 }}><CallFinderContractView c={p.aggressive_alternative} label={`${p.kind}-aggressive`} /></div>}
            </div>
          )}
        </>
      )}
    </section>
  );
}

export function CallFinderGlance({ data }: { data: CallFinderResponse }) {
  const g = data.glance;
  if (!g) return null;
  const item = (name: string, value: string) => (
    <span><span style={{ color: '#64748b' }}>{name} </span><strong style={{ color: '#e2e8f0' }}>{value}</strong></span>
  );
  return (
    <div aria-label="Options at a glance" style={{ ...CARD, display: 'flex', flexWrap: 'wrap', gap: 16, fontSize: 12, marginBottom: 10 }}>
      {item('Price', `${usd(g.price)} (${data.source_session} close)`)}
      {item('ATM IV', g.atm_iv_pct != null ? `${g.atm_iv_pct.toFixed(1)}%` : '—')}
      {item('Realised (20d)', g.hv20_pct != null ? `${g.hv20_pct.toFixed(1)}%` : '—')}
      {item('IV rank', g.iv_rank != null ? `${g.iv_rank.toFixed(0)}/100` : `unavailable (${g.iv_rank_status})`)}
      {item('Implied move', g.implied_move_pct != null ? `±${g.implied_move_pct.toFixed(1)}% to ${g.implied_move_expiry}` : '—')}
      {g.events.length > 0 ? g.events.map(e => <React.Fragment key={e.label}>{item(e.label, e.date)}</React.Fragment>) : item('Events', 'none known')}
    </div>
  );
}

export default function CallFinderCard({ symbol, holdingDays, analystTarget, onPick }: {
  symbol: string; holdingDays: number; analystTarget?: number | null;
  onPick?: (picks: { near?: CallFinderContract; leaps?: CallFinderContract }) => void;
}) {
  const [targetText, setTargetText] = useState('');
  const target = Number(targetText) > 0 ? Number(targetText) : null;
  const { data, error } = useSWR(
    symbol ? ['call-finder', symbol, holdingDays, target] : null,
    () => api.getOptionsCallFinder(symbol, { holdingDays, targetPrice: target }),
    { revalidateOnFocus: false, onSuccess: d => onPick?.({ near: d.near?.pick, leaps: d.leaps?.pick }) },
  );
  return (
    <section aria-label="Call Finder" id="find-a-contract" style={{ marginBottom: 16 }}>
      <h3 style={{ fontSize: 15, fontWeight: 700, color: '#cbd5e1', margin: '0 0 4px' }}>Call Finder</h3>
      <p style={{ fontSize: 12, color: '#64748b', margin: '0 0 8px' }}>
        A near-term call and a LEAPS call for your view. Each pick is the contract that pays best if the stock makes
        <strong> half</strong> of your target move by the end of the hold, so it rewards being roughly right. Research candidates, not recommendations.
      </p>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', fontSize: 12, marginBottom: 10 }}>
        <label>Target price{' '}
          <input aria-label="Call Finder target price" type="number" min="0" step="0.01" value={targetText} placeholder="one implied σ up"
            onChange={e => setTargetText(e.target.value)} style={{ width: 120 }} />
        </label>
        {analystTarget != null && analystTarget > 0 && (
          <button type="button" onClick={() => setTargetText(analystTarget.toFixed(2))} style={{ fontSize: 11, background: 'transparent', border: '1px solid #334155', color: '#94a3b8', borderRadius: 5, padding: '3px 8px', cursor: 'pointer' }}>
            Use analyst target ({usd(analystTarget)})
          </button>
        )}
        {targetText && <button type="button" onClick={() => setTargetText('')} style={{ fontSize: 11, background: 'transparent', border: 'none', color: '#38bdf8', cursor: 'pointer' }}>Reset to one implied σ</button>}
        <span style={{ color: '#64748b' }}>Near-term hold: {holdingDays} days (from the horizon selector)</span>
      </div>
      {error && <p role="alert" style={{ fontSize: 12, color: '#f87171' }}>The Call Finder could not be loaded.</p>}
      {!data && !error && <p style={{ fontSize: 12, color: '#475569' }}>Loading…</p>}
      {data && !data.available && (!data.near || !data.leaps) && (
        <p style={{ fontSize: 12, color: '#94a3b8' }}>Call Finder unavailable: {data.reason}{data.detail ? ` (${data.detail})` : ''}.</p>
      )}
      {data && data.glance && <CallFinderGlance data={data} />}
      {data && (data.near || data.leaps) && (
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <PickColumn title="Near-term call" p={data.near} />
          <PickColumn title="LEAPS call" p={data.leaps} />
        </div>
      )}
      {data?.assumptions && (
        <ul aria-label="Call Finder assumptions" style={{ fontSize: 10, color: '#475569', margin: '8px 0 0', paddingLeft: 18 }}>
          {data.assumptions.map(a => <li key={a}>{a}</li>)}
        </ul>
      )}
    </section>
  );
}
