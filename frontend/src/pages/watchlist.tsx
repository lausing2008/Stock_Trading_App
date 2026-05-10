import { useState, useMemo, useCallback, useEffect, useRef } from 'react';
import useSWR, { mutate as globalMutate } from 'swr';
import Link from 'next/link';
import { useRouter } from 'next/router';
import { api, type WatchlistItem, type WatchlistMeta, type RankingRow, type LatestPrice, type SignalSummary, type Stock } from '@/lib/api';
import { storage } from '@/lib/storage';

/* ── helpers ────────────────────────────────────────────── */
const NOTES_KEY  = 'watch_notes';
const ALERTS_KEY = 'watch_price_alerts';

function loadNotes(): Record<string, string> {
  if (typeof window === 'undefined') return {};
  try { return JSON.parse(storage.getItem(NOTES_KEY) ?? '{}'); } catch { return {}; }
}
function saveNotes(n: Record<string, string>) { storage.setItem(NOTES_KEY, JSON.stringify(n)); }
function loadAlerts(): Record<string, { target: number; dir: 'above' | 'below' }> {
  if (typeof window === 'undefined') return {};
  try { return JSON.parse(storage.getItem(ALERTS_KEY) ?? '{}'); } catch { return {}; }
}
function saveAlerts(a: Record<string, { target: number; dir: 'above' | 'below' }>) {
  storage.setItem(ALERTS_KEY, JSON.stringify(a));
}

function sigStyle(label: string) {
  if (label === 'BUY')  return { color: '#4ade80', bg: 'rgba(34,197,94,0.1)',   border: 'rgba(34,197,94,0.3)'   };
  if (label === 'SELL') return { color: '#f87171', bg: 'rgba(239,68,68,0.1)',   border: 'rgba(239,68,68,0.3)'   };
  if (label === 'WAIT') return { color: '#fb923c', bg: 'rgba(251,146,60,0.1)',  border: 'rgba(251,146,60,0.3)'  };
  return                       { color: '#facc15', bg: 'rgba(250,204,21,0.1)',  border: 'rgba(250,204,21,0.3)'  };
}
function signalFromScore(score?: number | null) {
  if (score == null) return null;
  return score >= 65 ? 'BUY' : score >= 40 ? 'HOLD' : 'SELL';
}
function scoreColor(s: number) { return s >= 70 ? '#4ade80' : s >= 50 ? '#facc15' : '#f87171'; }
function fmt2(n: number) { return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }

type SortKey = 'symbol' | 'change' | 'score' | 'signal' | 'price';
type SigFilter = 'ALL' | 'BUY' | 'HOLD' | 'WAIT' | 'SELL';

