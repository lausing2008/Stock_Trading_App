import { useState, useMemo, useCallback, useEffect, useRef } from 'react';
import useSWR, { mutate as globalMutate } from 'swr';
import Link from 'next/link';
import { useRouter } from 'next/router';
import { api, type AppUser, type WatchlistItem, type WatchlistMeta, type RankingRow, type LatestPrice, type SignalSummary, type Stock, type PriceAlert, type RelPerfPoint, type SignalAlertItem } from '@/lib/api';
import { storage } from '@/lib/storage';
import { getSignalStyle } from '@/lib/settings';

/* ── helpers ────────────────────────────────────────────── */
const NOTES_KEY = 'watch_notes';

function loadNotes(): Record<string, string> {
  if (typeof window === 'undefined') return {};
  try { return JSON.parse(storage.getItem(NOTES_KEY) ?? '{}'); } catch { return {}; }
}
function saveNotes(n: Record<string, string>) { storage.setItem(NOTES_KEY, JSON.stringify(n)); }

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
function AlertModal({ symbol, price, existingAlerts, hasEmail, onAdd, onDelete, onClose }: {
  symbol: string;
  price?: number;
  existingAlerts: import('@/lib/api').PriceAlert[];
  hasEmail: boolean;
  onAdd: (target: number, dir: 'above' | 'below') => Promise<void>;
  onDelete: (id: number) => Promise<void>;
  onClose: () => void;
}) {
  const [target, setTarget] = useState('');
  const [dir, setDir] = useState<'above' | 'below'>('above');
  const [adding, setAdding] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  async function handleAdd() {
    const t = parseFloat(target);
    if (!(t > 0)) return;
    setAdding(true);
    await onAdd(t, dir);
    setTarget('');
    setAdding(false);
  }

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '16px' }}>
      <div onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(6,8,20,0.8)', backdropFilter: 'blur(4px)' }} />
      <div style={{ position: 'relative', zIndex: 10, width: '100%', maxWidth: '340px', borderRadius: '14px', background: '#0d1424', border: '1px solid rgba(250,204,21,0.3)', boxShadow: '0 24px 48px rgba(0,0,0,0.5)', overflow: 'hidden' }}>
        <div style={{ height: '3px', background: 'linear-gradient(90deg,#ca8a04,#facc15,#ca8a04)' }} />
        <div style={{ padding: '18px 20px 20px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ fontWeight: 700, fontSize: '14px', color: '#f1f5f9' }}>🔔 Price Alerts — {symbol}</span>
            <button onClick={onClose} style={{ background: 'none', border: 'none', color: '#475569', cursor: 'pointer' }}>✕</button>
          </div>
          {price != null && <div style={{ fontSize: '11px', color: '#475569' }}>Current: <span style={{ color: '#94a3b8', fontWeight: 600 }}>${fmt2(price)}</span></div>}
          {!hasEmail && <div style={{ padding: '10px 12px', borderRadius: '10px', background: 'rgba(220,38,38,0.08)', border: '1px solid rgba(220,38,38,0.16)', color: '#fecaca', fontSize: '12px', lineHeight: 1.5 }}>
            No email configured. Alerts will still be saved, but notifications require an email address. <Link href="/settings" style={{ color: '#fbbf24', textDecoration: 'underline' }}>Go to Settings</Link>.
          </div>}

          {/* Existing alerts list */}
          {existingAlerts.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
              <div style={{ fontSize: '11px', color: '#475569', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Active alerts</div>
              {existingAlerts.map(a => (
                <div key={a.id} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '7px 10px', borderRadius: '7px', background: a.triggered ? 'rgba(34,197,94,0.06)' : 'rgba(250,204,21,0.06)', border: `1px solid ${a.triggered ? 'rgba(34,197,94,0.2)' : 'rgba(250,204,21,0.15)'}` }}>
                  <span style={{ fontSize: '12px', color: a.condition === 'above' ? '#4ade80' : '#f87171', fontWeight: 700 }}>{a.condition === 'above' ? '▲' : '▼'}</span>
                  <span style={{ fontSize: '13px', color: '#f1f5f9', fontWeight: 600, flex: 1 }}>${fmt2(a.threshold)}</span>
                  {a.triggered && <span style={{ fontSize: '10px', color: '#4ade80', fontWeight: 700 }}>✓ fired</span>}
                  <button
                    onClick={async () => { setDeletingId(a.id); await onDelete(a.id); setDeletingId(null); }}
                    disabled={deletingId === a.id}
                    style={{ background: 'none', border: 'none', color: '#475569', cursor: 'pointer', fontSize: '12px', padding: '2px 4px', opacity: deletingId === a.id ? 0.4 : 1 }}
                  >✕</button>
                </div>
              ))}
            </div>
          )}

          {/* Add new alert */}
          <div style={{ borderTop: existingAlerts.length > 0 ? '1px solid rgba(255,255,255,0.06)' : 'none', paddingTop: existingAlerts.length > 0 ? '10px' : '0', display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {existingAlerts.length > 0 && <div style={{ fontSize: '11px', color: '#475569', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Add another</div>}
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
              onKeyDown={e => e.key === 'Enter' && handleAdd()}
              placeholder="Target price"
              style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(148,163,184,0.12)', borderRadius: '8px', padding: '9px 12px', fontSize: '13px', color: '#f1f5f9', outline: 'none', width: '100%', boxSizing: 'border-box' }}
            />
            <button
              onClick={handleAdd}
              disabled={adding || !(parseFloat(target) > 0)}
              style={{ borderRadius: '8px', padding: '8px', background: parseFloat(target) > 0 ? 'linear-gradient(135deg,#ca8a04,#facc15)' : 'rgba(255,255,255,0.05)', border: 'none', color: parseFloat(target) > 0 ? '#000' : '#475569', fontSize: '13px', fontWeight: 700, cursor: parseFloat(target) > 0 ? 'pointer' : 'default', opacity: adding ? 0.6 : 1 }}
            >
              {adding ? '…' : '+ Add Alert'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── Create watchlist modal ─────────────────────────────── */
const STYLE_OPTS = [
  { value: null,     label: 'Global default',    desc: 'Follows your Settings → Trading Style', color: '#475569' },
  { value: 'SHORT',  label: 'Short Term',         desc: '1–5 days · pure TA',                   color: '#f87171' },
  { value: 'SWING',  label: 'Swing Trade',        desc: '5–20 days · balanced',                 color: '#818cf8' },
  { value: 'LONG',   label: 'Long Term',          desc: '30–90 days · fundamentals',            color: '#4ade80' },
  { value: 'GROWTH', label: 'Growth / Momentum',  desc: 'Relaxed thresholds for high-vol AI/tech stocks', color: '#a78bfa' },
] as const;

function CreateWatchlistModal({ onSave, onClose }: { onSave: (name: string, style: string | null) => Promise<void>; onClose: () => void }) {
  const [name, setName] = useState('');
  const [style, setStyle] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setSaving(true);
    await onSave(name.trim(), style);
    setSaving(false);
    onClose();
  }
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '16px' }}>
      <div onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(6,8,20,0.8)', backdropFilter: 'blur(4px)' }} />
      <div style={{ position: 'relative', zIndex: 10, width: '100%', maxWidth: '380px', borderRadius: '14px', background: '#0d1424', border: '1px solid rgba(99,102,241,0.3)', boxShadow: '0 24px 48px rgba(0,0,0,0.5)', overflow: 'hidden' }}>
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
            placeholder="e.g. Swing Trades, Long Holds…"
            maxLength={64}
            style={{ width: '100%', background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(148,163,184,0.15)', borderRadius: '8px', padding: '9px 12px', fontSize: '13px', color: '#f1f5f9', outline: 'none', boxSizing: 'border-box', marginBottom: '14px' }}
          />
          <div style={{ fontSize: '11px', color: '#64748b', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '8px' }}>Trading Style</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginBottom: '14px' }}>
            {STYLE_OPTS.map(opt => (
              <button
                key={String(opt.value)}
                type="button"
                onClick={() => setStyle(opt.value)}
                style={{
                  display: 'flex', alignItems: 'center', gap: '10px', padding: '8px 12px',
                  borderRadius: '8px', cursor: 'pointer', textAlign: 'left',
                  background: style === opt.value ? `${opt.color}12` : 'rgba(255,255,255,0.02)',
                  border: `1px solid ${style === opt.value ? `${opt.color}50` : 'rgba(255,255,255,0.06)'}`,
                }}
              >
                <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: style === opt.value ? opt.color : '#334155', flexShrink: 0 }} />
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: '12px', fontWeight: 700, color: style === opt.value ? opt.color : '#94a3b8' }}>{opt.label}</div>
                  <div style={{ fontSize: '11px', color: '#475569' }}>{opt.desc}</div>
                </div>
              </button>
            ))}
          </div>
          <button
            type="submit"
            disabled={!name.trim() || saving}
            style={{ width: '100%', borderRadius: '8px', padding: '9px', background: name.trim() ? 'linear-gradient(135deg,#4f46e5,#6366f1)' : 'rgba(255,255,255,0.05)', border: 'none', color: name.trim() ? '#fff' : '#475569', fontSize: '13px', fontWeight: 700, cursor: name.trim() ? 'pointer' : 'default' }}
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

