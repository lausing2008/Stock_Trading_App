import { useState, useMemo } from 'react';
import useSWR from 'swr';
import Link from 'next/link';
import { api, type EarningsItem } from '@/lib/api';

function fmtCap(v: number | null): string {
  if (v == null) return '—';
  if (v >= 1e12) return `$${(v / 1e12).toFixed(1)}T`;
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  return `$${v.toLocaleString()}`;
}

function fmtPct(v: number | null): string {
  if (v == null) return '—';
  return (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%';
}

function urgencyColor(days: number): string {
  if (days <= 3) return '#ef4444';
  if (days <= 7) return '#f97316';
  if (days <= 14) return '#facc15';
  return '#64748b';
}

function urgencyBg(days: number): string {
  if (days <= 3) return 'rgba(239,68,68,0.12)';
  if (days <= 7) return 'rgba(249,115,22,0.1)';
  if (days <= 14) return 'rgba(250,204,21,0.08)';
  return 'rgba(100,116,139,0.1)';
}

export default function EarningsPage() {
  const [daysAhead, setDaysAhead] = useState(45);
  const [market, setMarket] = useState<'All' | 'US' | 'HK'>('All');
  const [search, setSearch] = useState('');
  const [sortKey, setSortKey] = useState<'days' | 'cap' | 'eg'>('days');

  const { data, error, isLoading } = useSWR<EarningsItem[]>(
    `earnings-${daysAhead}`,
    () => api.earningsCalendar(daysAhead),
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
      if (sortKey === 'days') return a.days_to_earnings - b.days_to_earnings;
      if (sortKey === 'cap') return (b.market_cap ?? 0) - (a.market_cap ?? 0);
      if (sortKey === 'eg') return (b.earnings_growth ?? -99) - (a.earnings_growth ?? -99);
      return 0;
    });
  }, [data, market, search, sortKey]);

  // Group by week
  const grouped = useMemo(() => {
    const groups: { label: string; items: EarningsItem[] }[] = [];
    for (const item of rows) {
      const d = item.days_to_earnings;
      let label = '';
      if (d === 0) label = 'Today';
      else if (d === 1) label = 'Tomorrow';
      else if (d <= 7) label = 'This Week';
      else if (d <= 14) label = 'Next Week';
      else if (d <= 21) label = 'In 2–3 Weeks';
      else label = 'In 3+ Weeks';
      const last = groups[groups.length - 1];
      if (last && last.label === label) last.items.push(item);
      else groups.push({ label, items: [item] });
    }
    return groups;
  }, [rows]);

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px', flexWrap: 'wrap', gap: '12px' }}>
        <div>
          <h1 style={{ fontSize: '22px', fontWeight: 800, color: '#e2e8f0', marginBottom: '4px' }}>Earnings Calendar</h1>
          <p style={{ fontSize: '12px', color: '#475569' }}>Upcoming earnings reports for tracked stocks</p>
        </div>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          {[14, 30, 45, 90].map(d => (
            <button
              key={d}
              onClick={() => setDaysAhead(d)}
              style={{
                padding: '5px 12px', borderRadius: '6px', fontSize: '12px', cursor: 'pointer',
                border: '1px solid #1e293b', fontWeight: daysAhead === d ? 700 : 400,
                background: daysAhead === d ? '#4f46e5' : 'transparent',
                color: daysAhead === d ? '#fff' : '#64748b',
              }}
            >{d}d</button>
          ))}
        </div>
      </div>

      {/* Controls */}
      <div style={{ display: 'flex', gap: '10px', marginBottom: '18px', flexWrap: 'wrap' }}>
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search symbol or name…"
          style={{ flex: '1 1 180px', minWidth: '140px', padding: '7px 11px', borderRadius: '6px', border: '1px solid #1e293b', background: '#0f172a', color: '#e2e8f0', fontSize: '12px', outline: 'none' }}
        />
        <div style={{ display: 'flex', gap: '6px' }}>
          {(['All', 'US', 'HK'] as const).map(m => (
            <button key={m} onClick={() => setMarket(m)}
              style={{ padding: '5px 12px', borderRadius: '6px', fontSize: '12px', cursor: 'pointer', border: '1px solid #1e293b', background: market === m ? '#334155' : 'transparent', color: market === m ? '#e2e8f0' : '#64748b' }}
            >{m}</button>
          ))}
        </div>
        <select
          value={sortKey}
          onChange={e => setSortKey(e.target.value as 'days' | 'cap' | 'eg')}
          style={{ padding: '6px 10px', borderRadius: '6px', border: '1px solid #1e293b', background: '#0f172a', color: '#94a3b8', fontSize: '12px', cursor: 'pointer' }}
        >
          <option value="days">Sort: Soonest First</option>
          <option value="cap">Sort: Market Cap</option>
          <option value="eg">Sort: Earnings Growth</option>
        </select>
      </div>

      {isLoading && <div style={{ color: '#475569', fontSize: '13px', padding: '40px 0', textAlign: 'center' }}>Loading earnings calendar…</div>}
      {error && <div style={{ color: '#f87171', fontSize: '13px' }}>Failed to load earnings data.</div>}
      {!isLoading && !error && rows.length === 0 && (
        <div style={{ color: '#475569', fontSize: '13px', padding: '40px 0', textAlign: 'center' }}>
          No earnings found in the next {daysAhead} days.<br />
          <span style={{ fontSize: '11px' }}>Data available only for stocks with cached fundamentals — visit stock pages to populate.</span>
        </div>
      )}

      {grouped.map(group => (
        <div key={group.label} style={{ marginBottom: '24px' }}>
          <div style={{ fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#475569', marginBottom: '10px', paddingBottom: '6px', borderBottom: '1px solid #1e293b' }}>
            {group.label} ({group.items.length})
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: '8px' }}>
            {group.items.map(item => (
              <div key={item.symbol} style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: '10px', padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '8px' }}>
                  <div>
                    <Link href={`/stock/${item.symbol}`} style={{ color: '#818cf8', fontWeight: 800, fontSize: '15px', textDecoration: 'none' }}>
                      {item.symbol}
                    </Link>
                    <div style={{ fontSize: '12px', color: '#94a3b8', marginTop: '2px' }}>{item.name}</div>
                    {item.sector && <div style={{ fontSize: '10px', color: '#475569', marginTop: '1px' }}>{item.sector} · {item.market}</div>}
                  </div>
                  <div style={{ textAlign: 'right', flexShrink: 0 }}>
                    <div style={{ fontSize: '12px', fontWeight: 700, color: urgencyColor(item.days_to_earnings), background: urgencyBg(item.days_to_earnings), padding: '3px 8px', borderRadius: '5px' }}>
                      {item.days_to_earnings === 0 ? 'Today' : item.days_to_earnings === 1 ? 'Tomorrow' : `${item.days_to_earnings}d`}
                    </div>
                    <div style={{ fontSize: '10px', color: '#475569', marginTop: '4px' }}>
                      {item.next_earnings_date}
                    </div>
                  </div>
                </div>
                <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
                  <div style={{ fontSize: '11px' }}>
                    <span style={{ color: '#475569' }}>EPS est: </span>
                    <span style={{ color: '#e2e8f0', fontWeight: 700 }}>
                      {item.eps_estimate != null ? `$${item.eps_estimate.toFixed(2)}` : '—'}
                    </span>
                  </div>
                  <div style={{ fontSize: '11px' }}>
                    <span style={{ color: '#475569' }}>Trailing EPS: </span>
                    <span style={{ color: '#e2e8f0', fontWeight: 700 }}>
                      {item.trailing_eps != null ? `$${item.trailing_eps.toFixed(2)}` : '—'}
                    </span>
                  </div>
                  <div style={{ fontSize: '11px' }}>
                    <span style={{ color: '#475569' }}>Rev growth: </span>
                    <span style={{ color: (item.revenue_growth ?? 0) >= 0 ? '#4ade80' : '#f87171', fontWeight: 700 }}>
                      {fmtPct(item.revenue_growth)}
                    </span>
                  </div>
                  <div style={{ fontSize: '11px' }}>
                    <span style={{ color: '#475569' }}>EPS growth: </span>
                    <span style={{ color: (item.earnings_growth ?? 0) >= 0 ? '#4ade80' : '#f87171', fontWeight: 700 }}>
                      {fmtPct(item.earnings_growth)}
                    </span>
                  </div>
                  <div style={{ fontSize: '11px' }}>
                    <span style={{ color: '#475569' }}>Market cap: </span>
                    <span style={{ color: '#94a3b8', fontWeight: 700 }}>{fmtCap(item.market_cap)}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
