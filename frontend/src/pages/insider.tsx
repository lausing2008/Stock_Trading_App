/**
 * Insider / Congress Trade Tracker page.
 *
 * T233-ARCH-CONGRESS-DEDUP: data source is event-intelligence's canonical
 * GET /events/congress/recent (DB-persisted, feeds catalyst scoring) — previously called
 * market-data's now-deleted /congress/trades (Quiver-optional with an AI-fallback path).
 * The AI/Quiver fallback UI is gone since the live feed no longer needs one.
 *
 * Featured traders (always highlighted)
 * ───────────────────────────────────────────────────────────────
 *   Nancy Pelosi  — matched by "pelosi"      in Politician field
 *   Congressman Josh — matched by "josh"
 *   Mark Green    — matched by "green, mark"
 *
 * UI features
 * ───────────
 * - Featured summary cards: buy count, sell count, top ticker per trader
 * - Cluster panel: tickers bought 2+ times across all featured traders
 * - Filterable/sortable table: politician, ticker, buy/sell, date range
 */
import { useState, useMemo } from 'react';
import Link from 'next/link';
import useSWR from 'swr';
import { api, type CongressTrade } from '@/lib/api';

type CongressTradeRecord = {
  Ticker: string; Date: string; Politician: string; Transaction: string;
  Min: number | null; Max: number | null; Party: string | null; State: string | null;
  Chamber: string | null; ReportDate: string | null;
};

function adaptTrade(t: CongressTrade): CongressTradeRecord {
  return {
    Ticker: t.ticker,
    Date: t.trade_date ?? '',
    Politician: t.politician_name,
    Transaction: t.transaction_type === 'purchase' ? 'Purchase' : t.transaction_type === 'sale' ? 'Sale' : t.transaction_type,
    Min: t.amount_min,
    Max: t.amount_max,
    Party: t.party,
    State: t.state,
    Chamber: t.chamber,
    ReportDate: t.disclosure_date,
  };
}

// Featured traders to spotlight
const FEATURED = [
  { key: 'pelosi',  label: 'Nancy Pelosi',     match: 'pelosi',      party: 'D', color: '#60a5fa' },
  { key: 'josh',    label: 'Congressman Josh',  match: 'josh',        party: '?', color: '#a78bfa' },
  { key: 'green',   label: 'Mark Green',        match: 'green, mark', party: 'R', color: '#f87171' },
] as const;

const PARTY_COLOR: Record<string, string> = { D: '#60a5fa', R: '#f87171', I: '#4ade80' };

function partyBadge(party: string | null) {
  const p = (party || '?').toUpperCase();
  const color = PARTY_COLOR[p] ?? '#94a3b8';
  return (
    <span style={{
      fontSize: '10px', fontWeight: 800, padding: '1px 6px', borderRadius: '4px',
      background: `${color}22`, border: `1px solid ${color}55`, color,
    }}>{p}</span>
  );
}

function txBadge(tx: string) {
  const isPurchase = /purchase|buy/i.test(tx);
  const isUnknown = /unknown/i.test(tx);
  return (
    <span style={{
      fontSize: '11px', fontWeight: 700, padding: '2px 8px', borderRadius: '5px',
      background: isUnknown ? 'rgba(148,163,184,0.12)' : isPurchase ? 'rgba(34,197,94,0.12)' : 'rgba(239,68,68,0.12)',
      border: `1px solid ${isUnknown ? 'rgba(148,163,184,0.35)' : isPurchase ? 'rgba(34,197,94,0.35)' : 'rgba(239,68,68,0.35)'}`,
      color: isUnknown ? '#94a3b8' : isPurchase ? '#4ade80' : '#f87171',
    }}>{isUnknown ? '? UNKNOWN' : isPurchase ? '▲ BUY' : '▼ SELL'}</span>
  );
}