/* ── Note modal ─────────────────────────────────────────── */
function NoteModal({ symbol, initial, onSave, onClose }: { symbol: string; initial: string; onSave: (v: string) => void; onClose: () => void }) {
  const [val, setVal] = useState(initial);
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '16px' }}>
      <div onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(6,8,20,0.8)', backdropFilter: 'blur(4px)' }} />
      <div style={{ position: 'relative', zIndex: 10, width: '100%', maxWidth: '360px', borderRadius: '14px', background: '#0d1424', border: '1px solid rgba(99,102,241,0.3)', boxShadow: '0 24px 48px rgba(0,0,0,0.5)', overflow: 'hidden' }}>
        <div style={{ height: '3px', background: 'linear-gradient(90deg,#4f46e5,#818cf8,#4f46e5)' }} />
        <div style={{ padding: '18px 20px 20px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '12px' }}>
            <span style={{ fontWeight: 700, fontSize: '14px', color: '#f1f5f9' }}>📝 Note — {symbol}</span>
            <button onClick={onClose} style={{ background: 'none', border: 'none', color: '#475569', cursor: 'pointer' }}>✕</button>
          </div>
          <textarea
            autoFocus
            value={val}
            onChange={e => setVal(e.target.value)}
            placeholder="Why are you watching this? Price target, thesis…"
            rows={4}
            style={{ width: '100%', background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(148,163,184,0.12)', borderRadius: '8px', padding: '10px 12px', fontSize: '13px', color: '#e2e8f0', resize: 'vertical', outline: 'none', boxSizing: 'border-box', fontFamily: 'inherit' }}
          />
          <div style={{ display: 'flex', gap: '8px', marginTop: '12px' }}>
            <button onClick={() => { onSave(val); onClose(); }} style={{ flex: 1, borderRadius: '8px', padding: '8px', background: 'linear-gradient(135deg,#4f46e5,#6366f1)', border: 'none', color: '#fff', fontSize: '13px', fontWeight: 600, cursor: 'pointer' }}>Save</button>
            {initial && <button onClick={() => { onSave(''); onClose(); }} style={{ borderRadius: '8px', padding: '8px 14px', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.25)', color: '#f87171', fontSize: '12px', cursor: 'pointer' }}>Clear</button>}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── Alert modal ────────────────────────────────────────── */
function AlertModal({ symbol, price, initial, onSave, onClose }: { symbol: string; price?: number; initial?: { target: number; dir: 'above' | 'below' }; onSave: (target: number, dir: 'above' | 'below') => void; onClose: () => void }) {
  const [target, setTarget] = useState(initial?.target?.toString() ?? '');
  const [dir, setDir] = useState<'above' | 'below'>(initial?.dir ?? 'above');
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '16px' }}>
      <div onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(6,8,20,0.8)', backdropFilter: 'blur(4px)' }} />
      <div style={{ position: 'relative', zIndex: 10, width: '100%', maxWidth: '320px', borderRadius: '14px', background: '#0d1424', border: '1px solid rgba(250,204,21,0.3)', boxShadow: '0 24px 48px rgba(0,0,0,0.5)', overflow: 'hidden' }}>
        <div style={{ height: '3px', background: 'linear-gradient(90deg,#ca8a04,#facc15,#ca8a04)' }} />
        <div style={{ padding: '18px 20px 20px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ fontWeight: 700, fontSize: '14px', color: '#f1f5f9' }}>🔔 Price Alert — {symbol}</span>
            <button onClick={onClose} style={{ background: 'none', border: 'none', color: '#475569', cursor: 'pointer' }}>✕</button>
          </div>
          {price && <div style={{ fontSize: '11px', color: '#475569' }}>Current: <span style={{ color: '#94a3b8', fontWeight: 600 }}>${fmt2(price)}</span></div>}
          <div style={{ display: 'flex', gap: '8px' }}>
            {(['above', 'below'] as const).map(d => (
              <button key={d} onClick={() => setDir(d)} style={{ flex: 1, padding: '7px', borderRadius: '6px', border: `1px solid ${dir === d ? 'rgba(250,204,21,0.4)' : 'rgba(255,255,255,0.08)'}`, background: dir === d ? 'rgba(250,204,21,0.1)' : 'transparent', color: dir === d ? '#facc15' : '#64748b', fontSize: '12px', fontWeight: 600, cursor: 'pointer' }}>
                {d === 'above' ? '▲ Above' : '▼ Below'}
              </button>
            ))}
          </div>
          <input
            type="number" step="any" min="0"
            value={target}
            onChange={e => setTarget(e.target.value)}
            placeholder="Target price"
            style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(148,163,184,0.12)', borderRadius: '8px', padding: '9px 12px', fontSize: '13px', color: '#f1f5f9', outline: 'none', width: '100%', boxSizing: 'border-box' }}
          />
          <div style={{ display: 'flex', gap: '8px' }}>
            <button onClick={() => { const t = parseFloat(target); if (t > 0) { onSave(t, dir); onClose(); } }} style={{ flex: 1, borderRadius: '8px', padding: '8px', background: 'linear-gradient(135deg,#ca8a04,#facc15)', border: 'none', color: '#000', fontSize: '13px', fontWeight: 700, cursor: 'pointer' }}>Set Alert</button>
            {initial && <button onClick={() => { onSave(0, dir); onClose(); }} style={{ borderRadius: '8px', padding: '8px 14px', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.25)', color: '#f87171', fontSize: '12px', cursor: 'pointer' }}>Remove</button>}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── Create watchlist modal ─────────────────────────────── */
function CreateWatchlistModal({ onSave, onClose }: { onSave: (name: string) => Promise<void>; onClose: () => void }) {
  const [name, setName] = useState('');
  const [saving, setSaving] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setSaving(true);
    await onSave(name.trim());
    setSaving(false);
    onClose();
  }
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '16px' }}>
      <div onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(6,8,20,0.8)', backdropFilter: 'blur(4px)' }} />
      <div style={{ position: 'relative', zIndex: 10, width: '100%', maxWidth: '340px', borderRadius: '14px', background: '#0d1424', border: '1px solid rgba(99,102,241,0.3)', boxShadow: '0 24px 48px rgba(0,0,0,0.5)', overflow: 'hidden' }}>
        <div style={{ height: '3px', background: 'linear-gradient(90deg,#4f46e5,#818cf8,#4f46e5)' }} />
        <form onSubmit={submit} style={{ padding: '18px 20px 20px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '14px' }}>
            <span style={{ fontWeight: 700, fontSize: '14px', color: '#f1f5f9' }}>New Watchlist</span>
            <button type="button" onClick={onClose} style={{ background: 'none', border: 'none', color: '#475569', cursor: 'pointer' }}>✕</button>
          </div>
          <input
            autoFocus
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="e.g. Tech Stocks, Dividend Plays…"
            maxLength={64}
            style={{ width: '100%', background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(148,163,184,0.15)', borderRadius: '8px', padding: '9px 12px', fontSize: '13px', color: '#f1f5f9', outline: 'none', boxSizing: 'border-box' }}
          />
          <button
            type="submit"
            disabled={!name.trim() || saving}
            style={{ marginTop: '12px', width: '100%', borderRadius: '8px', padding: '9px', background: name.trim() ? 'linear-gradient(135deg,#4f46e5,#6366f1)' : 'rgba(255,255,255,0.05)', border: 'none', color: name.trim() ? '#fff' : '#475569', fontSize: '13px', fontWeight: 700, cursor: name.trim() ? 'pointer' : 'default' }}
          >
            {saving ? 'Creating…' : 'Create Watchlist'}
          </button>
        </form>
      </div>
    </div>
  );
}

/* ── Add-to-list modal ──────────────────────────────────── */
function AddToListModal({ listId, currentSymbols, onClose, onAdded }: {
  listId: number;
  currentSymbols: Set<string>;
  onClose: () => void;
  onAdded: () => void;
}) {
  const [query, setQuery] = useState('');
  const [adding, setAdding] = useState<string | null>(null);
  const [added, setAdded] = useState<Set<string>>(new Set());
  const { data: stocks } = useSWR<Stock[]>('stocks-universe', () => api.listStocks());
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => { inputRef.current?.focus(); }, []);
  useEffect(() => {
    const fn = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', fn);
    return () => window.removeEventListener('keydown', fn);
  }, [onClose]);

  const filtered = useMemo(() => {
    if (!stocks) return [];
    const q = query.trim().toLowerCase();
    return stocks.filter(s =>
      !q || s.symbol.toLowerCase().includes(q) || s.name.toLowerCase().includes(q)
    ).slice(0, 50);
  }, [stocks, query]);

  async function addStock(symbol: string) {
    setAdding(symbol);
    await api.addToWatchlist(symbol, listId);
    setAdded(prev => new Set(prev).add(symbol));
    setAdding(null);
    onAdded();
  }

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '16px' }}>
      <div onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(6,8,20,0.85)', backdropFilter: 'blur(6px)' }} />
      <div style={{ position: 'relative', zIndex: 10, width: '100%', maxWidth: '480px', maxHeight: '80vh', borderRadius: '16px', background: 'linear-gradient(160deg, #0d1424 0%, #090e1a 100%)', border: '1px solid rgba(99,102,241,0.3)', boxShadow: '0 32px 64px rgba(0,0,0,0.6)', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        <div style={{ height: '3px', background: 'linear-gradient(90deg, #4f46e5, #818cf8, #4f46e5)', flexShrink: 0 }} />
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '18px 20px 14px', flexShrink: 0 }}>
          <span style={{ fontSize: '15px', fontWeight: 700, color: '#f1f5f9' }}>Add stocks to list</span>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: '#475569', cursor: 'pointer', fontSize: '16px' }}>✕</button>
        </div>
        <div style={{ padding: '0 20px 12px', flexShrink: 0 }}>
          <input
            ref={inputRef}
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search by symbol or name…"
            style={{ width: '100%', background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(148,163,184,0.15)', borderRadius: '8px', padding: '9px 12px', fontSize: '13px', color: '#f1f5f9', outline: 'none', boxSizing: 'border-box' }}
          />
        </div>
        <div style={{ overflowY: 'auto', padding: '0 20px 20px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
          {!stocks && <div style={{ color: '#475569', fontSize: '13px', padding: '12px 0' }}>Loading…</div>}
          {filtered.map(stock => {
            const inList = currentSymbols.has(stock.symbol) || added.has(stock.symbol);
            const isAdding = adding === stock.symbol;
            return (
              <div key={stock.symbol} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '9px 12px', borderRadius: '8px', background: inList ? 'rgba(99,102,241,0.06)' : 'rgba(255,255,255,0.02)', border: `1px solid ${inList ? 'rgba(99,102,241,0.2)' : 'rgba(255,255,255,0.05)'}` }}>
                <div style={{ minWidth: 0 }}>
                  <span style={{ fontWeight: 700, fontSize: '13px', color: '#f1f5f9', fontFamily: 'ui-monospace, monospace' }}>{stock.symbol}</span>
                  <span style={{ fontSize: '12px', color: '#475569', marginLeft: '8px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{stock.name}</span>
                </div>
                <button
                  onClick={() => !inList && addStock(stock.symbol)}
                  disabled={inList || isAdding}
                  style={{ flexShrink: 0, marginLeft: '8px', padding: '5px 12px', borderRadius: '6px', border: 'none', fontSize: '12px', fontWeight: 700, cursor: inList ? 'default' : 'pointer', background: inList ? 'rgba(34,197,94,0.1)' : 'linear-gradient(135deg,#4f46e5,#6366f1)', color: inList ? '#4ade80' : '#fff', opacity: isAdding ? 0.6 : 1 }}
                >
                  {isAdding ? '…' : inList ? '✓' : '+ Add'}
                </button>
              </div>
            );
          })}
          {stocks && filtered.length === 0 && (
            <div style={{ color: '#475569', fontSize: '13px', padding: '16px 0', textAlign: 'center' }}>No tracked stocks match "{query}"</div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── Main ───────────────────────────────────────────────── */
export default function Watchlist() {
  const router = useRouter();

  // Watchlist meta (tabs)
  const { data: lists, mutate: mutateLists } = useSWR<WatchlistMeta[]>('watchlists', () => api.listWatchlists());
  const [activeListId, setActiveListId] = useState<number | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  // Resolve active list id from fetched lists
  const resolvedListId = activeListId ?? lists?.[0]?.id ?? null;

  const { data, error, isLoading, mutate: mutateWatchlist } = useSWR<WatchlistItem[]>(
    resolvedListId != null ? ['watchlist', resolvedListId] : null,
    () => api.listWatchlist(resolvedListId!),
  );
  const { data: rankingsData, mutate: mutateRankings } = useSWR<{ rankings: RankingRow[] }>('rankings-all', () => api.rankings());
  const { data: pricesData, mutate: mutatePrices } = useSWR<LatestPrice[]>('latest-prices', () => api.latestPrices(), { refreshInterval: 60_000 });
  const { data: signalsData, mutate: mutateSignals } = useSWR<SignalSummary[]>('signals-all', () => api.allSignals());

  const [showAddToList, setShowAddToList] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const [moveMenu, setMoveMenu] = useState<string | null>(null);
  const [moving, setMoving] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [sigFilter, setSigFilter] = useState<SigFilter>('ALL');
  const [sortKey, setSortKey] = useState<SortKey>('symbol');
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [alerts, setAlerts] = useState<Record<string, { target: number; dir: 'above' | 'below' }>>({});
  const [noteModal, setNoteModal] = useState<string | null>(null);
  const [alertModal, setAlertModal] = useState<string | null>(null);

  useEffect(() => { setNotes(loadNotes()); setAlerts(loadAlerts()); }, []);

  const rankMap = useMemo(() => { const m: Record<string, RankingRow> = {}; for (const r of rankingsData?.rankings ?? []) m[r.symbol] = r; return m; }, [rankingsData]);
  const priceMap = useMemo(() => { const m: Record<string, LatestPrice> = {}; for (const p of pricesData ?? []) m[p.symbol] = p; return m; }, [pricesData]);
  const signalMap = useMemo(() => { const m: Record<string, SignalSummary> = {}; for (const s of signalsData ?? []) m[s.symbol] = s; return m; }, [signalsData]);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    await Promise.all([mutateWatchlist(), mutateRankings(), mutatePrices(), mutateSignals(), mutateLists()]);
    setRefreshing(false);
  }, [mutateWatchlist, mutateRankings, mutatePrices, mutateSignals, mutateLists]);

  async function remove(symbol: string) {
    setRemoving(symbol);
    await api.removeFromWatchlist(symbol, resolvedListId ?? undefined);
    mutateWatchlist();
    mutateLists();
    setRemoving(null);
  }

  async function moveToList(symbol: string, targetId: number) {
    setMoveMenu(null);
    setMoving(symbol);
    await api.addToWatchlist(symbol, targetId);
    await api.removeFromWatchlist(symbol, resolvedListId ?? undefined);
    mutateWatchlist();
    mutateLists();
    globalMutate(['watchlist', targetId]);
    setMoving(null);
  }

  useEffect(() => {
    if (!moveMenu) return;
    function handler(e: MouseEvent) {
      const el = document.getElementById(`move-menu-${moveMenu}`);
      if (el && !el.contains(e.target as Node)) setMoveMenu(null);
    }
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [moveMenu]);

  async function handleCreateWatchlist(name: string) {
    await api.createWatchlist(name);
    const updated = await mutateLists();
    const newList = updated?.find(l => l.name === name);
    if (newList) setActiveListId(newList.id);
  }

  async function handleDeleteWatchlist(id: number) {
    setDeletingId(id);
    await api.deleteWatchlist(id);
    await mutateLists();
    if (activeListId === id) setActiveListId(null);
    setDeletingId(null);
  }

  function saveNote(symbol: string, val: string) {
    const next = { ...notes, [symbol]: val };
    if (!val) delete next[symbol];
    setNotes(next); saveNotes(next);
  }
  function saveAlert(symbol: string, target: number, dir: 'above' | 'below') {
    const next = { ...alerts };
    if (target === 0) delete next[symbol];
    else next[symbol] = { target, dir };
    setAlerts(next); saveAlerts(next);
  }

  /* Signal for each item: real signal engine first, K-Score fallback */
  function getSignal(symbol: string): string | null {
    if (signalMap[symbol]) return signalMap[symbol].signal;
    return signalFromScore(rankMap[symbol]?.score);
  }

  /* Stats */
  const stats = useMemo(() => {
    const counts = { BUY: 0, HOLD: 0, WAIT: 0, SELL: 0 };
    for (const item of data ?? []) {
      const s = getSignal(item.symbol);
      if (s === 'BUY' || s === 'HOLD' || s === 'WAIT' || s === 'SELL') counts[s]++;
    }
    return counts;
  }, [data, signalMap, rankMap]);

  /* Filtered + sorted */
  const visible = useMemo(() => {
    let list = (data ?? []).filter(item => {
      if (sigFilter === 'ALL') return true;
      return getSignal(item.symbol) === sigFilter;
    });
    list = [...list].sort((a, b) => {
      const lpA = priceMap[a.symbol], lpB = priceMap[b.symbol];
      const rkA = rankMap[a.symbol], rkB = rankMap[b.symbol];
      if (sortKey === 'symbol') return a.symbol.localeCompare(b.symbol);
      if (sortKey === 'change') return (lpB?.change_pct ?? 0) - (lpA?.change_pct ?? 0);
      if (sortKey === 'score')  return (rkB?.score ?? 0) - (rkA?.score ?? 0);
      if (sortKey === 'price')  return (lpB?.price ?? 0) - (lpA?.price ?? 0);
      if (sortKey === 'signal') {
        const order = { BUY: 0, HOLD: 1, WAIT: 2, SELL: 3, null: 4 };
        return (order[getSignal(a.symbol) as keyof typeof order] ?? 3) - (order[getSignal(b.symbol) as keyof typeof order] ?? 3);
      }
      return 0;
    });
    return list;
  }, [data, sigFilter, sortKey, priceMap, rankMap, signalMap]);

  const noteItem  = noteModal  ? data?.find(d => d.symbol === noteModal)  : null;
  const alertItem = alertModal ? data?.find(d => d.symbol === alertModal) : null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '12px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 700, margin: 0 }}>Watchlist</h1>
        <button onClick={handleRefresh} disabled={refreshing} style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '7px 14px', borderRadius: '6px', border: '1px solid rgba(148,163,184,0.15)', background: 'rgba(255,255,255,0.03)', color: refreshing ? '#818cf8' : '#64748b', cursor: 'pointer', fontSize: '13px' }}>
          <span style={{ display: 'inline-block', animation: refreshing ? 'spin 0.8s linear infinite' : 'none' }}>↻</span>
          {refreshing ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      {/* Watchlist tabs */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
        {(lists ?? []).map(list => {
          const isActive = list.id === resolvedListId;
          return (
            <div key={list.id} style={{ display: 'flex', alignItems: 'center', borderRadius: '8px', border: `1px solid ${isActive ? 'rgba(99,102,241,0.5)' : '#1e293b'}`, background: isActive ? 'rgba(99,102,241,0.12)' : 'rgba(255,255,255,0.02)', overflow: 'hidden' }}>
              <button
                onClick={() => setActiveListId(list.id)}
                style={{ padding: '6px 14px', background: 'none', border: 'none', cursor: 'pointer', fontSize: '13px', fontWeight: isActive ? 700 : 400, color: isActive ? '#818cf8' : '#64748b', whiteSpace: 'nowrap' }}
              >
                {list.name}
                <span style={{ marginLeft: '6px', fontSize: '11px', color: isActive ? '#6366f1' : '#334155' }}>{list.item_count}</span>
              </button>
              {(lists ?? []).length > 1 && (
                <button
                  onClick={() => handleDeleteWatchlist(list.id)}
                  disabled={deletingId === list.id}
                  title="Delete watchlist"
                  style={{ padding: '4px 8px', background: 'none', border: 'none', borderLeft: '1px solid rgba(255,255,255,0.05)', cursor: 'pointer', color: '#334155', fontSize: '11px' }}
                  className="del-tab-btn"
                >✕</button>
              )}
            </div>
          );
        })}
        <button
          onClick={() => setShowCreateModal(true)}
          style={{ display: 'flex', alignItems: 'center', gap: '5px', padding: '6px 12px', borderRadius: '8px', border: '1px dashed #334155', background: 'transparent', color: '#475569', cursor: 'pointer', fontSize: '13px', fontWeight: 500 }}
        >
          + New Watchlist
        </button>
        {resolvedListId != null && (
          <button
            onClick={() => setShowAddToList(true)}
            style={{ display: 'flex', alignItems: 'center', gap: '5px', padding: '6px 14px', borderRadius: '8px', border: '1px solid rgba(99,102,241,0.35)', background: 'rgba(99,102,241,0.08)', color: '#818cf8', cursor: 'pointer', fontSize: '13px', fontWeight: 600 }}
          >
            + Add Stocks
          </button>
        )}
      </div>

      {isLoading && <div style={{ color: '#475569', fontSize: '13px' }}>Loading…</div>}
      {error    && <div style={{ color: '#94a3b8', fontSize: '13px' }}>Failed to load watchlist.</div>}

      {data && data.length > 0 && (<>
        {/* Signal stats bar */}
        <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
          {([['BUY', '#4ade80', 'rgba(34,197,94,0.1)', 'rgba(34,197,94,0.25)'], ['HOLD', '#facc15', 'rgba(250,204,21,0.1)', 'rgba(250,204,21,0.25)'], ['WAIT', '#fb923c', 'rgba(251,146,60,0.1)', 'rgba(251,146,60,0.25)'], ['SELL', '#f87171', 'rgba(239,68,68,0.1)', 'rgba(239,68,68,0.25)']] as const).map(([label, color, bg, border]) => (
            <div key={label} style={{ borderRadius: '10px', border: `1px solid ${border}`, background: bg, padding: '10px 18px', textAlign: 'center', minWidth: '80px' }}>
              <div style={{ fontSize: '20px', fontWeight: 800, color, lineHeight: 1 }}>{stats[label as keyof typeof stats]}</div>
              <div style={{ fontSize: '10px', fontWeight: 700, color, marginTop: '3px', letterSpacing: '0.06em' }}>{label}</div>
            </div>
          ))}
          <div style={{ borderRadius: '10px', border: '1px solid #1e293b', background: '#0f172a', padding: '10px 18px', textAlign: 'center', minWidth: '80px' }}>
            <div style={{ fontSize: '20px', fontWeight: 800, color: '#e2e8f0', lineHeight: 1 }}>{data.length}</div>
            <div style={{ fontSize: '10px', fontWeight: 700, color: '#475569', marginTop: '3px', letterSpacing: '0.06em' }}>TOTAL</div>
          </div>
        </div>

        {/* Filter + Sort bar */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', borderRadius: '6px', border: '1px solid #334155', overflow: 'hidden', fontSize: '12px', fontWeight: 600 }}>
            {(['ALL', 'BUY', 'HOLD', 'WAIT', 'SELL'] as SigFilter[]).map(f => (
              <button key={f} onClick={() => setSigFilter(f)} style={{ padding: '6px 12px', border: 'none', cursor: 'pointer', transition: 'all 0.15s', background: sigFilter === f ? (f === 'BUY' ? 'rgba(34,197,94,0.2)' : f === 'SELL' ? 'rgba(239,68,68,0.2)' : f === 'WAIT' ? 'rgba(251,146,60,0.15)' : f === 'HOLD' ? 'rgba(250,204,21,0.15)' : '#334155') : 'transparent', color: sigFilter === f ? (f === 'BUY' ? '#4ade80' : f === 'SELL' ? '#f87171' : f === 'WAIT' ? '#fb923c' : f === 'HOLD' ? '#facc15' : '#e2e8f0') : '#64748b' }}>
                {f}
              </button>
            ))}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '4px', marginLeft: 'auto', fontSize: '12px', color: '#475569' }}>
            Sort:
            {([['symbol', 'Symbol'], ['signal', 'Signal'], ['score', 'K-Score'], ['change', 'Change%'], ['price', 'Price']] as [SortKey, string][]).map(([k, label]) => (
              <button key={k} onClick={() => setSortKey(k)} style={{ padding: '4px 8px', borderRadius: '4px', border: 'none', cursor: 'pointer', background: sortKey === k ? '#334155' : 'transparent', color: sortKey === k ? '#e2e8f0' : '#475569', fontSize: '11px' }}>{label}</button>
            ))}
          </div>
        </div>

        {visible.length === 0 && (
          <div style={{ textAlign: 'center', padding: '32px 0', color: '#475569', fontSize: '13px' }}>
            No {sigFilter} signals right now. <button onClick={() => setSigFilter('ALL')} style={{ color: '#818cf8', background: 'none', border: 'none', cursor: 'pointer' }}>Show all</button>
          </div>
        )}

        {/* Cards grid */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(290px, 1fr))', gap: '12px' }}>
          {visible.map(item => {
            const rank    = rankMap[item.symbol];
            const lp      = priceMap[item.symbol];
            const sigLabel = getSignal(item.symbol);
            const sig     = sigLabel ? sigStyle(sigLabel) : null;
            const changeUp = (lp?.change_pct ?? 0) >= 0;
            const note    = notes[item.symbol];
            const alert   = alerts[item.symbol];
            const alertTriggered = alert && lp && (alert.dir === 'above' ? lp.price >= alert.target : lp.price <= alert.target);

            return (
              <div key={item.symbol} style={{ position: 'relative', borderRadius: '10px', border: `1px solid ${alertTriggered ? 'rgba(250,204,21,0.4)' : '#1e293b'}`, background: '#0f172a', padding: '14px', transition: 'border-color 0.15s' }} className="watch-card">

                {/* Top row: symbol + price */}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '4px' }}>
                  <Link href={`/stock/${item.symbol}`} style={{ fontWeight: 700, fontSize: '17px', letterSpacing: '-0.01em', color: '#f1f5f9' }}>{item.symbol}</Link>
                  <div style={{ textAlign: 'right' }}>
                    {lp ? (<>
                      <div style={{ fontWeight: 600, fontSize: '14px', color: '#f1f5f9' }}>
                        {lp.currency !== 'USD' ? '' : '$'}{lp.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </div>
                      {lp.change_pct != null && <div style={{ fontSize: '11px', fontWeight: 600, color: changeUp ? '#4ade80' : '#f87171' }}>{changeUp ? '▲' : '▼'} {Math.abs(lp.change_pct).toFixed(2)}%</div>}
                    </>) : rank?.score != null && (
                      <div style={{ fontSize: '14px', fontWeight: 700, color: scoreColor(rank.score), textAlign: 'right' }}>
                        {rank.score.toFixed(0)}<div style={{ fontSize: '10px', fontWeight: 400, color: '#475569' }}>K-Score</div>
                      </div>
                    )}
                  </div>
                </div>

                {/* Company name */}
                <div style={{ marginBottom: '10px' }}>
                  <div style={{ fontSize: '12px', color: '#64748b', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.name}</div>
                  {item.name_zh && <div style={{ fontSize: '11px', color: '#475569', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', marginTop: '1px' }}>{item.name_zh}</div>}
                </div>

                {/* Signal + K-Score bar */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' }}>
                  {sig && sigLabel && (
                    <span style={{ fontSize: '11px', fontWeight: 700, padding: '3px 10px', borderRadius: '5px', color: sig.color, background: sig.bg, border: `1px solid ${sig.border}`, letterSpacing: '0.05em' }}>{sigLabel}</span>
                  )}
                  {rank?.score != null && (
                    <div style={{ flex: 1 }}>
                      <div style={{ height: '4px', borderRadius: '2px', background: '#1e293b', overflow: 'hidden' }}>
                        <div style={{ height: '100%', width: `${rank.score}%`, background: scoreColor(rank.score), borderRadius: '2px', transition: 'width 0.4s' }} />
                      </div>
                      <div style={{ fontSize: '10px', color: '#334155', marginTop: '2px' }}>K {rank.score.toFixed(0)}</div>
                    </div>
                  )}
                  {rank?.fair_price != null && (
                    <span style={{ fontSize: '11px', color: '#818cf8', fontWeight: 600, whiteSpace: 'nowrap' }}>Fair ${rank.fair_price.toFixed(2)}</span>
                  )}
                </div>

                {/* Alert triggered banner */}
                {alertTriggered && (
                  <div style={{ borderRadius: '6px', padding: '5px 10px', background: 'rgba(250,204,21,0.1)', border: '1px solid rgba(250,204,21,0.3)', fontSize: '11px', color: '#facc15', marginBottom: '10px', fontWeight: 600 }}>
                    🔔 Alert: price {alert.dir} ${alert.target.toFixed(2)}
                  </div>
                )}

                {/* Note preview */}
                {note && (
                  <div style={{ fontSize: '11px', color: '#475569', background: 'rgba(255,255,255,0.03)', borderRadius: '6px', padding: '6px 8px', marginBottom: '10px', borderLeft: '2px solid #334155', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    📝 {note}
                  </div>
                )}

                {/* Bottom action row */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', paddingTop: '8px', borderTop: '1px solid rgba(255,255,255,0.04)' }}>
                  {item.sector && <span style={{ fontSize: '10px', color: '#475569', background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.06)', padding: '2px 6px', borderRadius: '4px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '100px' }}>{item.sector}</span>}
                  <div style={{ marginLeft: 'auto', display: 'flex', gap: '4px', alignItems: 'center' }}>
                    <button onClick={() => setNoteModal(item.symbol)} title="Add note" style={{ background: note ? 'rgba(99,102,241,0.1)' : 'transparent', border: `1px solid ${note ? 'rgba(99,102,241,0.25)' : 'rgba(255,255,255,0.06)'}`, borderRadius: '5px', padding: '3px 7px', color: note ? '#818cf8' : '#475569', fontSize: '11px', cursor: 'pointer' }}>📝</button>
                    <button onClick={() => setAlertModal(item.symbol)} title="Set price alert" style={{ background: alert ? 'rgba(250,204,21,0.1)' : 'transparent', border: `1px solid ${alert ? 'rgba(250,204,21,0.3)' : 'rgba(255,255,255,0.06)'}`, borderRadius: '5px', padding: '3px 7px', color: alert ? '#facc15' : '#475569', fontSize: '11px', cursor: 'pointer' }}>🔔</button>
                    <button onClick={() => router.push(`/positions?add=${item.symbol}`)} title="Add to positions" style={{ background: 'rgba(99,102,241,0.08)', border: '1px solid rgba(99,102,241,0.2)', borderRadius: '5px', padding: '3px 8px', color: '#818cf8', fontSize: '10px', fontWeight: 700, cursor: 'pointer' }}>+ POS</button>
                    {(lists ?? []).length > 1 && (
                      <div id={`move-menu-${item.symbol}`} style={{ position: 'relative' }}>
                        <button
                          onClick={() => setMoveMenu(moveMenu === item.symbol ? null : item.symbol)}
                          disabled={moving === item.symbol}
                          title="Move to another list"
                          style={{ background: 'transparent', border: '1px solid rgba(255,255,255,0.06)', borderRadius: '5px', padding: '3px 7px', color: '#475569', fontSize: '11px', cursor: 'pointer' }}
                        >
                          {moving === item.symbol ? '…' : '⇄'}
                        </button>
                        {moveMenu === item.symbol && (
                          <div style={{ position: 'absolute', bottom: 'calc(100% + 4px)', right: 0, zIndex: 50, background: '#0d1424', border: '1px solid rgba(99,102,241,0.3)', borderRadius: '8px', boxShadow: '0 8px 24px rgba(0,0,0,0.5)', padding: '5px', minWidth: '140px' }}>
                            <div style={{ fontSize: '10px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', padding: '3px 8px 6px' }}>Move to</div>
                            {(lists ?? []).filter(l => l.id !== resolvedListId).map(l => (
                              <button
                                key={l.id}
                                onClick={() => moveToList(item.symbol, l.id)}
                                style={{ display: 'block', width: '100%', padding: '7px 10px', borderRadius: '5px', border: 'none', background: 'transparent', color: '#94a3b8', fontSize: '12px', cursor: 'pointer', textAlign: 'left' }}
                                className="move-list-btn"
                              >
                                {l.name}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                    <button onClick={() => remove(item.symbol)} disabled={removing === item.symbol} style={{ background: 'transparent', border: 'none', color: '#334155', cursor: 'pointer', fontSize: '12px', padding: '3px 5px' }} className="remove-btn">✕</button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </>)}

      {data && data.length === 0 && (
        <div style={{ textAlign: 'center', padding: '64px 0' }}>
          <div style={{ fontSize: '40px', marginBottom: '12px' }}>☆</div>
          <div style={{ fontSize: '15px', fontWeight: 600, color: '#cbd5e1', marginBottom: '6px' }}>No stocks watched yet</div>
          <div style={{ fontSize: '13px', color: '#475569' }}>Click ☆ on any stock card or detail page.</div>
          <Link href="/" style={{ display: 'inline-block', marginTop: '16px', fontSize: '13px', color: '#818cf8' }}>← Go to Dashboard</Link>
        </div>
      )}

      {noteModal  && <NoteModal  symbol={noteModal}  initial={notes[noteModal] ?? ''}  onSave={v => saveNote(noteModal, v)}  onClose={() => setNoteModal(null)} />}
      {alertModal && <AlertModal symbol={alertModal} price={priceMap[alertModal]?.price} initial={alerts[alertModal]} onSave={(t, d) => saveAlert(alertModal, t, d)} onClose={() => setAlertModal(null)} />}
      {showCreateModal && <CreateWatchlistModal onSave={handleCreateWatchlist} onClose={() => setShowCreateModal(false)} />}
      {showAddToList && resolvedListId != null && (
        <AddToListModal
          listId={resolvedListId}
          currentSymbols={new Set(data?.map(d => d.symbol) ?? [])}
          onClose={() => setShowAddToList(false)}
          onAdded={() => { mutateWatchlist(); mutateLists(); }}
        />
      )}

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        .watch-card:hover { border-color: #334155 !important; }
        .remove-btn:hover { color: #f87171 !important; }
        .del-tab-btn:hover { color: #f87171 !important; }
        .move-list-btn:hover { background: rgba(99,102,241,0.1) !important; color: #818cf8 !important; }
      `}</style>
    </div>
  );
}
