import { useState, useMemo } from 'react';
import Link from 'next/link';
import useSWR from 'swr';
import { api, type CongressTrade } from '@/lib/api';
import { loadSettings } from '@/lib/settings';
import { askAI, isAiConfigured } from '@/lib/ai';

const AI_PROMPT_SYSTEM = `You output ONLY raw JSON arrays. No markdown fences, no prose, no explanation — just the [ ... ] array starting on the very first character of your response.

Each element must have exactly these fields (use null for unknown numeric fields):
{"Ticker":"NVDA","Date":"2024-11-15","Politician":"Pelosi, Nancy","Transaction":"Purchase","Min":1000000,"Max":5000000,"Party":"D","State":"CA","Chamber":"House","ReportDate":"2024-11-30"}

Rules:
- Transaction is exactly "Purchase" or "Sale"
- Date and ReportDate are YYYY-MM-DD strings
- Min/Max are integers in USD (can be null)
- Your entire response must be parseable by JSON.parse()`;

const AI_PROMPT_USER = `List all known congressional stock trades from 2023 onwards.

Include trades from:
- Nancy Pelosi (D-CA) — known very active trader
- Mark Green (R-TN)
- Austin Scott (R-GA)
- Josh Gottheimer (D-NJ)
- Dan Crenshaw (R-TX)
- Tommy Tuberville (R-AL)
- Any other congress members particularly active in 2024-2025

Include both Purchases and Sales. Include the largest/most notable trades. Return at least 60 trades total across all politicians.

Return ONLY the JSON array.`;

const PARTY_COLOR: Record<string, string> = { D: '#60a5fa', R: '#f87171', I: '#4ade80' };

function partyBadge(party: string | null) {
  const p = (party || '?').toUpperCase();
  const color = PARTY_COLOR[p] ?? '#94a3b8';
  return (
    <span style={{ fontSize: 10, fontWeight: 800, padding: '1px 6px', borderRadius: 4,
      background: `${color}22`, border: `1px solid ${color}55`, color }}>{p}</span>
  );
}

function txBadge(tx: string) {
  const isBuy = /purchase|buy/i.test(tx);
  return (
    <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 5,
      background: isBuy ? 'rgba(34,197,94,0.12)' : 'rgba(239,68,68,0.12)',
      border: `1px solid ${isBuy ? 'rgba(34,197,94,0.35)' : 'rgba(239,68,68,0.35)'}`,
      color: isBuy ? '#4ade80' : '#f87171' }}>
      {isBuy ? '▲ BUY' : '▼ SELL'}
    </span>
  );
}

function fmtAmt(min: number | null, max: number | null): string {
  if (min == null && max == null) return '—';
  const f = (n: number) => n >= 1_000_000 ? `$${(n / 1_000_000).toFixed(1)}M` : `$${(n / 1_000).toFixed(0)}K`;
  if (min != null && max != null) return `${f(min)} – ${f(max)}`;
  return f(min ?? max!);
}

function midAmt(min: number | null, max: number | null): number {
  if (min == null && max == null) return 0;
  if (min != null && max != null) return (min + max) / 2;
  return min ?? max ?? 0;
}

function daysAgo(d: string) { return Math.floor((Date.now() - new Date(d).getTime()) / 86_400_000); }

function daysChip(d: string) {
  const n = daysAgo(d);
  const color = n <= 7 ? '#4ade80' : n <= 30 ? '#facc15' : '#64748b';
  return <span style={{ fontSize: 11, color, fontWeight: n <= 7 ? 700 : 400 }}>{n === 0 ? 'Today' : `${n}d ago`}</span>;
}

