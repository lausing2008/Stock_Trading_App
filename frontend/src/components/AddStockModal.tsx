import { useState, useEffect, useRef, useMemo } from 'react';
import useSWR from 'swr';
import { api, type WatchlistMeta, type Stock } from '@/lib/api';

type Props = { onClose: () => void; onAdded: (symbol: string, listId?: number) => Promise<void>; lists?: WatchlistMeta[] };

const QUICK_ADD = [
  { symbol: 'AAPL',    label: 'Apple',       flag: '🇺🇸' },
  { symbol: 'NVDA',    label: 'NVIDIA',      flag: '🇺🇸' },
  { symbol: 'MSFT',    label: 'Microsoft',   flag: '🇺🇸' },
  { symbol: 'TSM',     label: 'TSMC',        flag: '🇹🇼' },
  { symbol: 'BABA',    label: 'Alibaba',     flag: '🇨🇳' },
  { symbol: 'SHOP',    label: 'Shopify',     flag: '🇨🇦' },
  { symbol: 'PLTR',    label: 'Palantir',    flag: '🇺🇸' },
  { symbol: 'COIN',    label: 'Coinbase',    flag: '🇺🇸' },
];

export default function AddStockModal({ onClose, onAdded, lists = [] }: Props) {
  const [symbol, setSymbol] = useState('');
  const [query, setQuery] = useState('');
  const [dropOpen, setDropOpen] = useState(false);
  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle');
  const [result, setResult] = useState<{ name: string; sector?: string; sym: string } | null>(null);
  const [errMsg, setErrMsg]   = useState('');
  const [selectedListId, setSelectedListId] = useState<number | undefined>(undefined);
  const inputRef = useRef<HTMLInputElement>(null);
  const dropRef  = useRef<HTMLDivElement>(null);

  const { data: allStocks } = useSWR<Stock[]>('stocks-all', () => api.listStocks(), { revalidateOnFocus: false });

  const filtered = useMemo(() => {
    if (!query.trim() || !allStocks) return [];
    const q = query.toUpperCase();
    return allStocks
      .filter(s => s.symbol.includes(q) || s.name.toUpperCase().includes(q) || (s.name_zh ?? '').includes(query))
      .slice(0, 8);
  }, [query, allStocks]);

  const multiList = lists.length > 1;

  useEffect(() => { inputRef.current?.focus(); }, []);
  useEffect(() => {
    const fn = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', fn);
    return () => window.removeEventListener('keydown', fn);
  }, [onClose]);

  // Close dropdown on outside click
  useEffect(() => {
    const fn = (e: MouseEvent) => {
      if (dropRef.current && !dropRef.current.contains(e.target as Node)) setDropOpen(false);
    };
    document.addEventListener('mousedown', fn);
    return () => document.removeEventListener('mousedown', fn);
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const sym = symbol.trim().toUpperCase();
    if (!sym) return;
    setStatus('loading');
    setResult(null);
    setErrMsg('');
    try {
      const res = await api.addStock(sym);
      setResult({ name: res.name, sector: res.sector, sym });
      if (!multiList) {
        await onAdded(sym, lists[0]?.id);
      }
      setStatus('success');
    } catch (err: unknown) {
      setStatus('error');
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes('404')) setErrMsg(`"${sym}" not found on Yahoo Finance.`);
      else if (msg.includes('401')) setErrMsg('Session expired — please log out and log in again.');
      else setErrMsg('Failed to add — check the ticker symbol.');
    }
  }

  async function confirmList(listId: number) {
    if (!result) return;
    setSelectedListId(listId);
    try {
      await onAdded(result.sym, listId);
    } catch (err: unknown) {
      setSelectedListId(undefined);
      const msg = err instanceof Error ? err.message : String(err);
      setErrMsg(msg.includes('401') ? 'Session expired — please log out and log in again.' : 'Failed to add to list.');
    }
  }

  function pick(sym: string) {
    setSymbol(sym);
    setStatus('idle');
    setResult(null);
    setErrMsg('');
    inputRef.current?.focus();
  }

  const isLoading = status === 'loading';
  const isSuccess = status === 'success';
  const isError   = status === 'error';

  return (
    /* Full-screen overlay — inline styles guarantee fixed centering regardless of CSS loading */
    <div
      style={{
        position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
        zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: '16px',
      }}
    >
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position: 'absolute', top: 0, left: 0, right: 0, bottom: 0,
          background: 'rgba(6,8,20,0.85)', backdropFilter: 'blur(6px)',
        }}
      />

      {/* Card */}
      <div style={{
        position: 'relative', zIndex: 10, width: '100%', maxWidth: '460px',
        borderRadius: '16px', overflow: 'hidden',
        background: 'linear-gradient(160deg, #0d1424 0%, #090e1a 100%)',
        border: '1px solid rgba(99,102,241,0.3)',
        boxShadow: '0 32px 64px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.04)',
      }}>

        {/* Top accent bar */}
        <div style={{ height: '3px', background: 'linear-gradient(90deg, #4f46e5, #818cf8, #4f46e5)' }} />

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '20px 24px 16px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <div style={{
              width: '36px', height: '36px', borderRadius: '10px', display: 'flex',
              alignItems: 'center', justifyContent: 'center', fontSize: '18px',
              background: 'rgba(99,102,241,0.15)', border: '1px solid rgba(99,102,241,0.3)',
            }}>
              📈
            </div>
            <div>
              <div style={{ fontSize: '15px', fontWeight: 700, color: '#f1f5f9', lineHeight: 1.2 }}>Add to Universe</div>
              <div style={{ fontSize: '11px', color: '#475569', marginTop: '2px' }}>Search by ticker symbol</div>
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              width: '28px', height: '28px', borderRadius: '8px', border: 'none',
              background: 'transparent', color: '#475569', cursor: 'pointer',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: '14px', transition: 'all 0.15s',
            }}
            onMouseEnter={e => { (e.target as HTMLButtonElement).style.background = '#1e293b'; (e.target as HTMLButtonElement).style.color = '#94a3b8'; }}
            onMouseLeave={e => { (e.target as HTMLButtonElement).style.background = 'transparent'; (e.target as HTMLButtonElement).style.color = '#475569'; }}
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div style={{ padding: '0 24px 24px', display: 'flex', flexDirection: 'column', gap: '20px' }}>

          {/* Searchable combobox */}
          <form onSubmit={handleSubmit}>
            <div style={{ fontSize: '11px', fontWeight: 600, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '8px' }}>
              Search by name or ticker
            </div>
            <div style={{ display: 'flex', gap: '8px' }}>
              <div ref={dropRef} style={{ position: 'relative', flex: 1 }}>
                <input
                  ref={inputRef}
                  value={query}
                  onChange={e => {
                    const v = e.target.value;
                    setQuery(v);
                    setSymbol(v.toUpperCase());
                    setDropOpen(true);
                    setStatus('idle'); setResult(null); setErrMsg('');
                  }}
                  placeholder="Search: Apple, NVDA, 0700.HK…"
                  maxLength={40}
                  autoComplete="off"
                  style={{
                    width: '100%', padding: '10px 12px',
                    fontSize: '13px', fontWeight: 500, color: '#f1f5f9',
                    background: 'rgba(255,255,255,0.04)',
                    border: `1px solid ${isError ? 'rgba(239,68,68,0.5)' : isSuccess ? 'rgba(34,197,94,0.45)' : 'rgba(148,163,184,0.12)'}`,
                    borderRadius: '8px', outline: 'none', boxSizing: 'border-box',
                    transition: 'border-color 0.15s',
                  }}
                  onFocus={() => setDropOpen(true)}
                />
                {/* Dropdown results */}
                {dropOpen && filtered.length > 0 && (
                  <div style={{
                    position: 'absolute', top: 'calc(100% + 4px)', left: 0, right: 0, zIndex: 200,
                    background: '#0d1424', border: '1px solid rgba(99,102,241,0.3)', borderRadius: '10px',
                    boxShadow: '0 16px 32px rgba(0,0,0,0.5)', overflow: 'hidden',
                  }}>
                    {filtered.map((s: Stock) => (
                      <button
                        key={s.symbol}
                        type="button"
                        onMouseDown={e => {
                          e.preventDefault();
                          setQuery(`${s.symbol} – ${s.name}`);
                          setSymbol(s.symbol);
                          setDropOpen(false);
                          setStatus('idle'); setResult(null); setErrMsg('');
                        }}
                        style={{
                          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                          width: '100%', padding: '9px 14px', border: 'none',
                          background: 'transparent', color: '#e2e8f0',
                          cursor: 'pointer', textAlign: 'left', gap: '10px',
                          borderBottom: '1px solid rgba(255,255,255,0.04)',
                          transition: 'background 0.1s',
                        }}
                        className="stock-drop-item"
                      >
                        <span style={{ fontFamily: 'ui-monospace, monospace', fontWeight: 700, fontSize: '13px', color: '#818cf8', minWidth: '70px' }}>
                          {s.symbol}
                        </span>
                        <span style={{ flex: 1, fontSize: '12px', color: '#94a3b8', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {s.name_zh ? `${s.name} · ${s.name_zh}` : s.name}
                        </span>
                        <span style={{ fontSize: '10px', color: '#475569', flexShrink: 0 }}>{s.market}</span>
                      </button>
                    ))}
                    {allStocks && query.trim() && filtered.length === 0 && (
                      <div style={{ padding: '10px 14px', fontSize: '12px', color: '#475569' }}>
                        Not in universe — type exact ticker to add new stock
                      </div>
                    )}
                  </div>
                )}
              </div>
              <button
                type="submit"
                disabled={!symbol.trim() || isLoading}
                style={{
                  padding: '10px 20px', borderRadius: '8px', border: 'none',
                  cursor: !symbol.trim() || isLoading ? 'not-allowed' : 'pointer',
                  fontSize: '13px', fontWeight: 700, color: '#ffffff',
                  background: isLoading ? 'rgba(99,102,241,0.4)' : 'linear-gradient(135deg, #4f46e5, #6366f1)',
                  opacity: !symbol.trim() || isLoading ? 0.5 : 1,
                  transition: 'all 0.15s', whiteSpace: 'nowrap',
                  display: 'flex', alignItems: 'center', gap: '6px',
                  boxShadow: !symbol.trim() || isLoading ? 'none' : '0 4px 12px rgba(99,102,241,0.35)',
                }}
              >
                {isLoading ? (
                  <>
                    <svg style={{ width: '13px', height: '13px', animation: 'spin 1s linear infinite' }} viewBox="0 0 24 24" fill="none">
                      <circle style={{ opacity: 0.25 }} cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path style={{ opacity: 0.75 }} fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
                    </svg>
                    Adding
                  </>
                ) : 'Add →'}
              </button>
            </div>
          </form>

          {/* Status feedback */}
          {isSuccess && result && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              <div style={{
                display: 'flex', alignItems: 'flex-start', gap: '12px',
                padding: '12px 16px', borderRadius: '10px',
                background: 'rgba(34,197,94,0.07)', border: '1px solid rgba(34,197,94,0.2)',
              }}>
                <div style={{
                  width: '20px', height: '20px', borderRadius: '50%', flexShrink: 0,
                  background: 'rgba(34,197,94,0.2)', display: 'flex', alignItems: 'center',
                  justifyContent: 'center', fontSize: '11px', color: '#4ade80', marginTop: '1px',
                }}>✓</div>
                <div>
                  <div style={{ fontSize: '13px', fontWeight: 600, color: '#86efac' }}>{result.name}</div>
                  <div style={{ fontSize: '11px', color: '#16a34a', marginTop: '3px' }}>
                    {result.sector && <span>{result.sector} · </span>}Price data ingesting in background
                  </div>
                </div>
              </div>

              {/* Watchlist picker — only shown when user has multiple lists */}
              {multiList && (
                <div>
                  <div style={{ fontSize: '11px', fontWeight: 600, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: '8px' }}>
                    Add to watchlist
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    {lists.map(list => {
                      const picked = selectedListId === list.id;
                      return (
                        <button
                          key={list.id}
                          onClick={() => confirmList(list.id)}
                          disabled={picked}
                          style={{
                            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                            padding: '9px 14px', borderRadius: '8px', border: `1px solid ${picked ? 'rgba(99,102,241,0.5)' : 'rgba(255,255,255,0.08)'}`,
                            background: picked ? 'rgba(99,102,241,0.12)' : 'rgba(255,255,255,0.03)',
                            color: picked ? '#818cf8' : '#94a3b8', cursor: picked ? 'default' : 'pointer',
                            fontSize: '13px', fontWeight: picked ? 700 : 400, transition: 'all 0.15s',
                          }}
                        >
                          <span>{list.name}</span>
                          <span style={{ fontSize: '11px', color: picked ? '#6366f1' : '#334155' }}>
                            {picked ? '✓ Added' : `${list.item_count} stocks`}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          )}

          {isError && (
            <div style={{
              display: 'flex', alignItems: 'center', gap: '12px',
              padding: '12px 16px', borderRadius: '10px',
              background: 'rgba(239,68,68,0.07)', border: '1px solid rgba(239,68,68,0.2)',
            }}>
              <div style={{
                width: '20px', height: '20px', borderRadius: '50%', flexShrink: 0,
                background: 'rgba(239,68,68,0.2)', display: 'flex', alignItems: 'center',
                justifyContent: 'center', fontSize: '11px', color: '#f87171',
              }}>✕</div>
              <div style={{ fontSize: '13px', color: '#fca5a5' }}>{errMsg}</div>
            </div>
          )}

          {/* Divider */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <div style={{ flex: 1, height: '1px', background: 'rgba(255,255,255,0.06)' }} />
            <span style={{ fontSize: '11px', color: '#334155', fontWeight: 500, letterSpacing: '0.05em', textTransform: 'uppercase' }}>Popular picks</span>
            <div style={{ flex: 1, height: '1px', background: 'rgba(255,255,255,0.06)' }} />
          </div>

          {/* Quick-add grid */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '8px' }}>
            {QUICK_ADD.map(({ symbol: sym, label, flag }) => {
              const active = symbol === sym;
              return (
                <button
                  key={sym}
                  type="button"
                  onClick={() => pick(sym)}
                  style={{
                    display: 'flex', flexDirection: 'column', alignItems: 'center',
                    gap: '3px', padding: '10px 4px', borderRadius: '10px',
                    border: `1px solid ${active ? 'rgba(99,102,241,0.45)' : 'rgba(255,255,255,0.06)'}`,
                    background: active ? 'rgba(99,102,241,0.12)' : 'rgba(255,255,255,0.025)',
                    cursor: 'pointer', transition: 'all 0.15s', textAlign: 'center',
                  }}
                  onMouseEnter={e => { if (!active) { const el = e.currentTarget; el.style.background = 'rgba(255,255,255,0.05)'; el.style.borderColor = 'rgba(255,255,255,0.1)'; } }}
                  onMouseLeave={e => { if (!active) { const el = e.currentTarget; el.style.background = 'rgba(255,255,255,0.025)'; el.style.borderColor = 'rgba(255,255,255,0.06)'; } }}
                >
                  <span style={{ fontSize: '16px', lineHeight: 1 }}>{flag}</span>
                  <span style={{
                    fontSize: '11px', fontWeight: 700, lineHeight: 1.2,
                    color: active ? '#a5b4fc' : '#94a3b8',
                    fontFamily: 'ui-monospace, monospace',
                  }}>{sym.replace('.HK', '')}</span>
                  <span style={{
                    fontSize: '9px', color: '#334155', lineHeight: 1.2,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    width: '100%', textAlign: 'center', paddingLeft: '2px', paddingRight: '2px',
                  }}>{label}</span>
                </button>
              );
            })}
          </div>

          {/* Footer hint */}
          <div style={{ textAlign: 'center', fontSize: '11px', color: '#334155' }}>
            US markets: plain ticker &nbsp;·&nbsp; Hong Kong: append{' '}
            <span style={{ fontFamily: 'ui-monospace, monospace', color: '#475569' }}>.HK</span>
          </div>
        </div>
      </div>

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        input::placeholder { color: #334155; }
        .stock-drop-item:hover { background: rgba(99,102,241,0.1) !important; }
      `}</style>
    </div>
  );
}
