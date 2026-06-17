import { useState } from 'react';
import Link from 'next/link';
import useSWR from 'swr';
import { api, type PaperDecisionItem, type JournalTrade, type JournalTradeIn } from '@/lib/api';

// ─── Exit reason badges ───────────────────────────────────────────────────────

const EXIT_META: Record<string, { label: string; bg: string; color: string }> = {
  stop_hit:           { label: 'Stop Hit',    bg: 'rgba(239,68,68,0.15)',   color: '#f87171' },
  target_reached:     { label: 'Target',      bg: 'rgba(34,197,94,0.15)',   color: '#4ade80' },
  signal_exit:        { label: 'Signal Exit', bg: 'rgba(251,191,36,0.15)',  color: '#fbbf24' },
  time_stop:          { label: 'Time Stop',   bg: 'rgba(100,116,139,0.2)',  color: '#94a3b8' },
  hold_stall_timeout: { label: 'Stall',       bg: 'rgba(249,115,22,0.15)', color: '#fb923c' },
  momentum_exit:      { label: 'Mom. Exit',   bg: 'rgba(167,139,250,0.15)', color: '#a78bfa' },
  manual_reset:       { label: 'Reset',       bg: 'rgba(71,85,105,0.2)',   color: '#64748b' },
};

function ExitBadge({ reason }: { reason: string | null }) {
  if (!reason) return <span style={{ color: '#475569' }}>Open</span>;
  const m = EXIT_META[reason] ?? { label: reason, bg: 'rgba(99,102,241,0.15)', color: '#818cf8' };
  return (
    <span style={{ padding: '2px 7px', borderRadius: 4, fontSize: 11, fontWeight: 600,
      background: m.bg, color: m.color }}>{m.label}</span>
  );
}

function ScoreDots({ score }: { score: number | null }) {
  if (score == null) return <span style={{ color: '#475569' }}>—</span>;
  return (
    <span>
      {[1,2,3,4,5].map(i => (
        <span key={i} style={{ color: i <= score ? '#f59e0b' : '#1e293b', fontSize: 13 }}>●</span>
      ))}
    </span>
  );
}

const STYLE_COLOR: Record<string, string> = {
  GROWTH: '#22c55e', SWING: '#3b82f6', LONG: '#a78bfa', SHORT: '#f87171',
};

function fmtTime(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) +
    ' ' + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' });
}

// ─── Key entry indicators to surface from reasons dict ────────────────────────

const REASON_LABELS: Record<string, string> = {
  market_regime: 'Regime', rs_score: 'RS Score', ml_probability: 'ML Prob',
  rsi: 'RSI', ma_trend: 'MA Trend', sr_context: 'S/R', vol_spike: 'Vol Spike',
  macd_hist: 'MACD Hist', days_to_earnings: 'DTE', bb_position: 'BB Pos',
};

function EntryReasonsDetail({ reasons }: { reasons: Record<string, unknown> }) {
  const keys = Object.keys(REASON_LABELS).filter(k => reasons[k] != null);
  if (!keys.length) return null;
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 14px', marginTop: 6 }}>
      {keys.map(k => {
        const v = reasons[k];
        const display = typeof v === 'number'
          ? (k === 'ml_probability' ? `${(Number(v)*100).toFixed(0)}%` : k === 'rs_score' ? Number(v).toFixed(1) : String(Math.round(Number(v) * 100) / 100))
          : String(v);
        return (
          <span key={k} style={{ fontSize: 11, color: '#64748b' }}>
            <span style={{ color: '#475569' }}>{REASON_LABELS[k]}: </span>
            <span style={{ color: '#94a3b8' }}>{display}</span>
          </span>
        );
      })}
    </div>
  );
}

function ExitReasonsDetail({ reasons }: { reasons: Record<string, unknown> }) {
  const entries = Object.entries(reasons).filter(([, v]) => v != null && v !== '');
  if (!entries.length) return null;
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 14px', marginTop: 4 }}>
      {entries.map(([k, v]) => (
        <span key={k} style={{ fontSize: 11, color: '#64748b' }}>
          <span style={{ color: '#475569' }}>{k.replace(/_/g, ' ')}: </span>
          <span style={{ color: '#94a3b8' }}>{typeof v === 'number' ? String(Math.round(Number(v) * 100) / 100) : String(v)}</span>
        </span>
      ))}
    </div>
  );
}

// ─── AI Trades tab ────────────────────────────────────────────────────────────

