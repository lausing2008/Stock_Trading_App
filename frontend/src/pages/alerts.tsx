import { useState, useEffect } from 'react';
import Link from 'next/link';
import useSWR from 'swr';
import { api, type PriceAlert, type SignalAlertItem, type Stock, type SignalSummary, type WatchlistMeta, type WatchlistItem } from '@/lib/api';
import { getSignalStyle } from '@/lib/settings';

// ── helpers ────────────────────────────────────────────────────────────────

function relTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function alertLabel(a: PriceAlert): string {
  if (a.condition === 'above') return `Price rises above ${a.threshold}`;
  if (a.condition === 'below') return `Price falls below ${a.threshold}`;
  if (a.condition === 'cross_above_ema') return `Crosses above EMA${a.threshold}`;
  if (a.condition === 'cross_below_ema') return `Crosses below EMA${a.threshold}`;
  if (a.condition === 'new_52wk_high') return 'New 52-week high';
  if (a.condition === 'new_52wk_low') return 'New 52-week low';
  if (a.condition === 'golden_cross') return 'Golden Cross (EMA50 ↑ EMA200)';
  if (a.condition === 'death_cross') return 'Death Cross (EMA50 ↓ EMA200)';
  if (a.condition === 'macd_bullish_cross') return 'MACD Bullish Crossover';
  if (a.condition === 'rsi_oversold_bounce') return 'RSI Oversold Bounce (crosses 30)';
  if (a.condition === 'double_bottom') return 'Double Bottom (W-pattern)';
  if (a.condition === 'breakout') return 'Volume Breakout (20-day high + surge)';
  return a.condition;
}

function triggeredLabel(a: PriceAlert): string {
  if (a.condition === 'above') return `Price rose above ${a.threshold}`;
  if (a.condition === 'below') return `Price fell below ${a.threshold}`;
  if (a.condition === 'cross_above_ema') return `Crossed above EMA${a.threshold}`;
  if (a.condition === 'cross_below_ema') return `Crossed below EMA${a.threshold}`;
  if (a.condition === 'new_52wk_high') return 'Hit new 52-week high';
  if (a.condition === 'new_52wk_low') return 'Hit new 52-week low';
  if (a.condition === 'golden_cross') return 'Golden Cross fired';
  if (a.condition === 'death_cross') return 'Death Cross fired';
  if (a.condition === 'macd_bullish_cross') return 'MACD Bullish Cross fired';
  if (a.condition === 'rsi_oversold_bounce') return 'RSI Oversold Bounce fired';
  if (a.condition === 'double_bottom') return 'Double Bottom pattern fired';
  if (a.condition === 'breakout') return 'Volume Breakout fired';
  return a.condition;
}

const SIGNAL_COLOR: Record<string, string> = {
  BUY: '#4ade80', HOLD: '#facc15', SELL: '#f87171', WAIT: '#94a3b8',
};
const SIGNAL_BG: Record<string, string> = {
  BUY: 'rgba(74,222,128,0.12)', HOLD: 'rgba(250,204,21,0.12)',
  SELL: 'rgba(248,113,113,0.12)', WAIT: 'rgba(148,163,184,0.08)',
};

// ── shared styles ──────────────────────────────────────────────────────────

const inp: React.CSSProperties = {
  background: '#0f172a', border: '1px solid #1e293b', borderRadius: '8px',
  padding: '9px 12px', fontSize: '13px', color: '#e2e8f0', outline: 'none',
  width: '100%', boxSizing: 'border-box',
};
const lbl: React.CSSProperties = {
  fontSize: '11px', color: '#64748b', fontWeight: 600,
  textTransform: 'uppercase', letterSpacing: '0.05em', display: 'block', marginBottom: '5px',
};

const NO_THRESHOLD = ['new_52wk_high', 'new_52wk_low', 'golden_cross', 'death_cross', 'macd_bullish_cross', 'rsi_oversold_bounce', 'double_bottom', 'breakout'];
const EMA_CONDITIONS = ['cross_above_ema', 'cross_below_ema'];

// ── Bulk Pattern Alert card ────────────────────────────────────────────────

