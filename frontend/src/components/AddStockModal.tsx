import { useState, useEffect, useRef, useMemo } from 'react';
import useSWR from 'swr';
import { api, type WatchlistMeta, type Stock, type AddStockOutcome } from '@/lib/api';
import { parseSymbols, mergeSymbols, MAX_SYMBOLS, SYMBOL_SEPARATORS } from '@/lib/symbolInput';
import {
  EMPTY_SELECTION, canPickList, beginPick, resolvePick, type ListSelection,
} from '@/lib/watchlistSelection';

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
  const [symbols, setSymbols] = useState<string[]>([]);
  const [outcomes, setOutcomes] = useState<AddStockOutcome[] | null>(null);
  const [query, setQuery] = useState('');
  const [dropOpen, setDropOpen] = useState(false);
  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle');
  const [errMsg, setErrMsg]   = useState('');
  // A stock belongs to as many watchlists as you like, so this is the SET of lists these
  // symbols have landed in — not one choice. The single-id version made a second pick look
  // like it REPLACED the first (the earlier list's checkmark reverted to its stock count)
  // while the server had in fact added to both.
  const [listSel, setListSel] = useState<ListSelection>(EMPTY_SELECTION);
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
  // Symbols that reached the universe — the ones a watchlist pick should apply to. A throttled
  // or misspelled symbol is deliberately excluded: adding it to a list would assert it exists.
  const landed = (outcomes ?? []).filter(o => o.status === 'added' || o.status === 'exists');
  const retryable = (outcomes ?? []).filter(o => o.retryable).map(o => o.symbol);

  function addSymbols(text: string) {
    const parsed = parseSymbols(text);
    if (!parsed.length) return;
    setSymbols(prev => mergeSymbols(prev, parsed));
    setQuery('');
    setStatus('idle'); setOutcomes(null); setErrMsg(''); setListSel(EMPTY_SELECTION);
  }

  function removeSymbol(sym: string) {
    setSymbols(prev => prev.filter(s => s !== sym));
    setStatus('idle'); setOutcomes(null); setErrMsg(''); setListSel(EMPTY_SELECTION);
  }

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
    // Whatever is still in the input counts too — nobody should lose a ticker because they
    // did not press Enter before clicking Add.
    const all = mergeSymbols(symbols, parseSymbols(query));
    if (!all.length) return;

    setStatus('loading');
    setOutcomes(null);
    setErrMsg('');
    setListSel(EMPTY_SELECTION);
    try {
      const res = await api.addStocks(all);
      setSymbols(all);
      setQuery('');
      setOutcomes(res.results);
      const ok = res.results.filter(o => o.status === 'added' || o.status === 'exists');
      // With a single list there is nothing to choose, so land them immediately. With several,
      // the picker below applies to every symbol that made it.
      if (!multiList && ok.length) {
        for (const o of ok) await onAdded(o.symbol, lists[0]?.id);
      }
      setStatus(ok.length ? 'success' : 'error');
      if (!ok.length) {
        setErrMsg(res.results.every(o => o.status === 'rate_limited')
          ? 'Yahoo Finance is rate-limiting right now. Nothing is wrong with these symbols — retry in a minute.'
          : 'None of these could be added. See the per-symbol reasons below.');
      }
    } catch (err: unknown) {
      setStatus('error');
      const msg = err instanceof Error ? err.message : String(err);
      // AUD-ADDSTOCK-NORETRY: this used to fall through to "check the ticker symbol" for every
      // failure including a provider throttle, sending the user to hunt for a typo that did
      // not exist. A 503 is upstream and temporary; say so.
      if (msg.includes('503')) setErrMsg('Yahoo Finance is rate-limiting requests right now. This is temporary and not a problem with your symbols — try again in a minute.');
      else if (msg.includes('404')) setErrMsg('Not found on Yahoo Finance.');
      else if (msg.includes('401')) setErrMsg('Session expired — please log out and log in again.');
      else if (msg.includes('400')) setErrMsg(`Too many symbols at once — the limit is ${MAX_SYMBOLS} per request.`);
      else setErrMsg('Failed to add. The symbols were not changed.');
    }
  }

  async function confirmList(listId: number) {
    if (!canPickList(listSel, listId, landed.length)) return;
    setListSel(prev => beginPick(prev, listId));
    let ok = false;
    try {
      for (const o of landed) await onAdded(o.symbol, listId);
      ok = true;
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setErrMsg(msg.includes('401') ? 'Session expired — please log out and log in again.' : 'Failed to add to list.');
    }
    // Ticked only once the write actually returned: the earlier version marked the list chosen
    // BEFORE awaiting, so a failed add still showed as done.
    setListSel(prev => resolvePick(prev, listId, ok));
  }

  function retryThrottled() {
    setSymbols(retryable);
    setOutcomes(null);
    setListSel(EMPTY_SELECTION);
    setStatus('idle');
    setErrMsg('');
  }

  function pick(sym: string) {
    addSymbols(sym);
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
        position: 'relative', zIndex: 10, width: '100%', maxWidth: '520px',
        // Bound to the viewport (the overlay's own 16px padding on each side) and laid out as
        // a column so the header stays put and the body is what scrolls. Without this the card
        // simply grew past the screen edge and the ends were unreachable.
        maxHeight: 'calc(100vh - 32px)', display: 'flex', flexDirection: 'column',
        borderRadius: '16px', overflow: 'hidden',
        background: 'linear-gradient(160deg, #0d1424 0%, #090e1a 100%)',
        border: '1px solid rgba(99,102,241,0.3)',
        boxShadow: '0 32px 64px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.04)',
      }}>

        {/* Top accent bar */}
        <div style={{ height: '3px', flexShrink: 0, background: 'linear-gradient(90deg, #4f46e5, #818cf8, #4f46e5)' }} />

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '20px 24px 16px', flexShrink: 0 }}>
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
        <div style={{ padding: '0 24px 24px', display: 'flex', flexDirection: 'column', gap: '20px',
                      overflowY: 'auto', flex: 1, minHeight: 0 }}>

          {/* Searchable combobox */}
          <form onSubmit={handleSubmit}>
            <div style={{ fontSize: '11px', fontWeight: 600, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '8px' }}>
              Search by name or ticker — add as many as you like
            </div>
            {symbols.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginBottom: '8px' }}>
                {symbols.map(sym => {
                  const o = (outcomes ?? []).find(x => x.symbol === sym);
                  const tone = !o ? { bg: 'rgba(99,102,241,0.12)', bd: 'rgba(99,102,241,0.35)', fg: '#a5b4fc' }
                    : o.status === 'added' || o.status === 'exists'
                      ? { bg: 'rgba(34,197,94,0.10)', bd: 'rgba(34,197,94,0.35)', fg: '#86efac' }
                      : o.status === 'rate_limited'
                        ? { bg: 'rgba(234,179,8,0.10)', bd: 'rgba(234,179,8,0.35)', fg: '#fde047' }
                        : { bg: 'rgba(239,68,68,0.10)', bd: 'rgba(239,68,68,0.35)', fg: '#fca5a5' };
                  return (
                    <span key={sym} title={o?.message ?? ''} style={{
                      display: 'inline-flex', alignItems: 'center', gap: '6px',
                      padding: '4px 8px', borderRadius: '6px',
                      background: tone.bg, border: `1px solid ${tone.bd}`,
                      fontSize: '12px', fontWeight: 700, color: tone.fg,
                      fontFamily: 'ui-monospace, monospace',
                    }}>
                      {sym}
                      {o && (o.status === 'added' || o.status === 'exists') && <span>✓</span>}
                      {o && o.status === 'rate_limited' && <span>⏳</span>}
                      {o && (o.status === 'not_found' || o.status === 'error') && <span>✕</span>}
                      <button type="button" onClick={() => removeSymbol(sym)} aria-label={`Remove ${sym}`}
                        style={{ background: 'none', border: 'none', color: tone.fg, cursor: 'pointer',
                                 fontSize: '13px', lineHeight: 1, padding: 0, opacity: 0.7 }}>×</button>
                    </span>
                  );
                })}
                {symbols.length >= MAX_SYMBOLS && (
                  <span style={{ fontSize: '11px', color: '#eab308', alignSelf: 'center' }}>
                    {MAX_SYMBOLS} is the per-request limit
                  </span>
                )}
              </div>
            )}
            <div style={{ display: 'flex', gap: '8px' }}>
              <div ref={dropRef} style={{ position: 'relative', flex: 1 }}>
                <input
                  ref={inputRef}
                  value={query}
                  onChange={e => {
                    const v = e.target.value;
                    // A separator means the ticker before it is finished. Committing on the
                    // separator is what makes pasting a whole list work without extra steps.
                    if (SYMBOL_SEPARATORS.test(v)) { addSymbols(v); return; }
                    setQuery(v);
                    setDropOpen(true);
                    setStatus('idle'); setOutcomes(null); setErrMsg(''); setListSel(EMPTY_SELECTION);
                  }}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && query.trim()) { e.preventDefault(); addSymbols(query); }
                    else if (e.key === 'Backspace' && !query && symbols.length) {
                      removeSymbol(symbols[symbols.length - 1]);
                    }
                  }}
                  placeholder={symbols.length ? 'Add another…' : 'Search or paste: AAPL, NVDA, 0700.HK…'}
                  maxLength={200}
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
                          addSymbols(s.symbol);
                          setDropOpen(false);
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
                disabled={(!symbols.length && !query.trim()) || isLoading}
                style={{
                  padding: '10px 20px', borderRadius: '8px', border: 'none',
                  cursor: (!symbols.length && !query.trim()) || isLoading ? 'not-allowed' : 'pointer',
                  fontSize: '13px', fontWeight: 700, color: '#ffffff',
                  background: isLoading ? 'rgba(99,102,241,0.4)' : 'linear-gradient(135deg, #4f46e5, #6366f1)',
                  opacity: (!symbols.length && !query.trim()) || isLoading ? 0.5 : 1,
                  transition: 'all 0.15s', whiteSpace: 'nowrap',
                  display: 'flex', alignItems: 'center', gap: '6px',
                  boxShadow: (!symbols.length && !query.trim()) || isLoading ? 'none' : '0 4px 12px rgba(99,102,241,0.35)',
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
                ) : symbols.length > 1 ? `Add ${symbols.length} →` : 'Add →'}
              </button>
            </div>
          </form>

          {/* Status feedback */}
          {/* Per-symbol outcomes. EVERY symbol gets its own line, because a batch routinely
              splits under rate limiting and one verdict for the batch would be wrong for most
              of them — and would hide which ones are worth retrying. */}
          {outcomes && outcomes.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                {outcomes.map(o => {
                  const good = o.status === 'added' || o.status === 'exists';
                  const warn = o.status === 'rate_limited';
                  return (
                    <div key={o.symbol} style={{
                      display: 'flex', alignItems: 'flex-start', gap: '10px',
                      padding: '9px 12px', borderRadius: '8px',
                      background: good ? 'rgba(34,197,94,0.07)' : warn ? 'rgba(234,179,8,0.07)' : 'rgba(239,68,68,0.07)',
                      border: `1px solid ${good ? 'rgba(34,197,94,0.2)' : warn ? 'rgba(234,179,8,0.22)' : 'rgba(239,68,68,0.2)'}`,
                    }}>
                      <span style={{ fontSize: '12px', color: good ? '#4ade80' : warn ? '#fde047' : '#f87171', marginTop: '1px' }}>
                        {good ? '✓' : warn ? '⏳' : '✕'}
                      </span>
                      <div style={{ minWidth: 0 }}>
                        <div style={{ fontSize: '12px', fontWeight: 700,
                                      color: good ? '#86efac' : warn ? '#fde047' : '#fca5a5',
                                      fontFamily: 'ui-monospace, monospace' }}>
                          {o.symbol}{o.name && o.name !== o.symbol ? ` · ${o.name}` : ''}
                        </div>
                        <div style={{ fontSize: '11px', color: '#94a3b8', marginTop: '2px' }}>
                          {o.status === 'added' && (o.sector ? `${o.sector} · Price data ingesting in background` : 'Price data ingesting in background')}
                          {o.status !== 'added' && o.message}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>

              {retryable.length > 0 && (
                <button type="button" onClick={retryThrottled} style={{
                  alignSelf: 'flex-start', padding: '7px 12px', borderRadius: '8px',
                  border: '1px solid rgba(234,179,8,0.35)', background: 'rgba(234,179,8,0.10)',
                  color: '#fde047', fontSize: '12px', fontWeight: 600, cursor: 'pointer',
                }}>
                  Retry {retryable.length} that {retryable.length === 1 ? 'was' : 'were'} throttled
                </button>
              )}

              {/* Watchlist picker — only shown when user has multiple lists */}
              {/* Only when something actually landed — a picker over zero symbols would invite a
                  click that silently does nothing. */}
              {multiList && landed.length > 0 && (
                <div>
                  <div style={{ fontSize: '11px', fontWeight: 600, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: '8px' }}>
                    Add {landed.length > 1 ? `all ${landed.length}` : ''} to watchlists — pick as many as you like
                  </div>
                  {/* Two columns rather than one tall stack: with a dozen lists the single
                      column pushed the rest of the modal off the screen. */}
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: '6px' }}>
                    {lists.map(list => {
                      const added = listSel.added.includes(list.id);
                      const busy  = listSel.pending === list.id;
                      const other = listSel.pending !== undefined && !busy;
                      return (
                        <button
                          key={list.id}
                          type="button"
                          onClick={() => confirmList(list.id)}
                          disabled={added || listSel.pending !== undefined}
                          title={`${list.name} · ${list.item_count} stocks`}
                          style={{
                            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                            gap: '8px', minWidth: 0, textAlign: 'left',
                            padding: '8px 11px', borderRadius: '8px',
                            border: `1px solid ${added ? 'rgba(34,197,94,0.45)' : 'rgba(255,255,255,0.08)'}`,
                            background: added ? 'rgba(34,197,94,0.10)' : 'rgba(255,255,255,0.03)',
                            color: added ? '#86efac' : '#94a3b8',
                            cursor: added ? 'default' : other ? 'wait' : 'pointer',
                            opacity: other ? 0.5 : 1,
                            fontSize: '12px', fontWeight: added ? 700 : 400, transition: 'all 0.15s',
                          }}
                        >
                          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{list.name}</span>
                          <span style={{ fontSize: '10px', flexShrink: 0, color: added ? '#4ade80' : '#334155' }}>
                            {busy ? '…' : added ? '✓' : list.item_count}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                  {listSel.added.length > 0 && (
                    <div style={{ fontSize: '11px', color: '#4ade80', marginTop: '8px' }}>
                      Added {landed.length > 1 ? `all ${landed.length}` : landed[0]?.symbol} to{' '}
                      {listSel.added.length} {listSel.added.length === 1 ? 'list' : 'lists'} — pick more, or close.
                    </div>
                  )}
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
              const active = symbols.includes(sym);
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
