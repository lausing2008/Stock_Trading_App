import useSWR from 'swr';
import { api } from '@/lib/api';

/** MPE-06/MPE-03: real dealer gamma exposure (Unusual Whales, when configured/enabled) plus
 * a per-expiration open-interest concentration rollup. Self-contained (its own SWR fetches),
 * matching StockGoalsPanel's/SrWatchButton's own established pattern for keeping
 * stock/[symbol].tsx from growing further — it already sits at 4000+ lines.
 *
 * Renders NOTHING when neither real GEX data nor a real expirations rollup is available —
 * this is deliberately optional enrichment, never a required section every stock page shows,
 * since GEX specifically depends on an admin having configured and enabled a paid Unusual
 * Whales subscription (Settings → Market Pressure Data). */
export default function MarketPressurePanel({ symbol }: { symbol: string }) {
  const { data: gex } = useSWR(
    symbol ? `gamma-exposure-${symbol}` : null,
    () => api.getGammaExposure(symbol),
    { revalidateOnFocus: false },
  );
  const { data: expirations } = useSWR(
    symbol ? `options-expirations-${symbol}` : null,
    () => api.getOptionsExpirations(symbol),
    { revalidateOnFocus: false },
  );
  const { data: darkPool } = useSWR(
    symbol ? `dark-pool-prints-${symbol}` : null,
    () => api.getDarkPoolPrints(symbol),
    { revalidateOnFocus: false },
  );

  const hasGex = gex?.available && gex.source === 'unusual_whales';
  const hasExpirations = expirations?.available && (expirations.expirations?.length ?? 0) > 0;
  const hasDarkPool = darkPool?.available && (darkPool.prints?.length ?? 0) > 0;
  const hasNope = hasGex && gex?.nope?.nope != null;

  if (!hasGex && !hasExpirations && !hasDarkPool) return null;

  const levelColor = (level: string): string => {
    if (level === 'extreme') return '#ef4444';
    if (level === 'high') return '#f97316';
    if (level === 'elevated') return '#facc15';
    return '#64748b';
  };

  return (
    <div style={{ marginBottom: 24 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <h2 style={{ fontSize: '15px', fontWeight: 700, color: '#cbd5e1', margin: 0 }}>Market Pressure</h2>
        {hasGex && (
          <span style={{ fontSize: 10, fontWeight: 700, color: '#a78bfa', background: 'rgba(167,139,250,0.12)', border: '1px solid rgba(167,139,250,0.3)', borderRadius: 4, padding: '2px 7px' }}>
            Real GEX — Unusual Whales
          </span>
        )}
        {hasDarkPool && (
          <span style={{ fontSize: 10, fontWeight: 700, color: '#38bdf8', background: 'rgba(56,189,248,0.12)', border: '1px solid rgba(56,189,248,0.3)', borderRadius: 4, padding: '2px 7px' }}>
            Dark Pool — Unusual Whales
          </span>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12 }}>
        {hasGex && (
          <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '14px 16px' }}>
            <div style={{ fontSize: 11, color: '#64748b', marginBottom: 10 }}>
              Dealer gamma exposure — where hedging pressure concentrates.
              {gex.as_of_date && <span style={{ color: '#334155' }}> As of {gex.as_of_date}.</span>}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {gex.call_wall != null && (
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
                  <span style={{ color: '#4ade80' }}>Call wall</span>
                  <span style={{ fontWeight: 700, color: '#e2e8f0', fontVariantNumeric: 'tabular-nums' }}>${gex.call_wall.toFixed(2)}</span>
                </div>
              )}
              {gex.gamma_flip != null && (
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
                  <span style={{ color: '#facc15' }} title="The 'zero gamma' price level — dealer hedging flips direction here">Gamma flip</span>
                  <span style={{ fontWeight: 700, color: '#e2e8f0', fontVariantNumeric: 'tabular-nums' }}>${gex.gamma_flip.toFixed(2)}</span>
                </div>
              )}
              {gex.put_wall != null && (
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
                  <span style={{ color: '#f87171' }}>Put wall</span>
                  <span style={{ fontWeight: 700, color: '#e2e8f0', fontVariantNumeric: 'tabular-nums' }}>${gex.put_wall?.toFixed(2)}</span>
                </div>
              )}
              {gex.gamma_magnet != null && (
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
                  <span style={{ color: '#94a3b8' }}>Gamma magnet</span>
                  <span style={{ fontWeight: 700, color: '#e2e8f0', fontVariantNumeric: 'tabular-nums' }}>${gex.gamma_magnet?.toFixed(2)}</span>
                </div>
              )}
              {gex.max_pain?.[0]?.max_pain != null && (
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }} title="Where option WRITERS lose the least at expiry -- a different concept from the dealer-hedging walls above">
                  <span style={{ color: '#c084fc' }}>Max pain ({gex.max_pain[0].expiry})</span>
                  <span style={{ fontWeight: 700, color: '#e2e8f0', fontVariantNumeric: 'tabular-nums' }}>${gex.max_pain[0].max_pain.toFixed(2)}</span>
                </div>
              )}
            </div>
          </div>
        )}

        {hasGex && (gex.oi_per_strike?.length ?? 0) > 0 && (() => {
          // AUD-MAXPAIN: top strikes by total (call+put) open interest -- the raw OI
          // distribution GEX's own gamma-weighted call_wall/put_wall only imply indirectly.
          const topOi = [...(gex.oi_per_strike ?? [])]
            .filter(r => r.strike != null)
            .sort((a, b) => ((b.call_oi ?? 0) + (b.put_oi ?? 0)) - ((a.call_oi ?? 0) + (a.put_oi ?? 0)))
            .slice(0, 6)
            .sort((a, b) => (a.strike ?? 0) - (b.strike ?? 0));
          return (
            <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '14px 16px' }}>
              <div style={{ fontSize: 11, color: '#64748b', marginBottom: 10 }}>
                Open interest by strike (top {topOi.length} by total OI, across all expiries) — an &quot;OI wall&quot; is a strike with unusually heavy interest on one side.
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid #1e293b' }}>
                    {['Strike', 'Call OI', 'Put OI'].map(h => (
                      <th key={h} style={{ padding: '4px 6px', textAlign: h === 'Strike' ? 'left' : 'right', color: '#475569', fontWeight: 500 }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {topOi.map(row => (
                    <tr key={row.strike} style={{ borderBottom: '1px solid #0f172a' }}>
                      <td style={{ padding: '5px 6px', color: '#e2e8f0', fontVariantNumeric: 'tabular-nums' }}>${row.strike?.toFixed(2)}</td>
                      <td style={{ padding: '5px 6px', textAlign: 'right', color: '#4ade80', fontVariantNumeric: 'tabular-nums' }}>{row.call_oi != null ? row.call_oi.toLocaleString() : '—'}</td>
                      <td style={{ padding: '5px 6px', textAlign: 'right', color: '#f87171', fontVariantNumeric: 'tabular-nums' }}>{row.put_oi != null ? row.put_oi.toLocaleString() : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        })()}

        {hasNope && (() => {
          // AUD-NOPE: real, delta-weighted directional pressure -- genuinely different
          // construction from levelColor()'s own compute_options_pressure_score() (premium/
          // volume-ratio based). Not bounded to a fixed range by UW's own spec, but real
          // readings cluster within roughly [-1, 1] -- clamp only the VISUAL bar position,
          // never the displayed number itself.
          const n = gex!.nope!.nope!;
          const isBullish = n > 0;
          const barPct = Math.max(0, Math.min(100, 50 + n * 50));
          return (
            <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '14px 16px' }}>
              <div style={{ fontSize: 11, color: '#64748b', marginBottom: 10 }}>
                NOPE — real-time, delta-weighted options pressure (updates roughly every minute).
                {gex!.nope!.timestamp && <span style={{ color: '#334155' }}> As of {new Date(gex!.nope!.timestamp).toLocaleTimeString()}.</span>}
              </div>
              <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 8 }}>
                <span style={{ fontSize: 11, color: isBullish ? '#4ade80' : '#f87171', fontWeight: 700 }}>
                  {isBullish ? 'Bullish pressure' : 'Bearish pressure'}
                </span>
                <span style={{ fontSize: 15, fontWeight: 700, color: '#e2e8f0', fontVariantNumeric: 'tabular-nums' }}>{n.toFixed(3)}</span>
              </div>
              <div style={{ position: 'relative', height: 6, borderRadius: 3, background: '#1e293b', marginBottom: 10 }}>
                <div style={{ position: 'absolute', left: '50%', top: 0, bottom: 0, width: 1, background: '#334155' }} />
                <div
                  style={{
                    position: 'absolute', top: 0, bottom: 0, borderRadius: 3,
                    background: isBullish ? '#4ade80' : '#f87171',
                    left: isBullish ? '50%' : `${barPct}%`,
                    right: isBullish ? `${100 - barPct}%` : '50%',
                  }}
                />
              </div>
              {gex!.nope!.nope_fill != null && (
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: '#64748b' }}>
                  <span>Fill-weighted variant</span>
                  <span style={{ fontVariantNumeric: 'tabular-nums' }}>{gex!.nope!.nope_fill.toFixed(3)}</span>
                </div>
              )}
            </div>
          );
        })()}

        {hasExpirations && (
          <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '14px 16px' }}>
            <div style={{ fontSize: 11, color: '#64748b', marginBottom: 10 }}>
              Open interest concentration by expiration — relative to the other expiries shown
              here (no historical baseline exists to compare against).
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid #1e293b' }}>
                  {['Expiry', 'Total OI', 'P/C Ratio', 'Level'].map(h => (
                    <th key={h} style={{ padding: '4px 6px', textAlign: h === 'Expiry' ? 'left' : 'right', color: '#475569', fontWeight: 500 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {expirations!.expirations!.map(row => (
                  <tr key={row.expiry} style={{ borderBottom: '1px solid #0f172a' }}>
                    <td style={{ padding: '5px 6px', color: '#e2e8f0' }}>{row.expiry}</td>
                    <td style={{ padding: '5px 6px', textAlign: 'right', color: '#94a3b8', fontVariantNumeric: 'tabular-nums' }}>{row.total_oi.toLocaleString()}</td>
                    <td style={{ padding: '5px 6px', textAlign: 'right', color: '#94a3b8', fontVariantNumeric: 'tabular-nums' }}>{row.put_call_oi_ratio != null ? row.put_call_oi_ratio.toFixed(2) : '—'}</td>
                    <td style={{ padding: '5px 6px', textAlign: 'right' }}>
                      <span style={{ fontWeight: 700, color: levelColor(row.level), textTransform: 'capitalize' }}>
                        {row.level} <span style={{ color: '#475569', fontWeight: 400 }}>({row.concentration_pct.toFixed(0)}%)</span>
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {hasDarkPool && (
          <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '14px 16px' }}>
            <div style={{ fontSize: 11, color: '#64748b', marginBottom: 10 }}>
              Recent large off-exchange block prints — a measured fact (real size, real price),
              not a directional signal. <a href="/dark-pool-guide" style={{ color: '#38bdf8' }}>What is dark pool trading?</a>
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid #1e293b' }}>
                  {['Size', 'Price', 'Premium', 'Venue'].map(h => (
                    <th key={h} style={{ padding: '4px 6px', textAlign: h === 'Size' ? 'left' : 'right', color: '#475569', fontWeight: 500 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {darkPool!.prints!.slice(0, 8).map((p, i) => (
                  <tr key={`${p.executed_at}-${i}`} style={{ borderBottom: '1px solid #0f172a' }}>
                    <td style={{ padding: '5px 6px', color: '#e2e8f0', fontVariantNumeric: 'tabular-nums' }}>{p.size != null ? p.size.toLocaleString() : '—'}</td>
                    <td style={{ padding: '5px 6px', textAlign: 'right', color: '#94a3b8', fontVariantNumeric: 'tabular-nums' }}>{p.price != null ? `$${p.price.toFixed(2)}` : '—'}</td>
                    <td style={{ padding: '5px 6px', textAlign: 'right', color: '#38bdf8', fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{p.premium != null ? `$${p.premium.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '—'}</td>
                    <td style={{ padding: '5px 6px', textAlign: 'right', color: '#64748b' }}>{p.venue || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
