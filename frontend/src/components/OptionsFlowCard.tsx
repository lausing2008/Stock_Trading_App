import React from 'react';
import type { OptionsFlow } from '@/lib/api';
import { optionsFlowUnavailableText, composition, coverageLine, premiumText, turnoverText, requestFailedText, pct1,
  partialSum, money, lowSideNote, expiredSinceLine, pressureText, pressureTitle, pressureShare, selectionLine, CP_CALL_HEAVY, CP_PUT_HEAVY,
  fmtIv, moneynessText, moneynessReferenceLine, volumeScopeNote, expiryCandidateNote, etText, agoText } from '@/lib/optionsFlowStatus';
import { useNow } from '@/lib/useNow';

/** Options Flow card (options review Phase A): visible loading / request-failed / unavailable
 *  states, composition from exact volumes (never direction), source session and coverage, and
 *  paid premium kept apart from estimated turnover. */
export default function OptionsFlowCard({ flow, error, onRetry }: { flow?: OptionsFlow; error?: unknown; onRetry: () => void }) {
  // Audit R02: ages and expiry eligibility advance in an open tab.
  const now = useNow(30_000);
  const computed = etText(flow?.computed_at);
  const computedAge = flow?.computed_at ? agoText((now - Date.parse(flow.computed_at)) / 60_000) : null;
  return <>
        {/* R02: a failed refresh is shown even when earlier data is on screen — the cached
            answer stays, labelled with when it was computed, never presented as current. */}
        {flow && error ? (
          <div role="alert" aria-label="Options Flow refresh failed" style={{ marginBottom: 8, padding: '8px 12px', border: '1px solid rgba(245,158,11,0.4)', borderRadius: 8, color: '#fbbf24', fontSize: 12 }}>
            Latest refresh failed — {requestFailedText(error)}{' '}
            {computed ? `Showing the data computed ${computed} (${computedAge}).` : 'Showing the previously loaded data.'}
            <button type="button" onClick={() => onRetry()} style={{ marginLeft: 10, fontSize: 12 }}>Retry</button>
          </div>
        ) : null}
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
                  title={pressureTitle(flow.pressure_score)}
                  style={{
                    fontSize: 11, fontWeight: 700, cursor: 'help', borderRadius: 5, padding: '2px 8px',
                    color: pressureShare(flow.pressure_score) >= 0.6 ? '#a78bfa' : pressureShare(flow.pressure_score) >= 0.35 ? '#818cf8' : '#64748b',
                    background: pressureShare(flow.pressure_score) >= 0.6 ? 'rgba(167,139,250,0.12)' : 'rgba(100,116,139,0.1)',
                    border: `1px solid ${pressureShare(flow.pressure_score) >= 0.6 ? 'rgba(167,139,250,0.3)' : 'rgba(100,116,139,0.25)'}`,
                  }}
                >
                  {pressureText(flow.pressure_score)}
                </span>
              )}
            </div>
            <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '14px 16px' }}>
              <p aria-label="Options Flow source and coverage" style={{ margin: '0 0 10px', fontSize: 11, color: '#94a3b8' }}>
                {coverageLine(flow)}. Direction is not established by these volumes.
                {computed ? <> Computed {computed} · {computedAge}.</> : null}
                {expiredSinceLine(flow.expiry_scope, now) ? <> {expiredSinceLine(flow.expiry_scope, now)}</> : null}
              </p>
              {/* C/P ratio bar */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
                <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#64748b', marginBottom: 3 }}>
                    <span>Calls {flow.call_volume == null ? 'not measured' : flow.call_volume.toLocaleString()}</span>
                    <span>{composition(flow.call_volume, flow.put_volume).ratioText}</span>
                    <span>Puts {flow.put_volume == null ? 'not measured' : flow.put_volume.toLocaleString()}</span>
                  </div>
                  <div style={{ height: 8, borderRadius: 4, background: '#1e293b', overflow: 'hidden', display: 'flex' }}>
                    {(() => {
                      // R04: a side that was not measured draws no bar at all — not a 100/0 split.
                      if (flow.call_volume == null || flow.put_volume == null) return null;
                      const total = flow.call_volume + flow.put_volume;
                      const callPct = total > 0 ? flow.call_volume / total * 100 : 50;
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
              {volumeScopeNote(flow) && (
                <p aria-label="Volume coverage" style={{ margin: '0 0 10px', fontSize: 11, color: '#f59e0b' }}>{volumeScopeNote(flow)}</p>
              )}
              {/* Put-volume spike callout — surfaces whether TODAY's put activity is genuinely
                  elevated (volume already exceeding existing open interest on the put side),
                  not just restating the raw put_volume number already shown in the bar above. */}
              {/* F5: elevated activity callouts for BOTH sides, same wording and layout — a put-only
                  callout implied puts were the story. Volume above OI does not show opening trades. */}
              {flow.unusual && flow.unusual.length > 0 && (['call', 'put'] as const).map(side => {
                const rows = flow.unusual!.filter(c => c.side === side);
                if (rows.length === 0) return null;
                const paid = partialSum(rows.map(c => c.traded_premium ?? c.premium ?? null));
                const maxVolOi = Math.max(...rows.map(c => c.vol_oi));
                const whales = rows.filter(c => c.is_whale).length;
                const tint = side === 'call' ? ['rgba(74,222,128,0.06)', 'rgba(74,222,128,0.2)', '#4ade80'] : ['rgba(239,68,68,0.06)', 'rgba(239,68,68,0.2)', '#f87171'];
                return (
                  <div key={side} aria-label={`Elevated ${side} activity`} style={{
                    display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
                    background: tint[0], border: `1px solid ${tint[1]}`,
                    borderRadius: 6, padding: '8px 12px', marginBottom: 8, fontSize: 11,
                  }}>
                    <span style={{ fontWeight: 700, color: tint[2] }}>Elevated {side} activity (volume vs open interest)</span>
                    <span style={{ color: '#94a3b8' }}>
                      {rows.length} {side} contract{rows.length !== 1 ? 's' : ''} shown, up to{' '}
                      {maxVolOi.toFixed(1)}× today&apos;s volume vs. existing open interest
                      {paid.known > 0 && ` · ${paid.known < paid.total ? '≥' : ''}${money(paid.sum)} paid premium${paid.known < paid.total ? ` (${paid.known} of ${paid.total} contracts known)` : ''}`}
                      {whales > 0 && ` · ${whales} whale trade${whales !== 1 ? 's' : ''}`}
                    </span>
                  </div>
                );
              })}

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
                          {['Side', 'Strike', 'Expiry', 'Volume', 'OI', 'Vol/OI', 'IV', 'Moneyness', 'Paid premium', 'Est. turnover (vol × mark × 100)'].map(h => (
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
                            <td style={{ padding: '5px 8px', color: '#64748b' }}>{c.expiry}{expiryCandidateNote(c.expiry, flow.expiry_scope, now, c.current_candidate) ? <span style={{ color: '#f59e0b' }}>{expiryCandidateNote(c.expiry, flow.expiry_scope, now, c.current_candidate)}</span> : null}</td>
                            <td style={{ padding: '5px 8px', color: '#e2e8f0', fontWeight: 600 }}>{c.volume.toLocaleString()}</td>
                            <td style={{ padding: '5px 8px', color: '#64748b' }}>{c.oi.toLocaleString()}</td>
                            <td style={{ padding: '5px 8px', color: c.vol_oi > 1 ? '#f59e0b' : '#94a3b8', fontWeight: c.vol_oi > 1 ? 700 : 400 }}>{c.vol_oi.toFixed(2)}×</td>
                            <td style={{ padding: '5px 8px', color: '#94a3b8' }}>{fmtIv(c.iv)}</td>
                            <td style={{ padding: '5px 8px', color: c.moneyness === 'ITM' ? '#4ade80' : '#475569' }}>{moneynessText(c)}</td>
                            <td title={premiumText(c) === 'unavailable' ? 'Paid premium needs trade-level data; the settled chain has none' : 'Premium from the trade tape'}
                              style={{ padding: '5px 8px', color: c.is_whale ? '#f59e0b' : '#475569', fontWeight: c.is_whale ? 700 : 400 }}>
                              {premiumText(c)}
                              {c.is_whale && ' 🐋'}
                            </td>
                            <td title="Estimate: volume × mark × 100 over the day (midpoint, else last price); not premium anyone paid" style={{ padding: '5px 8px', color: '#64748b' }}>
                              {turnoverText(c)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p aria-label="Moneyness reference" style={{ margin: '6px 0 0', fontSize: 11, color: '#64748b' }}>
                    {moneynessReferenceLine(flow.moneyness_reference, flow.moneyness_unknown_reason)}
                  </p>
                  <p style={{ margin: '6px 0 0', fontSize: 11, color: '#64748b' }}>
                    Paid premium needs trade-level data and reads &quot;unavailable&quot; where none was captured (the settled chain has none).
                    Est. turnover = volume × mark × 100 over the day (midpoint, else last price) — an estimate, not premium anyone paid;
                    withheld for adjusted contracts whose deliverable may not be 100 shares.
                  </p>
                </>
              )}
              {/* Shown even when nothing qualified: "0 of 0 eligible" is not "no activity". */}
              {selectionLine(flow.unusual_selection) && (
                <p aria-label="Unusual activity selection" style={{ margin: '6px 0 0', fontSize: 11, color: '#64748b' }}>{selectionLine(flow.unusual_selection)}</p>
              )}
              {flow.low_oi_activity && (flow.low_oi_activity.count.call + flow.low_oi_activity.count.put) > 0 && (
                <div aria-label="Low open interest activity" style={{ marginTop: 10, fontSize: 11, color: '#94a3b8' }}>
                  <div style={{ fontSize: 10, color: '#475569', fontWeight: 700, letterSpacing: '0.06em', marginBottom: 4 }}>
                    NEW OR THINLY HELD STRIKES — {flow.low_oi_activity.count.call} call{flow.low_oi_activity.count.call === 1 ? '' : 's'}, {flow.low_oi_activity.count.put} put{flow.low_oi_activity.count.put === 1 ? '' : 's'}
                  </div>
                  {flow.low_oi_activity.top.map(r => (
                    <div key={`${r.expiry}-${r.side}-${r.strike}`}>{r.side.toUpperCase()} ${r.strike} · {r.expiry} · volume {r.volume.toLocaleString()} vs open interest {r.oi.toLocaleString()}</div>
                  ))}
                  <p style={{ margin: '4px 0 0', color: '#64748b' }}>{flow.low_oi_activity.rule}.</p>
                </div>
              )}
            </div>
          </div>
        )}
  </>;
}
