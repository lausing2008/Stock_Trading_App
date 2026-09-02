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
            </div>
          </div>
        )}

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
