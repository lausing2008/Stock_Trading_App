import { useState, useEffect, useMemo, useCallback } from 'react';
import dynamic from 'next/dynamic';
import useSWR, { mutate as globalMutate } from 'swr';
import Link from 'next/link';
import { useRouter } from 'next/router';
import { api, type LatestPrice, type RankingRow, type SignalSummary, type WatchlistItem } from '@/lib/api';
import { storage } from '@/lib/storage';

const DonutChart = dynamic(() => import('@/components/DonutChart'), { ssr: false });

/* ─── Types ─────────────────────────────────────────────── */
type Position = { id: string; symbol: string; shares: number; avgCost: number; currency: string; addedAt: string };
type Trade    = { type: 'BUY' | 'SELL'; shares: number; price: number; date: string };
type SortKey  = 'symbol' | 'pnl' | 'pnlPct' | 'value' | 'change' | 'score';

const STORAGE_KEY = 'positions';
const TRADES_KEY  = 'trades';
function loadPositions(): Position[] { if (typeof window === 'undefined') return []; try { return JSON.parse(storage.getItem(STORAGE_KEY) ?? '[]'); } catch { return []; } }
function savePositions(p: Position[]) { storage.setItem(STORAGE_KEY, JSON.stringify(p)); }
function loadTrades(): Record<string, Trade[]> { if (typeof window === 'undefined') return {}; try { return JSON.parse(storage.getItem(TRADES_KEY) ?? '{}'); } catch { return {}; } }
function saveTrades(t: Record<string, Trade[]>) { storage.setItem(TRADES_KEY, JSON.stringify(t)); }

/* ─── Helpers ────────────────────────────────────────────── */
function pnlColor(v: number) { return v > 0 ? '#4ade80' : v < 0 ? '#f87171' : '#94a3b8'; }
function scoreColor(s: number) { return s >= 70 ? '#4ade80' : s >= 50 ? '#facc15' : '#f87171'; }
function fmt(n: number, d = 2) { return n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }); }
function uid() { return Date.now().toString(36) + Math.random().toString(36).slice(2); }
function sigStyle(label: string) {
  if (label === 'BUY')  return { color: '#4ade80', bg: 'rgba(34,197,94,0.1)',  border: 'rgba(34,197,94,0.25)'  };
  if (label === 'SELL') return { color: '#f87171', bg: 'rgba(239,68,68,0.1)',  border: 'rgba(239,68,68,0.25)'  };
  return                       { color: '#facc15', bg: 'rgba(250,204,21,0.1)', border: 'rgba(250,204,21,0.25)' };
}
function signalFromScore(s?: number) { if (s == null) return null; return s >= 65 ? 'BUY' : s >= 40 ? 'HOLD' : 'SELL'; }

