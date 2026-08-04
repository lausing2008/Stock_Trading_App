import { useState, useMemo } from 'react';
import useSWR from 'swr';
import Link from 'next/link';
import { api, type MarketScreenerRow } from '@/lib/api';
import { getSession } from '@/lib/auth';

function fmtNum(n: number | null): string {
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

type SortKey = 'change_pct' | 'rvol' | 'volume' | 'market_cap';

export default function MarketScreenerPage() {
  const [search, setSearch] = useState('');
  const [hideTracked, setHideTracked] = useState(false);
  const [sort, setSort] = useState<{ key: SortKey; dir: 'asc' | 'desc' }>({ key: 'change_pct', dir: 'desc' });
  const [addStatus, setAddStatus] = useState<Record<string, string>>({});
  const [addBusy, setAddBusy] = useState<Record<string, boolean>>({});

  const session = getSession();
  const isAdmin = session?.role === 'admin';

  const { data, error, isLoading, mutate } = useSWR(
    session ? 'market-screener' : null,
    () => api.marketScreener(),
    { revalidateOnFocus: false },
  );

  const rows = useMemo(() => {
    let items = data?.rows ?? [];
    if (hideTracked) items = items.filter(i => !i.already_tracked);
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      items = items.filter(i => i.symbol.toLowerCase().includes(q) || i.name.toLowerCase().includes(q));
    }
    return [...items].sort((a, b) => {
      const getVal = (x: MarketScreenerRow): number => {
        if (sort.key === 'change_pct') return x.change_pct ?? -999;
        if (sort.key === 'rvol') return x.rvol ?? -999;
        if (sort.key === 'volume') return x.volume ?? -999;
        if (sort.key === 'market_cap') return x.market_cap ?? -999;
        return 0;
      };
      const diff = getVal(b) - getVal(a);
      return sort.dir === 'desc' ? diff : -diff;
    });
  }, [data, hideTracked, search, sort]);

  function toggleSort(key: SortKey) {
    setSort(s => (s.key === key ? { key, dir: s.dir === 'desc' ? 'asc' : 'desc' } : { key, dir: 'desc' }));
  }

  function SortTh({ label, col }: { label: string; col: SortKey }) {
    const active = sort.key === col;
    return (
      <th
        onClick={() => toggleSort(col)}
        style={{ padding: '9px 14px', textAlign: 'right', color: active ? '#a78bfa' : '#475569', fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: '1px solid #1e293b', background: '#080f1e', cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap' }}
      >
        {label} {active ? (sort.dir === 'desc' ? '↓' : '↑') : ''}
      </th>
    );
  }

  async function handleAdd(symbol: string) {
    setAddBusy(b => ({ ...b, [symbol]: true }));
    setAddStatus(s => ({ ...s, [symbol]: '' }));
    try {
      const res = await api.addStock(symbol);
      setAddStatus(s => ({ ...s, [symbol]: res.status === 'exists' ? 'Already tracked' : 'Added!' }));
      mutate();
    } catch (e: unknown) {
      setAddStatus(s => ({ ...s, [symbol]: (e as Error)?.message || 'Failed' }));
    } finally {
      setAddBusy(b => ({ ...b, [symbol]: false }));
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px', flexWrap: 'wrap', gap: '12px' }}>
        <div>
          <h1 style={{ fontSize: '22px', fontWeight: 800, color: '#e2e8f0', marginBottom: '4px' }}>Market-Wide Screener</h1>
          <p style={{ fontSize: '12px', color: '#475569', maxWidth: 640 }}>
            Unlike every other screener in this app, this one is NOT limited to your ~150 already-tracked
            stocks — it scans the whole US market via Yahoo Finance&apos;s free screener (top gainers, most-active,
            aggressive small caps) to surface a fast mover BEFORE it&apos;s on your radar. Find something new here?
            {isAdmin ? ' Click Add to start tracking it.' : ' Ask an admin to add it — the add action needs admin access.'}
          </p>
        </div>
        <button
          onClick={() => mutate()}
          style={{ padding: '6px 14px', borderRadius: '6px', border: '1px solid #1e293b', background: 'transparent', color: '#64748b', fontSize: '12px', cursor: 'pointer' }}
        >↻ Refresh</button>
      </div>

      <div style={{ display: 'flex', gap: '10px', marginBottom: '18px', flexWrap: 'wrap', alignItems: 'center' }}>
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search symbol or name…"
          style={{ flex: '1 1 160px', padding: '7px 11px', borderRadius: '6px', border: '1px solid #1e293b', background: '#0f172a', color: '#e2e8f0', fontSize: '12px', outline: 'none' }}
        />
        <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: '#64748b', cursor: 'pointer' }}>
          <input type="checkbox" checked={hideTracked} onChange={e => setHideTracked(e.target.checked)} />
          Only show new (not yet tracked)
        </label>
      </div>

      {isLoading && <div style={{ color: '#475569', fontSize: '13px', padding: '40px 0', textAlign: 'center' }}>Scanning the market…</div>}
      {error && <div style={{ color: '#f87171', fontSize: '13px' }}>Failed to load screener data.</div>}
      {!isLoading && !error && data && data.queries_failed.length > 0 && (
        <div style={{ background: 'rgba(250,204,21,0.06)', border: '1px solid rgba(250,204,21,0.2)', borderRadius: '8px', padding: '8px 14px', marginBottom: '14px', fontSize: '11px', color: '#facc15' }}>
          {data.queries_failed.join(', ')} screen(s) failed to load this cycle — results may be partial.
        </div>
      )}
      {!isLoading && !error && rows.length === 0 && (
        <div style={{ color: '#475569', fontSize: '13px', padding: '40px 0', textAlign: 'center' }}>
          No results{hideTracked ? ' — try unchecking "only show new"' : ''}.
        </div>
      )}

      {rows.length > 0 && (
        <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: '12px', overflow: 'hidden' }}>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px', minWidth: 640 }}>
              <thead>
                <tr>
                  <th style={{ padding: '9px 14px', textAlign: 'left', color: '#475569', fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: '1px solid #1e293b', background: '#080f1e', whiteSpace: 'nowrap' }}>Symbol</th>
                  <th style={{ padding: '9px 14px', textAlign: 'right', color: '#475569', fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: '1px solid #1e293b', background: '#080f1e', whiteSpace: 'nowrap' }}>Price</th>
                  <SortTh label="Change" col="change_pct" />
                  <SortTh label="Volume" col="volume" />
                  <SortTh label="RVOL" col="rvol" />
                  <SortTh label="Mkt Cap" col="market_cap" />
                  <th style={{ padding: '9px 14px', textAlign: 'right', color: '#475569', fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: '1px solid #1e293b', background: '#080f1e', whiteSpace: 'nowrap' }}>Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(r => (
                  <tr key={r.symbol} style={{ borderBottom: '1px solid rgba(30,41,59,0.5)' }}>
                    <td style={{ padding: '10px 14px', whiteSpace: 'nowrap' }}>
                      {r.already_tracked ? (
                        <Link href={`/stock/${r.symbol}`} style={{ color: '#818cf8', fontWeight: 700, textDecoration: 'none', fontSize: '13px' }}>
                          {r.symbol}
                        </Link>
                      ) : (
                        <span style={{ color: '#e2e8f0', fontWeight: 700, fontSize: '13px' }}>{r.symbol}</span>
                      )}
                      <div style={{ fontSize: '10px', color: '#475569', maxWidth: '160px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</div>
                      {r.exchange && <div style={{ fontSize: '9px', color: '#334155' }}>{r.exchange}</div>}
                    </td>
                    <td style={{ padding: '10px 14px', textAlign: 'right', color: '#e2e8f0', fontVariantNumeric: 'tabular-nums' }}>
                      {r.price != null ? `$${r.price.toFixed(2)}` : '—'}
                    </td>
                    <td style={{ padding: '10px 14px', textAlign: 'right', fontWeight: 700, fontVariantNumeric: 'tabular-nums', color: (r.change_pct ?? 0) >= 0 ? '#4ade80' : '#f87171' }}>
                      {fmtChg(r.change_pct)}
                    </td>
                    <td style={{ padding: '10px 14px', textAlign: 'right', color: '#64748b', fontVariantNumeric: 'tabular-nums' }}>
                      {fmtNum(r.volume)}
                    </td>
                    <td style={{ padding: '10px 14px', textAlign: 'right', color: r.rvol != null && r.rvol >= 2 ? '#f59e0b' : '#94a3b8', fontWeight: r.rvol != null && r.rvol >= 2 ? 700 : 400, fontVariantNumeric: 'tabular-nums' }}>
                      {r.rvol != null ? `${r.rvol.toFixed(1)}x` : '—'}
                    </td>
                    <td style={{ padding: '10px 14px', textAlign: 'right', color: '#94a3b8', fontVariantNumeric: 'tabular-nums' }}>
                      {fmtNum(r.market_cap)}
                    </td>
                    <td style={{ padding: '10px 14px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                      {r.already_tracked ? (
                        <span style={{ fontSize: '10px', color: '#4ade80', background: 'rgba(74,222,128,0.1)', padding: '2px 8px', borderRadius: '4px' }}>Tracked</span>
                      ) : isAdmin ? (
                        <button
                          onClick={() => handleAdd(r.symbol)}
                          disabled={addBusy[r.symbol]}
                          style={{ fontSize: '11px', padding: '4px 10px', borderRadius: '5px', border: '1px solid #38bdf8', background: 'rgba(56,189,248,0.1)', color: '#38bdf8', cursor: addBusy[r.symbol] ? 'wait' : 'pointer' }}
                        >
                          {addBusy[r.symbol] ? '…' : addStatus[r.symbol] || 'Add'}
                        </button>
                      ) : (
                        <span style={{ fontSize: '10px', color: '#334155' }}>New</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ padding: '10px 16px', fontSize: '11px', color: '#334155', borderTop: '1px solid #1e293b' }}>
            {rows.length} results · Yahoo Finance screens: {data?.queries_used.join(', ')} · 5-min cache
          </div>
        </div>
      )}
    </div>
  );
}
