import { useState, useEffect, useMemo } from 'react';
import Link from 'next/link';
import useSWR from 'swr';
import { api, type PriceAlert, type SignalAlertItem, type Stock, type SignalSummary, type WatchlistMeta, type WatchlistItem, type CompoundCondition } from '@/lib/api';
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

function conditionShortLabel(cond: string): string {
  if (cond === 'above') return 'Above';
  if (cond === 'below') return 'Below';
  if (cond === 'cross_above_ema') return 'EMA Cross ↑';
  if (cond === 'cross_below_ema') return 'EMA Cross ↓';
  if (cond === 'new_52wk_high') return '52W High';
  if (cond === 'new_52wk_low') return '52W Low';
  if (cond === 'golden_cross') return 'Golden Cross';
  if (cond === 'death_cross') return 'Death Cross';
  if (cond === 'macd_bullish_cross') return 'MACD Cross';
  if (cond === 'rsi_oversold_bounce') return 'RSI Bounce';
  if (cond === 'double_bottom') return 'Double Bottom';
  if (cond === 'breakout') return 'Breakout';
  return cond;
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

const COMPOUND_METRIC_LABEL: Record<string, string> = {
  volume_ratio: 'RVOL', rsi: 'RSI', signal: 'Signal',
};
const COMPOUND_OP_LABEL: Record<string, string> = {
  gte: '≥', lte: '≤', eq: '=',
};
function compoundConditionLabel(c: CompoundCondition): string {
  return `${COMPOUND_METRIC_LABEL[c.metric] ?? c.metric} ${COMPOUND_OP_LABEL[c.op] ?? c.op} ${c.value}`;
}
function compoundSummary(conds: CompoundCondition[] | null | undefined): string {
  if (!conds || !conds.length) return '';
  return conds.map(compoundConditionLabel).join(' AND ');
}

const SIGNAL_COLOR: Record<string, string> = {
  BUY: '#4ade80', HOLD: '#facc15', SELL: '#f87171', WAIT: '#94a3b8',
};
const SIGNAL_BG: Record<string, string> = {
  BUY: 'rgba(74,222,128,0.12)', HOLD: 'rgba(250,204,21,0.12)',
  SELL: 'rgba(248,113,113,0.12)', WAIT: 'rgba(148,163,184,0.08)',
};

const ALL_CONDITIONS = [
  'above', 'below', 'cross_above_ema', 'cross_below_ema',
  'new_52wk_high', 'new_52wk_low', 'golden_cross', 'death_cross',
  'macd_bullish_cross', 'rsi_oversold_bounce', 'double_bottom', 'breakout',
  'volume_spike', 'pct_below_52wk_high',
];

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

const PAGE_SIZE = 20;

// ── Bulk Pattern Alert card ────────────────────────────────────────────────

function BulkPatternAlertCard({ onDone }: { onDone: () => void }) {
  const { data: watchlists } = useSWR<WatchlistMeta[]>('watchlists', () => api.listWatchlists());
  const [listId, setListId]       = useState<number | ''>('');
  const [condition, setCondition] = useState('golden_cross');
  const [recurring, setRecurring] = useState(true);
  const [email, setEmail]         = useState('');
  const [applying, setApplying]   = useState(false);
  const [result, setResult]       = useState('');
  const [errors, setErrors]       = useState<string[]>([]);

  useEffect(() => {
    const s = typeof window !== 'undefined' ? localStorage.getItem('stockai_alert_email') : null;
    if (s) setEmail(s);
  }, []);

  async function handleApply(e: React.FormEvent) {
    e.preventDefault();
    if (!listId || !email) return;
    setApplying(true); setResult(''); setErrors([]);
    try {
      const items: WatchlistItem[] = await api.listWatchlist(Number(listId));
      if (!items.length) { setResult('Watchlist is empty.'); setApplying(false); return; }
      const threshold = ['cross_above_ema', 'cross_below_ema'].includes(condition) ? 20 : 0;
      let created = 0;
      const errs: string[] = [];
      await Promise.all(items.map(async item => {
        try {
          await api.createAlert({ symbol: item.symbol, condition, threshold, email, recurring });
          created++;
        } catch (err) {
          errs.push(`${item.symbol}: ${err instanceof Error ? err.message : String(err)}`);
        }
      }));
      localStorage.setItem('stockai_alert_email', email);
      setResult(`Created ${created} alert${created !== 1 ? 's' : ''} for ${items.length} stocks.`);
      if (errs.length) setErrors(errs.slice(0, 5));
      if (created > 0) onDone();
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
              <span style={{ fontSize: '12px', color: result.startsWith('Created') && !result.startsWith('Created 0') ? '#4ade80' : '#f87171' }}>{result}</span>
            )}
          </div>
        </form>
        {errors.length > 0 && (
          <div style={{ marginTop: '10px', padding: '10px 14px', borderRadius: '8px', background: 'rgba(248,113,113,0.08)', border: '1px solid rgba(248,113,113,0.2)' }}>
            <div style={{ fontSize: '11px', color: '#f87171', fontWeight: 700, marginBottom: '4px' }}>Failed for {errors.length} stocks (first 5 shown):</div>
            {errors.map((e, i) => <div key={i} style={{ fontSize: '11px', color: '#94a3b8', fontFamily: 'monospace' }}>{e}</div>)}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Price Alerts tab ───────────────────────────────────────────────────────

function PriceAlertsTab() {
  const { data: stocks } = useSWR<Stock[]>('stocks-all', () => api.listStocks());
  const { data: alerts, mutate } = useSWR<PriceAlert[]>('alerts', () => api.listAlerts(), { refreshInterval: 30000 });

  // Create form state
  const [symbol, setSymbol]       = useState('');
  const [condition, setCondition] = useState('above');
  const [threshold, setThreshold] = useState('');
  const [emaPeriod, setEmaPeriod] = useState('20');
  const [email, setEmail]         = useState('');
  const [note, setNote]           = useState('');
  const [webhookUrl, setWebhookUrl] = useState('');
  const [recurring, setRecurring] = useState(false);
  const [saving, setSaving]       = useState(false);
  const [saved, setSaved]         = useState(false);
  const [error, setError]         = useState('');

  // T230-ALERTING-COMPOUND-CONDITIONS: up to 3 extra AND-conditions (volume/RSI/signal)
  const [compoundConditions, setCompoundConditions] = useState<CompoundCondition[]>([]);
  const canAddCompound = compoundConditions.length < 3 && !NO_THRESHOLD.includes(condition);

  function addCompoundCondition() {
    if (compoundConditions.length >= 3) return;
    setCompoundConditions(prev => [...prev, { metric: 'volume_ratio', op: 'gte', value: 2 }]);
  }
  function updateCompoundCondition(i: number, patch: Partial<CompoundCondition>) {
    setCompoundConditions(prev => prev.map((c, idx) => {
      if (idx !== i) return c;
      const next = { ...c, ...patch } as CompoundCondition;
      // Reset value to a sane default when metric changes type (numeric <-> string)
      if (patch.metric && patch.metric !== c.metric) {
        next.value = patch.metric === 'signal' ? 'BUY' : patch.metric === 'rsi' ? 30 : 2;
        next.op = patch.metric === 'signal' ? 'eq' : 'gte';
      }
      return next;
    }));
  }
  function removeCompoundCondition(i: number) {
    setCompoundConditions(prev => prev.filter((_, idx) => idx !== i));
  }

  // Filter + pagination state
  const [filterSymbol, setFilterSymbol]       = useState('');
  const [filterCondition, setFilterCondition] = useState('');
  const [filterMode, setFilterMode]           = useState<'all' | 'active' | 'triggered'>('active');
  const [page, setPage]                       = useState(1);

  // Selection for bulk delete
  const [selected, setSelected]   = useState<Set<number>>(new Set());
  const [deleting, setDeleting]   = useState(false);

  const isEma              = EMA_CONDITIONS.includes(condition);
  const isNoThreshold      = NO_THRESHOLD.includes(condition);
  const isVolumeMultiplier = condition === 'volume_spike';
  const isPctCondition     = condition === 'pct_below_52wk_high';

  useEffect(() => {
    const s = typeof window !== 'undefined' ? localStorage.getItem('stockai_alert_email') : null;
    if (s) setEmail(s);
  }, []);

  // Filter logic
  const allAlerts = alerts ?? [];
  const filtered = useMemo(() => {
    return allAlerts.filter(a => {
      if (filterMode === 'active' && a.triggered) return false;
      if (filterMode === 'triggered' && !a.triggered) return false;
      if (filterSymbol && !a.symbol.toLowerCase().includes(filterSymbol.toLowerCase())) return false;
      if (filterCondition && a.condition !== filterCondition) return false;
      return true;
    });
  }, [allAlerts, filterMode, filterSymbol, filterCondition]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const paginated  = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  // Reset page when filters change
  useEffect(() => { setPage(1); }, [filterSymbol, filterCondition, filterMode]);

  // Unique conditions present in alerts (for filter dropdown)
  const presentConditions = useMemo(() => {
    return [...new Set(allAlerts.map(a => a.condition))].sort();
  }, [allAlerts]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!symbol || !email) return;
    if (!isNoThreshold && !isEma && !threshold) return;
    const thresholdVal = isNoThreshold ? 0 : isEma ? parseInt(emaPeriod) : parseFloat(threshold);
    setSaving(true); setError('');
    try {
      await api.createAlert({
        symbol, condition, threshold: thresholdVal, email, note: note || undefined,
        recurring: isNoThreshold ? recurring : false, webhook_url: webhookUrl || undefined,
        compound_conditions: canAddCompound && compoundConditions.length ? compoundConditions : undefined,
      });
      localStorage.setItem('stockai_alert_email', email);
      await mutate();
      setThreshold(''); setNote(''); setWebhookUrl(''); setCompoundConditions([]);
      setSaved(true); setTimeout(() => setSaved(false), 2000);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to create alert');
    } finally { setSaving(false); }
  }

  async function handleDelete(id: number) {
    try { await api.deleteAlert(id); await mutate(); } catch {}
  }

  // Bulk delete selected
  async function handleBulkDelete() {
    if (!selected.size || deleting) return;
    setDeleting(true);
    await Promise.all([...selected].map(id => api.deleteAlert(id).catch(() => {})));
    setSelected(new Set());
    await mutate();
    setDeleting(false);
  }

  // Delete all triggered one-time alerts
  async function handleClearTriggered() {
    const ids = (allAlerts ?? []).filter(a => a.triggered && !a.recurring).map(a => a.id);
    if (!ids.length || deleting) return;
    if (!confirm(`Delete all ${ids.length} triggered (one-time) alerts?`)) return;
    setDeleting(true);
    await Promise.all(ids.map(id => api.deleteAlert(id).catch(() => {})));
    setSelected(new Set());
    await mutate();
    setDeleting(false);
  }

  // Bulk delete all of a condition type
  async function handleDeleteByCondition(cond: string) {
    const ids = allAlerts.filter(a => a.condition === cond).map(a => a.id);
    if (!ids.length) return;
    if (!confirm(`Delete all ${ids.length} "${conditionShortLabel(cond)}" alerts?`)) return;
    setDeleting(true);
    await Promise.all(ids.map(id => api.deleteAlert(id).catch(() => {})));
    await mutate();
    setDeleting(false);
  }

  function toggleSelect(id: number) {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function toggleSelectPage() {
    const pageIds = paginated.map(a => a.id);
    const allSelected = pageIds.every(id => selected.has(id));
    setSelected(prev => {
      const next = new Set(prev);
      if (allSelected) pageIds.forEach(id => next.delete(id));
      else pageIds.forEach(id => next.add(id));
      return next;
    });
  }

  const active    = allAlerts.filter(a => !a.triggered).length;
  const triggered = allAlerts.filter(a => a.triggered).length;

  // Group counts by condition (for bulk-delete-by-type UI)
  const conditionCounts = useMemo(() => {
    const map: Record<string, number> = {};
    allAlerts.forEach(a => { map[a.condition] = (map[a.condition] ?? 0) + 1; });
    return map;
  }, [allAlerts]);

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
                    <option value="volume_spike">Volume Spike (X× above avg)</option>
                    <option value="pct_below_52wk_high">% Below 52-Week High</option>
                  </optgroup>
                </select>
              </div>
              {!isNoThreshold && !isEma && !isVolumeMultiplier && !isPctCondition && (
                <div>
                  <label style={lbl}>Target price</label>
                  <input type="number" step="any" min="0" value={threshold}
                    onChange={e => setThreshold(e.target.value)} placeholder="0.00" required style={inp} />
                </div>
              )}
              {isVolumeMultiplier && (
                <div>
                  <label style={lbl}>Volume multiplier (e.g. 3 = 3× avg)</label>
                  <input type="number" step="0.1" min="1" value={threshold}
                    onChange={e => setThreshold(e.target.value)} placeholder="3" required style={inp} />
                </div>
              )}
              {isPctCondition && (
                <div>
                  <label style={lbl}>% below 52w high to trigger</label>
                  <input type="number" step="1" min="1" max="80" value={threshold}
                    onChange={e => setThreshold(e.target.value)} placeholder="10" required style={inp} />
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
              <div style={{ flex: 2 }}>
                <label style={lbl}>Webhook URL (optional)</label>
                <input type="url" value={webhookUrl} onChange={e => setWebhookUrl(e.target.value)}
                  placeholder="https://..." style={inp} />
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

            {/* T230-ALERTING-COMPOUND-CONDITIONS: extra AND-conditions, price alerts only */}
            {canAddCompound && (
              <div style={{ marginTop: '14px', paddingTop: '14px', borderTop: '1px solid #1e293b' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: compoundConditions.length ? '10px' : '0' }}>
                  <span style={lbl}>Also require (AND) — reduces false positives</span>
                  {compoundConditions.length < 3 && (
                    <button type="button" onClick={addCompoundCondition}
                      style={{ fontSize: '11px', fontWeight: 700, color: '#818cf8', background: 'rgba(99,102,241,0.1)', border: '1px solid rgba(99,102,241,0.25)', borderRadius: '6px', padding: '3px 9px', cursor: 'pointer' }}>
                      + AND condition
                    </button>
                  )}
                </div>
                {compoundConditions.map((c, i) => (
                  <div key={i} style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '8px' }}>
                    <select value={c.metric} onChange={e => updateCompoundCondition(i, { metric: e.target.value as CompoundCondition['metric'] })}
                      style={{ ...inp, width: '140px' }}>
                      <option value="volume_ratio">Volume (RVOL)</option>
                      <option value="rsi">RSI</option>
                      <option value="signal">Signal</option>
                    </select>
                    {c.metric === 'signal' ? (
                      <select value={c.value as string} onChange={e => updateCompoundCondition(i, { value: e.target.value })}
                        style={{ ...inp, width: '110px' }}>
                        <option value="BUY">= BUY</option>
                        <option value="SELL">= SELL</option>
                        <option value="HOLD">= HOLD</option>
                        <option value="WAIT">= WAIT</option>
                      </select>
                    ) : (
                      <>
                        <select value={c.op} onChange={e => updateCompoundCondition(i, { op: e.target.value as CompoundCondition['op'] })}
                          style={{ ...inp, width: '70px' }}>
                          <option value="gte">≥</option>
                          <option value="lte">≤</option>
                        </select>
                        <input type="number" step="0.1" value={c.value as number}
                          onChange={e => updateCompoundCondition(i, { value: parseFloat(e.target.value) || 0 })}
                          style={{ ...inp, width: '90px' }} />
                      </>
                    )}
                    <span style={{ fontSize: '11px', color: '#475569' }}>
                      {c.metric === 'volume_ratio' ? '× avg volume' : c.metric === 'rsi' ? 'RSI (0-100)' : ''}
                    </span>
                    <button type="button" onClick={() => removeCompoundCondition(i)}
                      style={{ marginLeft: 'auto', fontSize: '11px', color: '#f87171', background: 'transparent', border: 'none', cursor: 'pointer', padding: '2px 6px' }}>
                      ✕
                    </button>
                  </div>
                ))}
                {compoundConditions.length > 0 && (
                  <div style={{ fontSize: '11px', color: '#64748b', marginTop: '4px' }}>
                    Fires only when the base condition AND all of the above are true: {compoundSummary(compoundConditions)}
                  </div>
                )}
              </div>
            )}
            {error && <div style={{ marginTop: '8px', fontSize: '12px', color: '#f87171' }}>{error}</div>}
          </form>
        </div>
      </div>

      <BulkPatternAlertCard onDone={() => mutate()} />

      {/* Bulk delete by type */}
      {Object.keys(conditionCounts).length > 1 && (
        <div style={{ marginBottom: '20px', padding: '14px 16px', borderRadius: '10px', background: 'rgba(15,23,42,0.8)', border: '1px solid #1e293b' }}>
          <div style={{ fontSize: '11px', color: '#64748b', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '10px' }}>
            Bulk Delete by Type
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
            {Object.entries(conditionCounts).sort((a, b) => b[1] - a[1]).map(([cond, count]) => (
              <button key={cond} onClick={() => handleDeleteByCondition(cond)} disabled={deleting}
                style={{ padding: '5px 12px', borderRadius: '6px', fontSize: '11px', fontWeight: 600, cursor: 'pointer', border: '1px solid rgba(248,113,113,0.25)', background: 'rgba(248,113,113,0.06)', color: '#f87171', opacity: deleting ? 0.5 : 1 }}>
                Delete all {conditionShortLabel(cond)} ({count})
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Filter bar */}
      <div style={{ display: 'flex', gap: '8px', marginBottom: '16px', alignItems: 'center', flexWrap: 'wrap' }}>
        {/* Status filter */}
        <div style={{ display: 'flex', gap: '4px', background: 'rgba(15,23,42,0.6)', border: '1px solid #1e293b', borderRadius: '8px', padding: '4px' }}>
          {([['all', `All (${allAlerts.length})`], ['active', `Active (${active})`], ['triggered', `Triggered (${triggered})`]] as const).map(([mode, label]) => (
            <button key={mode} onClick={() => setFilterMode(mode)}
              style={{ padding: '5px 12px', borderRadius: '6px', fontSize: '12px', fontWeight: 600, cursor: 'pointer', border: filterMode === mode ? '1px solid rgba(99,102,241,0.5)' : '1px solid transparent', background: filterMode === mode ? 'rgba(99,102,241,0.15)' : 'transparent', color: filterMode === mode ? '#818cf8' : '#64748b' }}>
              {label}
            </button>
          ))}
        </div>

        {/* Symbol search */}
        <input
          type="text" value={filterSymbol} onChange={e => setFilterSymbol(e.target.value)}
          placeholder="Filter symbol…"
          style={{ ...inp, width: '140px', padding: '6px 10px', fontSize: '12px' }}
        />

        {/* Condition filter */}
        <select value={filterCondition} onChange={e => setFilterCondition(e.target.value)}
          style={{ ...inp, width: '180px', padding: '6px 10px', fontSize: '12px' }}>
          <option value="">All conditions</option>
          {presentConditions.map(c => (
            <option key={c} value={c}>{conditionShortLabel(c)}</option>
          ))}
        </select>

        {/* Bulk delete selected */}
        {selected.size > 0 && (
          <button onClick={handleBulkDelete} disabled={deleting}
            style={{ padding: '6px 14px', borderRadius: '6px', fontSize: '12px', fontWeight: 700, cursor: 'pointer', border: '1px solid rgba(248,113,113,0.4)', background: 'rgba(248,113,113,0.1)', color: '#f87171', whiteSpace: 'nowrap', opacity: deleting ? 0.5 : 1 }}>
            {deleting ? 'Deleting…' : `Delete ${selected.size} selected`}
          </button>
        )}

        {/* Clear all triggered one-timers */}
        {(allAlerts ?? []).filter(a => a.triggered && !a.recurring).length > 0 && selected.size === 0 && (
          <button onClick={handleClearTriggered} disabled={deleting}
            style={{ padding: '6px 14px', borderRadius: '6px', fontSize: '12px', fontWeight: 700, cursor: 'pointer', border: '1px solid rgba(248,113,113,0.25)', background: 'rgba(248,113,113,0.06)', color: '#f87171', whiteSpace: 'nowrap', opacity: deleting ? 0.5 : 1 }}>
            {deleting ? 'Clearing…' : `Clear triggered (${(allAlerts ?? []).filter(a => a.triggered && !a.recurring).length})`}
          </button>
        )}

        <div style={{ marginLeft: 'auto', fontSize: '12px', color: '#475569' }}>
          {filtered.length} alert{filtered.length !== 1 ? 's' : ''}
          {filtered.length > PAGE_SIZE && ` · page ${page}/${totalPages}`}
        </div>
      </div>

      {/* Alerts list */}
      {filtered.length === 0 ? (
        <div style={{ padding: '40px', textAlign: 'center', borderRadius: '10px', border: '1px dashed #1e293b', color: '#334155', fontSize: '13px' }}>
          No alerts match the current filter.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
          {/* Select-all row */}
          {filtered.length > 1 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '6px 12px', fontSize: '11px', color: '#475569' }}>
              <input type="checkbox"
                checked={paginated.length > 0 && paginated.every(a => selected.has(a.id))}
                onChange={toggleSelectPage}
                style={{ cursor: 'pointer', accentColor: '#6366f1' }}
              />
              <span>Select page ({paginated.length})</span>
              {selected.size > 0 && <span style={{ color: '#818cf8', fontWeight: 600 }}>{selected.size} selected total</span>}
            </div>
          )}

          {paginated.map(a => (
            <div key={a.id} style={{
              display: 'flex', alignItems: 'center', gap: '10px', padding: '11px 14px',
              borderRadius: '10px',
              border: `1px solid ${selected.has(a.id) ? 'rgba(99,102,241,0.4)' : a.triggered ? '#1e293b' : a.recurring ? 'rgba(251,191,36,0.2)' : 'rgba(99,102,241,0.2)'}`,
              background: selected.has(a.id) ? 'rgba(99,102,241,0.06)' : a.triggered ? 'rgba(15,23,42,0.4)' : 'rgba(15,23,42,0.8)',
              opacity: a.triggered ? 0.65 : 1,
            }}>
              <input type="checkbox" checked={selected.has(a.id)} onChange={() => toggleSelect(a.id)}
                style={{ cursor: 'pointer', accentColor: '#6366f1', flexShrink: 0 }} />

              {a.triggered && <span style={{ fontSize: '12px', color: '#22c55e', flexShrink: 0 }}>✓</span>}

              <Link href={`/stock/${a.symbol}`} style={{ fontSize: '13px', fontWeight: 800, color: a.triggered ? '#64748b' : '#818cf8', fontFamily: 'monospace', minWidth: '70px', textDecoration: 'none', flexShrink: 0 }}>
                {a.symbol}
              </Link>

              {/* Condition badge */}
              <span style={{ fontSize: '10px', padding: '2px 7px', borderRadius: '4px', background: 'rgba(99,102,241,0.1)', color: '#818cf8', border: '1px solid rgba(99,102,241,0.2)', whiteSpace: 'nowrap', flexShrink: 0 }}>
                {conditionShortLabel(a.condition)}
              </span>

              <span style={{ fontSize: '13px', color: a.triggered ? '#64748b' : '#cbd5e1', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {a.triggered ? triggeredLabel(a) : alertLabel(a)}
              </span>

              {a.compound_conditions && a.compound_conditions.length > 0 && (
                <span title={compoundSummary(a.compound_conditions)} style={{ fontSize: '10px', padding: '2px 7px', borderRadius: '4px', background: 'rgba(74,222,128,0.08)', color: '#4ade80', border: '1px solid rgba(74,222,128,0.2)', whiteSpace: 'nowrap', flexShrink: 0, maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  AND {compoundSummary(a.compound_conditions)}
                </span>
              )}

              {a.note && <span style={{ fontSize: '11px', color: '#475569', fontStyle: 'italic', flexShrink: 0, maxWidth: '120px', overflow: 'hidden', textOverflow: 'ellipsis' }}>{a.note}</span>}

              {a.recurring && !a.triggered && (
                <span style={{ fontSize: '10px', padding: '2px 7px', borderRadius: '4px', background: 'rgba(251,191,36,0.08)', color: '#fbbf24', border: '1px solid rgba(251,191,36,0.2)', whiteSpace: 'nowrap', flexShrink: 0 }}>↻</span>
              )}

              {a.recurring && a.last_sent_at && (
                <span style={{ fontSize: '11px', color: '#475569', whiteSpace: 'nowrap', flexShrink: 0 }}>fired {relTime(a.last_sent_at)}</span>
              )}
              {a.triggered && a.triggered_at && (
                <span style={{ fontSize: '11px', color: '#334155', whiteSpace: 'nowrap', flexShrink: 0 }}>{relTime(a.triggered_at)}</span>
              )}

              <span style={{ fontSize: '11px', color: '#334155', whiteSpace: 'nowrap', flexShrink: 0 }}>{relTime(a.created_at)}</span>

              <button onClick={() => handleDelete(a.id)} title="Delete alert"
                style={{ background: 'none', border: 'none', color: '#475569', cursor: 'pointer', fontSize: '16px', padding: '2px 4px', flexShrink: 0 }}
                onMouseEnter={e => (e.currentTarget.style.color = '#f87171')}
                onMouseLeave={e => (e.currentTarget.style.color = '#475569')}>
                ✕
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div style={{ display: 'flex', gap: '6px', marginTop: '16px', justifyContent: 'center', alignItems: 'center' }}>
          <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}
            style={{ padding: '6px 14px', borderRadius: '6px', fontSize: '12px', fontWeight: 600, cursor: page === 1 ? 'not-allowed' : 'pointer', border: '1px solid #1e293b', background: 'transparent', color: page === 1 ? '#334155' : '#94a3b8', opacity: page === 1 ? 0.5 : 1 }}>
            ← Prev
          </button>
          {Array.from({ length: Math.min(7, totalPages) }, (_, i) => {
            const p = totalPages <= 7 ? i + 1 : page <= 4 ? i + 1 : page >= totalPages - 3 ? totalPages - 6 + i : page - 3 + i;
            return (
              <button key={p} onClick={() => setPage(p)}
                style={{ padding: '6px 10px', borderRadius: '6px', fontSize: '12px', fontWeight: 600, cursor: 'pointer', border: `1px solid ${page === p ? 'rgba(99,102,241,0.5)' : '#1e293b'}`, background: page === p ? 'rgba(99,102,241,0.15)' : 'transparent', color: page === p ? '#818cf8' : '#64748b', minWidth: '32px' }}>
                {p}
              </button>
            );
          })}
          <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages}
            style={{ padding: '6px 14px', borderRadius: '6px', fontSize: '12px', fontWeight: 600, cursor: page === totalPages ? 'not-allowed' : 'pointer', border: '1px solid #1e293b', background: 'transparent', color: page === totalPages ? '#334155' : '#94a3b8', opacity: page === totalPages ? 0.5 : 1 }}>
            Next →
          </button>
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

                  {/* Last signal + sent time */}
                  <div style={{ fontSize: '11px', color: '#475569', display: 'flex', flexDirection: 'column', gap: '2px' }}>
                    {sub.last_signal ? (
                      <span style={{ padding: '2px 8px', borderRadius: '4px', fontWeight: 600, background: 'rgba(99,102,241,0.15)', color: '#818cf8' }}>Last: {sub.last_signal}</span>
                    ) : <span style={{ color: '#334155' }}>Never sent</span>}
                    {sub.last_sent_at && (
                      <span style={{ fontSize: '10px', color: '#334155', paddingLeft: '2px' }}>
                        {(() => {
                          const diff = Date.now() - new Date(sub.last_sent_at).getTime();
                          const h = Math.floor(diff / 3600000);
                          const d = Math.floor(h / 24);
                          return d > 0 ? `${d}d ago` : h > 0 ? `${h}h ago` : 'just now';
                        })()}
                      </span>
                    )}
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
