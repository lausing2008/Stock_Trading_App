import { useState } from 'react';
import Link from 'next/link';
import useSWR from 'swr';
import { api, type JournalTrade, type JournalTradeIn } from '@/lib/api';

// ─── P&L helpers ─────────────────────────────────────────────────────────────

function calcPnl(t: JournalTrade): number | null {
  if (t.exit_price == null) return null;
  const dir = t.action === 'BUY' ? 1 : -1;
  return dir * (t.exit_price - t.entry_price) * t.shares;
}

function calcPnlPct(t: JournalTrade): number | null {
  if (t.exit_price == null) return null;
  const dir = t.action === 'BUY' ? 1 : -1;
  return dir * (t.exit_price - t.entry_price) / t.entry_price * 100;
}

function calcRR(t: JournalTrade): number | null {
  if (t.stop_loss == null || t.take_profit == null) return null;
  const risk = Math.abs(t.entry_price - t.stop_loss);
  const reward = Math.abs(t.take_profit - t.entry_price);
  return risk > 0 ? reward / risk : null;
}

// ─── Blank form ───────────────────────────────────────────────────────────────

const BLANK: JournalTradeIn = {
  symbol: '', action: 'BUY', shares: 0, entry_price: 0, exit_price: null,
  entry_date: new Date().toISOString().slice(0, 10), exit_date: null,
  stop_loss: null, take_profit: null, notes: null, strategy: null, signal_confidence: null,
};

// ─── Component ────────────────────────────────────────────────────────────────

