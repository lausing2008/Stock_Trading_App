import { useState, useEffect, useRef } from 'react';
import { api } from '@/lib/api';

type Props = { onClose: () => void; onAdded: (symbol: string) => void };

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

export default function AddStockModal({ onClose, onAdded }: Props) {
  const [symbol, setSymbol] = useState('');
  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle');
  const [result, setResult] = useState<{ name: string; sector?: string } | null>(null);
  const [errMsg, setErrMsg]   = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { inputRef.current?.focus(); }, []);
  useEffect(() => {
    const fn = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', fn);
    return () => window.removeEventListener('keydown', fn);
  }, [onClose]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const sym = symbol.trim().toUpperCase();
    if (!sym) return;
    setStatus('loading');
    setResult(null);
    setErrMsg('');
    try {
      const res = await api.addStock(sym);
      setResult({ name: res.name, sector: res.sector });
      setStatus('success');
      onAdded(sym);
    } catch (err: unknown) {
      setStatus('error');
      const msg = err instanceof Error ? err.message : String(err);
      setErrMsg(msg.includes('404') ? `"${sym}" not found on Yahoo Finance.` : 'Failed to add — check the ticker symbol.');
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

          {/* Input + Button */}
          <form onSubmit={handleSubmit}>
            <div style={{ fontSize: '11px', fontWeight: 600, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '8px' }}>
              Ticker Symbol
            </div>
            <div style={{ display: 'flex', gap: '8px' }}>
              <div style={{ position: 'relative', flex: 1 }}>
                <span style={{
                  position: 'absolute', left: '12px', top: '50%', transform: 'translateY(-50%)',
                  color: '#475569', fontSize: '13px', fontWeight: 700, pointerEvents: 'none',
                  fontFamily: 'ui-monospace, monospace',
                }}>$</span>
                <input
                  ref={inputRef}
                  value={symbol}
                  onChange={e => { setSymbol(e.target.value.toUpperCase()); setStatus('idle'); setResult(null); setErrMsg(''); }}
                  placeholder="NVDA, 0700.HK…"
                  maxLength={12}
                  style={{
                    width: '100%', paddingLeft: '28px', paddingRight: '12px',
                    paddingTop: '10px', paddingBottom: '10px',
                    fontSize: '14px', fontWeight: 600, color: '#f1f5f9',
                    fontFamily: 'ui-monospace, monospace',
                    background: 'rgba(255,255,255,0.04)',
                    border: `1px solid ${isError ? 'rgba(239,68,68,0.5)' : isSuccess ? 'rgba(34,197,94,0.45)' : 'rgba(148,163,184,0.12)'}`,
                    borderRadius: '8px', outline: 'none',
                    transition: 'border-color 0.15s',
                    boxSizing: 'border-box',
                  }}
                  onFocus={e => { if (!isError && !isSuccess) e.target.style.borderColor = 'rgba(99,102,241,0.6)'; }}
                  onBlur={e => { if (!isError && !isSuccess) e.target.style.borderColor = 'rgba(148,163,184,0.12)'; }}
                />
              </div>
              <button
                type="submit"
                disabled={!symbol.trim() || isLoading}
                style={{
                  padding: '10px 20px', borderRadius: '8px', border: 'none', cursor: !symbol.trim() || isLoading ? 'not-allowed' : 'pointer',
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
      `}</style>
    </div>
  );
}
