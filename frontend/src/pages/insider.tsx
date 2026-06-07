/**
 * Insider / Congress Trade Tracker page.
 *
 * AI provider: whichever is configured in Settings → AI Assistant
 *              (Claude or DeepSeek). Uses temperature=0.2 (default).
 *              AI is a FALLBACK only — used when no Quiver API key is set.
 *
 * Data source priority
 * ────────────────────
 * 1. Quiver Quantitative API (live, real-time disclosures)
 *    Requires a Quiver API key in Settings → Congressional & Insider Trading.
 *    Fetches from GET /congress/trades (proxied through api-gateway → congress.py).
 *    Returns actual STOCK Act filings with exact dollar ranges and dates.
 *
 * 2. AI fallback (loadWithAi) — used when no Quiver key is configured
 *    AI_PROMPT_SYSTEM: instructs AI to output ONLY a raw JSON array,
 *                      no markdown, no prose.
 *    AI_PROMPT_USER:   asks for all known congressional trades from 2023+
 *                      for Nancy Pelosi, Congressman Josh, and Mark Green.
 *    Response is stripped of markdown fences and parsed as CongressTrade[].
 *    A yellow disclaimer banner is shown to flag AI data as approximate
 *    (from training data, not live filings). max_tokens=4096.
 *
 * Featured traders (always highlighted regardless of data source)
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
import { loadSettings } from '@/lib/settings';
import { askAI, isAiConfigured } from '@/lib/ai';

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
  return (
    <span style={{
      fontSize: '11px', fontWeight: 700, padding: '2px 8px', borderRadius: '5px',
      background: isPurchase ? 'rgba(34,197,94,0.12)' : 'rgba(239,68,68,0.12)',
      border: `1px solid ${isPurchase ? 'rgba(34,197,94,0.35)' : 'rgba(239,68,68,0.35)'}`,
      color: isPurchase ? '#4ade80' : '#f87171',
    }}>{isPurchase ? '▲ BUY' : '▼ SELL'}</span>
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

const AI_PROMPT_SYSTEM = `You output ONLY raw JSON arrays. No markdown fences, no prose, no explanation — just the [ ... ] array starting on the very first character of your response.

Each element must have exactly these fields (use null for unknown numeric fields):
{"Ticker":"NVDA","Date":"2024-11-15","Politician":"Pelosi, Nancy","Transaction":"Purchase","Min":1000000,"Max":5000000,"Party":"D","State":"CA","Chamber":"House","ReportDate":"2024-11-30"}

Rules:
- Transaction is exactly "Purchase" or "Sale"
- Date and ReportDate are YYYY-MM-DD strings
- Min/Max are integers in USD (can be null)
- Your entire response must be parseable by JSON.parse()`;

const AI_PROMPT_USER = `List all known congressional stock trades you have in your training data from 2023 onwards for:
1. Nancy Pelosi (D-CA)
2. Congressman Josh (any: Gottheimer, Hawley, Brecheen, or other Josh)
3. Mark Green (R-TN)

Also include any other congress members who were particularly active traders in 2024-2025.

Include both Purchases and Sales. Focus on the most notable/largest trades. Return at least 40 trades if known.

Return ONLY the JSON array.`;

export default function InsiderPage() {
  const hasKey = typeof window !== 'undefined' ? !!loadSettings().quiverApiKey : false;
  const aiAvailable = isAiConfigured();

  const { data: liveTrades, error: liveError, isLoading: liveLoading } = useSWR<CongressTrade[]>(
    hasKey ? 'congress-trades' : null,
    () => api.congressTrades(90),
    { revalidateOnFocus: false },
  );

  const [aiTrades, setAiTrades]       = useState<CongressTrade[] | null>(null);
  const [aiLoading, setAiLoading]     = useState(false);
  const [aiError, setAiError]         = useState('');
  const [usingAi, setUsingAi]         = useState(false);

  const trades = liveTrades ?? aiTrades ?? null;
  const isLoading = liveLoading || aiLoading;
  const loadError = liveError?.message ?? (aiError || '');

  async function loadWithAi() {
    setAiLoading(true);
    setAiError('');
    setUsingAi(true);
    try {
      const raw = await askAI([{ role: 'user', content: AI_PROMPT_USER }], AI_PROMPT_SYSTEM, 4096);

      // Strip markdown code fences if present, then find the JSON array
      const stripped = raw
        .replace(/```(?:json)?\s*/gi, '')
        .replace(/```/g, '');
      const match = stripped.match(/\[[\s\S]*\]/);
      if (!match) {
        console.error('AI raw response:', raw.slice(0, 500));
        throw new Error('AI response did not contain a JSON array. Try again.');
      }
      const parsed = JSON.parse(match[0]) as CongressTrade[];
      if (!Array.isArray(parsed) || parsed.length === 0) throw new Error('AI returned an empty dataset.');
      setAiTrades(parsed);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'AI request failed.';
      setAiError(msg.toLowerCase().includes('networkerror') || msg.toLowerCase().includes('failed to fetch')
        ? 'AI request failed — go to Settings → AI Assistant and enter your API key.'
        : msg);
      setUsingAi(false);
    } finally {
      setAiLoading(false);
    }
  }

  const [filterPolitician, setFilterPolitician] = useState('');
  const [filterTicker, setFilterTicker]         = useState('');
  const [filterTx, setFilterTx]                 = useState<'all' | 'buy' | 'sell'>('all');
  const [days, setDays]                         = useState(365);
  const [sortBy, setSortBy]                     = useState<'date' | 'amount' | 'politician'>('date');
  const [showNetBuyersOnly, setShowNetBuyersOnly] = useState(false);

  const filtered = useMemo(() => {
    if (!trades) return [];
    return trades
      .filter(t => {
        const txOk = filterTx === 'all'
          ? true
          : filterTx === 'buy' ? /purchase|buy/i.test(t.Transaction) : /sale|sell/i.test(t.Transaction);
        const polOk = !filterPolitician || (t.Politician || '').toLowerCase().includes(filterPolitician.toLowerCase());
        const tkOk  = !filterTicker || (t.Ticker || '').toUpperCase().includes(filterTicker.toUpperCase());
        const dateOk = usingAi ? true : daysAgo(t.Date) <= days;
        return txOk && polOk && tkOk && dateOk;
      })
      .sort((a, b) => {
        if (sortBy === 'date')       return new Date(b.Date).getTime() - new Date(a.Date).getTime();
        if (sortBy === 'politician') return (a.Politician || '').localeCompare(b.Politician || '');
        const aAmt = a.Max ?? a.Min ?? 0;
        const bAmt = b.Max ?? b.Min ?? 0;
        return bAmt - aAmt;
      });
  }, [trades, filterTx, filterPolitician, filterTicker, days, sortBy, usingAi]);

  // Per-featured-trader summaries
  const featuredStats = useMemo(() => {
    if (!trades) return {};
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
    if (!trades) return [];
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
    if (!trades) return [];
    const recentBuys = trades.filter(t => /purchase|buy/i.test(t.Transaction) && daysAgo(t.Date) <= (usingAi ? 730 : 30));
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
  }, [trades, usingAi]);

  return (
    <div className="space-y-4">
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '12px', marginBottom: '4px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 800, color: '#f1f5f9', margin: 0 }}>
          Congressional Trade Tracker
        </h1>
        <span style={{ fontSize: '12px', color: '#475569' }}>
          {usingAi ? 'AI knowledge base — not real-time' : 'STOCK Act disclosures via Quiver Quantitative'}
        </span>
      </div>

      {/* No key — offer AI or setup */}
      {!hasKey && !usingAi && !aiLoading && !aiTrades && (
        <div style={{
          padding: '28px', borderRadius: '12px', border: '1px solid rgba(251,146,60,0.3)',
          background: 'rgba(251,146,60,0.06)',
        }}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '12px', textAlign: 'center' }}>
            <div style={{ fontSize: '30px' }}>📋</div>
            <div style={{ fontSize: '15px', fontWeight: 700, color: '#fdba74' }}>
              No Quiver API Key Configured
            </div>
            <div style={{ fontSize: '13px', color: '#94a3b8', maxWidth: '500px' }}>
              For real-time STOCK Act disclosures, add a free{' '}
              <span style={{ color: '#fb923c' }}>quiverquant.com</span> key in Settings.
              Or use AI to load known trades from Claude&apos;s knowledge base.
            </div>
            <div style={{ display: 'flex', gap: '12px', marginTop: '4px', flexWrap: 'wrap', justifyContent: 'center' }}>
              {aiAvailable ? (
                <button
                  onClick={loadWithAi}
                  style={{
                    padding: '10px 24px', borderRadius: '8px', fontWeight: 700, fontSize: '13px', cursor: 'pointer',
                    background: 'linear-gradient(135deg,#7c3aed,#a78bfa)', border: 'none', color: '#fff',
                  }}
                >
                  🤖 Load with AI
                </button>
              ) : (
                <div style={{ fontSize: '12px', color: '#475569' }}>
                  No AI configured.{' '}
                  <Link href="/settings" style={{ color: '#a78bfa' }}>Set up Claude in Settings</Link>
                  {' '}to use AI as data source.
                </div>
              )}
              <Link
                href="/settings"
                style={{
                  display: 'inline-block', padding: '10px 20px', borderRadius: '8px',
                  background: 'rgba(251,146,60,0.15)', border: '1px solid rgba(251,146,60,0.35)',
                  color: '#fb923c', fontWeight: 700, fontSize: '13px', textDecoration: 'none',
                }}
              >
                ⚙ Configure Quiver Key
              </Link>
            </div>
          </div>
        </div>
      )}

      {/* AI loading spinner */}
      {aiLoading && (
        <div style={{ padding: '48px', textAlign: 'center', color: '#a78bfa', fontSize: '13px' }}>
          <div style={{ fontSize: '24px', marginBottom: '8px', animation: 'spin 1s linear infinite', display: 'inline-block' }}>⟳</div>
          <div>Asking AI for congressional trading data…</div>
        </div>
      )}

      {/* Quiver loading */}
      {hasKey && liveLoading && (
        <div style={{ padding: '48px', textAlign: 'center', color: '#475569', fontSize: '13px' }}>
          Loading congressional trades…
        </div>
      )}

      {/* Error */}
      {loadError && !aiLoading && (
        <div style={{
          padding: '14px 18px', borderRadius: '10px',
          background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.25)',
          color: '#f87171', fontSize: '13px', display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <span>{loadError.includes('400') ? 'API key not pushed to backend — save it in Settings then refresh.' : loadError}</span>
          {!hasKey && aiAvailable && (
            <button onClick={loadWithAi} style={{ background: 'rgba(167,139,250,0.2)', border: '1px solid rgba(167,139,250,0.4)', color: '#a78bfa', borderRadius: '6px', padding: '5px 12px', fontSize: '12px', cursor: 'pointer' }}>
              Try AI instead
            </button>
          )}
        </div>
      )}

      {/* AI disclaimer banner */}
      {usingAi && trades && (
        <div style={{
          padding: '10px 16px', borderRadius: '8px', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          background: 'rgba(167,139,250,0.08)', border: '1px solid rgba(167,139,250,0.25)',
        }}>
          <div style={{ fontSize: '12px', color: '#a78bfa' }}>
            🤖 Data from AI knowledge base — trades may be from training data (pre-2025), not real-time.
            For live disclosures, add a{' '}
            <Link href="/settings" style={{ color: '#c4b5fd', textDecoration: 'underline' }}>Quiver API key in Settings</Link>.
          </div>
          <button
            onClick={() => { setAiTrades(null); setUsingAi(false); setAiError(''); }}
            style={{ background: 'none', border: 'none', color: '#475569', cursor: 'pointer', fontSize: '18px', lineHeight: 1 }}
          >×</button>
        </div>
      )}

      {trades && (
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
            {!usingAi && (
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
            )}
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
            {usingAi && aiAvailable && (
              <button
                onClick={loadWithAi}
                style={{ fontSize: '11px', padding: '5px 12px', borderRadius: '6px', background: 'rgba(167,139,250,0.15)', border: '1px solid rgba(167,139,250,0.3)', color: '#a78bfa', cursor: 'pointer' }}
              >
                ↻ Refresh AI
              </button>
            )}
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