function exportCSV(rows: { symbol: string; shares: number; avgCost: number; curPrice: number | null; mktVal: number | null; pnl: number | null; pnlPct: number | null; currency: string }[]) {
  const header = 'Symbol,Shares,Avg Cost,Current Price,Market Value,P&L ($),P&L (%),Currency';
  const lines = rows.map(r => [r.symbol, r.shares, r.avgCost, r.curPrice ?? '', r.mktVal ?? '', r.pnl ?? '', r.pnlPct != null ? r.pnlPct.toFixed(2) + '%' : '', r.currency].join(','));
  const blob = new Blob([header + '\n' + lines.join('\n')], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href = url; a.download = 'positions.csv'; a.click();
  URL.revokeObjectURL(url);
}

/* ─── Trade modal ─────────────────────────────────────────── */
type ModalProps = { mode: 'add' | 'buy' | 'sell'; position?: Position; currentPrice?: number; onConfirm: (shares: number, price: number, symbol?: string, currency?: string) => void; onClose: () => void };
function TradeModal({ mode, position, currentPrice, onConfirm, onClose }: ModalProps) {
  const [symbol, setSymbol] = useState(position?.symbol ?? '');
  const [shares, setShares] = useState('');
  const [price,  setPrice]  = useState(currentPrice?.toFixed(2) ?? position?.avgCost?.toFixed(2) ?? '');
  const [currency, setCurrency] = useState(position?.currency ?? 'USD');
  const title   = mode === 'add' ? 'Add Position' : mode === 'buy' ? `Buy more ${position?.symbol}` : `Sell ${position?.symbol}`;
  const btnColor = mode === 'sell' ? '#ef4444' : '#4f46e5';
  function submit(e: React.FormEvent) { e.preventDefault(); const sh = parseFloat(shares), pr = parseFloat(price); if (!sh || sh <= 0 || !pr || pr <= 0) return; onConfirm(sh, pr, mode === 'add' ? symbol.trim().toUpperCase() : undefined, mode === 'add' ? currency : undefined); }
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '16px' }}>
      <div onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(6,8,20,0.85)', backdropFilter: 'blur(4px)' }} />
      <div style={{ position: 'relative', zIndex: 10, width: '100%', maxWidth: '380px', borderRadius: '14px', background: 'linear-gradient(160deg,#0d1424,#090e1a)', border: '1px solid rgba(99,102,241,0.3)', boxShadow: '0 24px 48px rgba(0,0,0,0.5)', overflow: 'hidden' }}>
        <div style={{ height: '3px', background: 'linear-gradient(90deg,#4f46e5,#818cf8,#4f46e5)' }} />
        <div style={{ padding: '20px 22px 22px', display: 'flex', flexDirection: 'column', gap: '14px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h3 style={{ margin: 0, fontSize: '15px', fontWeight: 700, color: '#f1f5f9' }}>{title}</h3>
            <button onClick={onClose} style={{ background: 'none', border: 'none', color: '#475569', cursor: 'pointer', fontSize: '14px' }}>✕</button>
          </div>
          <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {mode === 'add' && (
              <div>
                <label style={{ fontSize: '11px', fontWeight: 600, color: '#64748b', display: 'block', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Symbol</label>
                <div style={{ display: 'flex', gap: '8px' }}>
                  <input value={symbol} onChange={e => setSymbol(e.target.value.toUpperCase())} placeholder="NVDA" required style={{ flex: 1, background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(148,163,184,0.12)', borderRadius: '8px', padding: '9px 12px', fontSize: '13px', fontWeight: 700, color: '#f1f5f9', fontFamily: 'monospace', outline: 'none' }} />
                  <select value={currency} onChange={e => setCurrency(e.target.value)} style={{ background: '#1e293b', border: '1px solid rgba(148,163,184,0.12)', borderRadius: '8px', padding: '9px 10px', fontSize: '12px', color: '#94a3b8', outline: 'none' }}>
                    {['USD','HKD','CAD','GBP','EUR','AUD'].map(c => <option key={c}>{c}</option>)}
                  </select>
                </div>
              </div>
            )}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
              <div>
                <label style={{ fontSize: '11px', fontWeight: 600, color: '#64748b', display: 'block', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Shares</label>
                <input type="number" min="0.001" step="any" value={shares} onChange={e => setShares(e.target.value)} placeholder="100" required style={{ width: '100%', background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(148,163,184,0.12)', borderRadius: '8px', padding: '9px 12px', fontSize: '13px', color: '#f1f5f9', outline: 'none', boxSizing: 'border-box' }} />
              </div>
              <div>
                <label style={{ fontSize: '11px', fontWeight: 600, color: '#64748b', display: 'block', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{mode === 'sell' ? 'Sell Price' : 'Buy Price'}</label>
                <input type="number" min="0.001" step="any" value={price} onChange={e => setPrice(e.target.value)} placeholder="0.00" required style={{ width: '100%', background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(148,163,184,0.12)', borderRadius: '8px', padding: '9px 12px', fontSize: '13px', color: '#f1f5f9', outline: 'none', boxSizing: 'border-box' }} />
              </div>
            </div>
            {currentPrice != null && <div style={{ fontSize: '11px', color: '#475569' }}>Market: <span style={{ color: '#94a3b8', fontWeight: 600 }}>${fmt(currentPrice)}</span></div>}
            <button type="submit" style={{ borderRadius: '8px', padding: '10px', border: 'none', background: btnColor, color: '#fff', fontSize: '13px', fontWeight: 700, cursor: 'pointer', marginTop: '2px' }}>{mode === 'add' ? 'Add Position' : mode === 'buy' ? 'Buy' : 'Sell'}</button>
          </form>
        </div>
      </div>
    </div>
  );
}

/* ─── Main page ──────────────────────────────────────────── */
export default function Positions() {
  const router  = useRouter();
  const [positions, setPositions] = useState<Position[]>([]);
  const [trades, setTrades]       = useState<Record<string, Trade[]>>({});
  const [modal, setModal]         = useState<{ mode: 'add' | 'buy' | 'sell'; posId?: string } | null>(null);
  const [showTradesFor, setShowTradesFor] = useState<string | null>(null);
  const [refreshing, setRefreshing]       = useState(false);
  const [sortKey, setSortKey]             = useState<SortKey>('symbol');
  const [sortAsc, setSortAsc]             = useState(true);

  const { data: pricesData, mutate: mutatePrices } = useSWR<LatestPrice[]>('latest-prices', () => api.latestPrices(), { refreshInterval: 60_000 });
  const { data: rankingsData }  = useSWR<{ rankings: RankingRow[] }>('rankings-all', () => api.rankings());
  const { data: signalsData }   = useSWR<SignalSummary[]>('signals-all', () => api.allSignals());
  const { data: watchlistData, mutate: mutateWatchlist } = useSWR<WatchlistItem[]>('watchlist', () => api.listWatchlist());

  /* pre-fill symbol from ?add= query (coming from watchlist) */
  useEffect(() => {
    const sym = router.query.add as string | undefined;
    if (sym) { setModal({ mode: 'add' }); router.replace('/positions', undefined, { shallow: true }); }
  }, [router.query.add]);

  useEffect(() => { setPositions(loadPositions()); setTrades(loadTrades()); }, []);

  const priceMap   = useMemo(() => { const m: Record<string, LatestPrice> = {}; for (const p of pricesData ?? []) m[p.symbol] = p; return m; }, [pricesData]);
  const rankMap    = useMemo(() => { const m: Record<string, RankingRow> = {}; for (const r of rankingsData?.rankings ?? []) m[r.symbol] = r; return m; }, [rankingsData]);
  const signalMap  = useMemo(() => { const m: Record<string, SignalSummary> = {}; for (const s of signalsData ?? []) m[s.symbol] = s; return m; }, [signalsData]);
  const watchedSet = useMemo(() => new Set(watchlistData?.map(w => w.symbol) ?? []), [watchlistData]);

  async function toggleWatch(symbol: string) {
    if (watchedSet.has(symbol)) await api.removeFromWatchlist(symbol);
    else await api.addToWatchlist(symbol);
    mutateWatchlist(); globalMutate('watchlist');
  }

  const handleRefresh = useCallback(async () => {
    setRefreshing(true); await mutatePrices(); setRefreshing(false);
  }, [mutatePrices]);

  /* ── Actions ── */
  function addPosition(shares: number, price: number, symbol?: string, currency?: string) {
    const newPos: Position = { id: uid(), symbol: symbol!, shares, avgCost: price, currency: currency ?? 'USD', addedAt: new Date().toISOString() };
    const next = [...positions, newPos]; setPositions(next); savePositions(next);
    recordTrade(newPos.id, 'BUY', shares, price); setModal(null);
  }
  function buyMore(posId: string, extra: number, price: number) {
    const next = positions.map(p => { if (p.id !== posId) return p; const total = p.shares + extra; return { ...p, shares: total, avgCost: (p.shares * p.avgCost + extra * price) / total }; });
    setPositions(next); savePositions(next); recordTrade(posId, 'BUY', extra, price); setModal(null);
  }
  function sellShares(posId: string, sell: number, price: number) {
    const next = positions.map(p => { if (p.id !== posId) return p; const rem = p.shares - sell; return rem <= 0 ? null : { ...p, shares: rem }; }).filter(Boolean) as Position[];
    setPositions(next); savePositions(next); recordTrade(posId, 'SELL', sell, price); setModal(null);
  }
  function removePosition(id: string) { const next = positions.filter(p => p.id !== id); setPositions(next); savePositions(next); }
  function recordTrade(posId: string, type: 'BUY' | 'SELL', shares: number, price: number) {
    const next = { ...trades }; if (!next[posId]) next[posId] = [];
    next[posId] = [{ type, shares, price, date: new Date().toISOString() }, ...next[posId]];
    setTrades(next); saveTrades(next);
  }
  function handleModalConfirm(shares: number, price: number, symbol?: string, currency?: string) {
    if (!modal) return;
    if (modal.mode === 'add') { addPosition(shares, price, symbol, currency); return; }
    if (!modal.posId) return;
    if (modal.mode === 'buy')  buyMore(modal.posId, shares, price);
    if (modal.mode === 'sell') sellShares(modal.posId, shares, price);
  }

  /* ── Enriched rows ── */
  const rows = useMemo(() => positions.map(p => {
    const lp  = priceMap[p.symbol];
    const cur = lp?.price ?? null;
    const cost = p.shares * p.avgCost;
    const mktVal = cur != null ? p.shares * cur : null;
    const pnl = mktVal != null ? mktVal - cost : null;
    const pnlPct = pnl != null && cost > 0 ? (pnl / cost) * 100 : null;
    const dayPnl = lp?.change_pct != null && mktVal != null ? mktVal * (lp.change_pct / 100) / (1 + lp.change_pct / 100) : null;
    const rank = rankMap[p.symbol];
    const sig  = signalMap[p.symbol]?.signal ?? signalFromScore(rank?.score);
    return { ...p, cur, cost, mktVal, pnl, pnlPct, dayPnl, changeUp: (lp?.change_pct ?? 0) >= 0, changePct: lp?.change_pct ?? null, rank, sig };
  }), [positions, priceMap, rankMap, signalMap]);

  /* ── Sorted rows ── */
  const sortedRows = useMemo(() => [...rows].sort((a, b) => {
    let diff = 0;
    if (sortKey === 'symbol')  diff = a.symbol.localeCompare(b.symbol);
    else if (sortKey === 'pnl')    diff = (a.pnl ?? -Infinity) - (b.pnl ?? -Infinity);
    else if (sortKey === 'pnlPct') diff = (a.pnlPct ?? -Infinity) - (b.pnlPct ?? -Infinity);
    else if (sortKey === 'value')  diff = (a.mktVal ?? 0) - (b.mktVal ?? 0);
    else if (sortKey === 'change') diff = (a.changePct ?? 0) - (b.changePct ?? 0);
    else if (sortKey === 'score')  diff = (a.rank?.score ?? 0) - (b.rank?.score ?? 0);
    return sortAsc ? diff : -diff;
  }), [rows, sortKey, sortAsc]);

  /* ── Portfolio totals ── */
  const totals = useMemo(() => {
    let invested = 0, currentVal = 0, dayPnlTotal = 0;
    for (const r of rows) { invested += r.cost; currentVal += r.mktVal ?? r.cost; if (r.dayPnl) dayPnlTotal += r.dayPnl; }
    return { invested, currentVal, pnl: currentVal - invested, pnlPct: invested > 0 ? ((currentVal - invested) / invested) * 100 : 0, dayPnl: dayPnlTotal };
  }, [rows]);

  const best  = rows.filter(r => r.pnlPct != null).sort((a, b) => (b.pnlPct ?? 0) - (a.pnlPct ?? 0))[0];
  const worst = rows.filter(r => r.pnlPct != null).sort((a, b) => (a.pnlPct ?? 0) - (b.pnlPct ?? 0))[0];

  /* ── Donut chart data ── */
  const chartValues = sortedRows.map(r => r.mktVal ?? r.cost);
  const chartLabels = sortedRows.map(r => r.symbol);
  const chartColors = ['#6366f1','#8b5cf6','#ec4899','#f59e0b','#10b981','#3b82f6','#f97316','#14b8a6','#ef4444','#84cc16','#06b6d4','#a855f7'];

  function sortBtn(key: SortKey, label: string) {
    const active = sortKey === key;
    return (
      <button key={key} onClick={() => { if (active) setSortAsc(v => !v); else { setSortKey(key); setSortAsc(false); } }}
        style={{ padding: '4px 9px', borderRadius: '4px', border: 'none', cursor: 'pointer', background: active ? '#334155' : 'transparent', color: active ? '#e2e8f0' : '#475569', fontSize: '11px', display: 'flex', alignItems: 'center', gap: '2px' }}>
        {label}{active ? (sortAsc ? ' ▲' : ' ▼') : ''}
      </button>
    );
  }

  const modalPos = modal?.posId ? positions.find(p => p.id === modal.posId) : undefined;
  const modalCurrentPrice = modalPos ? priceMap[modalPos.symbol]?.price : undefined;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '12px' }}>
        <div>
          <h1 style={{ fontSize: '20px', fontWeight: 700, margin: 0 }}>Positions</h1>
          <p style={{ fontSize: '12px', color: '#475569', margin: '4px 0 0' }}>Track your portfolio — prices live</p>
        </div>
        <div style={{ display: 'flex', gap: '8px' }}>
          <button onClick={handleRefresh} disabled={refreshing} style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '7px 13px', borderRadius: '6px', border: '1px solid rgba(148,163,184,0.15)', background: 'rgba(255,255,255,0.03)', color: refreshing ? '#818cf8' : '#64748b', cursor: 'pointer', fontSize: '13px' }}>
            <span style={{ display: 'inline-block', animation: refreshing ? 'spin 0.8s linear infinite' : 'none' }}>↻</span> Refresh
          </button>
          {rows.length > 0 && (
            <button onClick={() => exportCSV(rows.map(r => ({ symbol: r.symbol, shares: r.shares, avgCost: r.avgCost, curPrice: r.cur, mktVal: r.mktVal, pnl: r.pnl, pnlPct: r.pnlPct, currency: r.currency })))}
              style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '7px 13px', borderRadius: '6px', border: '1px solid rgba(148,163,184,0.12)', background: 'rgba(255,255,255,0.03)', color: '#64748b', cursor: 'pointer', fontSize: '13px' }}>
              ⬇ CSV
            </button>
          )}
          <button onClick={() => setModal({ mode: 'add' })} style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '7px 16px', borderRadius: '6px', background: 'linear-gradient(135deg,#4f46e5,#6366f1)', border: 'none', color: '#fff', fontSize: '13px', fontWeight: 600, cursor: 'pointer', boxShadow: '0 4px 12px rgba(99,102,241,0.3)' }}>
            + Add Position
          </button>
        </div>
      </div>

      {/* Summary stats */}
      {rows.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '10px' }}>
          {[
            { label: 'Positions',     value: String(rows.length),                                          sub: 'open',        color: '#e2e8f0' },
            { label: 'Invested',      value: `$${fmt(totals.invested)}`,                                   sub: 'cost basis',  color: '#e2e8f0' },
            { label: 'Market Value',  value: `$${fmt(totals.currentVal)}`,                                 sub: 'current',     color: '#e2e8f0' },
            { label: "Today's P&L",   value: `${totals.dayPnl >= 0 ? '+' : ''}$${fmt(Math.abs(totals.dayPnl))}`,  sub: 'unrealized', color: pnlColor(totals.dayPnl) },
            { label: 'Total P&L',     value: `${totals.pnl >= 0 ? '+' : ''}$${fmt(Math.abs(totals.pnl))}`, sub: `${totals.pnl >= 0 ? '+' : ''}${fmt(totals.pnlPct)}%`, color: pnlColor(totals.pnl) },
          ].map(c => (
            <div key={c.label} style={{ borderRadius: '10px', border: '1px solid #1e293b', background: '#0f172a', padding: '13px 15px' }}>
              <div style={{ fontSize: '10px', color: '#475569', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: '5px' }}>{c.label}</div>
              <div style={{ fontSize: '18px', fontWeight: 700, color: c.color, lineHeight: 1.2 }}>{c.value}</div>
              <div style={{ fontSize: '10px', color: '#475569', marginTop: '3px' }}>{c.sub}</div>
            </div>
          ))}
        </div>
      )}

      {/* Chart + highlights */}
      {rows.length > 1 && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>

          {/* Allocation donut */}
          <div style={{ borderRadius: '10px', border: '1px solid #1e293b', background: '#0f172a', padding: '16px' }}>
            <div style={{ fontSize: '12px', fontWeight: 700, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: '4px' }}>Allocation</div>
            <DonutChart labels={chartLabels} values={chartValues} colors={chartColors} height={220} />
          </div>

          {/* Best / Worst / Signal breakdown */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {best && (
              <div style={{ borderRadius: '10px', border: '1px solid rgba(34,197,94,0.25)', background: 'rgba(34,197,94,0.06)', padding: '14px 16px' }}>
                <div style={{ fontSize: '10px', color: '#4ade80', fontWeight: 700, letterSpacing: '0.07em', marginBottom: '6px' }}>🏆 BEST PERFORMER</div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <Link href={`/stock/${best.symbol}`} style={{ fontWeight: 800, fontSize: '16px', color: '#f1f5f9', fontFamily: 'monospace' }}>{best.symbol}</Link>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{ fontSize: '15px', fontWeight: 800, color: '#4ade80' }}>+{fmt(best.pnlPct ?? 0)}%</div>
                    <div style={{ fontSize: '11px', color: '#16a34a' }}>+${fmt(best.pnl ?? 0)}</div>
                  </div>
                </div>
              </div>
            )}
            {worst && worst.symbol !== best?.symbol && (
              <div style={{ borderRadius: '10px', border: '1px solid rgba(239,68,68,0.25)', background: 'rgba(239,68,68,0.06)', padding: '14px 16px' }}>
                <div style={{ fontSize: '10px', color: '#f87171', fontWeight: 700, letterSpacing: '0.07em', marginBottom: '6px' }}>📉 WORST PERFORMER</div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <Link href={`/stock/${worst.symbol}`} style={{ fontWeight: 800, fontSize: '16px', color: '#f1f5f9', fontFamily: 'monospace' }}>{worst.symbol}</Link>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{ fontSize: '15px', fontWeight: 800, color: '#f87171' }}>{fmt(worst.pnlPct ?? 0)}%</div>
                    <div style={{ fontSize: '11px', color: '#ef4444' }}>${fmt(worst.pnl ?? 0)}</div>
                  </div>
                </div>
              </div>
            )}
            {/* Signal summary */}
            <div style={{ borderRadius: '10px', border: '1px solid #1e293b', background: '#0f172a', padding: '14px 16px', flex: 1 }}>
              <div style={{ fontSize: '10px', color: '#475569', fontWeight: 700, letterSpacing: '0.07em', marginBottom: '10px' }}>SIGNALS IN PORTFOLIO</div>
              {(['BUY','HOLD','SELL'] as const).map(s => {
                const count = rows.filter(r => r.sig === s).length;
                const pct   = rows.length > 0 ? (count / rows.length) * 100 : 0;
                const c     = s === 'BUY' ? '#4ade80' : s === 'SELL' ? '#f87171' : '#facc15';
                const bg    = s === 'BUY' ? 'rgba(34,197,94,0.2)' : s === 'SELL' ? 'rgba(239,68,68,0.2)' : 'rgba(250,204,21,0.2)';
                return (
                  <div key={s} style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
                    <span style={{ width: '34px', fontSize: '10px', fontWeight: 700, color: c }}>{s}</span>
                    <div style={{ flex: 1, height: '6px', borderRadius: '3px', background: '#1e293b', overflow: 'hidden' }}>
                      <div style={{ height: '100%', width: `${pct}%`, background: bg, borderRadius: '3px', transition: 'width 0.4s' }} />
                    </div>
                    <span style={{ fontSize: '11px', color: '#475569', width: '20px', textAlign: 'right' }}>{count}</span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* Empty */}
      {rows.length === 0 && (
        <div style={{ textAlign: 'center', padding: '60px 0' }}>
          <div style={{ fontSize: '40px', marginBottom: '12px' }}>📊</div>
          <div style={{ fontSize: '15px', fontWeight: 600, color: '#cbd5e1', marginBottom: '6px' }}>No positions yet</div>
          <div style={{ fontSize: '13px', color: '#475569', marginBottom: '20px' }}>Add your holdings to track P&amp;L in real time.</div>
          <button onClick={() => setModal({ mode: 'add' })} style={{ padding: '9px 20px', borderRadius: '8px', background: 'linear-gradient(135deg,#4f46e5,#6366f1)', border: 'none', color: '#fff', fontSize: '13px', fontWeight: 600, cursor: 'pointer' }}>+ Add First Position</button>
        </div>
      )}

      {/* Table */}
      {rows.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {/* Sort bar */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '11px', color: '#475569', paddingLeft: '2px' }}>
            Sort: {sortBtn('symbol','Symbol')} {sortBtn('value','Value')} {sortBtn('pnl','P&L$')} {sortBtn('pnlPct','P&L%')} {sortBtn('change','Today')} {sortBtn('score','K-Score')}
          </div>

          {/* Column headers */}
          <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 70px 85px 85px 95px 105px 105px 120px', gap: '6px', padding: '6px 14px', fontSize: '10px', fontWeight: 700, color: '#334155', textTransform: 'uppercase', letterSpacing: '0.07em' }}>
            <div>Symbol</div>
            <div style={{ textAlign: 'right' }}>Shares</div>
            <div style={{ textAlign: 'right' }}>Avg Cost</div>
            <div style={{ textAlign: 'right' }}>Cur Price</div>
            <div style={{ textAlign: 'right' }}>Mkt Value</div>
            <div style={{ textAlign: 'right' }}>P&L ($)</div>
            <div style={{ textAlign: 'right' }}>P&L (%)</div>
            <div style={{ textAlign: 'right' }}>Actions</div>
          </div>

          {sortedRows.map(r => {
            const ss = r.sig ? sigStyle(r.sig) : null;
            return (
              <div key={r.id}>
                <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 70px 85px 85px 95px 105px 105px 120px', gap: '6px', padding: '11px 14px', borderRadius: '10px', border: '1px solid #1e293b', background: '#0f172a', alignItems: 'center', transition: 'border-color 0.15s' }} className="pos-row">

                  {/* Symbol + signal */}
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <Link href={`/stock/${r.symbol}`} style={{ fontWeight: 700, fontSize: '14px', color: '#f1f5f9', fontFamily: 'monospace' }}>{r.symbol}</Link>
                      {ss && r.sig && <span style={{ fontSize: '9px', fontWeight: 800, padding: '2px 6px', borderRadius: '4px', color: ss.color, background: ss.bg, border: `1px solid ${ss.border}`, letterSpacing: '0.05em' }}>{r.sig}</span>}
                    </div>
                    <div style={{ fontSize: '10px', color: '#334155', marginTop: '2px', display: 'flex', gap: '6px', alignItems: 'center' }}>
                      <span>{r.currency}</span>
                      {r.changePct != null && <span style={{ color: r.changeUp ? '#4ade80' : '#f87171' }}>{r.changeUp ? '▲' : '▼'} {Math.abs(r.changePct).toFixed(2)}%</span>}
                      {r.rank?.score != null && <span style={{ color: scoreColor(r.rank.score) }}>K{r.rank.score.toFixed(0)}</span>}
                    </div>
                  </div>

                  <div style={{ textAlign: 'right', fontSize: '12px', color: '#cbd5e1', fontWeight: 600 }}>{fmt(r.shares, 2).replace(/\.?0+$/, '')}</div>
                  <div style={{ textAlign: 'right', fontSize: '12px', color: '#94a3b8' }}>{fmt(r.avgCost)}</div>
                  <div style={{ textAlign: 'right', fontSize: '12px', color: r.cur != null ? '#e2e8f0' : '#334155' }}>{r.cur != null ? fmt(r.cur) : '—'}</div>
                  <div style={{ textAlign: 'right', fontSize: '12px', fontWeight: 600, color: '#e2e8f0' }}>${fmt(r.mktVal ?? r.cost)}</div>
                  <div style={{ textAlign: 'right', fontSize: '12px', fontWeight: 700, color: r.pnl != null ? pnlColor(r.pnl) : '#334155' }}>{r.pnl != null ? `${r.pnl >= 0 ? '+' : ''}$${fmt(Math.abs(r.pnl))}` : '—'}</div>
                  <div style={{ textAlign: 'right', fontSize: '12px', fontWeight: 700, color: r.pnlPct != null ? pnlColor(r.pnlPct) : '#334155' }}>{r.pnlPct != null ? `${r.pnlPct >= 0 ? '+' : ''}${fmt(r.pnlPct)}%` : '—'}</div>

                  {/* Actions */}
                  <div style={{ display: 'flex', gap: '3px', justifyContent: 'flex-end', flexWrap: 'wrap' }}>
                    <button onClick={() => setModal({ mode: 'buy', posId: r.id })} style={{ padding: '3px 7px', borderRadius: '4px', background: 'rgba(79,70,229,0.15)', border: '1px solid rgba(79,70,229,0.3)', color: '#818cf8', fontSize: '9px', fontWeight: 800, cursor: 'pointer' }}>BUY</button>
                    <button onClick={() => setModal({ mode: 'sell', posId: r.id })} style={{ padding: '3px 7px', borderRadius: '4px', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.25)', color: '#f87171', fontSize: '9px', fontWeight: 800, cursor: 'pointer' }}>SELL</button>
                    <button onClick={() => toggleWatch(r.symbol)} title={watchedSet.has(r.symbol) ? 'Unwatch' : 'Watch'} style={{ padding: '3px 6px', borderRadius: '4px', background: watchedSet.has(r.symbol) ? 'rgba(99,102,241,0.15)' : 'rgba(255,255,255,0.04)', border: `1px solid ${watchedSet.has(r.symbol) ? 'rgba(99,102,241,0.3)' : 'rgba(255,255,255,0.08)'}`, color: watchedSet.has(r.symbol) ? '#818cf8' : '#475569', fontSize: '11px', cursor: 'pointer' }}>★</button>
                    <button onClick={() => setShowTradesFor(showTradesFor === r.id ? null : r.id)} style={{ padding: '3px 6px', borderRadius: '4px', background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', color: '#64748b', fontSize: '10px', cursor: 'pointer' }}>{showTradesFor === r.id ? '▲' : '▼'}</button>
                    <button onClick={() => removePosition(r.id)} style={{ background: 'transparent', border: 'none', color: '#334155', cursor: 'pointer', fontSize: '11px', padding: '3px 4px' }} className="del-btn">✕</button>
                  </div>
                </div>

                {/* Trade history drawer */}
                {showTradesFor === r.id && trades[r.id] && trades[r.id].length > 0 && (
                  <div style={{ marginTop: '2px', marginLeft: '14px', borderLeft: '2px solid #1e293b', paddingLeft: '14px', display: 'flex', flexDirection: 'column', gap: '3px', paddingBottom: '4px' }}>
                    <div style={{ fontSize: '10px', color: '#334155', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', padding: '5px 0 2px' }}>Trade History</div>
                    {trades[r.id].map((t, i) => (
                      <div key={i} style={{ display: 'flex', gap: '12px', fontSize: '11px', color: '#475569', padding: '4px 8px', borderRadius: '6px', background: 'rgba(255,255,255,0.02)' }}>
                        <span style={{ fontWeight: 700, color: t.type === 'BUY' ? '#818cf8' : '#f87171', width: '30px' }}>{t.type}</span>
                        <span>{fmt(t.shares, 2).replace(/\.?0+$/, '')} shares</span>
                        <span>@ ${fmt(t.price)}</span>
                        <span style={{ color: '#334155', marginLeft: 'auto' }}>{new Date(t.date).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {modal && <TradeModal mode={modal.mode} position={modalPos} currentPrice={modalCurrentPrice} onConfirm={handleModalConfirm} onClose={() => setModal(null)} />}

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        .pos-row:hover { border-color: #334155 !important; }
        .del-btn:hover { color: #f87171 !important; }
      `}</style>
    </div>
  );
}