function BulkPatternAlertCard({ onDone }: { onDone: () => void }) {
  const { data: watchlists } = useSWR<WatchlistMeta[]>('watchlists', () => api.listWatchlists());
  const [listId, setListId]       = useState<number | ''>('');
  const [condition, setCondition] = useState('golden_cross');
  const [recurring, setRecurring] = useState(true);
  const [email, setEmail]         = useState('');
  const [applying, setApplying]   = useState(false);
  const [result, setResult]       = useState('');

  useEffect(() => {
    const s = typeof window !== 'undefined' ? localStorage.getItem('stockai_alert_email') : null;
    if (s) setEmail(s);
  }, []);

  async function handleApply(e: React.FormEvent) {
    e.preventDefault();
    if (!listId || !email) return;
    setApplying(true); setResult('');
    try {
      const items: WatchlistItem[] = await api.listWatchlist(Number(listId));
      if (!items.length) { setResult('Watchlist is empty.'); return; }
      const threshold = ['cross_above_ema', 'cross_below_ema'].includes(condition) ? 20 : 0;
      let created = 0;
      await Promise.all(items.map(async item => {
        try {
          await api.createAlert({ symbol: item.symbol, condition, threshold, email, recurring });
          created++;
        } catch {}
      }));
      localStorage.setItem('stockai_alert_email', email);
      setResult(`Created ${created} alert${created !== 1 ? 's' : ''} for ${items.length} stocks.`);
      onDone();
    } catch (err) {
      setResult('Failed — ' + (err instanceof Error ? err.message : String(err)));
    } finally { setApplying(false); }
  }

  const selected = watchlists?.find(w => w.id === Number(listId));

  return (
    <div style={{ borderRadius: '12px', border: '1px solid rgba(251,191,36,0.2)', background: 'rgba(15,23,42,0.95)', overflow: 'hidden', marginBottom: '24px' }}>
      <div style={{ height: '3px', background: 'linear-gradient(90deg,#f59e0b,#fbbf24,#f59e0b)' }} />
      <div style={{ padding: '20px 24px' }}>
        <h2 style={{ margin: '0 0 4px', fontSize: '14px', fontWeight: 700, color: '#e2e8f0' }}>Bulk Pattern Alert — Apply to Watchlist</h2>
        <p style={{ margin: '0 0 16px', fontSize: '12px', color: '#475569' }}>Creates one alert per stock in the selected watchlist.</p>
        <form onSubmit={handleApply}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '12px', alignItems: 'end' }}>
            <div>
              <label style={lbl}>Watchlist</label>
              <select value={listId} onChange={e => setListId(e.target.value === '' ? '' : Number(e.target.value))} required style={inp}>
                <option value="">Select watchlist…</option>
                {(watchlists ?? []).map(w => (
                  <option key={w.id} value={w.id}>{w.name} ({w.item_count} stocks)</option>
                ))}
              </select>
            </div>
            <div>
              <label style={lbl}>Pattern</label>
              <select value={condition} onChange={e => setCondition(e.target.value)} style={inp}>
                <optgroup label="EMA50 vs EMA200">
                  <option value="golden_cross">Golden Cross (EMA50 ↑ EMA200)</option>
                  <option value="death_cross">Death Cross (EMA50 ↓ EMA200)</option>
                </optgroup>
                <optgroup label="Milestone">
                  <option value="new_52wk_high">New 52-week high</option>
                  <option value="new_52wk_low">New 52-week low</option>
                </optgroup>
                <optgroup label="Pattern Signals">
                  <option value="macd_bullish_cross">MACD Bullish Crossover</option>
                  <option value="rsi_oversold_bounce">RSI Oversold Bounce (crosses 30)</option>
                  <option value="double_bottom">Double Bottom (W-pattern)</option>
                  <option value="breakout">Volume Breakout (20-day high + surge)</option>
                </optgroup>
              </select>
            </div>
            <div>
              <label style={lbl}>Email</label>
              <input type="email" value={email} onChange={e => setEmail(e.target.value)}
                placeholder="you@example.com" required style={inp} />
            </div>
          </div>
          <div style={{ marginTop: '12px', display: 'flex', gap: '10px', alignItems: 'center' }}>
            <div style={{ display: 'flex', gap: '4px' }}>
              <button type="button" onClick={() => setRecurring(false)}
                style={{ padding: '7px 12px', borderRadius: '6px', fontSize: '11px', fontWeight: 600, cursor: 'pointer', border: `1px solid ${!recurring ? 'rgba(99,102,241,0.5)' : '#1e293b'}`, background: !recurring ? 'rgba(99,102,241,0.15)' : 'transparent', color: !recurring ? '#a5b4fc' : '#475569' }}>
                Once
              </button>
              <button type="button" onClick={() => setRecurring(true)}
                style={{ padding: '7px 12px', borderRadius: '6px', fontSize: '11px', fontWeight: 600, cursor: 'pointer', border: `1px solid ${recurring ? 'rgba(251,191,36,0.5)' : '#1e293b'}`, background: recurring ? 'rgba(251,191,36,0.08)' : 'transparent', color: recurring ? '#fbbf24' : '#475569' }}>
                ↻ Recurring
              </button>
            </div>
            <button type="submit" disabled={applying || !listId || !email} style={{
              background: 'linear-gradient(135deg,#d97706,#f59e0b)',
              border: 'none', color: '#fff', padding: '9px 24px', borderRadius: '8px',
              fontSize: '13px', fontWeight: 700, cursor: applying || !listId || !email ? 'not-allowed' : 'pointer',
              opacity: applying || !listId || !email ? 0.5 : 1, whiteSpace: 'nowrap',
            }}>
              {applying ? `Creating…` : `Apply to ${selected ? selected.item_count + ' stocks' : 'watchlist'}`}
            </button>
            {result && (
              <span style={{ fontSize: '12px', color: result.startsWith('Created') ? '#4ade80' : '#f87171' }}>{result}</span>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}

// ── Price Alerts tab ───────────────────────────────────────────────────────

function PriceAlertsTab() {
  const { data: stocks } = useSWR<Stock[]>('stocks-all', () => api.listStocks());
  const { data: alerts, mutate } = useSWR<PriceAlert[]>('alerts', () => api.listAlerts(), { refreshInterval: 30000 });

  const [symbol, setSymbol]       = useState('');
  const [condition, setCondition] = useState('above');
  const [threshold, setThreshold] = useState('');
  const [emaPeriod, setEmaPeriod] = useState('20');
  const [email, setEmail]         = useState('');
  const [note, setNote]           = useState('');
  const [recurring, setRecurring] = useState(false);
  const [saving, setSaving]       = useState(false);
  const [saved, setSaved]         = useState(false);
  const [error, setError]         = useState('');

  const isEma         = EMA_CONDITIONS.includes(condition);
  const isNoThreshold = NO_THRESHOLD.includes(condition);

  useEffect(() => {
    const s = typeof window !== 'undefined' ? localStorage.getItem('stockai_alert_email') : null;
    if (s) setEmail(s);
  }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!symbol || !email) return;
    if (!isNoThreshold && !isEma && !threshold) return;
    const thresholdVal = isNoThreshold ? 0 : isEma ? parseInt(emaPeriod) : parseFloat(threshold);
    setSaving(true); setError('');
    try {
      await api.createAlert({ symbol, condition, threshold: thresholdVal, email, note: note || undefined, recurring: isNoThreshold ? recurring : false });
      localStorage.setItem('stockai_alert_email', email);
      await mutate();
      setThreshold(''); setNote('');
      setSaved(true); setTimeout(() => setSaved(false), 2000);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to create alert');
    } finally { setSaving(false); }
  }

  async function handleDelete(id: number) {
    try { await api.deleteAlert(id); await mutate(); } catch {}
  }

  const active = (alerts ?? []).filter(a => !a.triggered);
  const fired  = (alerts ?? []).filter(a => a.triggered);

  return (
    <div>
      {/* Create form */}
      <div style={{ borderRadius: '12px', border: '1px solid rgba(99,102,241,0.25)', background: 'rgba(15,23,42,0.95)', overflow: 'hidden', marginBottom: '24px' }}>
        <div style={{ height: '3px', background: 'linear-gradient(90deg,#4f46e5,#818cf8,#4f46e5)' }} />
        <div style={{ padding: '20px 24px' }}>
          <h2 style={{ margin: '0 0 18px', fontSize: '14px', fontWeight: 700, color: '#e2e8f0' }}>Create Price Alert</h2>
          <form onSubmit={handleCreate}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 200px 1fr', gap: '12px', alignItems: 'end' }}>
              <div>
                <label style={lbl}>Stock</label>
                <select value={symbol} onChange={e => setSymbol(e.target.value)} required style={inp}>
                  <option value="">Select stock…</option>
                  {(stocks ?? []).map(s => (
                    <option key={s.symbol} value={s.symbol}>{s.symbol} — {s.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label style={lbl}>Condition</label>
                <select value={condition} onChange={e => setCondition(e.target.value)} style={inp}>
                  <optgroup label="Price">
                    <option value="above">Price rises above</option>
                    <option value="below">Price falls below</option>
                  </optgroup>
                  <optgroup label="Price vs EMA">
                    <option value="cross_above_ema">Crosses above EMA</option>
                    <option value="cross_below_ema">Crosses below EMA</option>
                  </optgroup>
                  <optgroup label="EMA50 vs EMA200">
                    <option value="golden_cross">Golden Cross (EMA50 ↑ EMA200)</option>
                    <option value="death_cross">Death Cross (EMA50 ↓ EMA200)</option>
                  </optgroup>
                  <optgroup label="Milestone">
                    <option value="new_52wk_high">New 52-week high</option>
                    <option value="new_52wk_low">New 52-week low</option>
                  </optgroup>
                  <optgroup label="Pattern Signals">
                    <option value="macd_bullish_cross">MACD Bullish Crossover</option>
                    <option value="rsi_oversold_bounce">RSI Oversold Bounce (crosses 30)</option>
                    <option value="double_bottom">Double Bottom (W-pattern)</option>
                    <option value="breakout">Volume Breakout (20-day high + surge)</option>
                  </optgroup>
                </select>
              </div>
              {!isNoThreshold && !isEma && (
                <div>
                  <label style={lbl}>Target price</label>
                  <input type="number" step="any" min="0" value={threshold}
                    onChange={e => setThreshold(e.target.value)} placeholder="0.00" required style={inp} />
                </div>
              )}
              {isEma && (
                <div>
                  <label style={lbl}>EMA period</label>
                  <select value={emaPeriod} onChange={e => setEmaPeriod(e.target.value)} style={inp}>
                    <option value="20">20-day</option>
                    <option value="50">50-day</option>
                    <option value="200">200-day</option>
                  </select>
                </div>
              )}
              {isNoThreshold && (
                <div>
                  <label style={lbl}>Email</label>
                  <input type="email" value={email} onChange={e => setEmail(e.target.value)}
                    placeholder="you@example.com" required style={inp} />
                </div>
              )}
            </div>
            <div style={{ marginTop: '10px', display: 'flex', gap: '12px', alignItems: 'end' }}>
              {!isNoThreshold && (
                <div style={{ flex: 1 }}>
                  <label style={lbl}>Email</label>
                  <input type="email" value={email} onChange={e => setEmail(e.target.value)}
                    placeholder="you@example.com" required style={inp} />
                </div>
              )}
              <div style={{ flex: 2 }}>
                <label style={lbl}>Note (optional)</label>
                <input type="text" value={note} onChange={e => setNote(e.target.value)}
                  placeholder="e.g. buy target" style={inp} />
              </div>
              {isNoThreshold && (
                <div>
                  <label style={lbl}>Mode</label>
                  <div style={{ display: 'flex', gap: '4px' }}>
                    <button type="button" onClick={() => setRecurring(false)}
                      style={{ padding: '7px 10px', borderRadius: '6px', fontSize: '11px', fontWeight: 600, cursor: 'pointer', border: `1px solid ${!recurring ? 'rgba(99,102,241,0.5)' : '#1e293b'}`, background: !recurring ? 'rgba(99,102,241,0.15)' : 'transparent', color: !recurring ? '#a5b4fc' : '#475569' }}>
                      Once
                    </button>
                    <button type="button" onClick={() => setRecurring(true)}
                      style={{ padding: '7px 10px', borderRadius: '6px', fontSize: '11px', fontWeight: 600, cursor: 'pointer', border: `1px solid ${recurring ? 'rgba(251,191,36,0.5)' : '#1e293b'}`, background: recurring ? 'rgba(251,191,36,0.08)' : 'transparent', color: recurring ? '#fbbf24' : '#475569' }}>
                      ↻ Recur
                    </button>
                  </div>
                </div>
              )}
              <button type="submit" disabled={saving} style={{
                background: saved ? 'rgba(34,197,94,0.2)' : 'linear-gradient(135deg,#4f46e5,#6366f1)',
                border: saved ? '1px solid rgba(34,197,94,0.4)' : 'none',
                color: saved ? '#4ade80' : '#fff', padding: '9px 24px', borderRadius: '8px',
                fontSize: '13px', fontWeight: 700, cursor: saving ? 'not-allowed' : 'pointer',
                whiteSpace: 'nowrap', opacity: saving ? 0.6 : 1,
              }}>
                {saved ? '✓ Saved' : saving ? 'Saving…' : '+ Add Alert'}
              </button>
            </div>
            {error && <div style={{ marginTop: '8px', fontSize: '12px', color: '#f87171' }}>{error}</div>}
          </form>
        </div>
      </div>

      <BulkPatternAlertCard onDone={() => mutate()} />

      {/* Active */}
      <div style={{ marginBottom: '24px' }}>
        <h2 style={{ fontSize: '13px', fontWeight: 700, color: '#94a3b8', marginBottom: '12px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
          Active ({active.length})
        </h2>
        {active.length === 0 ? (
          <div style={{ padding: '32px', textAlign: 'center', borderRadius: '10px', border: '1px dashed #1e293b', color: '#334155', fontSize: '13px' }}>
            No active alerts. Create one above.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {active.map(a => (
              <div key={a.id} style={{ display: 'flex', alignItems: 'center', gap: '12px', padding: '12px 16px', borderRadius: '10px', border: `1px solid ${a.recurring ? 'rgba(251,191,36,0.2)' : 'rgba(99,102,241,0.2)'}`, background: 'rgba(15,23,42,0.8)' }}>
                <Link href={`/stock/${a.symbol}`} style={{ fontSize: '13px', fontWeight: 800, color: '#818cf8', fontFamily: 'monospace', minWidth: '70px', textDecoration: 'none' }}>
                  {a.symbol}
                </Link>
                <span style={{ fontSize: '13px', color: '#cbd5e1', flex: 1 }}>{alertLabel(a)}</span>
                {a.note && <span style={{ fontSize: '11px', color: '#475569', fontStyle: 'italic' }}>{a.note}</span>}
                {a.recurring && (
                  <span style={{ fontSize: '10px', padding: '2px 7px', borderRadius: '4px', background: 'rgba(251,191,36,0.08)', color: '#fbbf24', border: '1px solid rgba(251,191,36,0.2)', whiteSpace: 'nowrap' }}>↻ recurring</span>
                )}
                {a.recurring && a.last_sent_at && (
                  <span style={{ fontSize: '11px', color: '#475569', whiteSpace: 'nowrap' }}>fired {relTime(a.last_sent_at)}</span>
                )}
                {!a.recurring && <span style={{ fontSize: '11px', color: '#475569' }}>{a.email}</span>}
                <span style={{ fontSize: '11px', color: '#334155' }}>{relTime(a.created_at)}</span>
                <button onClick={() => handleDelete(a.id)} title="Delete alert"
                  style={{ background: 'none', border: 'none', color: '#475569', cursor: 'pointer', fontSize: '16px', padding: '2px 4px' }}>✕</button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Triggered */}
      {fired.length > 0 && (
        <div>
          <h2 style={{ fontSize: '13px', fontWeight: 700, color: '#94a3b8', marginBottom: '12px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            Triggered ({fired.length})
          </h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {fired.map(a => (
              <div key={a.id} style={{ display: 'flex', alignItems: 'center', gap: '12px', padding: '10px 16px', borderRadius: '10px', border: '1px solid #1e293b', background: 'rgba(15,23,42,0.4)', opacity: 0.7 }}>
                <span style={{ fontSize: '12px', fontWeight: 700, color: '#22c55e', minWidth: '70px', fontFamily: 'monospace' }}>✓ {a.symbol}</span>
                <span style={{ fontSize: '12px', color: '#64748b', flex: 1 }}>{triggeredLabel(a)}</span>
                {a.note && <span style={{ fontSize: '11px', color: '#334155', fontStyle: 'italic' }}>{a.note}</span>}
                <span style={{ fontSize: '11px', color: '#334155' }}>{a.triggered_at ? relTime(a.triggered_at) : ''}</span>
                <button onClick={() => handleDelete(a.id)} title="Delete"
                  style={{ background: 'none', border: 'none', color: '#334155', cursor: 'pointer', fontSize: '16px', padding: '2px 4px' }}>✕</button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Signal Alerts tab ──────────────────────────────────────────────────────

function SignalAlertsTab() {
  const { data: stocks } = useSWR<Stock[]>('stocks-all', () => api.listStocks());
  const { data: signalAlerts, mutate } = useSWR<SignalAlertItem[]>('signal-alerts', () => api.listSignalAlerts(), { refreshInterval: 30000 });
  const { data: allSignals } = useSWR<SignalSummary[]>('signals-' + getSignalStyle(), () => api.allSignals(getSignalStyle()), { refreshInterval: 120000 });

  const [addSymbol, setAddSymbol] = useState('');
  const [addHorizon, setAddHorizon] = useState('SWING');
  const [email, setEmail]         = useState('');
  const [adding, setAdding]       = useState(false);
  const [addError, setAddError]   = useState('');
  const [addOk, setAddOk]         = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [togglingId, setTogglingId] = useState<number | null>(null);

  async function handleToggleMode(sub: SignalAlertItem) {
    const next = (sub.alert_mode ?? 'all') === 'all' ? 'buy_only' : 'all';
    setTogglingId(sub.id);
    try { await api.updateSignalAlert(sub.id, { alert_mode: next }); await mutate(); } catch {}
    setTogglingId(null);
  }

  async function handleToggleConsensus(sub: SignalAlertItem) {
    setTogglingId(sub.id);
    try { await api.updateSignalAlert(sub.id, { require_consensus: !sub.require_consensus }); await mutate(); } catch {}
    setTogglingId(null);
  }

  useEffect(() => {
    const s = typeof window !== 'undefined' ? localStorage.getItem('stockai_alert_email') : null;
    if (s) setEmail(s);
  }, []);

  // Build a symbol→signal lookup
  const sigMap: Record<string, SignalSummary> = {};
  for (const s of allSignals ?? []) sigMap[s.symbol] = s;

  // Stocks not yet fully subscribed (show all for picker — user can add multiple horizons per stock)
  const available = stocks ?? [];

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    if (!addSymbol || !email) return;
    setAdding(true); setAddError('');
    try {
      await api.createSignalAlert(addSymbol, email, 'all', addHorizon);
      localStorage.setItem('stockai_alert_email', email);
      await mutate();
      setAddSymbol('');
      setAddOk(true); setTimeout(() => setAddOk(false), 2000);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setAddError(msg.includes('400') ? 'No email — enter one below' : 'Failed to subscribe');
    } finally { setAdding(false); }
  }

  async function handleDelete(id: number) {
    setDeletingId(id);
    try { await api.deleteSignalAlert(id); await mutate(); } catch {}
    setDeletingId(null);
  }

  const subscriptions = signalAlerts ?? [];

  // Summary counts from live signals
  const buys  = subscriptions.filter(a => sigMap[a.symbol]?.signal === 'BUY').length;
  const holds = subscriptions.filter(a => sigMap[a.symbol]?.signal === 'HOLD').length;
  const sells = subscriptions.filter(a => sigMap[a.symbol]?.signal === 'SELL').length;
  const waits = subscriptions.filter(a => sigMap[a.symbol]?.signal === 'WAIT').length;

  return (
    <div>
      {/* How it works */}
      <div style={{ marginBottom: '20px', padding: '14px 18px', borderRadius: '10px', background: 'rgba(99,102,241,0.07)', border: '1px solid rgba(99,102,241,0.2)', fontSize: '12px', color: '#94a3b8', lineHeight: 1.6 }}>
        <span style={{ color: '#818cf8', fontWeight: 700 }}>How it works: </span>
        You get an email when the AI signal improves (SELL → HOLD, or HOLD → BUY) <em>and</em> analysts rate the stock BUY or STRONG BUY. Checked every minute during market hours.
      </div>

      {/* Add subscription form */}
      <div style={{ borderRadius: '12px', border: '1px solid rgba(99,102,241,0.25)', background: 'rgba(15,23,42,0.95)', overflow: 'hidden', marginBottom: '24px' }}>
        <div style={{ height: '3px', background: 'linear-gradient(90deg,#6366f1,#a78bfa,#6366f1)' }} />
        <div style={{ padding: '20px 24px' }}>
          <h2 style={{ margin: '0 0 16px', fontSize: '14px', fontWeight: 700, color: '#e2e8f0' }}>Add Subscription</h2>
          <form onSubmit={handleAdd}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 120px 1fr auto', gap: '12px', alignItems: 'end' }}>
              <div>
                <label style={lbl}>Stock</label>
                <select value={addSymbol} onChange={e => setAddSymbol(e.target.value)} required style={inp}>
                  <option value="">Select stock…</option>
                  {available.map(s => (
                    <option key={s.symbol} value={s.symbol}>{s.symbol} — {s.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label style={lbl}>Horizon</label>
                <select value={addHorizon} onChange={e => setAddHorizon(e.target.value)} style={inp}>
                  {['SHORT','SWING','LONG','GROWTH'].map(h => <option key={h} value={h}>{h}</option>)}
                </select>
              </div>
              <div>
                <label style={lbl}>Email</label>
                <input type="email" value={email} onChange={e => setEmail(e.target.value)}
                  placeholder="you@example.com" required style={inp} />
              </div>
              <button type="submit" disabled={adding} style={{
                background: addOk ? 'rgba(34,197,94,0.2)' : 'linear-gradient(135deg,#6366f1,#818cf8)',
                border: addOk ? '1px solid rgba(34,197,94,0.4)' : 'none',
                color: addOk ? '#4ade80' : '#fff', padding: '9px 20px',
                borderRadius: '8px', fontSize: '13px', fontWeight: 700,
                cursor: adding ? 'not-allowed' : 'pointer', whiteSpace: 'nowrap',
                opacity: adding ? 0.6 : 1, height: '38px',
              }}>
                {addOk ? '✓ Added' : adding ? 'Adding…' : '+ Subscribe'}
              </button>
            </div>
            {addError && <div style={{ marginTop: '8px', fontSize: '12px', color: '#f87171' }}>{addError}</div>}
          </form>
        </div>
      </div>

      {/* Stats summary */}
      {subscriptions.length > 0 && (
        <div style={{ display: 'flex', gap: '10px', marginBottom: '20px' }}>
          {[
            { label: 'Total', value: subscriptions.length, color: '#94a3b8', bg: 'rgba(148,163,184,0.08)' },
            { label: 'BUY', value: buys,  color: SIGNAL_COLOR.BUY,  bg: SIGNAL_BG.BUY  },
            { label: 'HOLD', value: holds, color: SIGNAL_COLOR.HOLD, bg: SIGNAL_BG.HOLD },
            { label: 'SELL', value: sells, color: SIGNAL_COLOR.SELL, bg: SIGNAL_BG.SELL },
            { label: 'WAIT', value: waits, color: SIGNAL_COLOR.WAIT, bg: SIGNAL_BG.WAIT },
          ].map(s => (
            <div key={s.label} style={{ flex: 1, padding: '12px', borderRadius: '10px', border: `1px solid ${s.color}33`, background: s.bg, textAlign: 'center' }}>
              <div style={{ fontSize: '22px', fontWeight: 800, color: s.color }}>{s.value}</div>
              <div style={{ fontSize: '11px', color: '#64748b', fontWeight: 600, marginTop: '2px' }}>{s.label}</div>
            </div>
          ))}
        </div>
      )}

      {/* Subscriptions list */}
      <div>
        <h2 style={{ fontSize: '13px', fontWeight: 700, color: '#94a3b8', marginBottom: '12px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
          Watching ({subscriptions.length})
        </h2>

        {subscriptions.length === 0 ? (
          <div style={{ padding: '48px', textAlign: 'center', borderRadius: '12px', border: '1px dashed #1e293b', color: '#334155', fontSize: '13px' }}>
            <div style={{ fontSize: '32px', marginBottom: '12px' }}>🔕</div>
            <div style={{ fontWeight: 600, color: '#475569', marginBottom: '6px' }}>No signal subscriptions yet</div>
            <div style={{ fontSize: '12px' }}>Add stocks above to get notified when the AI signal improves.</div>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {subscriptions.map(sub => {
              const sig = sigMap[sub.symbol];
              const signal = sig?.signal ?? '—';
              const conf   = sig?.confidence ?? 0;
              const color  = SIGNAL_COLOR[signal] ?? '#94a3b8';
              const bg     = SIGNAL_BG[signal]    ?? 'rgba(148,163,184,0.05)';
              const stockInfo = (stocks ?? []).find(s => s.symbol === sub.symbol);

              return (
                <div key={sub.id} style={{
                  display: 'grid', alignItems: 'center',
                  gridTemplateColumns: '90px 60px 1fr 100px 110px 120px 80px 90px 36px',
                  gap: '10px', padding: '13px 16px', borderRadius: '10px',
                  border: '1px solid #1e293b', background: 'rgba(15,23,42,0.7)',
                  transition: 'border-color 0.15s',
                }}>

                  {/* Symbol */}
                  <Link href={`/stock/${sub.symbol}`} style={{ textDecoration: 'none' }}>
                    <div style={{ fontSize: '13px', fontWeight: 800, color: '#818cf8', fontFamily: 'monospace' }}>{sub.symbol}</div>
                    {stockInfo && <div style={{ fontSize: '10px', color: '#475569', marginTop: '2px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{stockInfo.name}</div>}
                  </Link>

                  {/* Horizon badge */}
                  <div>
                    <span style={{ padding: '2px 7px', borderRadius: '5px', fontSize: '10px', fontWeight: 700, background: 'rgba(99,102,241,0.12)', color: '#818cf8', border: '1px solid rgba(99,102,241,0.25)' }}>
                      {sub.horizon ?? 'SWING'}
                    </span>
                  </div>

                  {/* Email */}
                  <div style={{ fontSize: '11px', color: '#475569', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={sub.email ?? ''}>
                    {sub.email ?? '—'}
                  </div>

                  {/* Current signal badge */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <span style={{ padding: '3px 10px', borderRadius: '6px', fontSize: '11px', fontWeight: 700, color, background: bg, border: `1px solid ${color}44`, letterSpacing: '0.04em' }}>{signal}</span>
                  </div>

                  {/* Confidence */}
                  <div style={{ fontSize: '12px', color: '#64748b' }}>
                    {sig ? (
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                        <div style={{ flex: 1, height: '4px', borderRadius: '2px', background: '#1e293b', overflow: 'hidden' }}>
                          <div style={{ width: `${conf}%`, height: '100%', background: color, borderRadius: '2px' }} />
                        </div>
                        <span style={{ fontSize: '11px', color: '#64748b', minWidth: '28px', textAlign: 'right' }}>{conf.toFixed(0)}%</span>
                      </div>
                    ) : <span style={{ color: '#334155' }}>—</span>}
                  </div>

                  {/* Last triggered */}
                  <div style={{ fontSize: '11px', color: '#475569' }}>
                    {sub.last_signal ? (
                      <span style={{ padding: '2px 8px', borderRadius: '4px', fontWeight: 600, background: 'rgba(99,102,241,0.15)', color: '#818cf8' }}>Last: {sub.last_signal}</span>
                    ) : <span style={{ color: '#334155' }}>Never sent</span>}
                  </div>

                  {/* Consensus toggle */}
                  <button
                    onClick={() => handleToggleConsensus(sub)}
                    disabled={togglingId === sub.id}
                    title={sub.require_consensus ? 'Consensus required (≥2 horizons agree) — click to disable' : 'Any transition — click to require ≥2 horizons to agree'}
                    style={{
                      padding: '3px 7px', borderRadius: '6px', fontSize: '10px', fontWeight: 700,
                      cursor: 'pointer', border: '1px solid',
                      background: sub.require_consensus ? 'rgba(251,191,36,0.12)' : 'rgba(148,163,184,0.06)',
                      color: sub.require_consensus ? '#fbbf24' : '#475569',
                      borderColor: sub.require_consensus ? 'rgba(251,191,36,0.3)' : '#1e293b',
                      opacity: togglingId === sub.id ? 0.5 : 1, whiteSpace: 'nowrap',
                    }}
                  >
                    {togglingId === sub.id ? '…' : sub.require_consensus ? '⚡ Consensus' : 'Any'}
                  </button>

                  {/* Mode toggle */}
                  <button
                    onClick={() => handleToggleMode(sub)}
                    disabled={togglingId === sub.id}
                    title={(sub.alert_mode ?? 'all') === 'buy_only' ? 'BUY transitions only — click for all' : 'All transitions — click for BUY only'}
                    style={{
                      padding: '3px 9px', borderRadius: '6px', fontSize: '11px', fontWeight: 700,
                      cursor: 'pointer', border: '1px solid',
                      background: (sub.alert_mode ?? 'all') === 'buy_only' ? 'rgba(74,222,128,0.12)' : 'rgba(148,163,184,0.08)',
                      color: (sub.alert_mode ?? 'all') === 'buy_only' ? '#4ade80' : '#64748b',
                      borderColor: (sub.alert_mode ?? 'all') === 'buy_only' ? 'rgba(74,222,128,0.3)' : '#1e293b',
                      opacity: togglingId === sub.id ? 0.5 : 1,
                    }}
                  >
                    {togglingId === sub.id ? '…' : (sub.alert_mode ?? 'all') === 'buy_only' ? 'BUY only' : 'All'}
                  </button>

                  {/* Delete */}
                  <button
                    onClick={() => handleDelete(sub.id)}
                    disabled={deletingId === sub.id}
                    title="Remove subscription"
                    style={{ background: 'none', border: 'none', color: '#334155', cursor: 'pointer', fontSize: '16px', padding: '4px', borderRadius: '4px', transition: 'color 0.15s' }}
                    onMouseEnter={e => (e.currentTarget.style.color = '#f87171')}
                    onMouseLeave={e => (e.currentTarget.style.color = '#334155')}
                  >
                    {deletingId === sub.id ? '…' : '✕'}
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Page shell ─────────────────────────────────────────────────────────────

type Tab = 'price' | 'signal';

export default function AlertsPage() {
  const [tab, setTab] = useState<Tab>('price');
  const { data: priceAlerts } = useSWR<PriceAlert[]>('alerts', () => api.listAlerts());
  const { data: signalAlerts } = useSWR<SignalAlertItem[]>('signal-alerts', () => api.listSignalAlerts());

  const activePrice  = (priceAlerts  ?? []).filter(a => !a.triggered).length;
  const activeSignal = (signalAlerts ?? []).length;

  function TabBtn({ id, label, count }: { id: Tab; label: string; count: number }) {
    const active = tab === id;
    return (
      <button
        onClick={() => setTab(id)}
        style={{
          padding: '8px 20px', borderRadius: '8px', fontSize: '13px', fontWeight: 600,
          cursor: 'pointer', border: active ? '1px solid rgba(99,102,241,0.5)' : '1px solid transparent',
          background: active ? 'rgba(99,102,241,0.15)' : 'transparent',
          color: active ? '#818cf8' : '#64748b', transition: 'all 0.15s', display: 'flex', alignItems: 'center', gap: '8px',
        }}
      >
        {label}
        {count > 0 && (
          <span style={{
            fontSize: '10px', fontWeight: 700, padding: '1px 6px', borderRadius: '10px',
            background: active ? 'rgba(99,102,241,0.3)' : 'rgba(148,163,184,0.15)',
            color: active ? '#a5b4fc' : '#64748b',
          }}>{count}</span>
        )}
      </button>
    );
  }

  return (
    <div style={{ maxWidth: '960px', margin: '0 auto', paddingTop: '8px' }}>

      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: '20px', fontWeight: 800, color: '#f1f5f9' }}>Alerts</h1>
          <div style={{ fontSize: '12px', color: '#475569', marginTop: '3px' }}>
            Price alerts checked every minute · Signal alerts checked every minute during market hours
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: '6px', marginBottom: '24px', padding: '6px', borderRadius: '10px', background: 'rgba(15,23,42,0.6)', border: '1px solid #1e293b', width: 'fit-content' }}>
        <TabBtn id="price"  label="Price Alerts"  count={activePrice}  />
        <TabBtn id="signal" label="Signal Alerts" count={activeSignal} />
      </div>

      {tab === 'price'  && <PriceAlertsTab />}
      {tab === 'signal' && <SignalAlertsTab />}
    </div>
  );
}