const DAYS_OPTIONS = [30, 60, 90, 180] as const;

function AITradesTab() {
  const [daysBack, setDaysBack] = useState<30 | 60 | 90 | 180>(90);
  const [filterStage, setFilterStage] = useState<'all' | 'open' | 'closed'>('all');
  const [filterExit, setFilterExit] = useState<string | null>(null);
  const [filterStyle, setFilterStyle] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState<'date' | 'pnl' | 'symbol'>('date');
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [page, setPage] = useState(1);

  const { data, isLoading } = useSWR(
    ['paper-decisions', daysBack, page],
    () => api.paperDecisions({ days_back: daysBack, limit: 50, page }),
    { revalidateOnFocus: false },
  );

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const pages = data?.pages ?? 1;

  const exitReasons = [...new Set(items.map(i => i.exit_reason).filter(Boolean))];
  const styles = [...new Set(items.map(i => (i.entry_reasons as any)?.horizon || '').filter(Boolean))];

  const filtered = items
    .filter(i => filterStage === 'all' || (filterStage === 'open' ? i.stage === 'open' : i.stage === 'closed'))
    .filter(i => !filterExit || i.exit_reason === filterExit)
    .filter(i => !filterStyle || ((i.entry_reasons as any)?.horizon || '') === filterStyle)
    .sort((a, b) => {
      if (sortBy === 'date') return (b.entry_time ?? '').localeCompare(a.entry_time ?? '');
      if (sortBy === 'symbol') return a.symbol.localeCompare(b.symbol);
      return (b.pnl ?? -Infinity) - (a.pnl ?? -Infinity);
    });

  const closed = items.filter(i => i.stage === 'closed');
  const wins = closed.filter(i => (i.pnl ?? 0) > 0);
  const totalPnl = closed.reduce((s, i) => s + (i.pnl ?? 0), 0);
  const winRate = closed.length ? (wins.length / closed.length * 100) : null;
  const avgHold = closed.length ? (closed.reduce((s, i) => s + i.hold_days, 0) / closed.length) : null;

  function toggleExpand(id: number) {
    setExpanded(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  return (
    <div>
      {/* Stats bar */}
      {closed.length > 0 && (
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 20 }}>
          {[
            ['Total P&L', `${totalPnl >= 0 ? '+' : ''}$${Math.abs(totalPnl).toFixed(0)}`, totalPnl >= 0 ? '#4ade80' : '#f87171', `${closed.length} closed`],
            ['Win Rate', winRate != null ? `${winRate.toFixed(0)}%` : '—', winRate != null && winRate >= 50 ? '#4ade80' : '#f87171', `${wins.length}W / ${closed.length - wins.length}L`],
            ['Avg Hold', avgHold != null ? `${avgHold.toFixed(1)}d` : '—', '#94a3b8', 'trading days'],
            ['Open', String(items.filter(i => i.stage === 'open').length), '#818cf8', 'positions'],
          ].map(([label, value, color, sub]) => (
            <div key={label as string} style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '10px 14px', minWidth: 100 }}>
              <div style={{ fontSize: 18, fontWeight: 700, color: color as string }}>{value}</div>
              <div style={{ fontSize: 10, color: '#64748b', marginTop: 1 }}>{label}</div>
              <div style={{ fontSize: 10, color: '#334155', marginTop: 1 }}>{sub}</div>
            </div>
          ))}
        </div>
      )}

      {/* Filters */}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12, alignItems: 'center' }}>
        {(['all', 'open', 'closed'] as const).map(v => (
          <button key={v} onClick={() => setFilterStage(v)}
            style={{ padding: '3px 10px', borderRadius: 6, fontSize: 12, cursor: 'pointer', border: '1px solid',
              borderColor: filterStage === v ? '#6366f1' : '#1e293b',
              background: filterStage === v ? 'rgba(99,102,241,0.15)' : 'transparent',
              color: filterStage === v ? '#818cf8' : '#64748b' }}>
            {v.charAt(0).toUpperCase() + v.slice(1)}
          </button>
        ))}

        {exitReasons.map(r => (
          <button key={r} onClick={() => setFilterExit(filterExit === r ? null : r)}
            style={{ padding: '3px 10px', borderRadius: 6, fontSize: 11, cursor: 'pointer', border: '1px solid',
              borderColor: filterExit === r ? '#334155' : '#1e293b',
              background: filterExit === r ? 'rgba(51,65,85,0.4)' : 'transparent',
              color: filterExit === r ? '#94a3b8' : '#475569' }}>
            {EXIT_META[r!]?.label ?? r}
          </button>
        ))}

        <div style={{ flex: 1 }} />

        <span style={{ fontSize: 11, color: '#475569' }}>Days:</span>
        {DAYS_OPTIONS.map(d => (
          <button key={d} onClick={() => { setDaysBack(d); setPage(1); }}
            style={{ padding: '3px 8px', borderRadius: 4, fontSize: 11, cursor: 'pointer', border: '1px solid',
              borderColor: daysBack === d ? '#475569' : '#1e293b',
              background: daysBack === d ? 'rgba(71,85,105,0.2)' : 'transparent',
              color: daysBack === d ? '#94a3b8' : '#475569' }}>
            {d}d
          </button>
        ))}
        <span style={{ fontSize: 11, color: '#475569', marginLeft: 8 }}>Sort:</span>
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

      {isLoading ? (
        <div style={{ textAlign: 'center', padding: '40px 0', color: '#475569' }}>Loading…</div>
      ) : filtered.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '40px 0', color: '#475569' }}>
          No trades in the last {daysBack} days.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {filtered.map(item => {
            const isOpen = item.stage === 'open';
            const isExpanded = expanded.has(item.id);
            const pnlColor = (item.pnl ?? 0) >= 0 ? '#4ade80' : '#f87171';
            const horizonStyle = (item.entry_reasons as any)?.horizon as string | undefined;
            const styleColor = horizonStyle ? STYLE_COLOR[horizonStyle] ?? '#94a3b8' : '#94a3b8';
            const scaleIn = (item.decision_notes || []).some(n => n.includes('SCALE_IN'));
            const partials = (item.decision_notes || []).filter(n =>
              n.startsWith('Scale-out') || n.startsWith('Partial'),
            );

            return (
              <div key={item.id} style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, overflow: 'hidden' }}>
                {/* Main row */}
                <div
                  onClick={() => toggleExpand(item.id)}
                  style={{ display: 'grid', gridTemplateColumns: '130px 60px 80px 110px 110px 90px 60px 70px 50px', gap: 8,
                    alignItems: 'center', padding: '10px 14px', cursor: 'pointer', userSelect: 'none' }}>
                  <div>
                    <Link href={`/stock/${item.symbol}`} onClick={e => e.stopPropagation()}
                      style={{ color: '#818cf8', fontWeight: 700, fontSize: 13 }}>{item.symbol}</Link>
                    {horizonStyle && (
                      <span style={{ marginLeft: 6, fontSize: 10, color: styleColor, fontWeight: 600 }}>{horizonStyle}</span>
                    )}
                    {scaleIn && (
                      <span style={{ marginLeft: 4, fontSize: 9, color: '#fb923c', fontWeight: 600 }}>+SI</span>
                    )}
                  </div>
                  <div><ScoreDots score={item.entry_score} /></div>
                  <div style={{ fontSize: 11, color: '#64748b' }}>
                    <div>{fmtDate(item.entry_time)}</div>
                    {!isOpen && item.exit_time && <div style={{ color: '#475569', fontSize: 10 }}>{fmtDate(item.exit_time)}</div>}
                  </div>
                  <div style={{ fontSize: 12, color: '#94a3b8' }}>
                    <span style={{ color: '#64748b' }}>In: </span>${item.entry_price.toFixed(2)}
                    {!isOpen && item.exit_price != null && (
                      <div><span style={{ color: '#64748b' }}>Out: </span>${item.exit_price.toFixed(2)}</div>
                    )}
                  </div>
                  <div>
                    {isOpen ? (
                      <span style={{ fontSize: 11, color: '#facc15' }}>Open</span>
                    ) : (
                      item.pnl != null ? (
                        <>
                          <span style={{ fontSize: 13, fontWeight: 700, color: pnlColor }}>
                            {item.pnl >= 0 ? '+' : ''}${Math.abs(item.pnl).toFixed(0)}
                          </span>
                          {item.pct_return != null && (
                            <span style={{ fontSize: 11, color: pnlColor, marginLeft: 4 }}>
                              ({item.pct_return >= 0 ? '+' : ''}{item.pct_return.toFixed(1)}%)
                            </span>
                          )}
                        </>
                      ) : '—'
                    )}
                  </div>
                  <div><ExitBadge reason={item.exit_reason} /></div>
                  <div style={{ fontSize: 11, color: '#64748b' }}>
                    {item.hold_days > 0 ? `${item.hold_days}d` : isOpen ? 'holding' : '—'}
                  </div>
                  <div style={{ fontSize: 11, color: '#64748b' }}>
                    {item.confidence_at_entry != null ? `${item.confidence_at_entry.toFixed(0)}%` : '—'}
                  </div>
                  <div style={{ fontSize: 12, color: '#334155' }}>{isExpanded ? '▲' : '▼'}</div>
                </div>

                {/* Expanded detail */}
                {isExpanded && (
                  <div style={{ padding: '12px 14px 14px', borderTop: '1px solid #1e293b', background: '#020617' }}>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                      {/* Left: entry context */}
                      <div>
                        <div style={{ fontSize: 11, fontWeight: 600, color: '#475569', marginBottom: 6 }}>ENTRY CONTEXT</div>
                        <div style={{ fontSize: 11, color: '#64748b', marginBottom: 4 }}>
                          {item.market_regime_at_entry && (
                            <span style={{ marginRight: 10 }}>Regime: <span style={{ color: '#94a3b8' }}>{item.market_regime_at_entry}</span></span>
                          )}
                          {item.rr_ratio_at_entry != null && (
                            <span style={{ marginRight: 10 }}>R:R: <span style={{ color: '#94a3b8' }}>{item.rr_ratio_at_entry.toFixed(1)}:1</span></span>
                          )}
                          {item.kscore_at_entry != null && (
                            <span>K-Score: <span style={{ color: '#94a3b8' }}>{item.kscore_at_entry.toFixed(0)}</span></span>
                          )}
                        </div>
                        <EntryReasonsDetail reasons={item.entry_reasons} />
                      </div>

                      {/* Right: stop/target + partial notes */}
                      <div>
                        <div style={{ fontSize: 11, fontWeight: 600, color: '#475569', marginBottom: 6 }}>TRADE PLAN</div>
                        <div style={{ fontSize: 11, color: '#64748b', marginBottom: 6 }}>
                          <span style={{ marginRight: 10 }}>Stop: <span style={{ color: '#f87171' }}>${item.stop_loss.toFixed(2)}</span></span>
                          {item.take_profit != null && (
                            <span>Target: <span style={{ color: '#4ade80' }}>${item.take_profit.toFixed(2)}</span></span>
                          )}
                          <span style={{ marginLeft: 10 }}>{item.shares.toFixed(2)}sh</span>
                        </div>
                        {partials.length > 0 && (
                          <div style={{ fontSize: 10, color: '#fb923c', marginBottom: 4 }}>
                            {partials.map((n, i) => <div key={i}>{n}</div>)}
                          </div>
                        )}
                      </div>
                    </div>

                    {/* AI entry decision notes */}
                    {item.decision_notes.length > 0 && (
                      <div style={{ marginTop: 10 }}>
                        <div style={{ fontSize: 11, fontWeight: 600, color: '#475569', marginBottom: 4 }}>AI ENTRY NOTES</div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                          {item.decision_notes
                            .filter(n => !n.startsWith('PARTIAL') && !n.startsWith('Scale-out') && n !== 'SCALE_IN' && !n.startsWith('Scale-in:'))
                            .map((note, i) => (
                              <div key={i} style={{ fontSize: 11, color: '#64748b', padding: '2px 0', borderLeft: '2px solid #1e293b', paddingLeft: 8 }}>
                                {note}
                              </div>
                            ))}
                        </div>
                      </div>
                    )}

                    {/* Exit reasoning */}
                    {!isOpen && Object.keys(item.exit_reasons).length > 0 && (
                      <div style={{ marginTop: 10 }}>
                        <div style={{ fontSize: 11, fontWeight: 600, color: '#475569', marginBottom: 4 }}>EXIT REASONING</div>
                        <ExitReasonsDetail reasons={item.exit_reasons} />
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Pagination */}
      {pages > 1 && (
        <div style={{ display: 'flex', justifyContent: 'center', gap: 8, marginTop: 16 }}>
          <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}
            style={{ padding: '4px 12px', borderRadius: 6, fontSize: 12, cursor: page === 1 ? 'not-allowed' : 'pointer',
              border: '1px solid #1e293b', background: 'transparent', color: page === 1 ? '#334155' : '#64748b' }}>
            ← Prev
          </button>
          <span style={{ fontSize: 12, color: '#475569', display: 'flex', alignItems: 'center' }}>
            {page} / {pages} ({total} trades)
          </span>
          <button onClick={() => setPage(p => Math.min(pages, p + 1))} disabled={page === pages}
            style={{ padding: '4px 12px', borderRadius: 6, fontSize: 12, cursor: page === pages ? 'not-allowed' : 'pointer',
              border: '1px solid #1e293b', background: 'transparent', color: page === pages ? '#334155' : '#64748b' }}>
            Next →
          </button>
        </div>
      )}
    </div>
  );
}

// ─── Manual Log tab (unchanged) ───────────────────────────────────────────────

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

const BLANK: JournalTradeIn = {
  symbol: '', action: 'BUY', shares: 0, entry_price: 0, exit_price: null,
  entry_date: new Date().toISOString().slice(0, 10), exit_date: null,
  stop_loss: null, take_profit: null, notes: null, strategy: null, signal_confidence: null,
};

function ManualLogTab() {
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
      if (editingId != null) { await api.updateJournalTrade(editingId, form); }
      else { await api.createJournalTrade(form); }
      await mutate();
      setShowForm(false);
    } finally { setSaving(false); }
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

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <p style={{ fontSize: 13, color: '#64748b' }}>Manually log your own trades outside the AI engine.</p>
        <button onClick={openAdd}
          style={{ padding: '7px 16px', borderRadius: 8, background: '#6366f1', color: '#fff', fontSize: 13, fontWeight: 600, border: 'none', cursor: 'pointer' }}>
          + Log Trade
        </button>
      </div>

      {closed.length > 0 && (
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 20 }}>
          {[
            ['Total P&L', `${totalPnl >= 0 ? '+' : '-'}$${Math.abs(totalPnl).toFixed(2)}`, totalPnl >= 0 ? '#4ade80' : '#f87171', `${closed.length} closed`],
            ['Win Rate', winRate != null ? `${winRate.toFixed(0)}%` : '—', winRate != null && winRate >= 50 ? '#4ade80' : '#f87171', `${wins.length}W / ${losses.length}L`],
            ['Avg Win', avgWin != null ? `+$${avgWin.toFixed(2)}` : '—', '#4ade80', ''],
            ['Avg Loss', avgLoss != null ? `-$${Math.abs(avgLoss).toFixed(2)}` : '—', '#f87171', ''],
            ['Profit Factor', profitFactor != null ? profitFactor.toFixed(2) : '—', profitFactor != null && profitFactor >= 1.5 ? '#4ade80' : '#facc15', 'reward/risk'],
            ['Open', String(open.length), '#818cf8', 'positions'],
          ].map(([label, value, color, sub]) => (
            <div key={label as string} style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '10px 14px', minWidth: 100 }}>
              <div style={{ fontSize: 18, fontWeight: 700, color: color as string }}>{value}</div>
              <div style={{ fontSize: 10, color: '#64748b', marginTop: 1 }}>{label}</div>
              {sub && <div style={{ fontSize: 10, color: '#334155' }}>{sub}</div>}
            </div>
          ))}
        </div>
      )}

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

      {isLoading ? (
        <div style={{ textAlign: 'center', padding: '40px 0', color: '#475569' }}>Loading…</div>
      ) : displayTrades.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '40px 0', color: '#475569' }}>
          <div style={{ fontSize: 36, marginBottom: 8 }}>📓</div>
          <div style={{ color: '#64748b' }}>No trades logged. Click &quot;Log Trade&quot; to start.</div>
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid #1e293b' }}>
                {['Symbol', 'Dir', 'Shares', 'Entry', 'Exit', 'P&L', 'R:R', 'Strategy', 'Notes', ''].map(h => (
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
                        {t.action === 'BUY' ? 'L' : 'S'}
                      </span>
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
                        <span style={{ color: '#94a3b8' }}>${t.exit_price!.toFixed(2)}</span>
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
                    <td style={{ padding: '8px 10px', color: '#475569', maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {t.notes || '—'}
                    </td>
                    <td style={{ padding: '8px 10px' }}>
                      <div style={{ display: 'flex', gap: 4 }}>
                        <button onClick={() => openEdit(t)}
                          style={{ padding: '3px 7px', borderRadius: 4, fontSize: 11, cursor: 'pointer', border: '1px solid #1e293b', background: 'transparent', color: '#64748b' }}>
                          Edit
                        </button>
                        {deleteConfirm === t.id ? (
                          <>
                            <button onClick={() => handleDelete(t.id)}
                              style={{ padding: '3px 7px', borderRadius: 4, fontSize: 11, cursor: 'pointer', border: '1px solid #991b1b', background: 'rgba(153,27,27,0.2)', color: '#f87171' }}>
                              Yes
                            </button>
                            <button onClick={() => setDeleteConfirm(null)}
                              style={{ padding: '3px 7px', borderRadius: 4, fontSize: 11, cursor: 'pointer', border: '1px solid #1e293b', background: 'transparent', color: '#64748b' }}>
                              No
                            </button>
                          </>
                        ) : (
                          <button onClick={() => setDeleteConfirm(t.id)}
                            style={{ padding: '3px 7px', borderRadius: 4, fontSize: 11, cursor: 'pointer', border: '1px solid #1e293b', background: 'transparent', color: '#475569' }}>
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
              {[
                ['Shares *', 'shares', 'number', ''],
                ['Entry Price *', 'entry_price', 'number', '0.01'],
                ['Exit Price', 'exit_price', 'number', '0.01'],
                ['Entry Date *', 'entry_date', 'date', ''],
                ['Exit Date', 'exit_date', 'date', ''],
                ['Stop Loss', 'stop_loss', 'number', '0.01'],
                ['Take Profit', 'take_profit', 'number', '0.01'],
                ['Signal Confidence %', 'signal_confidence', 'number', ''],
              ].map(([label, key, type, step]) => (
                <div key={key as string}>
                  <label style={{ fontSize: 11, color: '#64748b', display: 'block', marginBottom: 4 }}>{label}</label>
                  <input type={type as string} step={step || undefined}
                    value={(form as any)[key as string] ?? ''}
                    onChange={e => setForm(f => ({
                      ...f,
                      [key as string]: e.target.value
                        ? (type === 'date' ? e.target.value : Number(e.target.value))
                        : (type === 'date' ? '' : null),
                    }))}
                    style={{ width: '100%', padding: '7px 10px', borderRadius: 6, border: '1px solid #1e293b', background: '#020617', color: '#e2e8f0', fontSize: 13 }} />
                </div>
              ))}
              <div style={{ gridColumn: '1 / -1' }}>
                <label style={{ fontSize: 11, color: '#64748b', display: 'block', marginBottom: 4 }}>Strategy / Setup</label>
                <input value={form.strategy ?? ''} onChange={e => setForm(f => ({ ...f, strategy: e.target.value || null }))}
                  placeholder="e.g. Bull flag breakout"
                  style={{ width: '100%', padding: '7px 10px', borderRadius: 6, border: '1px solid #1e293b', background: '#020617', color: '#e2e8f0', fontSize: 13 }} />
              </div>
              <div style={{ gridColumn: '1 / -1' }}>
                <label style={{ fontSize: 11, color: '#64748b', display: 'block', marginBottom: 4 }}>Notes / Rationale</label>
                <textarea value={form.notes ?? ''} onChange={e => setForm(f => ({ ...f, notes: e.target.value || null }))}
                  rows={3} placeholder="Why you entered, what you observed…"
                  style={{ width: '100%', padding: '7px 10px', borderRadius: 6, border: '1px solid #1e293b', background: '#020617', color: '#e2e8f0', fontSize: 13, resize: 'vertical' }} />
              </div>
            </div>
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

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function JournalPage() {
  const [tab, setTab] = useState<'ai' | 'manual'>('ai');

  return (
    <div style={{ padding: '24px 0' }}>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, color: '#e2e8f0', marginBottom: 4 }}>Trade Journal</h1>
        <p style={{ fontSize: 13, color: '#64748b' }}>Review every AI paper trade — entry score, indicators, exit reasoning, and scaling events.</p>
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 20, borderBottom: '1px solid #1e293b', paddingBottom: 0 }}>
        {([['ai', 'AI Paper Trades'], ['manual', 'Manual Log']] as const).map(([key, label]) => (
          <button key={key} onClick={() => setTab(key)}
            style={{ padding: '8px 16px', borderRadius: '6px 6px 0 0', fontSize: 13, fontWeight: 600,
              cursor: 'pointer', border: '1px solid',
              borderColor: tab === key ? '#1e293b' : 'transparent',
              borderBottom: tab === key ? '1px solid #0f172a' : '1px solid transparent',
              background: tab === key ? '#0f172a' : 'transparent',
              color: tab === key ? '#e2e8f0' : '#64748b',
              marginBottom: -1 }}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'ai' ? <AITradesTab /> : <ManualLogTab />}
    </div>
  );
}
