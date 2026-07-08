import { useState, useMemo } from 'react';
import useSWR from 'swr';
import Link from 'next/link';
import { api, type SectorGroup, type SectorStock } from '@/lib/api';

function heatColor(pct: number | null): string {
  if (pct == null) return '#1e293b';
  const intensity = Math.min(Math.abs(pct) / 4, 1);
  if (pct > 0) {
    const g = Math.round(34 + intensity * (74 - 34));
    const b = Math.round(intensity * 20);
    return `rgba(${Math.round(20 + intensity * 14)},${g + 123},${b + 60},${0.15 + intensity * 0.55})`;
  } else {
    const r = Math.round(120 + intensity * 119);
    return `rgba(${r},${Math.round(30 + intensity * 10)},${Math.round(30 + intensity * 14)},${0.15 + intensity * 0.55})`;
  }
}

function textColorFor(pct: number | null): string {
  if (pct == null) return '#475569';
  return pct >= 0 ? '#4ade80' : '#f87171';
}

function fmt(pct: number | null): string {
  if (pct == null) return '—';
  return (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%';
}

function fmtPrice(p: number | null): string {
  if (p == null) return '—';
  return p >= 100 ? p.toFixed(2) : p.toPrecision(4);
}

function fmtMktCap(n: number | null): string {
  if (n == null) return '';
  if (n >= 1e12) return `$${(n / 1e12).toFixed(1)}T`;
  if (n >= 1e9) return `$${(n / 1e9).toFixed(0)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(0)}M`;
  return `$${n.toFixed(0)}`;
}

type FilteredGroup = SectorGroup & {
  avg_change_pct: number | null;
  stock_count: number;
  total_mkt_cap: number | null;
};

export default function HeatmapPage() {
  const { data, error, isLoading } = useSWR<SectorGroup[]>(
    'sector-performance',
    () => api.sectorPerformance(),
    { refreshInterval: 60_000 },
  );

  const [selected, setSelected] = useState<string | null>(null);
  const [market, setMarket] = useState<'All' | 'US' | 'HK'>('All');
  const [viewMode, setViewMode] = useState<'grid' | 'treemap'>('treemap');

  const filtered = useMemo<FilteredGroup[]>(() => {
    if (!data) return [];
    const groups = market === 'All' ? data : data.map(g => ({
      ...g,
      stocks: g.stocks.filter(s => s.market === market),
    })).filter(g => g.stocks.length > 0);

    return groups.map(g => {
      const changes = g.stocks.map(s => s.change_pct).filter((x): x is number => x != null);
      const caps = g.stocks.map(s => s.market_cap).filter((x): x is number => x != null);
      return {
        ...g,
        avg_change_pct: changes.length ? parseFloat((changes.reduce((a, b) => a + b, 0) / changes.length).toFixed(3)) : null,
        stock_count: g.stocks.length,
        total_mkt_cap: caps.length ? caps.reduce((a, b) => a + b, 0) : null,
      };
    }).sort((a, b) => (b.avg_change_pct ?? -999) - (a.avg_change_pct ?? -999));
  }, [data, market]);

  const selectedGroup = selected ? filtered.find(g => g.sector === selected) : null;

  const totalMktCap = useMemo(() =>
    filtered.reduce((s, g) => s + (g.total_mkt_cap ?? 0), 0),
    [filtered]
  );

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px', flexWrap: 'wrap', gap: '12px' }}>
        <div>
          <h1 style={{ fontSize: '22px', fontWeight: 800, color: '#e2e8f0', marginBottom: '4px' }}>Sector Heat Map</h1>
          <p style={{ fontSize: '12px', color: '#475569' }}>
            {viewMode === 'treemap'
              ? 'Market-cap weighted treemap — tile size = relative market cap'
              : 'Live intraday performance by sector — color intensity = magnitude of move'}
          </p>
        </div>
        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
          {(['All', 'US', 'HK'] as const).map(m => (
            <button
              key={m}
              onClick={() => setMarket(m)}
              style={{
                padding: '5px 14px', borderRadius: '6px', fontSize: '12px', cursor: 'pointer',
                border: '1px solid #1e293b', fontWeight: market === m ? 700 : 400,
                background: market === m ? '#4f46e5' : 'transparent',
                color: market === m ? '#fff' : '#64748b',
              }}
            >{m}</button>
          ))}
          <div style={{ width: '1px', background: '#1e293b', margin: '0 4px' }} />
          {(['treemap', 'grid'] as const).map(v => (
            <button
              key={v}
              onClick={() => setViewMode(v)}
              style={{
                padding: '5px 14px', borderRadius: '6px', fontSize: '12px', cursor: 'pointer',
                border: '1px solid #1e293b', fontWeight: viewMode === v ? 700 : 400,
                background: viewMode === v ? '#0f766e' : 'transparent',
                color: viewMode === v ? '#fff' : '#64748b',
              }}
            >{v === 'treemap' ? '⊞ Treemap' : '⊟ Grid'}</button>
          ))}
        </div>
      </div>

      {isLoading && <div style={{ color: '#475569', fontSize: '13px', padding: '40px 0', textAlign: 'center' }}>Loading sector data…</div>}
      {error && <div style={{ color: '#f87171', fontSize: '13px' }}>Failed to load sector data.</div>}

      {filtered.length > 0 && (
        <>
          {viewMode === 'treemap' ? (
            /* ── Treemap view ── */
            <div style={{ marginBottom: '28px' }}>
              {/* Sector row — proportional widths */}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', marginBottom: '4px' }}>
                {filtered.map(g => {
                  const weight = totalMktCap > 0 && g.total_mkt_cap
                    ? Math.max(g.total_mkt_cap / totalMktCap, 0.04)
                    : 1 / filtered.length;
                  const widthPct = (weight * 100).toFixed(2);
                  return (
                    <button
                      key={g.sector}
                      onClick={() => setSelected(selected === g.sector ? null : g.sector)}
                      style={{
                        width: `calc(${widthPct}% - 4px)`,
                        minWidth: '80px',
                        height: '90px',
                        background: heatColor(g.avg_change_pct),
                        border: `1px solid ${selected === g.sector ? '#818cf8' : 'rgba(148,163,184,0.08)'}`,
                        borderRadius: '8px',
                        padding: '10px 8px',
                        cursor: 'pointer',
                        textAlign: 'left',
                        transition: 'all 0.15s',
                        outline: 'none',
                        boxShadow: selected === g.sector ? '0 0 0 2px #818cf8' : 'none',
                        overflow: 'hidden',
                        flexShrink: 0,
                        flexGrow: 0,
                      }}
                    >
                      <div style={{ fontSize: '9px', color: '#94a3b8', fontWeight: 700, marginBottom: '4px', textTransform: 'uppercase', letterSpacing: '0.04em', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {g.sector}
                      </div>
                      <div style={{ fontSize: '18px', fontWeight: 800, color: textColorFor(g.avg_change_pct), fontVariantNumeric: 'tabular-nums', lineHeight: 1.2 }}>
                        {fmt(g.avg_change_pct)}
                      </div>
                      {g.total_mkt_cap && (
                        <div style={{ fontSize: '9px', color: '#475569', marginTop: '4px' }}>
                          {fmtMktCap(g.total_mkt_cap)}
                        </div>
                      )}
                    </button>
                  );
                })}
              </div>

              {/* Expanded sector — proportional stock tiles */}
              {selectedGroup && (
                <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: '12px', overflow: 'hidden', marginTop: '8px' }}>
                  <div style={{ padding: '12px 16px', borderBottom: '1px solid #1e293b', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <div>
                      <span style={{ fontSize: '14px', fontWeight: 700, color: '#e2e8f0' }}>{selectedGroup.sector}</span>
                      <span style={{ marginLeft: '10px', fontSize: '13px', color: textColorFor(selectedGroup.avg_change_pct), fontWeight: 700 }}>
                        {fmt(selectedGroup.avg_change_pct)}
                      </span>
                      {selectedGroup.total_mkt_cap && (
                        <span style={{ marginLeft: '10px', fontSize: '11px', color: '#475569' }}>
                          {fmtMktCap(selectedGroup.total_mkt_cap)} total cap
                        </span>
                      )}
                    </div>
                    <button onClick={() => setSelected(null)} style={{ background: 'none', border: 'none', color: '#475569', cursor: 'pointer', fontSize: '18px', lineHeight: 1 }}>×</button>
                  </div>
                  <div style={{ padding: '12px', display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                    {(() => {
                      const stocks = [...selectedGroup.stocks].sort((a, b) => (b.market_cap ?? 0) - (a.market_cap ?? 0));
                      const sectorCap = stocks.reduce((s, st) => s + (st.market_cap ?? 0), 0);
                      return stocks.map((s: SectorStock) => {
                        const w = sectorCap > 0 && s.market_cap
                          ? Math.max(s.market_cap / sectorCap, 0.03)
                          : 1 / stocks.length;
                        return (
                          <Link
                            key={s.symbol}
                            href={`/stock/${s.symbol}`}
                            style={{
                              width: `calc(${(w * 100).toFixed(2)}% - 4px)`,
                              minWidth: '64px',
                              height: '70px',
                              background: heatColor(s.change_pct),
                              border: '1px solid rgba(148,163,184,0.08)',
                              borderRadius: '6px',
                              padding: '8px 6px',
                              textDecoration: 'none',
                              display: 'flex',
                              flexDirection: 'column',
                              justifyContent: 'space-between',
                              overflow: 'hidden',
                              flexShrink: 0,
                              flexGrow: 0,
                              transition: 'border-color 0.15s',
                            }}
                          >
                            <div style={{ fontSize: '10px', fontWeight: 700, color: '#c7d2fe', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.symbol}</div>
                            <div>
                              <div style={{ fontSize: '11px', fontWeight: 700, color: textColorFor(s.change_pct), fontVariantNumeric: 'tabular-nums' }}>{fmt(s.change_pct)}</div>
                              {s.market_cap && <div style={{ fontSize: '8px', color: '#475569' }}>{fmtMktCap(s.market_cap)}</div>}
                            </div>
                          </Link>
                        );
                      });
                    })()}
                  </div>
                </div>
              )}
            </div>
          ) : (
            /* ── Equal grid view ── */
            <>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: '10px', marginBottom: '28px' }}>
                {filtered.map(g => (
                  <button
                    key={g.sector}
                    onClick={() => setSelected(selected === g.sector ? null : g.sector)}
                    style={{
                      background: heatColor(g.avg_change_pct),
                      border: `1px solid ${selected === g.sector ? '#818cf8' : 'rgba(148,163,184,0.1)'}`,
                      borderRadius: '10px',
                      padding: '16px 14px',
                      cursor: 'pointer',
                      textAlign: 'left',
                      transition: 'all 0.15s',
                      outline: 'none',
                      boxShadow: selected === g.sector ? '0 0 0 2px #818cf8' : 'none',
                    }}
                  >
                    <div style={{ fontSize: '11px', color: '#94a3b8', fontWeight: 600, marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                      {g.sector}
                    </div>
                    <div style={{ fontSize: '24px', fontWeight: 800, color: textColorFor(g.avg_change_pct), fontVariantNumeric: 'tabular-nums' }}>
                      {fmt(g.avg_change_pct)}
                    </div>
                    <div style={{ fontSize: '10px', color: '#475569', marginTop: '4px' }}>
                      {g.stock_count} stock{g.stock_count !== 1 ? 's' : ''}
                    </div>
                  </button>
                ))}
              </div>

              {/* Expanded sector detail — table */}
              {selectedGroup && (
                <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: '12px', overflow: 'hidden', marginBottom: '20px' }}>
                  <div style={{ padding: '14px 18px', borderBottom: '1px solid #1e293b', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <div>
                      <span style={{ fontSize: '14px', fontWeight: 700, color: '#e2e8f0' }}>{selectedGroup.sector}</span>
                      <span style={{ marginLeft: '10px', fontSize: '13px', color: textColorFor(selectedGroup.avg_change_pct), fontWeight: 700 }}>
                        {fmt(selectedGroup.avg_change_pct)}
                      </span>
                    </div>
                    <button onClick={() => setSelected(null)} style={{ background: 'none', border: 'none', color: '#475569', cursor: 'pointer', fontSize: '18px', lineHeight: 1 }}>×</button>
                  </div>
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                      <thead>
                        <tr style={{ background: '#080f1e' }}>
                          {['Symbol', 'Name', 'Market', 'Price', 'Change'].map(h => (
                            <th key={h} style={{ padding: '8px 14px', textAlign: h === 'Change' || h === 'Price' ? 'right' : 'left', color: '#475569', fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: '1px solid #1e293b', whiteSpace: 'nowrap' }}>
                              {h}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {[...selectedGroup.stocks]
                          .sort((a, b) => (b.change_pct ?? -999) - (a.change_pct ?? -999))
                          .map((s: SectorStock) => (
                            <tr key={s.symbol} style={{ borderBottom: '1px solid rgba(30,41,59,0.5)' }}>
                              <td style={{ padding: '9px 14px', whiteSpace: 'nowrap' }}>
                                <Link href={`/stock/${s.symbol}`} style={{ color: '#818cf8', fontWeight: 700, textDecoration: 'none', fontSize: '13px' }}>
                                  {s.symbol}
                                </Link>
                              </td>
                              <td style={{ padding: '9px 14px', color: '#94a3b8', maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                {s.name}
                              </td>
                              <td style={{ padding: '9px 14px' }}>
                                <span style={{ fontSize: '10px', color: '#64748b', background: 'rgba(30,41,59,0.8)', padding: '2px 6px', borderRadius: '4px' }}>
                                  {s.market}
                                </span>
                              </td>
                              <td style={{ padding: '9px 14px', textAlign: 'right', color: '#e2e8f0', fontVariantNumeric: 'tabular-nums' }}>
                                {fmtPrice(s.price)}
                              </td>
                              <td style={{ padding: '9px 14px', textAlign: 'right', fontWeight: 700, fontVariantNumeric: 'tabular-nums', color: textColorFor(s.change_pct) }}>
                                {fmt(s.change_pct)}
                              </td>
                            </tr>
                          ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </>
          )}

          {/* Summary bar */}
          <div style={{ padding: '12px 16px', background: 'rgba(255,255,255,0.02)', border: '1px solid #1e293b', borderRadius: '8px', display: 'flex', gap: '20px', flexWrap: 'wrap', fontSize: '12px', color: '#64748b' }}>
            <span>
              <span style={{ color: '#4ade80', fontWeight: 700 }}>
                {filtered.filter(g => (g.avg_change_pct ?? 0) > 0).length}
              </span> sectors up
            </span>
            <span>
              <span style={{ color: '#f87171', fontWeight: 700 }}>
                {filtered.filter(g => (g.avg_change_pct ?? 0) < 0).length}
              </span> sectors down
            </span>
            <span>
              {filtered.reduce((acc, g) => acc + g.stock_count, 0)} total stocks · refreshes every 60s
            </span>
            {totalMktCap > 0 && (
              <span>{fmtMktCap(totalMktCap)} total tracked cap</span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
