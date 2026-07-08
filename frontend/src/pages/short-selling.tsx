import { useState, useMemo } from 'react';
import useSWR from 'swr';
import Link from 'next/link';
import { api, type ShortInterestRow } from '@/lib/api';

type SortKey = 'short_percent_of_float' | 'short_ratio' | 'market_cap' | 'symbol';

function fmtPct(v: number | null): string {
  if (v == null) return '—';
  return v.toFixed(1) + '%';
}

function fmtRatio(v: number | null): string {
  if (v == null) return '—';
  return v.toFixed(1) + 'd';
}

function fmtCap(v: number | null): string {
  if (v == null) return '—';
  if (v >= 1e12) return `$${(v / 1e12).toFixed(1)}T`;
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  return `$${v.toLocaleString()}`;
}

function shortBadge(pct: number | null): React.ReactNode {
  if (pct == null) return null;
  if (pct >= 20) return (
    <span style={{ fontSize: '10px', fontWeight: 700, padding: '2px 7px', borderRadius: '4px', background: 'rgba(239,68,68,0.15)', color: '#ef4444' }}>
      {pct.toFixed(1)}%
    </span>
  );
  if (pct >= 10) return (
    <span style={{ fontSize: '10px', fontWeight: 700, padding: '2px 7px', borderRadius: '4px', background: 'rgba(249,115,22,0.15)', color: '#f97316' }}>
      {pct.toFixed(1)}%
    </span>
  );
  return (
    <span style={{ fontSize: '10px', fontWeight: 600, padding: '2px 7px', borderRadius: '4px', color: '#94a3b8' }}>
      {pct.toFixed(1)}%
    </span>
  );
}