function fmtAmount(min: number | null, max: number | null): string {
  if (min == null && max == null) return '—';
  const fmt = (n: number) => n >= 1_000_000 ? `$${(n / 1_000_000).toFixed(1)}M` : `$${(n / 1_000).toFixed(0)}K`;
  if (min != null && max != null) return `${fmt(min)} – ${fmt(max)}`;
  return fmt(min ?? max!);
}

function daysAgo(dateStr: string): number {
  return Math.floor((Date.now() - new Date(dateStr).getTime()) / 86_400_000);
}

function daysAgoBadge(dateStr: string) {
  const d = daysAgo(dateStr);
  const color = d <= 7 ? '#4ade80' : d <= 30 ? '#facc15' : '#64748b';
  return <span style={{ fontSize: '11px', color, fontWeight: d <= 7 ? 700 : 400 }}>{d === 0 ? 'Today' : `${d}d ago`}</span>;
}

export default function InsiderPage() {
  const { data: rawTrades, error: loadErrorObj, isLoading } = useSWR<CongressTrade[]>(
    'congress-trades',
    () => api.eventsCongressRecent(365, { limit: 500 }),
    { revalidateOnFocus: false },
  );

  const trades = useMemo(() => (rawTrades ?? []).map(adaptTrade), [rawTrades]);
  const loadError = loadErrorObj?.message ?? '';

  const [filterPolitician, setFilterPolitician] = useState('');
  const [filterTicker, setFilterTicker]         = useState('');
  const [filterTx, setFilterTx]                 = useState<'all' | 'buy' | 'sell'>('all');
  const [days, setDays]                         = useState(365);
  const [sortBy, setSortBy]                     = useState<'date' | 'amount' | 'politician'>('date');
  const [showNetBuyersOnly, setShowNetBuyersOnly] = useState(false);

  const filtered = useMemo(() => {
    return trades
      .filter(t => {
        const txOk = filterTx === 'all'
          ? true
          : filterTx === 'buy' ? /purchase|buy/i.test(t.Transaction) : /sale|sell/i.test(t.Transaction);
        const polOk = !filterPolitician || (t.Politician || '').toLowerCase().includes(filterPolitician.toLowerCase());
        const tkOk  = !filterTicker || (t.Ticker || '').toUpperCase().includes(filterTicker.toUpperCase());
        const dateOk = daysAgo(t.Date) <= days;
        return txOk && polOk && tkOk && dateOk;
      })
      .sort((a, b) => {
        if (sortBy === 'date')       return new Date(b.Date).getTime() - new Date(a.Date).getTime();
        if (sortBy === 'politician') return (a.Politician || '').localeCompare(b.Politician || '');
        const aAmt = a.Max ?? a.Min ?? 0;
        const bAmt = b.Max ?? b.Min ?? 0;
        return bAmt - aAmt;
      });
  }, [trades, filterTx, filterPolitician, filterTicker, days, sortBy]);

  // Per-featured-trader summaries
  const featuredStats = useMemo(() => {
    return Object.fromEntries(FEATURED.map(f => {
      const rows = trades.filter(t => (t.Politician || '').toLowerCase().includes(f.match));
      const buys = rows.filter(t => /purchase|buy/i.test(t.Transaction));
      const recentBuys = buys.filter(t => daysAgo(t.Date) <= 365);
      const topTicker = (() => {
        const freq: Record<string, number> = {};
        buys.forEach(t => { freq[t.Ticker] = (freq[t.Ticker] ?? 0) + 1; });
        return Object.entries(freq).sort((a, b) => b[1] - a[1])[0]?.[0] ?? null;
      })();
      return [f.key, { total: rows.length, buys: buys.length, recentBuys, topTicker }];
    }));
  }, [trades]);

  // Conviction screener: net buy $ per ticker, distinct buyers
  const convictionScores = useMemo(() => {
    const cutoff = Date.now() - days * 24 * 60 * 60 * 1000;
    const byTicker: Record<string, { netBuy: number; buyers: Set<string>; sellers: Set<string>; buyCount: number; sellCount: number }> = {};
    trades.filter(t => new Date(t.Date).getTime() >= cutoff).forEach(t => {
      const tk = (t.Ticker || '').toUpperCase();
      if (!tk) return;
      if (!byTicker[tk]) byTicker[tk] = { netBuy: 0, buyers: new Set(), sellers: new Set(), buyCount: 0, sellCount: 0 };
      const amt = ((t.Min ?? 0) + (t.Max ?? 0)) / 2;
      if (/purchase|buy/i.test(t.Transaction)) {
        byTicker[tk].netBuy += amt;
        byTicker[tk].buyers.add(t.Politician || '?');
        byTicker[tk].buyCount++;
      } else {
        byTicker[tk].netBuy -= amt;
        byTicker[tk].sellers.add(t.Politician || '?');
        byTicker[tk].sellCount++;
      }
    });
    return Object.entries(byTicker)
      .map(([ticker, v]) => ({ ticker, netBuy: v.netBuy, distinctBuyers: v.buyers.size, distinctSellers: v.sellers.size, buyCount: v.buyCount, sellCount: v.sellCount }))
      .filter(r => showNetBuyersOnly ? r.netBuy > 0 : true)
      .sort((a, b) => b.netBuy - a.netBuy)
      .slice(0, 15);
  }, [trades, days, showNetBuyersOnly]);

  // Sudden activity: tickers bought 2+ times across politicians
  const suddenActivity = useMemo(() => {
    const recentBuys = trades.filter(t => /purchase|buy/i.test(t.Transaction) && daysAgo(t.Date) <= 30);
    const freq: Record<string, { count: number; politicians: Set<string> }> = {};
    recentBuys.forEach(t => {
      if (!freq[t.Ticker]) freq[t.Ticker] = { count: 0, politicians: new Set() };
      freq[t.Ticker].count++;
      freq[t.Ticker].politicians.add((t.Politician || '').split(',')[0].trim());
    });
    return Object.entries(freq)
      .filter(([, v]) => v.count >= 2)
      .sort((a, b) => b[1].count - a[1].count)
      .slice(0, 10)
      .map(([ticker, v]) => ({ ticker, count: v.count, politicians: Array.from(v.politicians).slice(0, 3) }));
  }, [trades]);

  return (
    <div className="space-y-4">
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '12px', marginBottom: '4px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 800, color: '#f1f5f9', margin: 0 }}>
          Congressional Trade Tracker
        </h1>
        <span style={{ fontSize: '12px', color: '#475569' }}>STOCK Act disclosures</span>
      </div>

      {/* Loading */}
      {isLoading && (
        <div style={{ padding: '48px', textAlign: 'center', color: '#475569', fontSize: '13px' }}>
          Loading congressional trades…
        </div>
      )}

      {/* Error */}
      {loadError && !isLoading && (
        <div style={{
          padding: '14px 18px', borderRadius: '10px',
          background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.25)',
          color: '#f87171', fontSize: '13px',
        }}>
          {loadError}
        </div>
      )}

      {!isLoading && (
        <>
          {/* ── Featured trader cards ─────────────────────────────────── */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: '12px' }}>
            {FEATURED.map(f => {
              const stats = featuredStats[f.key] ?? { total: 0, buys: 0, recentBuys: 0, topTicker: null };
              return (
                <div
                  key={f.key}
                  onClick={() => setFilterPolitician(filterPolitician === f.match ? '' : f.match)}
                  style={{
                    borderRadius: '10px', padding: '16px',
                    border: `1px solid ${filterPolitician === f.match ? f.color + '66' : 'rgba(148,163,184,0.1)'}`,
                    background: filterPolitician === f.match ? `${f.color}11` : 'rgba(15,23,42,0.6)',
                    cursor: 'pointer', transition: 'all 0.15s',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '10px' }}>
                    <div style={{ fontSize: '13px', fontWeight: 700, color: f.color }}>{f.label}</div>
                    {partyBadge(f.party)}
                  </div>
                  <div style={{ display: 'flex', gap: '16px' }}>
                    <div style={{ textAlign: 'center' }}>
                      <div style={{ fontSize: '20px', fontWeight: 800, color: '#4ade80' }}>{stats.buys}</div>
                      <div style={{ fontSize: '10px', color: '#475569', textTransform: 'uppercase' }}>Buys</div>
                    </div>
                    <div style={{ textAlign: 'center' }}>
                      <div style={{ fontSize: '20px', fontWeight: 800, color: '#e2e8f0' }}>{stats.total - stats.buys}</div>
                      <div style={{ fontSize: '10px', color: '#475569', textTransform: 'uppercase' }}>Sells</div>
                    </div>
                    {stats.topTicker && (
                      <div style={{ textAlign: 'center' }}>
                        <Link
                          href={`/stock/${stats.topTicker}`}
                          onClick={e => e.stopPropagation()}
                          style={{ fontSize: '13px', fontWeight: 800, color: '#818cf8', textDecoration: 'none', display: 'block', marginBottom: '2px' }}
                        >
                          {stats.topTicker}
                        </Link>
                        <div style={{ fontSize: '10px', color: '#475569', textTransform: 'uppercase' }}>Top Buy</div>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {/* ── Conviction Screener ─────────────────────────────────────── */}
          {convictionScores.length > 0 && (
            <div style={{ borderRadius: '10px', border: '1px solid #1e293b', background: '#0f172a', padding: '14px 16px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
                <div style={{ fontSize: '12px', fontWeight: 700, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                  Conviction Screener — net buy $ by stock ({days}d)
                </div>
                <button onClick={() => setShowNetBuyersOnly(v => !v)}
                  style={{ fontSize: '11px', padding: '3px 10px', borderRadius: '5px', border: `1px solid ${showNetBuyersOnly ? '#4ade80' : '#1e293b'}`, background: showNetBuyersOnly ? 'rgba(74,222,128,0.1)' : 'transparent', color: showNetBuyersOnly ? '#4ade80' : '#475569', cursor: 'pointer' }}>
                  Net buyers only
                </button>
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                <thead>
                  <tr style={{ color: '#475569', textAlign: 'left' }}>
                    <th style={{ padding: '3px 8px' }}>Ticker</th>
                    <th style={{ padding: '3px 8px', textAlign: 'right' }}>Net Buy $</th>
                    <th style={{ padding: '3px 8px', textAlign: 'right' }}>Buyers</th>
                    <th style={{ padding: '3px 8px', textAlign: 'right' }}>Buys</th>
                    <th style={{ padding: '3px 8px', textAlign: 'right' }}>Sells</th>
                    <th style={{ padding: '3px 8px' }}>Conviction</th>
                  </tr>
                </thead>
                <tbody>
                  {convictionScores.map(r => {
                    const isNet = r.netBuy > 0;
                    const barW = Math.min(100, Math.abs(r.netBuy) / 500_000 * 100);
                    return (
                      <tr key={r.ticker} style={{ borderTop: '1px solid #1e293b' }}>
                        <td style={{ padding: '5px 8px' }}>
                          <Link href={`/stock/${r.ticker}`} style={{ fontWeight: 800, color: '#e2e8f0', fontFamily: 'monospace', fontSize: '13px', textDecoration: 'none' }}>{r.ticker}</Link>
                        </td>
                        <td style={{ padding: '5px 8px', textAlign: 'right', fontWeight: 700, color: isNet ? '#4ade80' : '#f87171' }}>
                          {isNet ? '+' : '-'}${Math.abs(r.netBuy / 1000).toFixed(0)}k
                        </td>
                        <td style={{ padding: '5px 8px', textAlign: 'right', color: '#94a3b8' }}>{r.distinctBuyers}</td>
                        <td style={{ padding: '5px 8px', textAlign: 'right', color: '#4ade80' }}>{r.buyCount}</td>
                        <td style={{ padding: '5px 8px', textAlign: 'right', color: r.sellCount > 0 ? '#f87171' : '#334155' }}>{r.sellCount}</td>
                        <td style={{ padding: '5px 8px' }}>
                          <div style={{ height: 8, background: '#1e293b', borderRadius: 4, overflow: 'hidden', width: 80 }}>
                            <div style={{ height: '100%', width: `${barW}%`, background: isNet ? '#4ade80' : '#f87171', borderRadius: 4 }} />
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* ── Sudden / clustered activity ────────────────────────────── */}
          {suddenActivity.length > 0 && (
            <div style={{
              borderRadius: '10px', padding: '14px 16px',
              border: '1px solid rgba(251,191,36,0.3)', background: 'rgba(251,191,36,0.05)',
            }}>
              <div style={{ fontSize: '12px', fontWeight: 700, color: '#fbbf24', marginBottom: '10px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                ⚡ Clustered Buys — Multiple Congress Members Buying the Same Stock
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                {suddenActivity.map(({ ticker, count, politicians }) => (
                  <Link key={ticker} href={`/stock/${ticker}`} style={{ textDecoration: 'none' }}>
                    <div style={{
                      padding: '6px 12px', borderRadius: '8px',
                      background: 'rgba(251,191,36,0.1)', border: '1px solid rgba(251,191,36,0.3)', cursor: 'pointer',
                    }}>
                      <div style={{ fontSize: '13px', fontWeight: 800, color: '#fbbf24' }}>{ticker}</div>
                      <div style={{ fontSize: '10px', color: '#94a3b8', marginTop: '2px' }}>
                        {count}× · {politicians.join(', ')}
                      </div>
                    </div>
                  </Link>
                ))}
              </div>
            </div>
          )}

          {/* ── Filters ───────────────────────────────────────────────── */}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px', alignItems: 'center' }}>
            <input
              type="text"
              placeholder="Filter by politician…"
              value={filterPolitician}
              onChange={e => setFilterPolitician(e.target.value)}
              style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: '7px', padding: '7px 12px', fontSize: '12px', color: '#e2e8f0', width: '180px' }}
            />
            <input
              type="text"
              placeholder="Filter by ticker…"
              value={filterTicker}
              onChange={e => setFilterTicker(e.target.value.toUpperCase())}
              style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: '7px', padding: '7px 12px', fontSize: '12px', color: '#e2e8f0', width: '130px', textTransform: 'uppercase' }}
            />
            {(['all', 'buy', 'sell'] as const).map(v => (
              <button
                key={v}
                onClick={() => setFilterTx(v)}
                style={{
                  padding: '6px 14px', borderRadius: '7px', fontSize: '12px', fontWeight: 600, cursor: 'pointer',
                  background: filterTx === v ? (v === 'buy' ? 'rgba(34,197,94,0.2)' : v === 'sell' ? 'rgba(239,68,68,0.2)' : 'rgba(99,102,241,0.2)') : 'transparent',
                  border: `1px solid ${filterTx === v ? (v === 'buy' ? 'rgba(34,197,94,0.5)' : v === 'sell' ? 'rgba(239,68,68,0.5)' : 'rgba(99,102,241,0.5)') : '#1e293b'}`,
                  color: filterTx === v ? (v === 'buy' ? '#4ade80' : v === 'sell' ? '#f87171' : '#818cf8') : '#64748b',
                }}
              >
                {v === 'all' ? 'All' : v === 'buy' ? '▲ Buys' : '▼ Sells'}
              </button>
            ))}
            <select
              value={days}
              onChange={e => setDays(Number(e.target.value))}
              style={{ background: '#1e293b', color: '#cbd5e1', border: '1px solid #1e293b', borderRadius: '7px', padding: '6px 10px', fontSize: '12px' }}
            >
              <option value={7}>Last 7 days</option>
              <option value={30}>Last 30 days</option>
              <option value={60}>Last 60 days</option>
              <option value={90}>Last 90 days</option>
              <option value={365}>Last year</option>
            </select>
            <div style={{ marginLeft: 'auto', display: 'flex', gap: '8px', alignItems: 'center' }}>
              <span style={{ fontSize: '11px', color: '#475569' }}>Sort:</span>
              {(['date', 'amount', 'politician'] as const).map(v => (
                <button
                  key={v}
                  onClick={() => setSortBy(v)}
                  style={{
                    padding: '5px 10px', borderRadius: '6px', fontSize: '11px', cursor: 'pointer',
                    background: sortBy === v ? 'rgba(99,102,241,0.2)' : 'transparent',
                    border: `1px solid ${sortBy === v ? 'rgba(99,102,241,0.4)' : '#1e293b'}`,
                    color: sortBy === v ? '#818cf8' : '#475569',
                  }}
                >{v.charAt(0).toUpperCase() + v.slice(1)}</button>
              ))}
            </div>
            <div style={{ fontSize: '12px', color: '#475569' }}>{filtered.length} trades</div>
          </div>

          {/* ── Trade table ──────────────────────────────────────────────── */}
          <div style={{ borderRadius: '10px', border: '1px solid #1e293b', overflow: 'hidden' }}>
            <div style={{
              display: 'grid',
              gridTemplateColumns: '100px 1fr 48px 90px 110px 130px 80px 70px',
              gap: '0 8px', padding: '8px 14px',
              background: 'rgba(15,23,42,0.8)', borderBottom: '1px solid #1e293b',
              fontSize: '10px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em',
            }}>
              <div>Date</div><div>Politician</div><div>Party</div><div>Ticker</div>
              <div>Action</div><div>Amount</div><div>Chamber</div><div>Days Ago</div>
            </div>

            {filtered.length === 0 && (
              <div style={{ padding: '32px', textAlign: 'center', fontSize: '13px', color: '#334155' }}>
                No trades match your filters.
              </div>
            )}

            {filtered.map((t, i) => {
              const isPurchase = /purchase|buy/i.test(t.Transaction);
              const recent = daysAgo(t.Date) <= 14;
              return (
                <div
                  key={i}
                  style={{
                    display: 'grid',
                    gridTemplateColumns: '100px 1fr 48px 90px 110px 130px 80px 70px',
                    gap: '0 8px', padding: '10px 14px',
                    borderBottom: i < filtered.length - 1 ? '1px solid rgba(30,41,59,0.5)' : 'none',
                    background: recent ? (isPurchase ? 'rgba(34,197,94,0.04)' : 'rgba(239,68,68,0.04)') : 'transparent',
                    alignItems: 'center',
                  }}
                >
                  <div style={{ fontSize: '12px', color: '#94a3b8', fontFamily: 'monospace' }}>{t.Date}</div>
                  <div style={{ fontSize: '12px', color: '#e2e8f0', fontWeight: 500 }}>
                    {t.Politician}
                    {recent && (
                      <span style={{ marginLeft: '6px', fontSize: '9px', fontWeight: 700, color: '#facc15', background: 'rgba(250,204,21,0.1)', border: '1px solid rgba(250,204,21,0.3)', padding: '1px 5px', borderRadius: '3px' }}>
                        NEW
                      </span>
                    )}
                  </div>
                  <div>{partyBadge(t.Party)}</div>
                  <div>
                    <Link href={`/stock/${t.Ticker}`} style={{ fontSize: '13px', fontWeight: 800, color: '#818cf8', textDecoration: 'none' }}>
                      {t.Ticker}
                    </Link>
                  </div>
                  <div>{txBadge(t.Transaction)}</div>
                  <div style={{ fontSize: '12px', color: '#94a3b8', fontFamily: 'monospace' }}>{fmtAmount(t.Min, t.Max)}</div>
                  <div style={{ fontSize: '11px', color: '#475569' }}>{t.Chamber ?? '—'}</div>
                  <div>{daysAgoBadge(t.Date)}</div>
                </div>
              );
            })}
          </div>
        </>
      )}

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}