/* ── Compare Panel ──────────────────────────────────────── */

const LINE_COLORS = ['#818cf8', '#4ade80', '#fb923c', '#f87171', '#38bdf8', '#a78bfa', '#facc15', '#34d399'];

function ComparePanel({ symbols, selected, onToggle, days, onDaysChange, data, loading }: {
  symbols: string[];
  selected: string[];
  onToggle: (sym: string) => void;
  days: number;
  onDaysChange: (d: number) => void;
  data: Record<string, RelPerfPoint[]> | null;
  loading: boolean;
}) {
  const chartW = 700;
  const chartH = 280;
  const pad = { top: 16, right: 20, bottom: 32, left: 48 };

  const lines = useMemo(() => {
    if (!data) return [];
    return selected.map((sym, i) => ({ sym, pts: data[sym] ?? [], color: LINE_COLORS[i % LINE_COLORS.length] }))
      .filter(l => l.pts.length > 0);
  }, [data, selected]);

  const { minVal, maxVal, allDates } = useMemo(() => {
    if (lines.length === 0) return { minVal: 90, maxVal: 110, allDates: [] };
    const allPts = lines.flatMap(l => l.pts);
    const allValues = allPts.map(p => p.value);
    const allDates = [...new Set(allPts.map(p => p.date))].sort();
    return {
      minVal: Math.min(80, ...allValues) - 2,
      maxVal: Math.max(120, ...allValues) + 2,
      allDates,
    };
  }, [lines]);

  function xOf(date: string): number {
    const idx = allDates.indexOf(date);
    if (idx < 0 || allDates.length < 2) return pad.left;
    return pad.left + ((idx / (allDates.length - 1)) * (chartW - pad.left - pad.right));
  }

  function yOf(val: number): number {
    return pad.top + ((maxVal - val) / (maxVal - minVal)) * (chartH - pad.top - pad.bottom);
  }

  function toPath(pts: RelPerfPoint[]): string {
    return pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${xOf(p.date).toFixed(1)},${yOf(p.value).toFixed(1)}`).join(' ');
  }

  const gridLines = useMemo(() => {
    const step = (maxVal - minVal) / 5;
    return Array.from({ length: 6 }, (_, i) => minVal + i * step);
  }, [minVal, maxVal]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
      {/* Symbol selector */}
      <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
        <span style={{ fontSize: '11px', color: '#475569', whiteSpace: 'nowrap' }}>Select up to 8:</span>
        {symbols.map((sym, i) => {
          const idx = selected.indexOf(sym);
          const isSelected = idx >= 0;
          const color = isSelected ? LINE_COLORS[idx % LINE_COLORS.length] : undefined;
          return (
            <button
              key={sym}
              onClick={() => onToggle(sym)}
              style={{
                padding: '3px 10px', borderRadius: '5px', fontSize: '12px', fontWeight: 700, cursor: 'pointer',
                border: `1px solid ${isSelected ? color : '#1e293b'}`,
                background: isSelected ? `${color}22` : 'transparent',
                color: isSelected ? color : '#475569',
              }}
            >{sym}</button>
          );
        })}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: '4px' }}>
          {[30, 60, 90, 180, 365].map(d => (
            <button key={d} onClick={() => onDaysChange(d)}
              style={{ padding: '3px 8px', borderRadius: '4px', fontSize: '11px', cursor: 'pointer', border: '1px solid #1e293b', background: days === d ? '#334155' : 'transparent', color: days === d ? '#e2e8f0' : '#475569' }}
            >{d}d</button>
          ))}
        </div>
      </div>

      {selected.length === 0 && (
        <div style={{ padding: '40px 0', textAlign: 'center', fontSize: '12px', color: '#334155' }}>
          Select symbols above to compare their relative performance (base 100 = start of period)
        </div>
      )}

      {selected.length > 0 && loading && (
        <div style={{ padding: '40px 0', textAlign: 'center', fontSize: '12px', color: '#475569' }}>Loading performance data…</div>
      )}

      {selected.length > 0 && !loading && (
        <div style={{ background: '#080f1e', border: '1px solid #1e293b', borderRadius: '10px', padding: '16px', overflowX: 'auto' }}>
          {lines.length === 0 ? (
            <div style={{ padding: '32px 0', textAlign: 'center', fontSize: '12px', color: '#475569' }}>
              No price history found for the selected symbols in this period.
            </div>
          ) : (
            <>
              <svg viewBox={`0 0 ${chartW} ${chartH}`} style={{ width: '100%', maxWidth: `${chartW}px`, height: `${chartH}px`, display: 'block' }}>
                {/* Grid lines */}
                {gridLines.map(v => (
                  <g key={v}>
                    <line x1={pad.left} x2={chartW - pad.right} y1={yOf(v)} y2={yOf(v)} stroke="#1e293b" strokeWidth="1" />
                    <text x={pad.left - 4} y={yOf(v) + 4} textAnchor="end" fontSize="9" fill="#475569">{v.toFixed(0)}</text>
                  </g>
                ))}
                {/* Base-100 reference line */}
                <line x1={pad.left} x2={chartW - pad.right} y1={yOf(100)} y2={yOf(100)} stroke="#334155" strokeWidth="1" strokeDasharray="4,3" />
                <text x={chartW - pad.right + 4} y={yOf(100) + 4} fontSize="9" fill="#475569">100</text>
                {/* X axis labels (first and last date) */}
                {allDates.length > 0 && (<>
                  <text x={pad.left} y={chartH - 6} textAnchor="start" fontSize="9" fill="#334155">{allDates[0]?.slice(5)}</text>
                  <text x={chartW - pad.right} y={chartH - 6} textAnchor="end" fontSize="9" fill="#334155">{allDates[allDates.length - 1]?.slice(5)}</text>
                </>)}
                {/* Lines */}
                {lines.map(l => (
                  <path key={l.sym} d={toPath(l.pts)} fill="none" stroke={l.color} strokeWidth="1.8" strokeLinejoin="round" strokeLinecap="round" />
                ))}
                {/* End-point labels */}
                {lines.map(l => {
                  const last = l.pts[l.pts.length - 1];
                  if (!last) return null;
                  const finalVal = last.value.toFixed(1);
                  const isUp = last.value >= 100;
                  return (
                    <g key={`lbl-${l.sym}`}>
                      <circle cx={xOf(last.date)} cy={yOf(last.value)} r="3" fill={l.color} />
                      <text x={xOf(last.date) + 5} y={yOf(last.value) + 4} fontSize="10" fontWeight="700" fill={l.color}>
                        {l.sym} {isUp ? '+' : ''}{(last.value - 100).toFixed(1)}%
                      </text>
                    </g>
                  );
                })}
              </svg>
              {/* Legend */}
              <div style={{ display: 'flex', gap: '14px', flexWrap: 'wrap', marginTop: '8px' }}>
                {lines.map(l => {
                  const last = l.pts[l.pts.length - 1];
                  const ret = last ? last.value - 100 : null;
                  return (
                    <div key={l.sym} style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <div style={{ width: '20px', height: '2px', borderRadius: '1px', background: l.color }} />
                      <span style={{ fontSize: '12px', fontWeight: 700, color: l.color }}>{l.sym}</span>
                      {ret != null && (
                        <span style={{ fontSize: '11px', color: ret >= 0 ? '#4ade80' : '#f87171' }}>
                          {ret >= 0 ? '+' : ''}{ret.toFixed(1)}%
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
              <div style={{ fontSize: '10px', color: '#334155', marginTop: '6px' }}>Base 100 = first day of period · daily closes</div>
            </>
          )}
        </div>
      )}
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
  // Use the active list's trading style if set, else fall back to global setting
  const activeList = (lists ?? []).find(l => l.id === resolvedListId);
  const effectiveStyle = activeList?.trading_style ?? getSignalStyle();

  const { data, error, isLoading, mutate: mutateWatchlist } = useSWR<WatchlistItem[]>(
    resolvedListId != null ? ['watchlist', resolvedListId] : null,
    () => api.listWatchlist(resolvedListId!),
  );
  const { data: rankingsData, mutate: mutateRankings } = useSWR<{ rankings: RankingRow[] }>('rankings-all', () => api.rankings());
  const { data: pricesData, mutate: mutatePrices } = useSWR<LatestPrice[]>('latest-prices', () => api.latestPrices(), { refreshInterval: 60_000 });
  const { data: signalsData, mutate: mutateSignals } = useSWR<SignalSummary[]>('signals-' + effectiveStyle, () => api.allSignals(effectiveStyle));

  const { data: alertsData, mutate: mutateAlerts } = useSWR<PriceAlert[]>('alerts', () => api.listAlerts(), { refreshInterval: 30_000 });
  const { data: signalAlerts, mutate: mutateSignalAlerts } = useSWR<SignalAlertItem[]>('signal-alerts', () => api.listSignalAlerts(), { refreshInterval: 60_000 });
  const { data: me } = useSWR<AppUser>('me', () => api.getMe());
  const hasEmail = me === undefined ? true : Boolean(me.email);

  const [showAddToList, setShowAddToList] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const [bulkSubscribing, setBulkSubscribing] = useState(false);
  const [togglingSignal, setTogglingSignal] = useState<string | null>(null);
  const [moveMenu, setMoveMenu] = useState<string | null>(null);
  const [moving, setMoving] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [sigFilter, setSigFilter] = useState<SigFilter>('ALL');
  const [sortKey, setSortKey] = useState<SortKey>('symbol');
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [noteModal, setNoteModal] = useState<string | null>(null);
  const [alertModal, setAlertModal] = useState<string | null>(null);
  const [alertToast, setAlertToast] = useState<{ msg: string; ok: boolean } | null>(null);
  const [viewMode, setViewMode] = useState<'list' | 'compare'>('list');
  const [compareSymbols, setCompareSymbols] = useState<string[]>([]);
  const [compareDays, setCompareDays] = useState(90);

  const { data: relPerfData, isLoading: relPerfLoading } = useSWR<Record<string, RelPerfPoint[]>>(
    viewMode === 'compare' && compareSymbols.length > 0
      ? `rel-perf-${compareSymbols.join(',')}-${compareDays}`
      : null,
    () => api.relativePerformance(compareSymbols, compareDays),
    { revalidateOnFocus: false },
  );

  useEffect(() => { setNotes(loadNotes()); }, []);

  const alertMap = useMemo(() => {
    const m: Record<string, PriceAlert[]> = {};
    for (const a of alertsData ?? []) {
      if (!m[a.symbol]) m[a.symbol] = [];
      m[a.symbol].push(a);
    }
    return m;
  }, [alertsData]);

  const signalAlertMap = useMemo(() => {
    const m: Record<string, SignalAlertItem> = {};
    for (const a of signalAlerts ?? []) m[a.symbol] = a;
    return m;
  }, [signalAlerts]);

  const rankMap = useMemo(() => { const m: Record<string, RankingRow> = {}; for (const r of rankingsData?.rankings ?? []) m[r.symbol] = r; return m; }, [rankingsData]);
  const priceMap = useMemo(() => { const m: Record<string, LatestPrice> = {}; for (const p of pricesData ?? []) m[p.symbol] = p; return m; }, [pricesData]);
  const signalMap = useMemo(() => { const m: Record<string, SignalSummary> = {}; for (const s of signalsData ?? []) m[s.symbol] = s; return m; }, [signalsData]);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    await Promise.all([mutateWatchlist(), mutateRankings(), mutatePrices(), mutateSignals(), mutateLists(), mutateAlerts()]);
    setRefreshing(false);
  }, [mutateWatchlist, mutateRankings, mutatePrices, mutateSignals, mutateLists, mutateAlerts]);

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

  async function handleCreateWatchlist(name: string, style: string | null) {
    await api.createWatchlist(name, style);
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
  async function handleAddAlert(symbol: string, target: number, dir: 'above' | 'below') {
    try {
      await api.createAlert({ symbol, condition: dir, threshold: target });
      setAlertToast({ msg: `Alert added: ${symbol} ${dir} $${target}`, ok: true });
      await mutateAlerts();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to create alert';
      setAlertToast({ msg: msg.includes('No email') ? 'No email on account — set one in Settings → Profile' : msg, ok: false });
    }
    setTimeout(() => setAlertToast(null), 4000);
  }

  async function handleDeleteAlert(id: number) {
    try {
      await api.deleteAlert(id);
      await mutateAlerts();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to delete alert';
      setAlertToast({ msg, ok: false });
      setTimeout(() => setAlertToast(null), 4000);
    }
  }

  async function handleToggleSignalAlert(symbol: string) {
    setTogglingSignal(symbol);
    try {
      const existing = signalAlertMap[symbol];
      if (existing) {
        await api.deleteSignalAlert(existing.id);
        setAlertToast({ msg: `Signal alerts off for ${symbol}`, ok: true });
      } else {
        const email = me?.email ?? (typeof window !== 'undefined' ? localStorage.getItem('stockai_alert_email') ?? undefined : undefined);
        await api.createSignalAlert(symbol, email);
        setAlertToast({ msg: `Signal alerts on for ${symbol}`, ok: true });
      }
      await mutateSignalAlerts();
    } catch {
      setAlertToast({ msg: 'Failed to update signal alert', ok: false });
    }
    setTimeout(() => setAlertToast(null), 3000);
    setTogglingSignal(null);
  }

  async function handleNotifyAll() {
    const unsubscribed = (data ?? []).filter(item => !signalAlertMap[item.symbol]);
    if (unsubscribed.length === 0) return;
    setBulkSubscribing(true);
    const email = me?.email ?? (typeof window !== 'undefined' ? localStorage.getItem('stockai_alert_email') ?? undefined : undefined);
    const results = await Promise.allSettled(unsubscribed.map(item => api.createSignalAlert(item.symbol, email)));
    const succeeded = results.filter(r => r.status === 'fulfilled').length;
    const failed = results.filter(r => r.status === 'rejected').length;
    await mutateSignalAlerts();
    setAlertToast(
      failed === 0
        ? { msg: `Signal alerts enabled for ${succeeded} stocks`, ok: true }
        : { msg: `${succeeded} subscribed, ${failed} failed — check your email in Settings`, ok: false }
    );
    setTimeout(() => setAlertToast(null), 4000);
    setBulkSubscribing(false);
  }

  async function handleMuteAll() {
    const subscribed = (data ?? []).filter(item => signalAlertMap[item.symbol]);
    if (subscribed.length === 0) return;
    setBulkSubscribing(true);
    const results = await Promise.allSettled(subscribed.map(item => api.deleteSignalAlert(signalAlertMap[item.symbol].id)));
    const succeeded = results.filter(r => r.status === 'fulfilled').length;
    const failed = results.filter(r => r.status === 'rejected').length;
    await mutateSignalAlerts();
    setAlertToast(
      failed === 0
        ? { msg: `Signal alerts removed for ${succeeded} stocks`, ok: true }
        : { msg: `${succeeded} removed, ${failed} failed`, ok: false }
    );
    setTimeout(() => setAlertToast(null), 4000);
    setBulkSubscribing(false);
  }

  /* Signal for each item: real signal engine first, K-Score fallback */
  function getSignal(symbol: string): string | null {
    if (signalMap[symbol]) return signalMap[symbol].signal;
    return signalFromScore(rankMap[symbol]?.score ?? undefined);
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

      {/* Alert toast */}
      {alertToast && (
        <div style={{ position: 'fixed', bottom: '24px', right: '24px', zIndex: 2000, padding: '12px 20px', borderRadius: '10px', background: alertToast.ok ? '#0f2a1e' : '#1f0a0a', border: `1px solid ${alertToast.ok ? 'rgba(34,197,94,0.4)' : 'rgba(239,68,68,0.4)'}`, color: alertToast.ok ? '#4ade80' : '#f87171', fontSize: '13px', fontWeight: 600, boxShadow: '0 8px 32px rgba(0,0,0,0.5)' }}>
          {alertToast.ok ? '🔔 ' : '⚠️ '}{alertToast.msg}
        </div>
      )}

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
          const styleOpt = STYLE_OPTS.find(o => o.value === list.trading_style);
          const styleColor = styleOpt?.color ?? '#334155';
          const CYCLE = [null, 'SHORT', 'SWING', 'LONG', 'GROWTH'] as const;
          async function cycleStyle() {
            const idx = CYCLE.indexOf(list.trading_style as typeof CYCLE[number]);
            const next = CYCLE[(idx + 1) % CYCLE.length];
            await api.renameWatchlist(list.id, list.name, next ?? '');
            mutateLists();
          }
          return (
            <div key={list.id} style={{ display: 'flex', alignItems: 'center', borderRadius: '8px', border: `1px solid ${isActive ? 'rgba(99,102,241,0.5)' : '#1e293b'}`, background: isActive ? 'rgba(99,102,241,0.12)' : 'rgba(255,255,255,0.02)', overflow: 'hidden' }}>
              <button
                onClick={() => setActiveListId(list.id)}
                style={{ padding: '6px 14px', background: 'none', border: 'none', cursor: 'pointer', fontSize: '13px', fontWeight: isActive ? 700 : 400, color: isActive ? '#818cf8' : '#64748b', whiteSpace: 'nowrap' }}
              >
                {list.name}
                <span style={{ marginLeft: '6px', fontSize: '11px', color: isActive ? '#6366f1' : '#334155' }}>{list.item_count}</span>
              </button>
              {list.trading_style ? (
                <button
                  onClick={cycleStyle}
                  title={`Style: ${list.trading_style} — click to change`}
                  style={{ padding: '3px 7px', background: 'none', border: 'none', borderLeft: '1px solid rgba(255,255,255,0.05)', cursor: 'pointer', fontSize: '9px', fontWeight: 800, letterSpacing: '0.05em', color: styleColor }}
                >
                  {list.trading_style}
                </button>
              ) : isActive ? (
                <button
                  onClick={cycleStyle}
                  title="Set trading style for this list"
                  style={{ padding: '3px 7px', background: 'none', border: 'none', borderLeft: '1px solid rgba(255,255,255,0.05)', cursor: 'pointer', fontSize: '9px', color: '#334155' }}
                >
                  +style
                </button>
              ) : null}
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
        {data && data.length > 0 && (() => {
          const subscribedCount = data.filter(item => signalAlertMap[item.symbol]).length;
          const allOn = subscribedCount === data.length;
          return (
            <div style={{ display: 'flex', gap: '6px', marginLeft: 'auto' }}>
              <button
                onClick={handleNotifyAll}
                disabled={bulkSubscribing || allOn}
                title={allOn ? 'All stocks already have signal alerts' : `Enable signal alerts for all ${data.length - subscribedCount} unsubscribed stocks`}
                style={{ display: 'flex', alignItems: 'center', gap: '5px', padding: '6px 12px', borderRadius: '8px', border: `1px solid ${allOn ? 'rgba(74,222,128,0.3)' : 'rgba(129,140,248,0.35)'}`, background: allOn ? 'rgba(74,222,128,0.08)' : 'rgba(129,140,248,0.1)', color: allOn ? '#4ade80' : '#818cf8', cursor: allOn || bulkSubscribing ? 'default' : 'pointer', fontSize: '12px', fontWeight: 600, opacity: bulkSubscribing ? 0.6 : 1 }}
              >
                📡 {bulkSubscribing ? 'Working…' : allOn ? `All notified (${subscribedCount})` : `Notify All (${data.length - subscribedCount})`}
              </button>
              {subscribedCount > 0 && (
                <button
                  onClick={handleMuteAll}
                  disabled={bulkSubscribing}
                  title={`Disable signal alerts for all ${subscribedCount} subscribed stocks`}
                  style={{ display: 'flex', alignItems: 'center', gap: '5px', padding: '6px 12px', borderRadius: '8px', border: '1px solid rgba(100,116,139,0.3)', background: 'transparent', color: '#475569', cursor: bulkSubscribing ? 'default' : 'pointer', fontSize: '12px', fontWeight: 600, opacity: bulkSubscribing ? 0.6 : 1 }}
                >
                  Mute All
                </button>
              )}
            </div>
          );
        })()}
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
            {viewMode === 'list' && (<>
              Sort:
              {([['symbol', 'Symbol', '↑'], ['signal', 'Signal', '↓'], ['score', 'K-Score', '↓'], ['change', 'Change%', '↓'], ['price', 'Price', '↓']] as [SortKey, string, string][]).map(([k, label, arrow]) => (
                <button key={k} onClick={() => setSortKey(k)} style={{ padding: '4px 8px', borderRadius: '4px', border: 'none', cursor: 'pointer', background: sortKey === k ? '#334155' : 'transparent', color: sortKey === k ? '#e2e8f0' : '#475569', fontSize: '11px' }}>
                  {label}{sortKey === k ? <span style={{ marginLeft: 2, opacity: 0.7 }}>{arrow}</span> : ''}
                </button>
              ))}
            </>)}
            <div style={{ display: 'flex', borderRadius: '6px', border: '1px solid #1e293b', overflow: 'hidden', marginLeft: '8px' }}>
              <button onClick={() => setViewMode('list')} style={{ padding: '4px 10px', border: 'none', cursor: 'pointer', fontSize: '11px', fontWeight: 600, background: viewMode === 'list' ? '#334155' : 'transparent', color: viewMode === 'list' ? '#e2e8f0' : '#475569' }}>List</button>
              <button onClick={() => setViewMode('compare')} style={{ padding: '4px 10px', border: 'none', cursor: 'pointer', fontSize: '11px', fontWeight: 600, background: viewMode === 'compare' ? '#4f46e5' : 'transparent', color: viewMode === 'compare' ? '#fff' : '#475569' }}>Compare</button>
            </div>
          </div>
        </div>

        {/* Compare view */}
        {viewMode === 'compare' && (
          <ComparePanel
            symbols={data.map(i => i.symbol)}
            selected={compareSymbols}
            onToggle={sym => setCompareSymbols(s => s.includes(sym) ? s.filter(x => x !== sym) : s.length < 8 ? [...s, sym] : s)}
            days={compareDays}
            onDaysChange={setCompareDays}
            data={relPerfData ?? null}
            loading={relPerfLoading}
          />
        )}

        {visible.length === 0 && (
          <div style={{ textAlign: 'center', padding: '32px 0', color: '#475569', fontSize: '13px' }}>
            No {sigFilter} signals right now. <button onClick={() => setSigFilter('ALL')} style={{ color: '#818cf8', background: 'none', border: 'none', cursor: 'pointer' }}>Show all</button>
          </div>
        )}

        {/* Cards grid — hidden in compare mode */}
        <div style={{ display: viewMode === 'compare' ? 'none' : 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(290px, 1fr))', gap: '12px' }}>
          {visible.map(item => {
            const rank    = rankMap[item.symbol];
            const lp      = priceMap[item.symbol];
            const sigLabel = getSignal(item.symbol);
            const sig     = sigLabel ? sigStyle(sigLabel) : null;
            const changeUp = (lp?.change_pct ?? 0) >= 0;
            const note    = notes[item.symbol];
            const itemAlerts = alertMap[item.symbol] ?? [];
            const hasAlert = itemAlerts.length > 0;
            const triggeredAlerts = itemAlerts.filter(a => a.triggered || (lp && (a.condition === 'above' ? lp.price >= a.threshold : lp.price <= a.threshold)));
            const hasSignalAlert = Boolean(signalAlertMap[item.symbol]);
            const isTogglingThis = togglingSignal === item.symbol;

            return (
              <div key={item.symbol} style={{ position: 'relative', borderRadius: '10px', border: `1px solid ${triggeredAlerts.length > 0 ? 'rgba(250,204,21,0.4)' : '#1e293b'}`, background: '#0f172a', padding: '14px', transition: 'border-color 0.15s' }} className="watch-card">

                {/* Top row: symbol + price */}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '4px' }}>
                  <Link href={`/stock/${item.symbol}${activeList?.trading_style ? `?style=${activeList.trading_style}` : ''}`} style={{ fontWeight: 700, fontSize: '17px', letterSpacing: '-0.01em', color: '#f1f5f9' }}>{item.symbol}</Link>
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
                {triggeredAlerts.length > 0 && (
                  <div style={{ borderRadius: '6px', padding: '5px 10px', background: 'rgba(250,204,21,0.1)', border: '1px solid rgba(250,204,21,0.3)', fontSize: '11px', color: '#facc15', marginBottom: '10px', fontWeight: 600 }}>
                    🔔 {triggeredAlerts.map(a => `${a.condition} $${a.threshold.toFixed(2)}`).join(' · ')}
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
                    <button onClick={() => setAlertModal(item.symbol)} title="Set price alert" style={{ background: hasAlert ? 'rgba(250,204,21,0.1)' : 'transparent', border: `1px solid ${hasAlert ? 'rgba(250,204,21,0.3)' : 'rgba(255,255,255,0.06)'}`, borderRadius: '5px', padding: '3px 7px', color: hasAlert ? '#facc15' : '#475569', fontSize: '11px', cursor: 'pointer', position: 'relative' }}>
                      🔔{hasAlert && itemAlerts.length > 1 && <span style={{ position: 'absolute', top: '-4px', right: '-4px', background: '#facc15', color: '#000', fontSize: '9px', fontWeight: 800, borderRadius: '50%', width: '14px', height: '14px', display: 'flex', alignItems: 'center', justifyContent: 'center', lineHeight: 1 }}>{itemAlerts.length}</span>}
                    </button>
                    <button
                      onClick={() => handleToggleSignalAlert(item.symbol)}
                      disabled={isTogglingThis}
                      title={hasSignalAlert ? 'Signal alerts ON — click to turn off' : 'Enable AI signal alerts for this stock'}
                      style={{ background: hasSignalAlert ? 'rgba(129,140,248,0.15)' : 'transparent', border: `1px solid ${hasSignalAlert ? 'rgba(129,140,248,0.4)' : 'rgba(255,255,255,0.06)'}`, borderRadius: '5px', padding: '3px 7px', color: hasSignalAlert ? '#818cf8' : '#475569', fontSize: '11px', cursor: isTogglingThis ? 'default' : 'pointer', opacity: isTogglingThis ? 0.5 : 1 }}
                    >
                      {isTogglingThis ? '…' : '📡'}
                    </button>
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
      {alertModal && <AlertModal symbol={alertModal} price={priceMap[alertModal]?.price} existingAlerts={alertMap[alertModal] ?? []} hasEmail={hasEmail} onAdd={(t, d) => handleAddAlert(alertModal, t, d)} onDelete={handleDeleteAlert} onClose={() => setAlertModal(null)} />}
      {showCreateModal && <CreateWatchlistModal onSave={(name, style) => handleCreateWatchlist(name, style)} onClose={() => setShowCreateModal(false)} />}
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