export default function ShortSellingPage() {
  const [sort, setSort] = useState<{ key: SortKey; dir: 'asc' | 'desc' }>({ key: 'short_percent_of_float', dir: 'desc' });
  const [market, setMarket] = useState<'All' | 'US' | 'HK'>('All');
  const [search, setSearch] = useState('');

  const { data, error, isLoading } = useSWR<ShortInterestRow[]>(
    'short-interest',
    () => api.shortInterest(),
    { revalidateOnFocus: false },
  );

  const rows = useMemo(() => {
    let items = data ?? [];
    if (market !== 'All') items = items.filter(r => r.market === market);
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      items = items.filter(r => r.symbol.toLowerCase().includes(q) || r.name.toLowerCase().includes(q));
    }
    return [...items].sort((a, b) => {
      let av: number | string, bv: number | string;
      if (sort.key === 'symbol') { av = a.symbol; bv = b.symbol; }
      else if (sort.key === 'short_percent_of_float') { av = a.short_percent_of_float ?? -1; bv = b.short_percent_of_float ?? -1; }
      else if (sort.key === 'short_ratio') { av = a.short_ratio ?? -1; bv = b.short_ratio ?? -1; }
      else { av = a.market_cap ?? -1; bv = b.market_cap ?? -1; }
      if (typeof av === 'string') return sort.dir === 'asc' ? av.localeCompare(bv as string) : (bv as string).localeCompare(av);
      return sort.dir === 'asc' ? av - (bv as number) : (bv as number) - av;
    });
  }, [data, market, search, sort]);

  function toggleSort(key: SortKey) {
    setSort(s => s.key === key ? { key, dir: s.dir === 'desc' ? 'asc' : 'desc' } : { key, dir: 'desc' });
  }

  const thStyle = (col: SortKey, right = false): React.CSSProperties => ({
    padding: '9px 14px',
    textAlign: right ? 'right' : 'left',
    fontSize: '10px',
    fontWeight: 700,
    textTransform: 'uppercase',
    letterSpacing: '0.06em',
    color: sort.key === col ? '#a78bfa' : '#475569',
    borderBottom: '1px solid #1e293b',
    background: '#080f1e',
    cursor: 'pointer',
    userSelect: 'none',
    whiteSpace: 'nowrap',
  });

  const indicator = (col: SortKey) => sort.key === col ? (sort.dir === 'desc' ? ' ↓' : ' ↑') : '';

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '20px 16px' }}>
      <div style={{ marginBottom: '20px' }}>
        <h1 style={{ fontSize: '22px', fontWeight: 800, color: '#e2e8f0', marginBottom: '4px' }}>
          Short Interest Dashboard
        </h1>
        <p style={{ fontSize: '12px', color: '#475569' }}>
          Stocks ranked by short % of float — sourced from fundamentals data
        </p>
      </div>

      <div style={{ display: 'flex', gap: '10px', marginBottom: '18px', flexWrap: 'wrap', alignItems: 'center' }}>
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search symbol or name…"
          style={{ flex: '1 1 160px', padding: '7px 11px', borderRadius: '6px', border: '1px solid #1e293b', background: '#0f172a', color: '#e2e8f0', fontSize: '12px', outline: 'none' }}
        />
        <div style={{ display: 'flex', gap: '6px' }}>
          {(['All', 'US', 'HK'] as const).map(m => (
            <button key={m} onClick={() => setMarket(m)}
              style={{ padding: '5px 12px', borderRadius: '6px', fontSize: '12px', cursor: 'pointer', border: '1px solid #1e293b', background: market === m ? '#334155' : 'transparent', color: market === m ? '#e2e8f0' : '#64748b' }}
            >{m}</button>
          ))}
        </div>
      </div>

      {isLoading && (
        <div style={{ color: '#475569', fontSize: '13px', padding: '40px 0', textAlign: 'center' }}>Loading short interest data…</div>
      )}
      {error && (
        <div style={{ color: '#f87171', fontSize: '13px', padding: '20px 0' }}>Failed to load short interest data.</div>
      )}

      {!isLoading && !error && rows.length === 0 && (
        <div style={{ color: '#475569', fontSize: '13px', padding: '40px 0', textAlign: 'center' }}>
          No short interest data available.
        </div>
      )}

      {rows.length > 0 && (
        <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: '12px', overflow: 'hidden' }}>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
              <thead>
                <tr>
                  <th onClick={() => toggleSort('symbol')} style={thStyle('symbol')}>
                    Symbol{indicator('symbol')}
                  </th>
                  <th style={{ padding: '9px 14px', fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#475569', borderBottom: '1px solid #1e293b', background: '#080f1e', whiteSpace: 'nowrap' }}>
                    Market
                  </th>
                  <th onClick={() => toggleSort('short_percent_of_float')} style={thStyle('short_percent_of_float', true)}>
                    Short % Float{indicator('short_percent_of_float')}
                  </th>
                  <th onClick={() => toggleSort('short_ratio')} style={thStyle('short_ratio', true)}>
                    Short Ratio{indicator('short_ratio')}
                  </th>
                  <th onClick={() => toggleSort('market_cap')} style={thStyle('market_cap', true)}>
                    Market Cap{indicator('market_cap')}
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr
                    key={r.symbol}
                    style={{
                      borderBottom: '1px solid rgba(30,41,59,0.5)',
                      background: i % 2 === 0 ? '#080f1e' : '#09101f',
                      cursor: 'pointer',
                    }}
                    onMouseEnter={e => (e.currentTarget.style.background = '#0f1e35')}
                    onMouseLeave={e => (e.currentTarget.style.background = i % 2 === 0 ? '#080f1e' : '#09101f')}
                  >
                    <td style={{ padding: '10px 14px', whiteSpace: 'nowrap' }}>
                      <Link href={`/stock/${r.symbol}`} style={{ color: '#818cf8', fontWeight: 700, textDecoration: 'none', fontSize: '13px' }}>
                        {r.symbol}
                      </Link>
                      <div style={{ fontSize: '10px', color: '#475569', maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</div>
                    </td>
                    <td style={{ padding: '10px 14px' }}>
                      <span style={{ fontSize: '10px', fontWeight: 600, padding: '2px 5px', borderRadius: '3px', background: '#1e293b', color: '#64748b' }}>
                        {r.market}
                      </span>
                    </td>
                    <td style={{ padding: '10px 14px', textAlign: 'right' }}>
                      {shortBadge(r.short_percent_of_float)}
                    </td>
                    <td style={{ padding: '10px 14px', textAlign: 'right', color: '#94a3b8' }}>
                      {fmtRatio(r.short_ratio)}
                    </td>
                    <td style={{ padding: '10px 14px', textAlign: 'right', color: '#94a3b8' }}>
                      {fmtCap(r.market_cap)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ padding: '10px 16px', fontSize: '11px', color: '#334155', borderTop: '1px solid #1e293b' }}>
            {rows.length} stocks · short % float &gt; 20% shown in red, 10–20% in orange
          </div>
        </div>
      )}
    </div>
  );
}