export default function JournalPage() {
  const { data: trades = [], mutate, isLoading } = useSWR<JournalTrade[]>('journal', () => api.listJournal());

  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState<JournalTradeIn>({ ...BLANK });
  const [filterOpen, setFilterOpen] = useState<boolean | null>(null);
  const [sortBy, setSortBy] = useState<'date' | 'pnl' | 'symbol'>('date');
  const [deleteConfirm, setDeleteConfirm] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);

  function openAdd() {
    setEditingId(null);
    setForm({ ...BLANK, entry_date: new Date().toISOString().slice(0, 10) });
    setShowForm(true);
  }

  function openEdit(t: JournalTrade) {
    setEditingId(t.id);
    setForm({
      symbol: t.symbol, action: t.action, shares: t.shares,
      entry_price: t.entry_price, exit_price: t.exit_price,
      entry_date: t.entry_date, exit_date: t.exit_date,
      stop_loss: t.stop_loss, take_profit: t.take_profit,
      notes: t.notes, strategy: t.strategy, signal_confidence: t.signal_confidence,
    });
    setShowForm(true);
  }

  async function handleSave() {
    if (!form.symbol || form.entry_price <= 0 || form.shares <= 0) return;
    setSaving(true);
    try {
      if (editingId != null) {
        await api.updateJournalTrade(editingId, form);
      } else {
        await api.createJournalTrade(form);
      }
      await mutate();
      setShowForm(false);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: number) {
    await api.deleteJournalTrade(id);
    await mutate();
    setDeleteConfirm(null);
  }

  const closed  = trades.filter(t => t.exit_price != null);
  const open    = trades.filter(t => t.exit_price == null);
  const totalPnl = closed.reduce((s, t) => s + (calcPnl(t) ?? 0), 0);
  const wins    = closed.filter(t => (calcPnl(t) ?? 0) > 0);
  const losses  = closed.filter(t => (calcPnl(t) ?? 0) <= 0);
  const winRate = closed.length > 0 ? (wins.length / closed.length * 100) : null;
  const avgWin  = wins.length > 0 ? wins.reduce((s, t) => s + (calcPnl(t) ?? 0), 0) / wins.length : null;
  const avgLoss = losses.length > 0 ? losses.reduce((s, t) => s + (calcPnl(t) ?? 0), 0) / losses.length : null;
  const profitFactor = losses.length > 0 && avgLoss != null && avgWin != null && avgLoss !== 0
    ? Math.abs(avgWin / avgLoss) : null;

  const displayTrades = trades
    .filter(t => filterOpen === null || (filterOpen ? t.exit_price == null : t.exit_price != null))
    .sort((a, b) => {
      if (sortBy === 'date') return b.entry_date.localeCompare(a.entry_date);
      if (sortBy === 'symbol') return a.symbol.localeCompare(b.symbol);
      const pa = calcPnl(a) ?? -Infinity;
      const pb = calcPnl(b) ?? -Infinity;
      return pb - pa;
    });

  const statCard = (label: string, value: string, color?: string, sub?: string) => (
    <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '12px 16px', minWidth: 110 }}>
      <div style={{ fontSize: 20, fontWeight: 700, color: color ?? '#e2e8f0' }}>{value}</div>
      <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>{label}</div>
      {sub && <div style={{ fontSize: 10, color: '#475569', marginTop: 1 }}>{sub}</div>}
    </div>
  );

  return (
    <div style={{ padding: '24px 0' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, color: '#e2e8f0', marginBottom: 4 }}>Trade Journal</h1>
          <p style={{ fontSize: 13, color: '#64748b' }}>Track every trade — review what works, cut what doesn&apos;t.</p>
        </div>
        <button onClick={openAdd}
          style={{ padding: '8px 18px', borderRadius: 8, background: '#6366f1', color: '#fff', fontSize: 13, fontWeight: 600, border: 'none', cursor: 'pointer' }}>
          + Log Trade
        </button>
      </div>

      {/* Stats */}
      {closed.length > 0 && (
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 24 }}>
          {statCard('Total P&L', `${totalPnl >= 0 ? '+' : '-'}$${Math.abs(totalPnl).toFixed(2)}`, totalPnl >= 0 ? '#4ade80' : '#f87171', `${closed.length} closed trades`)}
          {statCard('Win Rate', winRate != null ? `${winRate.toFixed(0)}%` : '—', winRate != null && winRate >= 50 ? '#4ade80' : '#f87171', `${wins.length}W / ${losses.length}L`)}
          {statCard('Avg Win', avgWin != null ? `+$${avgWin.toFixed(2)}` : '—', '#4ade80')}
          {statCard('Avg Loss', avgLoss != null ? `-$${Math.abs(avgLoss).toFixed(2)}` : '—', '#f87171')}
          {statCard('Profit Factor', profitFactor != null ? profitFactor.toFixed(2) : '—', profitFactor != null && profitFactor >= 1.5 ? '#4ade80' : '#facc15', 'reward / risk ratio')}
          {statCard('Open Positions', String(open.length), '#818cf8')}
        </div>
      )}

      {/* Filters */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 14, alignItems: 'center' }}>
        {([null, true, false] as (boolean | null)[]).map((v, i) => {
          const label = v === null ? 'All' : v ? 'Open' : 'Closed';
          const active = filterOpen === v;
          return (
            <button key={i} onClick={() => setFilterOpen(v)}
              style={{ padding: '4px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer', border: '1px solid',
                borderColor: active ? '#6366f1' : '#1e293b',
                background: active ? 'rgba(99,102,241,0.15)' : 'transparent',
                color: active ? '#818cf8' : '#64748b' }}>
              {label}
            </button>
          );
        })}
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 11, color: '#475569' }}>Sort:</span>
        {([['date', 'Date'], ['symbol', 'Symbol'], ['pnl', 'P&L']] as const).map(([k, label]) => (
          <button key={k} onClick={() => setSortBy(k)}
            style={{ padding: '3px 8px', borderRadius: 4, fontSize: 11, cursor: 'pointer', border: '1px solid',
              borderColor: sortBy === k ? '#475569' : '#1e293b',
              background: sortBy === k ? 'rgba(71,85,105,0.2)' : 'transparent',
              color: sortBy === k ? '#94a3b8' : '#475569' }}>
            {label}
          </button>
        ))}
      </div>

      {/* Trade table */}
      {isLoading ? (
        <div style={{ textAlign: 'center', padding: '60px 0', color: '#475569' }}>Loading…</div>
      ) : displayTrades.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '60px 0', color: '#475569' }}>
          <div style={{ fontSize: 40, marginBottom: 10 }}>📓</div>
          <div style={{ fontWeight: 600, color: '#64748b', marginBottom: 4 }}>No trades yet</div>
          <div style={{ fontSize: 12 }}>Click &quot;Log Trade&quot; to record your first entry.</div>
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid #1e293b' }}>
                {['Symbol', 'Action', 'Shares', 'Entry', 'Exit', 'P&L', 'R:R', 'Strategy', 'Notes', ''].map(h => (
                  <th key={h} style={{ padding: '6px 10px', textAlign: 'left', color: '#64748b', fontWeight: 500 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {displayTrades.map(t => {
                const pnl = calcPnl(t);
                const pnlPct = calcPnlPct(t);
                const rr = calcRR(t);
                const isOpen = t.exit_price == null;
                return (
                  <tr key={t.id} style={{ borderBottom: '1px solid #0f172a' }}>
                    <td style={{ padding: '8px 10px' }}>
                      <Link href={`/stock/${t.symbol}`} style={{ color: '#818cf8', fontWeight: 700 }}>{t.symbol}</Link>
                      <div style={{ fontSize: 10, color: '#475569' }}>{t.entry_date}</div>
                    </td>
                    <td style={{ padding: '8px 10px' }}>
                      <span style={{ padding: '2px 6px', borderRadius: 4, fontSize: 11, fontWeight: 700,
                        background: t.action === 'BUY' ? 'rgba(22,101,52,0.3)' : 'rgba(153,27,27,0.3)',
                        color: t.action === 'BUY' ? '#4ade80' : '#f87171' }}>
                        {t.action === 'BUY' ? 'LONG' : 'SHORT'}
                      </span>
                      {t.signal_confidence != null && (
                        <div style={{ fontSize: 10, color: '#475569', marginTop: 2 }}>{t.signal_confidence}% conf.</div>
                      )}
                    </td>
                    <td style={{ padding: '8px 10px', color: '#94a3b8' }}>{t.shares}</td>
                    <td style={{ padding: '8px 10px', color: '#94a3b8' }}>
                      ${t.entry_price.toFixed(2)}
                      {t.stop_loss != null && <div style={{ fontSize: 10, color: '#f87171' }}>SL ${t.stop_loss.toFixed(2)}</div>}
                    </td>
                    <td style={{ padding: '8px 10px' }}>
                      {isOpen ? (
                        <span style={{ color: '#facc15', fontSize: 11 }}>Open</span>
                      ) : (
                        <>
                          <span style={{ color: '#94a3b8' }}>${t.exit_price!.toFixed(2)}</span>
                          {t.exit_date && <div style={{ fontSize: 10, color: '#475569' }}>{t.exit_date}</div>}
                        </>
                      )}
                    </td>
                    <td style={{ padding: '8px 10px' }}>
                      {pnl == null ? <span style={{ color: '#475569' }}>—</span> : (
                        <>
                          <span style={{ fontWeight: 700, color: pnl >= 0 ? '#4ade80' : '#f87171' }}>
                            {pnl >= 0 ? '+' : '-'}${Math.abs(pnl).toFixed(2)}
                          </span>
                          {pnlPct != null && (
                            <div style={{ fontSize: 10, color: pnlPct >= 0 ? '#22c55e' : '#ef4444' }}>
                              {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(1)}%
                            </div>
                          )}
                        </>
                      )}
                    </td>
                    <td style={{ padding: '8px 10px', color: rr != null && rr >= 2 ? '#4ade80' : '#94a3b8' }}>
                      {rr != null ? `${rr.toFixed(1)}:1` : '—'}
                    </td>
                    <td style={{ padding: '8px 10px', color: '#64748b', maxWidth: 100 }}>{t.strategy || '—'}</td>
                    <td style={{ padding: '8px 10px', color: '#475569', maxWidth: 150, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {t.notes || '—'}
                    </td>
                    <td style={{ padding: '8px 10px' }}>
                      <div style={{ display: 'flex', gap: 6 }}>
                        <button onClick={() => openEdit(t)}
                          style={{ padding: '3px 8px', borderRadius: 4, fontSize: 11, cursor: 'pointer', border: '1px solid #1e293b', background: 'transparent', color: '#64748b' }}>
                          Edit
                        </button>
                        {deleteConfirm === t.id ? (
                          <>
                            <button onClick={() => handleDelete(t.id)}
                              style={{ padding: '3px 8px', borderRadius: 4, fontSize: 11, cursor: 'pointer', border: '1px solid #991b1b', background: 'rgba(153,27,27,0.2)', color: '#f87171' }}>
                              Confirm
                            </button>
                            <button onClick={() => setDeleteConfirm(null)}
                              style={{ padding: '3px 8px', borderRadius: 4, fontSize: 11, cursor: 'pointer', border: '1px solid #1e293b', background: 'transparent', color: '#64748b' }}>
                              Cancel
                            </button>
                          </>
                        ) : (
                          <button onClick={() => setDeleteConfirm(t.id)}
                            style={{ padding: '3px 8px', borderRadius: 4, fontSize: 11, cursor: 'pointer', border: '1px solid #1e293b', background: 'transparent', color: '#475569' }}>
                            ✕
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Add/Edit modal */}
      {showForm && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
          <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 12, padding: 24, width: '100%', maxWidth: 560, maxHeight: '90vh', overflowY: 'auto' }}>
            <h2 style={{ fontSize: 16, fontWeight: 700, color: '#e2e8f0', marginBottom: 20 }}>
              {editingId ? 'Edit Trade' : 'Log New Trade'}
            </h2>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div>
                <label style={{ fontSize: 11, color: '#64748b', display: 'block', marginBottom: 4 }}>Symbol *</label>
                <input value={form.symbol} onChange={e => setForm(f => ({ ...f, symbol: e.target.value.toUpperCase() }))}
                  placeholder="e.g. AAPL"
                  style={{ width: '100%', padding: '7px 10px', borderRadius: 6, border: '1px solid #1e293b', background: '#020617', color: '#e2e8f0', fontSize: 13 }} />
              </div>

              <div>
                <label style={{ fontSize: 11, color: '#64748b', display: 'block', marginBottom: 4 }}>Direction *</label>
                <div style={{ display: 'flex', gap: 6 }}>
                  {(['BUY', 'SELL_SHORT'] as const).map(v => (
                    <button key={v} onClick={() => setForm(f => ({ ...f, action: v }))}
                      style={{ flex: 1, padding: '7px 0', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer', border: '1px solid',
                        borderColor: form.action === v ? (v === 'BUY' ? '#166534' : '#991b1b') : '#1e293b',
                        background: form.action === v ? (v === 'BUY' ? 'rgba(22,101,52,0.3)' : 'rgba(153,27,27,0.3)') : 'transparent',
                        color: form.action === v ? (v === 'BUY' ? '#4ade80' : '#f87171') : '#64748b' }}>
                      {v === 'BUY' ? 'Long' : 'Short'}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label style={{ fontSize: 11, color: '#64748b', display: 'block', marginBottom: 4 }}>Shares *</label>
                <input type="number" value={form.shares || ''} onChange={e => setForm(f => ({ ...f, shares: Number(e.target.value) }))}
                  style={{ width: '100%', padding: '7px 10px', borderRadius: 6, border: '1px solid #1e293b', background: '#020617', color: '#e2e8f0', fontSize: 13 }} />
              </div>

              <div>
                <label style={{ fontSize: 11, color: '#64748b', display: 'block', marginBottom: 4 }}>Entry Price *</label>
                <input type="number" step="0.01" value={form.entry_price || ''} onChange={e => setForm(f => ({ ...f, entry_price: Number(e.target.value) }))}
                  style={{ width: '100%', padding: '7px 10px', borderRadius: 6, border: '1px solid #1e293b', background: '#020617', color: '#e2e8f0', fontSize: 13 }} />
              </div>

              <div>
                <label style={{ fontSize: 11, color: '#64748b', display: 'block', marginBottom: 4 }}>Exit Price <span style={{ color: '#475569' }}>(leave blank if open)</span></label>
                <input type="number" step="0.01" value={form.exit_price ?? ''} onChange={e => setForm(f => ({ ...f, exit_price: e.target.value ? Number(e.target.value) : null }))}
                  style={{ width: '100%', padding: '7px 10px', borderRadius: 6, border: '1px solid #1e293b', background: '#020617', color: '#e2e8f0', fontSize: 13 }} />
              </div>

              <div>
                <label style={{ fontSize: 11, color: '#64748b', display: 'block', marginBottom: 4 }}>Entry Date *</label>
                <input type="date" value={form.entry_date} onChange={e => setForm(f => ({ ...f, entry_date: e.target.value }))}
                  style={{ width: '100%', padding: '7px 10px', borderRadius: 6, border: '1px solid #1e293b', background: '#020617', color: '#e2e8f0', fontSize: 13 }} />
              </div>

              <div>
                <label style={{ fontSize: 11, color: '#64748b', display: 'block', marginBottom: 4 }}>Exit Date</label>
                <input type="date" value={form.exit_date ?? ''} onChange={e => setForm(f => ({ ...f, exit_date: e.target.value || null }))}
                  style={{ width: '100%', padding: '7px 10px', borderRadius: 6, border: '1px solid #1e293b', background: '#020617', color: '#e2e8f0', fontSize: 13 }} />
              </div>

              <div>
                <label style={{ fontSize: 11, color: '#64748b', display: 'block', marginBottom: 4 }}>Stop Loss</label>
                <input type="number" step="0.01" value={form.stop_loss ?? ''} onChange={e => setForm(f => ({ ...f, stop_loss: e.target.value ? Number(e.target.value) : null }))}
                  style={{ width: '100%', padding: '7px 10px', borderRadius: 6, border: '1px solid #1e293b', background: '#020617', color: '#e2e8f0', fontSize: 13 }} />
              </div>

              <div>
                <label style={{ fontSize: 11, color: '#64748b', display: 'block', marginBottom: 4 }}>Take Profit</label>
                <input type="number" step="0.01" value={form.take_profit ?? ''} onChange={e => setForm(f => ({ ...f, take_profit: e.target.value ? Number(e.target.value) : null }))}
                  style={{ width: '100%', padding: '7px 10px', borderRadius: 6, border: '1px solid #1e293b', background: '#020617', color: '#e2e8f0', fontSize: 13 }} />
              </div>

              <div>
                <label style={{ fontSize: 11, color: '#64748b', display: 'block', marginBottom: 4 }}>AI Signal Confidence %</label>
                <input type="number" min="0" max="100" value={form.signal_confidence ?? ''} onChange={e => setForm(f => ({ ...f, signal_confidence: e.target.value ? Number(e.target.value) : null }))}
                  style={{ width: '100%', padding: '7px 10px', borderRadius: 6, border: '1px solid #1e293b', background: '#020617', color: '#e2e8f0', fontSize: 13 }} />
              </div>

              <div>
                <label style={{ fontSize: 11, color: '#64748b', display: 'block', marginBottom: 4 }}>Strategy / Setup</label>
                <input value={form.strategy ?? ''} onChange={e => setForm(f => ({ ...f, strategy: e.target.value || null }))}
                  placeholder="e.g. Bull flag breakout"
                  style={{ width: '100%', padding: '7px 10px', borderRadius: 6, border: '1px solid #1e293b', background: '#020617', color: '#e2e8f0', fontSize: 13 }} />
              </div>
            </div>

            <div style={{ marginTop: 12 }}>
              <label style={{ fontSize: 11, color: '#64748b', display: 'block', marginBottom: 4 }}>Notes / Rationale</label>
              <textarea value={form.notes ?? ''} onChange={e => setForm(f => ({ ...f, notes: e.target.value || null }))}
                rows={3} placeholder="Why you entered, what you observed…"
                style={{ width: '100%', padding: '7px 10px', borderRadius: 6, border: '1px solid #1e293b', background: '#020617', color: '#e2e8f0', fontSize: 13, resize: 'vertical' }} />
            </div>

            {form.stop_loss != null && form.take_profit != null && form.entry_price > 0 && (
              <div style={{ marginTop: 12, padding: '8px 12px', borderRadius: 6, background: 'rgba(99,102,241,0.1)', border: '1px solid rgba(99,102,241,0.2)', fontSize: 12, color: '#818cf8' }}>
                Risk: ${Math.abs(form.entry_price - form.stop_loss).toFixed(2)} per share ·
                Reward: ${Math.abs(form.take_profit - form.entry_price).toFixed(2)} per share ·
                R:R = {(Math.abs(form.take_profit - form.entry_price) / Math.abs(form.entry_price - form.stop_loss)).toFixed(1)}:1
              </div>
            )}

            <div style={{ display: 'flex', gap: 10, marginTop: 20 }}>
              <button onClick={handleSave} disabled={saving || !form.symbol || form.entry_price <= 0 || form.shares <= 0}
                style={{ flex: 1, padding: '9px 0', borderRadius: 8, background: '#6366f1', color: '#fff', fontSize: 13, fontWeight: 600, border: 'none', cursor: 'pointer', opacity: (saving || !form.symbol || form.entry_price <= 0 || form.shares <= 0) ? 0.5 : 1 }}>
                {saving ? 'Saving…' : editingId ? 'Save Changes' : 'Log Trade'}
              </button>
              <button onClick={() => setShowForm(false)}
                style={{ padding: '9px 20px', borderRadius: 8, background: 'transparent', border: '1px solid #1e293b', color: '#64748b', fontSize: 13, cursor: 'pointer' }}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
