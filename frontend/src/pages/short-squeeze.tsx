import { useState, useMemo } from 'react';
import useSWR from 'swr';
import Link from 'next/link';
import { api, type SqueezeCandidate } from '@/lib/api';

function fmtShares(n: number | null): string {
  if (n == null) return '—';
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)}K`;
  return n.toString();
}

function fmtChg(v: number | null): string {
  if (v == null) return '—';
  return (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
}

function fmtScore(v: number | null): string {
  if (v == null) return '—';
  return v.toFixed(1);
}

function shortBg(pct: number): string {
  if (pct >= 40) return 'rgba(239,68,68,0.18)';
  if (pct >= 25) return 'rgba(249,115,22,0.15)';
  if (pct >= 15) return 'rgba(250,204,21,0.1)';
  return 'rgba(100,116,139,0.08)';
}

function shortColor(pct: number): string {
  if (pct >= 40) return '#ef4444';
  if (pct >= 25) return '#f97316';
  if (pct >= 15) return '#facc15';
  return '#94a3b8';
}

type SortKey = 'short_pct' | 'change_pct' | 'momentum' | 'k_score' | 'short_ratio';

export default function ShortSqueezePage() {
  const [minShortFloat, setMinShortFloat] = useState(10);
  const [market, setMarket] = useState<'All' | 'US' | 'HK'>('All');
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<{ key: SortKey; dir: 'asc' | 'desc' }>({ key: 'short_pct', dir: 'desc' });

  const { data, error, isLoading, mutate } = useSWR<SqueezeCandidate[]>(
    `short-squeeze-${minShortFloat}`,
    () => api.shortSqueeze(minShortFloat),
    { revalidateOnFocus: false },
  );

  const rows = useMemo(() => {
    let items = data ?? [];
    if (market !== 'All') items = items.filter(i => i.market === market);
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      items = items.filter(i => i.symbol.toLowerCase().includes(q) || i.name.toLowerCase().includes(q));
    }
    return [...items].sort((a, b) => {
      const getVal = (x: SqueezeCandidate): number => {
        if (sort.key === 'short_pct') return x.short_percent_of_float;
        if (sort.key === 'change_pct') return x.change_pct ?? -999;
        if (sort.key === 'momentum') return x.momentum_score ?? -999;
        if (sort.key === 'k_score') return x.k_score ?? -999;
        if (sort.key === 'short_ratio') return x.short_ratio ?? -999;
        return 0;
      };
      const diff = getVal(b) - getVal(a);
      return sort.dir === 'desc' ? diff : -diff;
    });
  }, [data, market, search, sort]);

  function toggleSort(key: SortKey) {
    setSort(s => s.key === key ? { key, dir: s.dir === 'desc' ? 'asc' : 'desc' } : { key, dir: 'desc' });
  }

  function SortTh({ label, col, right }: { label: string; col: SortKey; right?: boolean }) {
    const active = sort.key === col;
    return (
      <th onClick={() => toggleSort(col)} style={{ padding: '9px 14px', textAlign: right ? 'right' : 'left', color: active ? '#a78bfa' : '#475569', fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: '1px solid #1e293b', background: '#080f1e', cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap' }}>
        {label} {active ? (sort.dir === 'desc' ? '↓' : '↑') : ''}
      </th>
    );
  }

  // Squeeze score: high short float + positive momentum = best candidates
  const topCandidates = rows.filter(r => r.momentum_score != null && r.momentum_score > 50 && r.short_percent_of_float >= 15);

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px', flexWrap: 'wrap', gap: '12px' }}>
        <div>
          <h1 style={{ fontSize: '22px', fontWeight: 800, color: '#e2e8f0', marginBottom: '4px' }}>Short Squeeze Scanner</h1>
          <p style={{ fontSize: '12px', color: '#475569' }}>High short interest stocks with upward momentum — classic squeeze setup</p>
        </div>
        <button
          onClick={() => mutate()}
          style={{ padding: '6px 14px', borderRadius: '6px', border: '1px solid #1e293b', background: 'transparent', color: '#64748b', fontSize: '12px', cursor: 'pointer' }}
        >↻ Refresh</button>
      </div>

      {/* Alert banner for top candidates */}
      {!isLoading && topCandidates.length > 0 && (
        <div style={{ background: 'rgba(239,68,68,0.06)', border: '1px solid rgba(239,68,68,0.2)', borderRadius: '10px', padding: '12px 16px', marginBottom: '18px', display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
          <span style={{ fontSize: '13px', fontWeight: 700, color: '#f87171' }}>🔥 {topCandidates.length} Prime Candidate{topCandidates.length > 1 ? 's' : ''}</span>
          <span style={{ fontSize: '12px', color: '#94a3b8' }}>High short interest + bullish momentum:</span>
          {topCandidates.slice(0, 5).map(c => (
            <Link key={c.symbol} href={`/stock/${c.symbol}`} style={{ fontSize: '11px', fontWeight: 700, color: '#f87171', background: 'rgba(239,68,68,0.1)', padding: '2px 8px', borderRadius: '4px', textDecoration: 'none' }}>
              {c.symbol} {c.short_percent_of_float.toFixed(0)}% short
            </Link>
          ))}
        </div>
      )}

      {/* Controls */}
      <div style={{ display: 'flex', gap: '10px', marginBottom: '18px', flexWrap: 'wrap', alignItems: 'center' }}>
        <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search symbol or name…"
          style={{ flex: '1 1 160px', padding: '7px 11px', borderRadius: '6px', border: '1px solid #1e293b', background: '#0f172a', color: '#e2e8f0', fontSize: '12px', outline: 'none' }} />
        <div style={{ display: 'flex', gap: '6px' }}>
          {(['All', 'US', 'HK'] as const).map(m => (
            <button key={m} onClick={() => setMarket(m)}
              style={{ padding: '5px 12px', borderRadius: '6px', fontSize: '12px', cursor: 'pointer', border: '1px solid #1e293b', background: market === m ? '#334155' : 'transparent', color: market === m ? '#e2e8f0' : '#64748b' }}
            >{m}</button>
          ))}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <label style={{ fontSize: '11px', color: '#64748b', whiteSpace: 'nowrap' }}>Min short %:</label>
          <select value={minShortFloat} onChange={e => setMinShortFloat(Number(e.target.value))}
            style={{ padding: '6px 10px', borderRadius: '6px', border: '1px solid #1e293b', background: '#0f172a', color: '#94a3b8', fontSize: '12px', cursor: 'pointer' }}>
            {[5, 10, 15, 20, 25, 30].map(v => <option key={v} value={v}>{v}%+</option>)}
          </select>
        </div>
      </div>

      {isLoading && <div style={{ color: '#475569', fontSize: '13px', padding: '40px 0', textAlign: 'center' }}>Scanning for squeeze candidates…</div>}
      {error && <div style={{ color: '#f87171', fontSize: '13px' }}>Failed to load scanner data.</div>}
      {!isLoading && !error && rows.length === 0 && (
        <div style={{ color: '#475569', fontSize: '13px', padding: '40px 0', textAlign: 'center' }}>
          No stocks found with {minShortFloat}%+ short interest.<br />
          <span style={{ fontSize: '11px' }}>Short interest data comes from cached fundamentals — visit stock pages to populate.</span>
        </div>
      )}

      {rows.length > 0 && (
        <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: '12px', overflow: 'hidden' }}>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
              <thead>
                <tr>
                  <th style={{ padding: '9px 14px', textAlign: 'left', color: '#475569', fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: '1px solid #1e293b', background: '#080f1e', whiteSpace: 'nowrap' }}>Symbol</th>
                  <SortTh label="Short %" col="short_pct" right />
                  <SortTh label="Days to Cover" col="short_ratio" right />
                  <th style={{ padding: '9px 14px', textAlign: 'right', color: '#475569', fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: '1px solid #1e293b', background: '#080f1e', whiteSpace: 'nowrap' }}>Shares Short</th>
                  <th style={{ padding: '9px 14px', textAlign: 'right', color: '#475569', fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: '1px solid #1e293b', background: '#080f1e', whiteSpace: 'nowrap' }}>Price</th>
                  <SortTh label="Change" col="change_pct" right />
                  <SortTh label="Momentum" col="momentum" right />
                  <SortTh label="K-Score" col="k_score" right />
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => {
                  const isPrime = r.momentum_score != null && r.momentum_score > 50 && r.short_percent_of_float >= 15;
                  return (
                    <tr key={i} style={{ borderBottom: '1px solid rgba(30,41,59,0.5)', background: isPrime ? 'rgba(239,68,68,0.03)' : 'transparent' }}>
                      <td style={{ padding: '10px 14px', whiteSpace: 'nowrap' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                          {isPrime && <span style={{ fontSize: '8px', color: '#f87171' }}>🔥</span>}
                          <Link href={`/stock/${r.symbol}`} style={{ color: '#818cf8', fontWeight: 700, textDecoration: 'none', fontSize: '13px' }}>
                            {r.symbol}
                          </Link>
                        </div>
                        <div style={{ fontSize: '10px', color: '#475569', maxWidth: '140px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</div>
                        {r.sector && <div style={{ fontSize: '9px', color: '#334155' }}>{r.sector}</div>}
                      </td>
                      <td style={{ padding: '10px 14px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                        <span style={{ fontWeight: 800, fontSize: '13px', color: shortColor(r.short_percent_of_float), background: shortBg(r.short_percent_of_float), padding: '2px 8px', borderRadius: '5px', fontVariantNumeric: 'tabular-nums' }}>
                          {r.short_percent_of_float.toFixed(1)}%
                        </span>
                      </td>
                      <td style={{ padding: '10px 14px', textAlign: 'right', color: '#94a3b8', fontVariantNumeric: 'tabular-nums' }}>
                        {r.short_ratio != null ? `${r.short_ratio.toFixed(1)}d` : '—'}
                      </td>
                      <td style={{ padding: '10px 14px', textAlign: 'right', color: '#64748b', fontVariantNumeric: 'tabular-nums' }}>
                        <div>{fmtShares(r.shares_short)}</div>
                        {r.shares_short != null && r.shares_short_prior_month != null && (() => {
                          const rising = r.shares_short > r.shares_short_prior_month!;
                          const pctChg = Math.abs((r.shares_short - r.shares_short_prior_month!) / r.shares_short_prior_month! * 100);
                          return (
                            <div style={{ fontSize: '9px', color: rising ? '#f87171' : '#4ade80', fontWeight: 700 }}>
                              {rising ? '↑' : '↓'} {pctChg.toFixed(0)}% MoM
                            </div>
                          );
                        })()}
                      </td>
                      <td style={{ padding: '10px 14px', textAlign: 'right', color: '#e2e8f0', fontVariantNumeric: 'tabular-nums' }}>
                        {r.price != null ? (r.price >= 100 ? r.price.toFixed(2) : r.price.toPrecision(4)) : '—'}
                      </td>
                      <td style={{ padding: '10px 14px', textAlign: 'right', fontWeight: 700, fontVariantNumeric: 'tabular-nums', color: (r.change_pct ?? 0) >= 0 ? '#4ade80' : '#f87171' }}>
                        {fmtChg(r.change_pct)}
                      </td>
                      <td style={{ padding: '10px 14px', textAlign: 'right' }}>
                        {r.momentum_score != null ? (
                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: '5px' }}>
                            <div style={{ width: '32px', height: '4px', borderRadius: '2px', background: '#1e293b' }}>
                              <div style={{ width: `${Math.min(100, r.momentum_score)}%`, height: '100%', borderRadius: '2px', background: r.momentum_score > 60 ? '#22c55e' : r.momentum_score > 40 ? '#facc15' : '#ef4444' }} />
                            </div>
                            <span style={{ fontSize: '11px', color: '#94a3b8', fontVariantNumeric: 'tabular-nums' }}>{fmtScore(r.momentum_score)}</span>
                          </div>
                        ) : <span style={{ color: '#334155' }}>—</span>}
                      </td>
                      <td style={{ padding: '10px 14px', textAlign: 'right', color: '#94a3b8', fontVariantNumeric: 'tabular-nums' }}>
                        {r.k_score != null ? r.k_score.toFixed(1) : '—'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div style={{ padding: '10px 16px', fontSize: '11px', color: '#334155', borderTop: '1px solid #1e293b', display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: '6px' }}>
            <span>{rows.length} candidates · short interest from Yahoo Finance · momentum from K-Score model</span>
            <span style={{ color: '#1e293b' }}>🔥 = high short % + bullish momentum (prime squeeze candidate)</span>
          </div>
        </div>
      )}
    </div>
  );
}