export default function CongressPage() {
  const hasKey = typeof window !== 'undefined' ? !!loadSettings().quiverApiKey : false;
  const aiAvailable = isAiConfigured();

  const [days, setDays] = useState(90);
  const [txFilter, setTxFilter] = useState<'all' | 'buy' | 'sell'>('all');
  const [partyFilter, setPartyFilter] = useState<'all' | 'D' | 'R'>('all');
  const [symbolSearch, setSymbolSearch] = useState('');
  const [politicianSearch, setPoliticianSearch] = useState('');
  const [sortBy, setSortBy] = useState<'date' | 'amount' | 'politician'>('date');
  const [netBuyersOnly, setNetBuyersOnly] = useState(false);

  // Live data via Quiver (paid) — only when key is configured
  const { data: liveTrades, isLoading: liveLoading } = useSWR<CongressTrade[]>(
    hasKey ? ['congress-trades', days] : null,
    () => api.congressTrades(days),
    { revalidateOnFocus: false },
  );

  // AI fallback state
  const [aiTrades, setAiTrades] = useState<CongressTrade[] | null>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState('');
  const [usingAi, setUsingAi] = useState(false);

  const trades = liveTrades ?? aiTrades ?? null;
  const isLoading = liveLoading || aiLoading;

  async function loadWithAi() {
    setAiLoading(true);
    setAiError('');
    setUsingAi(true);
    try {
      const raw = await askAI([{ role: 'user', content: AI_PROMPT_USER }], AI_PROMPT_SYSTEM, 4096);
      const stripped = raw.replace(/```(?:json)?\s*/gi, '').replace(/```/g, '');
      const match = stripped.match(/\[[\s\S]*\]/);
      if (!match) throw new Error('AI response did not contain a JSON array.');
      const parsed = JSON.parse(match[0]) as CongressTrade[];
      if (!Array.isArray(parsed) || parsed.length === 0) throw new Error('AI returned an empty dataset.');
      setAiTrades(parsed);
    } catch (e: unknown) {
      setAiError(e instanceof Error ? e.message : 'AI request failed.');
      setUsingAi(false);
    } finally {
      setAiLoading(false);
    }
  }

  // ── Filtered trades ──────────────────────────────────────────────────────────
  const filtered = useMemo(() => {
    if (!trades) return [];
    return trades.filter(t => {
      if (txFilter === 'buy' && !/purchase|buy/i.test(t.Transaction)) return false;
      if (txFilter === 'sell' && /purchase|buy/i.test(t.Transaction)) return false;
      if (partyFilter !== 'all' && (t.Party || '').toUpperCase() !== partyFilter) return false;
      if (symbolSearch && !(t.Ticker || '').toUpperCase().includes(symbolSearch.toUpperCase())) return false;
      if (politicianSearch && !(t.Politician || '').toLowerCase().includes(politicianSearch.toLowerCase())) return false;
      return true;
    });
  }, [trades, txFilter, partyFilter, symbolSearch, politicianSearch]);

  const sorted = useMemo(() => {
    const arr = [...filtered];
    if (sortBy === 'date') arr.sort((a, b) => new Date(b.Date).getTime() - new Date(a.Date).getTime());
    else if (sortBy === 'amount') arr.sort((a, b) => midAmt(b.Min, b.Max) - midAmt(a.Min, a.Max));
    else arr.sort((a, b) => (a.Politician || '').localeCompare(b.Politician || ''));
    return arr;
  }, [filtered, sortBy]);

  // ── Conviction screener — by ticker ─────────────────────────────────────────
  const tickerConviction = useMemo(() => {
    if (!trades) return [];
    const cutoff = Date.now() - days * 86_400_000;
    const byTicker: Record<string, { netBuy: number; buyers: Set<string>; sellers: Set<string>; buyCount: number; sellCount: number }> = {};
    trades.filter(t => new Date(t.Date).getTime() >= cutoff).forEach(t => {
      const tk = (t.Ticker || '').toUpperCase();
      if (!tk) return;
      if (!byTicker[tk]) byTicker[tk] = { netBuy: 0, buyers: new Set(), sellers: new Set(), buyCount: 0, sellCount: 0 };
      const amt = midAmt(t.Min, t.Max);
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
      .filter(r => netBuyersOnly ? r.netBuy > 0 : true)
      .sort((a, b) => b.netBuy - a.netBuy)
      .slice(0, 12);
  }, [trades, days, netBuyersOnly]);

  // ── Politician conviction — who is buying most ────────────────────────────
  const politicianConviction = useMemo(() => {
    if (!trades) return [];
    const cutoff = Date.now() - days * 86_400_000;
    const byPol: Record<string, { netBuy: number; buyCount: number; sellCount: number; party: string | null }> = {};
    trades.filter(t => new Date(t.Date).getTime() >= cutoff).forEach(t => {
      const pol = t.Politician || '?';
      if (!byPol[pol]) byPol[pol] = { netBuy: 0, buyCount: 0, sellCount: 0, party: t.Party };
      const amt = midAmt(t.Min, t.Max);
      if (/purchase|buy/i.test(t.Transaction)) { byPol[pol].netBuy += amt; byPol[pol].buyCount++; }
      else { byPol[pol].netBuy -= amt; byPol[pol].sellCount++; }
    });
    return Object.entries(byPol)
      .map(([name, v]) => ({ name, ...v }))
      .filter(r => r.buyCount > 0)
      .sort((a, b) => b.netBuy - a.netBuy)
      .slice(0, 8);
  }, [trades, days]);

  // ── Summary stats ─────────────────────────────────────────────────────────
  const stats = useMemo(() => {
    if (!trades || !trades.length) return null;
    const buys = trades.filter(t => /purchase|buy/i.test(t.Transaction));
    const sells = trades.filter(t => !/purchase|buy/i.test(t.Transaction));
    const totalBuyAmt = buys.reduce((s, t) => s + midAmt(t.Min, t.Max), 0);
    const totalSellAmt = sells.reduce((s, t) => s + midAmt(t.Min, t.Max), 0);
    const uniquePols = new Set(trades.map(t => t.Politician)).size;
    const uniqueTickers = new Set(trades.map(t => t.Ticker)).size;
    return { buys: buys.length, sells: sells.length, totalBuyAmt, totalSellAmt, uniquePols, uniqueTickers };
  }, [trades]);

  const maxNetBuy = tickerConviction.length ? Math.max(...tickerConviction.map(r => Math.abs(r.netBuy)), 1) : 1;

  return (
    <div style={{ padding: '24px 20px', maxWidth: 1200, margin: '0 auto' }}>
      {/* Header */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 22, fontWeight: 800, color: '#e2e8f0' }}>🏛️ Congressional Trading</div>
        <div style={{ fontSize: 12, color: '#475569', marginTop: 4 }}>
          {hasKey ? 'Quiver Quantitative (live STOCK Act filings)' : usingAi ? 'AI training data — approximate, not live filings' : 'STOCK Act disclosures'}
          {' · '} Last {days} days
          {!hasKey && (
            <span style={{ marginLeft: 10, fontSize: 11, color: '#334155' }}>
              · <Link href="/settings" style={{ color: '#475569', textDecoration: 'underline' }}>Add Quiver key</Link> for live data
            </span>
          )}
        </div>
      </div>

      {/* Summary stats row */}
      {stats && (
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 20 }}>
          {[
            { label: 'Total Buys', value: stats.buys, color: '#4ade80' },
            { label: 'Total Sells', value: stats.sells, color: '#f87171' },
            { label: 'Buy Volume', value: stats.totalBuyAmt >= 1_000_000 ? `$${(stats.totalBuyAmt / 1_000_000).toFixed(1)}M` : `$${(stats.totalBuyAmt / 1_000).toFixed(0)}K`, color: '#4ade80' },
            { label: 'Sell Volume', value: stats.totalSellAmt >= 1_000_000 ? `$${(stats.totalSellAmt / 1_000_000).toFixed(1)}M` : `$${(stats.totalSellAmt / 1_000).toFixed(0)}K`, color: '#f87171' },
            { label: 'Politicians', value: stats.uniquePols },
            { label: 'Tickers Traded', value: stats.uniqueTickers },
          ].map(s => (
            <div key={s.label} style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '10px 14px', minWidth: 100 }}>
              <div style={{ fontSize: 18, fontWeight: 700, color: s.color ?? '#e2e8f0' }}>{s.value}</div>
              <div style={{ fontSize: 10, color: '#475569', marginTop: 2 }}>{s.label}</div>
            </div>
          ))}
        </div>
      )}

      {/* AI load button — shown when no Quiver key and no data yet */}
      {!hasKey && !trades && !aiLoading && (
        <div style={{ marginBottom: 20, padding: '16px 20px', background: '#0f172a', border: '1px solid #1e293b', borderRadius: 10, display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: '#94a3b8' }}>No live data source configured</div>
            <div style={{ fontSize: 12, color: '#475569', marginTop: 3 }}>
              {aiAvailable
                ? 'Use AI to generate approximate congressional trades from training data (2023–2025). Data may not reflect recent filings.'
                : 'Add a Quiver API key in Settings for live STOCK Act filings, or configure an AI assistant for approximate historical data.'}
            </div>
          </div>
          {aiAvailable && (
            <button onClick={loadWithAi}
              style={{ padding: '8px 18px', borderRadius: 8, background: 'rgba(99,102,241,0.15)', border: '1px solid #6366f1',
                color: '#818cf8', fontWeight: 600, fontSize: 12, cursor: 'pointer', whiteSpace: 'nowrap' }}>
              Ask AI for data
            </button>
          )}
          {!aiAvailable && (
            <Link href="/settings" style={{ padding: '8px 18px', borderRadius: 8, background: 'rgba(99,102,241,0.15)', border: '1px solid #6366f1',
              color: '#818cf8', fontWeight: 600, fontSize: 12, textDecoration: 'none', whiteSpace: 'nowrap' }}>
              Go to Settings
            </Link>
          )}
        </div>
      )}

      {/* AI loading */}
      {aiLoading && (
        <div style={{ marginBottom: 20, padding: '14px 20px', background: '#0f172a', border: '1px solid #1e293b', borderRadius: 10, color: '#64748b', fontSize: 13 }}>
          Asking AI for congressional trading data…
        </div>
      )}

      {/* AI disclaimer banner */}
      {usingAi && trades && (
        <div style={{ marginBottom: 16, padding: '10px 14px', background: 'rgba(251,191,36,0.06)', border: '1px solid rgba(251,191,36,0.2)', borderRadius: 8, fontSize: 12, color: '#fbbf24' }}>
          ⚠ Data sourced from AI training data — approximate figures from 2023–2025. Not live STOCK Act filings.
          Add a <Link href="/settings" style={{ color: '#fbbf24' }}>Quiver API key</Link> for real-time disclosures.
        </div>
      )}

      {/* AI error */}
      {aiError && (
        <div style={{ marginBottom: 16, padding: '10px 14px', background: 'rgba(248,113,113,0.08)', border: '1px solid rgba(248,113,113,0.2)', borderRadius: 8, fontSize: 12, color: '#f87171' }}>
          {aiError}
        </div>
      )}

      {/* Two-column screeners */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 24 }}>
        {/* Ticker conviction screener */}
        <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 10, padding: '14px 16px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.07em' }}>
              Stock Conviction Screener
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: '#64748b', cursor: 'pointer' }}>
              <input type="checkbox" checked={netBuyersOnly} onChange={e => setNetBuyersOnly(e.target.checked)}
                style={{ accentColor: '#6366f1' }} />
              Net buyers only
            </label>
          </div>
          {isLoading && <div style={{ color: '#475569', fontSize: 12, padding: 8 }}>Loading…</div>}
          {tickerConviction.length === 0 && !isLoading && <div style={{ color: '#334155', fontSize: 12 }}>No data</div>}
          {tickerConviction.map(r => (
            <div key={r.ticker} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 7 }}>
              <Link href={`/stock/${r.ticker}`} style={{ fontSize: 12, fontWeight: 700, color: '#e2e8f0', minWidth: 44, textDecoration: 'none' }}>{r.ticker}</Link>
              <div style={{ flex: 1, height: 6, background: '#1e293b', borderRadius: 3, overflow: 'hidden' }}>
                <div style={{ height: '100%', borderRadius: 3, width: `${Math.min(Math.abs(r.netBuy) / maxNetBuy * 100, 100)}%`,
                  background: r.netBuy >= 0 ? '#4ade80' : '#f87171' }} />
              </div>
              <div style={{ fontSize: 11, color: r.netBuy >= 0 ? '#4ade80' : '#f87171', minWidth: 56, textAlign: 'right', fontWeight: 600 }}>
                {r.netBuy >= 0 ? '+' : ''}{r.netBuy >= 1_000_000 ? `$${(r.netBuy/1_000_000).toFixed(1)}M` : `$${(r.netBuy/1_000).toFixed(0)}K`}
              </div>
              <div style={{ fontSize: 10, color: '#475569', minWidth: 40, textAlign: 'right' }}>
                {r.distinctBuyers}👤 {r.buyCount}↑
              </div>
            </div>
          ))}
        </div>

        {/* Politician conviction */}
        <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 10, padding: '14px 16px' }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10 }}>
            Most Active Buyers
          </div>
          {isLoading && <div style={{ color: '#475569', fontSize: 12, padding: 8 }}>Loading…</div>}
          {politicianConviction.length === 0 && !isLoading && <div style={{ color: '#334155', fontSize: 12 }}>No data</div>}
          {politicianConviction.map((r, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 7 }}>
              {partyBadge(r.party)}
              <div style={{ fontSize: 12, color: '#94a3b8', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</div>
              <div style={{ fontSize: 11, color: '#4ade80', fontWeight: 600, minWidth: 56, textAlign: 'right' }}>
                {r.netBuy >= 1_000_000 ? `$${(r.netBuy/1_000_000).toFixed(1)}M` : `$${(r.netBuy/1_000).toFixed(0)}K`}
              </div>
              <div style={{ fontSize: 10, color: '#475569' }}>{r.buyCount}↑ {r.sellCount}↓</div>
            </div>
          ))}
        </div>
      </div>

      {/* Filter bar */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 16, alignItems: 'flex-end' }}>
        {/* Days */}
        <div>
          <div style={{ fontSize: 10, color: '#475569', marginBottom: 4 }}>Period</div>
          <div style={{ display: 'flex', gap: 3 }}>
            {[30, 60, 90, 180].map(d => (
              <button key={d} onClick={() => setDays(d)}
                style={{ padding: '4px 10px', borderRadius: 5, fontSize: 11, cursor: 'pointer', border: '1px solid',
                  borderColor: days === d ? '#6366f1' : '#1e293b',
                  background: days === d ? 'rgba(99,102,241,0.15)' : 'transparent',
                  color: days === d ? '#818cf8' : '#64748b' }}>{d}d</button>
            ))}
          </div>
        </div>
        {/* Transaction */}
        <div>
          <div style={{ fontSize: 10, color: '#475569', marginBottom: 4 }}>Type</div>
          <div style={{ display: 'flex', gap: 3 }}>
            {(['all', 'buy', 'sell'] as const).map(v => (
              <button key={v} onClick={() => setTxFilter(v)}
                style={{ padding: '4px 10px', borderRadius: 5, fontSize: 11, cursor: 'pointer', border: '1px solid',
                  borderColor: txFilter === v ? '#6366f1' : '#1e293b',
                  background: txFilter === v ? 'rgba(99,102,241,0.15)' : 'transparent',
                  color: txFilter === v ? '#818cf8' : '#64748b', textTransform: 'capitalize' }}>{v}</button>
            ))}
          </div>
        </div>
        {/* Party */}
        <div>
          <div style={{ fontSize: 10, color: '#475569', marginBottom: 4 }}>Party</div>
          <div style={{ display: 'flex', gap: 3 }}>
            {(['all', 'D', 'R'] as const).map(v => (
              <button key={v} onClick={() => setPartyFilter(v)}
                style={{ padding: '4px 10px', borderRadius: 5, fontSize: 11, cursor: 'pointer', border: '1px solid',
                  borderColor: partyFilter === v ? '#6366f1' : '#1e293b',
                  background: partyFilter === v ? 'rgba(99,102,241,0.15)' : 'transparent',
                  color: partyFilter === v ? '#818cf8' : '#64748b' }}>
                {v === 'all' ? 'All' : v === 'D' ? '🔵 Dem' : '🔴 Rep'}
              </button>
            ))}
          </div>
        </div>
        {/* Sort */}
        <div>
          <div style={{ fontSize: 10, color: '#475569', marginBottom: 4 }}>Sort by</div>
          <div style={{ display: 'flex', gap: 3 }}>
            {([['date', 'Date'], ['amount', 'Amount'], ['politician', 'Politician']] as const).map(([v, label]) => (
              <button key={v} onClick={() => setSortBy(v)}
                style={{ padding: '4px 10px', borderRadius: 5, fontSize: 11, cursor: 'pointer', border: '1px solid',
                  borderColor: sortBy === v ? '#6366f1' : '#1e293b',
                  background: sortBy === v ? 'rgba(99,102,241,0.15)' : 'transparent',
                  color: sortBy === v ? '#818cf8' : '#64748b' }}>{label}</button>
            ))}
          </div>
        </div>
        {/* Symbol search */}
        <div style={{ marginLeft: 'auto' }}>
          <div style={{ fontSize: 10, color: '#475569', marginBottom: 4 }}>Symbol</div>
          <input value={symbolSearch} onChange={e => setSymbolSearch(e.target.value)} placeholder="AAPL…"
            style={{ background: '#020617', border: '1px solid #1e293b', borderRadius: 6, color: '#e2e8f0',
              padding: '4px 10px', fontSize: 12, width: 80, outline: 'none' }} />
        </div>
        {/* Politician search */}
        <div>
          <div style={{ fontSize: 10, color: '#475569', marginBottom: 4 }}>Politician</div>
          <input value={politicianSearch} onChange={e => setPoliticianSearch(e.target.value)} placeholder="Pelosi…"
            style={{ background: '#020617', border: '1px solid #1e293b', borderRadius: 6, color: '#e2e8f0',
              padding: '4px 10px', fontSize: 12, width: 110, outline: 'none' }} />
        </div>
      </div>

      {/* Table */}
      {isLoading && <div style={{ color: '#475569', textAlign: 'center', padding: 48 }}>Loading congressional trades…</div>}
      {!isLoading && trades && sorted.length === 0 && (
        <div style={{ color: '#475569', textAlign: 'center', padding: 48 }}>No trades match the current filters.</div>
      )}
      {!isLoading && sorted.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <div style={{ fontSize: 11, color: '#334155', marginBottom: 6 }}>{sorted.length} trades</div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid #1e293b' }}>
                {['Politician', 'Party', 'Stock', 'Type', 'Date', 'Amount', 'Reported', 'Chamber'].map(h => (
                  <th key={h} style={{ textAlign: 'left', padding: '6px 10px', color: '#475569', fontWeight: 600, whiteSpace: 'nowrap', fontSize: 11 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((t, i) => (
                <tr key={i} style={{ borderBottom: '1px solid #0f172a' }}>
                  <td style={{ padding: '7px 10px', color: '#94a3b8', maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {t.Politician || '—'}
                  </td>
                  <td style={{ padding: '7px 10px' }}>{partyBadge(t.Party)}</td>
                  <td style={{ padding: '7px 10px', fontWeight: 700 }}>
                    <Link href={`/stock/${t.Ticker}`} style={{ color: '#e2e8f0', textDecoration: 'none' }}>{t.Ticker || '—'}</Link>
                  </td>
                  <td style={{ padding: '7px 10px' }}>{txBadge(t.Transaction)}</td>
                  <td style={{ padding: '7px 10px', color: '#64748b', whiteSpace: 'nowrap' }}>
                    <span style={{ marginRight: 8 }}>{t.Date}</span>{daysChip(t.Date)}
                  </td>
                  <td style={{ padding: '7px 10px', color: '#94a3b8', whiteSpace: 'nowrap' }}>{fmtAmt(t.Min, t.Max)}</td>
                  <td style={{ padding: '7px 10px', color: '#475569', whiteSpace: 'nowrap' }}>{t.ReportDate ?? '—'}</td>
                  <td style={{ padding: '7px 10px', color: '#475569', textTransform: 'capitalize', fontSize: 11 }}>{(t.Chamber || '—').toLowerCase()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
