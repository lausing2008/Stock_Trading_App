import React from 'react';
import type { OptionsFlow } from '@/lib/api';
import { optionsFlowUnavailableText, composition, coverageLine, premiumText, turnoverText, requestFailedText, pct1,
  partialSum, money, lowSideNote, CP_CALL_HEAVY, CP_PUT_HEAVY } from '@/lib/optionsFlowStatus';

/** Options Flow card (options review Phase A): visible loading / request-failed / unavailable
 *  states, composition from exact volumes (never direction), source session and coverage, and
 *  paid premium kept apart from estimated turnover. */
export default function OptionsFlowCard({ flow, error, onRetry }: { flow?: OptionsFlow; error?: unknown; onRetry: () => void }) {
  return <>
        {!flow && (
          <div role="status" aria-label="Options Flow status" style={{ marginBottom: 24, padding: '10px 14px', border: '1px solid #334155', borderRadius: 8, color: '#94a3b8', fontSize: 13 }}>
            <strong style={{ color: '#cbd5e1' }}>Options Flow</strong> — {error ? requestFailedText(error) : 'loading…'}
            {error ? <button type="button" onClick={() => onRetry()} style={{ marginLeft: 10, fontSize: 12 }}>Retry</button> : null}
          </div>
        )}
        {flow && !flow.available && (
          <div role="status" aria-label="Options Flow status" style={{ marginBottom: 24, padding: '10px 14px', border: '1px solid #334155', borderRadius: 8, color: '#94a3b8', fontSize: 13 }}>
            <strong style={{ color: '#cbd5e1' }}>Options Flow</strong> — {optionsFlowUnavailableText(flow)}
          </div>
        )}
        {flow && flow.available && (
          <div style={{ marginBottom: 24 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
              <h2 style={{ fontSize: '15px', fontWeight: 700, color: '#cbd5e1', margin: 0 }}>Options Flow</h2>
              {(flow.whale_count ?? 0) > 0 && (
                <span style={{ fontSize: 11, fontWeight: 700, color: '#f59e0b', background: 'rgba(245,158,11,0.12)', border: '1px solid rgba(245,158,11,0.3)', borderRadius: 5, padding: '2px 8px' }}>
                  🐋 {flow.whale_count} whale {(flow.whale_count ?? 0) === 1 ? 'trade' : 'trades'}
                  {flow.whale_count_assessed != null && flow.whale_count_total != null && flow.whale_count_assessed < flow.whale_count_total
                    && ` (${flow.whale_count_assessed} of ${flow.whale_count_total} contracts assessable)`}
                </span>
              )}
              {flow.pressure_score != null && (
                <span
                  title={`Activity intensity points, not direction or confidence (direction is not established) — cp_ratio: ${flow.pressure_score.components.cp_ratio_pts}/40 · whales: ${flow.pressure_score.components.whale_pts}/30 · volume: ${flow.pressure_score.components.volume_pts}/10${flow.pressure_score.components.uw_gex_proximity_pts != null ? ` · GEX proximity (UW): ${flow.pressure_score.components.uw_gex_proximity_pts}/20` : ''}`}
                  style={{
                    fontSize: 11, fontWeight: 700, cursor: 'help', borderRadius: 5, padding: '2px 8px',
                    color: flow.pressure_score.score >= 60 ? '#a78bfa' : flow.pressure_score.score >= 35 ? '#818cf8' : '#64748b',
                    background: flow.pressure_score.score >= 60 ? 'rgba(167,139,250,0.12)' : 'rgba(100,116,139,0.1)',
                    border: `1px solid ${flow.pressure_score.score >= 60 ? 'rgba(167,139,250,0.3)' : 'rgba(100,116,139,0.25)'}`,
                  }}
                >
                  Pressure {flow.pressure_score.score.toFixed(0)}
                </span>
              )}
            </div>
            <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '14px 16px' }}>
              <p aria-label="Options Flow source and coverage" style={{ margin: '0 0 10px', fontSize: 11, color: '#94a3b8' }}>
                {coverageLine(flow)}. Direction is not established by these volumes.
              </p>
              {/* C/P ratio bar */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
                <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#64748b', marginBottom: 3 }}>
                    <span>Calls {flow.call_volume?.toLocaleString()}</span>
                    <span>{composition(flow.call_volume, flow.put_volume).ratioText}</span>
                    <span>Puts {flow.put_volume?.toLocaleString()}</span>
                  </div>
                  <div style={{ height: 8, borderRadius: 4, background: '#1e293b', overflow: 'hidden', display: 'flex' }}>
                    {(() => {
                      const total = (flow.call_volume ?? 0) + (flow.put_volume ?? 0);
                      const callPct = total > 0 ? (flow.call_volume ?? 0) / total * 100 : 50;
                      return <>
                        <div style={{ width: `${callPct}%`, background: '#22c55e', borderRadius: '4px 0 0 4px' }} />
                        <div style={{ flex: 1, background: '#ef4444', borderRadius: '0 4px 4px 0' }} />
                      </>;
                    })()}
                  </div>
                </div>
                {(() => {
                  const comp = composition(flow.call_volume, flow.put_volume);
                  const tone = comp.kind === 'call_heavy' ? ['rgba(34,197,94,0.12)', '#4ade80'] : comp.kind === 'put_heavy' ? ['rgba(239,68,68,0.12)', '#f87171'] : ['rgba(100,116,139,0.15)', '#94a3b8'];
                  return (
                    <div aria-label="Volume composition" title={`Composition of observed contract volume, not direction: call-heavy when C/P ≥ ${CP_CALL_HEAVY} (≈56.5% calls), put-heavy when C/P ≤ ${CP_PUT_HEAVY} (≈55.6% puts), mixed otherwise. Calls can be sold and puts bought for either view.`}
                      style={{ padding: '3px 10px', borderRadius: 5, fontSize: 11, fontWeight: 700, flexShrink: 0, background: tone[0], color: tone[1] }}>
                      {comp.label} · {pct1(comp.callShare)} calls / {pct1(comp.putShare)} puts
                      {lowSideNote(flow.call_volume, flow.put_volume) && ' · few contracts on one side'}
                    </div>
                  );
                })()}
              </div>

              <p style={{ margin: '0 0 10px', fontSize: 11, color: '#64748b' }}>
                Call-heavy when C/P ≥ {CP_CALL_HEAVY} (≈56.5% calls), put-heavy when C/P ≤ {CP_PUT_HEAVY} (≈55.6% puts), mixed otherwise
                {' '}— a description of volume, not of direction{lowSideNote(flow.call_volume, flow.put_volume) ? `; ${lowSideNote(flow.call_volume, flow.put_volume)}` : ''}.
              </p>
              {/* Put-volume spike callout — surfaces whether TODAY's put activity is genuinely
                  elevated (volume already exceeding existing open interest on the put side),
                  not just restating the raw put_volume number already shown in the bar above. */}
              {flow.unusual && flow.unusual.length > 0 && (() => {
                const putUnusual = flow.unusual.filter(c => c.side === 'put');
                if (putUnusual.length === 0) return null;
                const paid = partialSum(putUnusual.map(c => c.traded_premium ?? c.premium ?? null));
                const putPremium = paid.sum;
                const maxVolOi = Math.max(...putUnusual.map(c => c.vol_oi));
                const putWhales = putUnusual.filter(c => c.is_whale).length;
                return (
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
                    background: 'rgba(239,68,68,0.06)', border: '1px solid rgba(239,68,68,0.2)',
                    borderRadius: 6, padding: '8px 12px', marginBottom: 12, fontSize: 11,
                  }}>
                    <span style={{ fontWeight: 700, color: '#f87171' }}>Elevated put activity (volume vs open interest)</span>
                    <span style={{ color: '#94a3b8' }}>
                      {putUnusual.length} put contract{putUnusual.length !== 1 ? 's' : ''} trading at{' '}
                      {maxVolOi.toFixed(1)}× today&apos;s volume vs. existing open interest
                      {paid.known > 0 && ` · ${paid.known < paid.total ? '≥' : ''}${money(putPremium)} paid premium${paid.known < paid.total ? ` (${paid.known} of ${paid.total} contracts known)` : ''}`}
                      {putWhales > 0 && ` · ${putWhales} whale trade${putWhales !== 1 ? 's' : ''}`}
                    </span>
                  </div>
                );
              })()}

              {/* Unusual contracts table */}
              {flow.unusual && flow.unusual.length > 0 && (
                <>
                  <div style={{ fontSize: 10, color: '#475569', fontWeight: 700, letterSpacing: '0.06em', marginBottom: 6 }}>
                    UNUSUAL ACTIVITY — {flow.expiries_used?.join(', ')}
                  </div>
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                      <thead>
                        <tr style={{ borderBottom: '1px solid #1e293b' }}>
                          {['Side', 'Strike', 'Expiry', 'Volume', 'OI', 'Vol/OI', 'IV', 'ITM', 'Paid premium', 'Est. turnover (vol × mid × 100)'].map(h => (
                            <th key={h} style={{ padding: '4px 8px', textAlign: 'left', color: '#475569', fontWeight: 500 }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {flow.unusual.map((c, i) => (
                          <tr key={i} style={{ borderBottom: '1px solid #0f172a', background: c.is_whale ? 'rgba(245,158,11,0.05)' : 'transparent', outline: c.is_whale ? '1px solid rgba(245,158,11,0.2)' : 'none' }}>
                            <td style={{ padding: '5px 8px' }}>
                              <span style={{ fontWeight: 700, color: c.side === 'call' ? '#4ade80' : '#f87171' }}>
                                {c.side.toUpperCase()}
                              </span>
                            </td>
                            <td style={{ padding: '5px 8px', color: '#e2e8f0' }}>${c.strike}</td>
                            <td style={{ padding: '5px 8px', color: '#64748b' }}>{c.expiry}</td>
                            <td style={{ padding: '5px 8px', color: '#e2e8f0', fontWeight: 600 }}>{c.volume.toLocaleString()}</td>
                            <td style={{ padding: '5px 8px', color: '#64748b' }}>{c.oi.toLocaleString()}</td>
                            <td style={{ padding: '5px 8px', color: c.vol_oi > 1 ? '#f59e0b' : '#94a3b8', fontWeight: c.vol_oi > 1 ? 700 : 400 }}>{c.vol_oi.toFixed(2)}×</td>
                            <td style={{ padding: '5px 8px', color: '#94a3b8' }}>{c.iv.toFixed(0)}%</td>
                            <td style={{ padding: '5px 8px', color: c.itm ? '#4ade80' : '#475569' }}>{c.itm ? 'ITM' : 'OTM'}</td>
                            <td title={premiumText(c) === 'unavailable' ? 'Paid premium needs trade-level data; the settled chain has none' : 'Premium from the trade tape'}
                              style={{ padding: '5px 8px', color: c.is_whale ? '#f59e0b' : '#475569', fontWeight: c.is_whale ? 700 : 400 }}>
                              {premiumText(c)}
                              {c.is_whale && ' 🐋'}
                            </td>
                            <td title="Estimate: volume × mid × 100 over the day; not premium anyone paid" style={{ padding: '5px 8px', color: '#64748b' }}>
                              {turnoverText(c)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p style={{ margin: '6px 0 0', fontSize: 11, color: '#64748b' }}>
                    Paid premium needs trade-level data and reads &quot;unavailable&quot; where none was captured (the settled chain has none).
                    Est. turnover = volume × mid × 100 over the day — an estimate, not premium anyone paid.
                  </p>
                </>
              )}
            </div>
          </div>
        )}
  </>;
}
