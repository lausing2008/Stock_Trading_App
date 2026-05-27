import { useState, useMemo } from 'react';
import useSWR from 'swr';
import Link from 'next/link';
import { api, type TradePlan, type PriceAlert, type SignalAlertItem } from '@/lib/api';

const STAGES = ['watch', 'planning', 'active', 'closed'] as const;
type Stage = typeof STAGES[number];

const STAGE_META: Record<Stage, { label: string; color: string; bg: string; border: string; desc: string }> = {
  watch:    { label: 'Watch',    color: '#94a3b8', bg: 'rgba(148,163,184,0.06)', border: 'rgba(148,163,184,0.15)', desc: 'Tracking — no position yet' },
  planning: { label: 'Planning', color: '#818cf8', bg: 'rgba(129,140,248,0.06)', border: 'rgba(129,140,248,0.2)',  desc: 'AI plan generated, evaluating entry' },
  active:   { label: 'Active',   color: '#4ade80', bg: 'rgba(74,222,128,0.06)',  border: 'rgba(74,222,128,0.2)',   desc: 'In trade — monitoring' },
  closed:   { label: 'Closed',   color: '#475569', bg: 'rgba(71,85,105,0.05)',   border: 'rgba(71,85,105,0.15)',   desc: 'Trade completed' },
};

const SOURCE_LABEL: Record<string, string> = {
  gameplan: '📋 Game Plan',
  forecast: '🔮 Forecast',
  manual:   '✏️ Manual',
};

function fmt(n: number | null | undefined) {
  if (n == null) return '—';
  return `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function relDate(iso: string) {
  const d = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (d === 0) return 'Today';
  if (d === 1) return 'Yesterday';
  return `${d}d ago`;
}

/* ── Card ─────────────────────────────────────────────── */
type StoredGamePlan = {
  title?: string;
  entries?: { label: string; price: number; rationale: string }[];
  stop_loss?: { price: number; rationale: string };
  take_profit?: { price: number; rationale: string } | null;
  catalysts?: string[];
  risk?: string;
};

type Suggestion = { label: string; price: number; condition: 'above' | 'below'; color: string; rationale?: string };

function PlanCard({ plan, priceAlerts, signalAlert, onStageChange, onDelete, onAlertsChange }: {
  plan: TradePlan;
  priceAlerts: PriceAlert[];
  signalAlert: SignalAlertItem | null;
  onStageChange: (id: number, stage: Stage) => void;
  onDelete: (id: number) => void;
  onAlertsChange: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [alertOpen, setAlertOpen] = useState(false);
  const [threshold, setThreshold] = useState('');
  const [condition, setCondition] = useState<'above' | 'below'>('above');
  const [addingAlert, setAddingAlert] = useState(false);
  const [settingAll, setSettingAll] = useState(false);
  const [togglingSignal, setTogglingSignal] = useState(false);
  const meta = STAGE_META[plan.stage as Stage] ?? STAGE_META.watch;
  const gp = plan.game_plan as StoredGamePlan | null;

  // Build suggested price levels — prefer TradePlan DB fields, fall back to game_plan JSON
  const suggestions = useMemo<Suggestion[]>(() => {
    const s: Suggestion[] = [];
    if (gp?.entries?.length) {
      gp.entries.forEach(e => s.push({ label: e.label, price: e.price, condition: 'below', color: '#818cf8', rationale: e.rationale }));
    } else if (plan.entry_price != null) {
      s.push({ label: 'Entry', price: plan.entry_price, condition: 'below', color: '#818cf8', rationale: undefined });
    }
    const stopPrice = plan.stop_loss ?? gp?.stop_loss?.price ?? null;
    const stopRationale = gp?.stop_loss?.rationale;
    if (stopPrice != null) s.push({ label: 'Stop Loss', price: stopPrice, condition: 'below', color: '#f87171', rationale: stopRationale });
    const targetPrice = plan.take_profit ?? gp?.take_profit?.price ?? null;
    const targetRationale = gp?.take_profit?.rationale;
    if (targetPrice != null) s.push({ label: 'Take Profit', price: targetPrice, condition: 'above', color: '#4ade80', rationale: targetRationale });
    return s;
  }, [gp, plan.entry_price, plan.stop_loss, plan.take_profit]);

  // Track which suggestions are selected (all by default)
  const [selected, setSelected] = useState<Set<number>>(() => new Set(suggestions.map((_, i) => i)));
  // Reset selection when suggestions change
  useMemo(() => setSelected(new Set(suggestions.map((_, i) => i))), [suggestions.length]);

  const existingThresholds = new Set(priceAlerts.map(a => `${a.condition}:${a.threshold}`));

  async function handleAddPriceAlert() {
    const val = parseFloat(threshold);
    if (!val || isNaN(val)) return;
    setAddingAlert(true);
    try {
      await api.createAlert({ symbol: plan.symbol, condition, threshold: val });
      setThreshold('');
      onAlertsChange();
    } finally { setAddingAlert(false); }
  }

  async function handleSetAll() {
    const toCreate = suggestions.filter((_, i) => selected.has(i) && !existingThresholds.has(`${suggestions[i].condition}:${suggestions[i].price}`));
    if (!toCreate.length) return;
    setSettingAll(true);
    try {
      await Promise.all(toCreate.map(s => api.createAlert({ symbol: plan.symbol, condition: s.condition, threshold: s.price, note: s.label })));
      onAlertsChange();
    } finally { setSettingAll(false); }
  }

  async function handleToggleSignal() {
    setTogglingSignal(true);
    try {
      if (signalAlert) {
        await api.deleteSignalAlert(signalAlert.id);
      } else {
        const email = typeof window !== 'undefined' ? localStorage.getItem('stockai_alert_email') ?? undefined : undefined;
        await api.createSignalAlert(plan.symbol, email);
      }
      onAlertsChange();
    } finally { setTogglingSignal(false); }
  }

  const hasAlerts = priceAlerts.length > 0 || !!signalAlert;

  return (
    <div style={{ borderRadius: '10px', border: `1px solid ${meta.border}`, background: '#0f172a', overflow: 'hidden', marginBottom: '8px' }}>
      {/* Colour stripe */}
      <div style={{ height: '2px', background: meta.color, opacity: 0.6 }} />

      <div style={{ padding: '12px 14px' }}>
        {/* Top row */}
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '6px' }}>
          <div>
            <Link href={`/stock/${plan.symbol}`} style={{ fontSize: '16px', fontWeight: 800, color: '#818cf8', textDecoration: 'none', fontFamily: 'ui-monospace, monospace' }}>
              {plan.symbol}
            </Link>
            {plan.source && (
              <span style={{ marginLeft: '8px', fontSize: '10px', color: '#475569' }}>{SOURCE_LABEL[plan.source] ?? plan.source}</span>
            )}
          </div>
          <div style={{ display: 'flex', gap: '4px' }}>
            <button onClick={() => setExpanded(e => !e)} style={{ background: 'none', border: 'none', color: '#475569', cursor: 'pointer', fontSize: '11px', padding: '2px 5px' }}>
              {expanded ? '▲' : '▼'}
            </button>
            {confirmDelete ? (
              <>
                <button onClick={() => onDelete(plan.id)} style={{ background: 'rgba(239,68,68,0.15)', border: '1px solid rgba(239,68,68,0.35)', color: '#f87171', borderRadius: '4px', padding: '2px 8px', fontSize: '10px', cursor: 'pointer', fontWeight: 700 }}>Delete</button>
                <button onClick={() => setConfirmDelete(false)} style={{ background: 'transparent', border: '1px solid #1e293b', color: '#475569', borderRadius: '4px', padding: '2px 8px', fontSize: '10px', cursor: 'pointer' }}>Cancel</button>
              </>
            ) : (
              <button onClick={() => setConfirmDelete(true)} style={{ background: 'none', border: 'none', color: '#334155', cursor: 'pointer', fontSize: '12px', padding: '2px 5px' }} title="Delete">✕</button>
            )}
          </div>
        </div>

        {/* Alert panel */}
        {alertOpen && (
          <div style={{ marginBottom: '12px', borderRadius: '10px', border: '1px solid rgba(251,191,36,0.25)', background: '#0a1628', overflow: 'hidden' }}>
            {/* Panel header */}
            <div style={{ padding: '10px 14px', background: 'rgba(251,191,36,0.07)', borderBottom: '1px solid rgba(251,191,36,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontSize: '12px', fontWeight: 700, color: '#fbbf24' }}>🔔 Alerts — {plan.symbol}</span>
              <button onClick={() => setAlertOpen(false)} style={{ background: 'none', border: 'none', color: '#475569', cursor: 'pointer', fontSize: '14px', lineHeight: 1, padding: '0 2px' }}>✕</button>
            </div>

            <div style={{ padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: '14px' }}>

              {/* Signal alert section */}
              <div>
                <div style={{ fontSize: '10px', color: '#475569', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: '8px' }}>AI Signal Alert</div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 12px', borderRadius: '8px', background: signalAlert ? 'rgba(129,140,248,0.08)' : 'rgba(255,255,255,0.02)', border: `1px solid ${signalAlert ? 'rgba(129,140,248,0.3)' : '#1e293b'}` }}>
                  <div>
                    <div style={{ fontSize: '13px', color: '#e2e8f0', fontWeight: 600 }}>📡 Notify when signal changes</div>
                    <div style={{ fontSize: '11px', color: '#475569', marginTop: '2px' }}>Email alert when BUY / SELL / HOLD changes</div>
                  </div>
                  <button
                    onClick={handleToggleSignal}
                    disabled={togglingSignal}
                    style={{ flexShrink: 0, marginLeft: '12px', padding: '6px 16px', borderRadius: '8px', cursor: 'pointer', fontWeight: 700, fontSize: '12px', border: `1px solid ${signalAlert ? 'rgba(129,140,248,0.5)' : '#334155'}`, background: signalAlert ? 'rgba(129,140,248,0.2)' : 'rgba(255,255,255,0.04)', color: signalAlert ? '#818cf8' : '#64748b', transition: 'all 0.15s' }}
                  >
                    {togglingSignal ? '…' : signalAlert ? '🔔 On' : '🔕 Off'}
                  </button>
                </div>
              </div>

              {/* Price levels from game plan */}
              {suggestions.length > 0 && (
                <div>
                  <div style={{ fontSize: '10px', color: '#475569', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: '8px' }}>Price Levels — select to alert</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginBottom: '10px' }}>
                    {suggestions.map((s, i) => {
                      const alreadySet = existingThresholds.has(`${s.condition}:${s.price}`);
                      const isSel = selected.has(i);
                      return (
                        <div
                          key={i}
                          onClick={() => !alreadySet && setSelected(prev => { const n = new Set(prev); isSel ? n.delete(i) : n.add(i); return n; })}
                          style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '10px 12px', borderRadius: '8px', cursor: alreadySet ? 'default' : 'pointer', border: `1px solid ${alreadySet ? 'rgba(74,222,128,0.3)' : isSel ? `${s.color}50` : '#1e293b'}`, background: alreadySet ? 'rgba(74,222,128,0.06)' : isSel ? `${s.color}0d` : 'rgba(255,255,255,0.02)', transition: 'all 0.12s' }}
                        >
                          {/* Checkbox */}
                          <div style={{ flexShrink: 0, width: '18px', height: '18px', borderRadius: '4px', border: `2px solid ${alreadySet ? '#4ade80' : isSel ? s.color : '#334155'}`, background: alreadySet ? 'rgba(74,222,128,0.2)' : isSel ? `${s.color}30` : 'transparent', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '11px' }}>
                            {alreadySet ? '✓' : isSel ? '✓' : ''}
                          </div>
                          {/* Label + rationale */}
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                              <span style={{ fontSize: '13px', color: s.color, fontWeight: 700 }}>{s.label}</span>
                              <span style={{ fontSize: '11px', color: '#475569', background: 'rgba(255,255,255,0.04)', padding: '1px 6px', borderRadius: '4px' }}>{s.condition === 'above' ? '↑ rises above' : '↓ drops below'}</span>
                            </div>
                            {s.rationale && <div style={{ fontSize: '11px', color: '#475569', marginTop: '2px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.rationale}</div>}
                          </div>
                          {/* Price */}
                          <span style={{ flexShrink: 0, fontSize: '14px', fontFamily: 'ui-monospace, monospace', color: alreadySet ? '#4ade80' : '#e2e8f0', fontWeight: 700 }}>${s.price.toFixed(2)}</span>
                        </div>
                      );
                    })}
                  </div>
                  {/* Set All button */}
                  {(() => {
                    const pendingCount = [...selected].filter(i => suggestions[i] && !existingThresholds.has(`${suggestions[i].condition}:${suggestions[i].price}`)).length;
                    return (
                      <button
                        onClick={handleSetAll}
                        disabled={settingAll || pendingCount === 0}
                        style={{ width: '100%', padding: '8px', borderRadius: '8px', border: 'none', background: pendingCount > 0 ? 'linear-gradient(135deg,rgba(251,191,36,0.25),rgba(251,191,36,0.15))' : '#1e293b', color: pendingCount > 0 ? '#fbbf24' : '#334155', fontSize: '13px', fontWeight: 700, cursor: pendingCount > 0 ? 'pointer' : 'default' }}
                      >
                        {settingAll ? 'Setting alerts…' : pendingCount > 0 ? `Set ${pendingCount} Alert${pendingCount !== 1 ? 's' : ''}` : 'All alerts already set ✓'}
                      </button>
                    );
                  })()}
                </div>
              )}

              {/* Custom price alert */}
              <div>
                <div style={{ fontSize: '10px', color: '#475569', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: '8px' }}>Custom Alert</div>
                <div style={{ display: 'flex', gap: '6px', alignItems: 'stretch' }}>
                  <select value={condition} onChange={e => setCondition(e.target.value as 'above' | 'below')} style={{ fontSize: '12px', background: '#0f172a', border: '1px solid #1e293b', color: '#94a3b8', borderRadius: '7px', padding: '7px 8px', cursor: 'pointer', flexShrink: 0 }}>
                    <option value="above">↑ Above</option>
                    <option value="below">↓ Below</option>
                  </select>
                  <input type="number" value={threshold} onChange={e => setThreshold(e.target.value)} placeholder="Enter price…" style={{ flex: 1, fontSize: '13px', background: 'rgba(255,255,255,0.04)', border: '1px solid #1e293b', borderRadius: '7px', padding: '7px 10px', color: '#f1f5f9', outline: 'none', minWidth: 0 }} />
                  <button onClick={handleAddPriceAlert} disabled={!threshold || addingAlert} style={{ fontSize: '12px', padding: '7px 14px', borderRadius: '7px', border: 'none', background: threshold ? 'rgba(251,191,36,0.2)' : '#1e293b', color: threshold ? '#fbbf24' : '#334155', cursor: threshold ? 'pointer' : 'default', fontWeight: 700, whiteSpace: 'nowrap', flexShrink: 0 }}>
                    {addingAlert ? '…' : '+ Add'}
                  </button>
                </div>
              </div>

              {/* Active price alerts */}
              {priceAlerts.length > 0 && (
                <div>
                  <div style={{ fontSize: '10px', color: '#475569', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: '8px' }}>Active Price Alerts ({priceAlerts.length})</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                    {priceAlerts.map(a => (
                      <div key={a.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 10px', borderRadius: '7px', background: a.triggered ? 'rgba(74,222,128,0.06)' : 'rgba(255,255,255,0.02)', border: `1px solid ${a.triggered ? 'rgba(74,222,128,0.25)' : '#1e293b'}` }}>
                        <div>
                          <span style={{ fontSize: '13px', fontFamily: 'ui-monospace, monospace', color: a.triggered ? '#4ade80' : '#e2e8f0', fontWeight: 700 }}>
                            {a.condition === 'above' ? '↑' : '↓'} ${Number(a.threshold).toFixed(2)}
                          </span>
                          {a.note && <span style={{ fontSize: '11px', color: '#475569', marginLeft: '8px' }}>· {a.note}</span>}
                          {a.triggered && <span style={{ fontSize: '11px', color: '#4ade80', marginLeft: '6px' }}>✓ Triggered</span>}
                        </div>
                        <button onClick={async () => { await api.deleteAlert(a.id); onAlertsChange(); }} style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.2)', color: '#f87171', cursor: 'pointer', fontSize: '11px', padding: '3px 8px', borderRadius: '5px', fontWeight: 600 }}>Remove</button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Prices */}
        <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', marginBottom: '8px' }}>
          {plan.entry_price != null && (
            <div style={{ fontSize: '11px' }}>
              <span style={{ color: '#475569' }}>Entry </span>
              <span style={{ color: '#818cf8', fontWeight: 700, fontFamily: 'monospace' }}>{fmt(plan.entry_price)}</span>
            </div>
          )}
          {plan.stop_loss != null && (
            <div style={{ fontSize: '11px' }}>
              <span style={{ color: '#475569' }}>Stop </span>
              <span style={{ color: '#f87171', fontWeight: 700, fontFamily: 'monospace' }}>{fmt(plan.stop_loss)}</span>
            </div>
          )}
          {plan.take_profit != null && (
            <div style={{ fontSize: '11px' }}>
              <span style={{ color: '#475569' }}>Target </span>
              <span style={{ color: '#4ade80', fontWeight: 700, fontFamily: 'monospace' }}>{fmt(plan.take_profit)}</span>
            </div>
          )}
          {plan.entry_price != null && plan.stop_loss != null && plan.take_profit != null && plan.entry_price > plan.stop_loss && (
            <div style={{ fontSize: '11px' }}>
              <span style={{ color: '#475569' }}>R:R </span>
              <span style={{ color: '#facc15', fontWeight: 700 }}>{((plan.take_profit - plan.entry_price) / (plan.entry_price - plan.stop_loss)).toFixed(1)}x</span>
            </div>
          )}
        </div>

        {/* Notes */}
        {plan.notes && (
          <div style={{ fontSize: '11px', color: '#475569', lineHeight: 1.4, marginBottom: '8px', borderLeft: '2px solid #1e293b', paddingLeft: '8px' }}>
            {plan.notes.length > 120 && !expanded ? plan.notes.slice(0, 120) + '…' : plan.notes}
          </div>
        )}

        {/* Expanded: full game plan details */}
        {expanded && gp && (
          <div style={{ marginBottom: '8px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {gp.title && <div style={{ fontSize: '11px', fontWeight: 700, color: '#e2e8f0' }}>{gp.title}</div>}
            {gp.entries && gp.entries.map((e, i) => (
              <div key={i} style={{ fontSize: '10px', color: '#64748b', paddingLeft: '8px', borderLeft: '2px solid #1e293b' }}>
                <span style={{ color: '#818cf8', fontWeight: 700 }}>{e.label}</span> ${e.price.toFixed(2)} — {e.rationale}
              </div>
            ))}
            {gp.catalysts && (
              <div style={{ fontSize: '10px', color: '#64748b' }}>
                {gp.catalysts.map((c, i) => <div key={i}>› {c}</div>)}
              </div>
            )}
            {gp.risk && <div style={{ fontSize: '10px', color: '#fbbf24' }}>⚠ {gp.risk}</div>}
          </div>
        )}

        {/* Footer: stage selector + alerts + date */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', paddingTop: '8px', borderTop: '1px solid rgba(255,255,255,0.04)' }}>
          <div style={{ display: 'flex', gap: '3px', flexWrap: 'wrap', alignItems: 'center' }}>
            {STAGES.map(s => (
              <button
                key={s}
                onClick={() => onStageChange(plan.id, s)}
                style={{ padding: '2px 7px', borderRadius: '4px', fontSize: '10px', fontWeight: plan.stage === s ? 700 : 400, cursor: plan.stage === s ? 'default' : 'pointer', border: `1px solid ${plan.stage === s ? STAGE_META[s].color : 'transparent'}`, background: plan.stage === s ? STAGE_META[s].bg : 'transparent', color: plan.stage === s ? STAGE_META[s].color : '#334155' }}
              >
                {STAGE_META[s].label}
              </button>
            ))}
            <button
              onClick={() => setAlertOpen(o => !o)}
              style={{ padding: '2px 8px', borderRadius: '4px', fontSize: '10px', fontWeight: 600, cursor: 'pointer', border: `1px solid ${hasAlerts ? 'rgba(251,191,36,0.4)' : '#1e293b'}`, background: hasAlerts ? 'rgba(251,191,36,0.1)' : 'transparent', color: hasAlerts ? '#fbbf24' : '#475569', marginLeft: '2px' }}
            >
              🔔 {hasAlerts ? `Alerts (${priceAlerts.length + (signalAlert ? 1 : 0)})` : 'Set Alerts'}
            </button>
          </div>
          <span style={{ fontSize: '10px', color: '#334155' }}>{relDate(plan.updated_at)}</span>
        </div>
      </div>
    </div>
  );
}

/* ── Add card form ─────────────────────────────────────── */
function AddCardForm({ onAdd }: { onAdd: (symbol: string, notes: string) => Promise<void> }) {
  const [symbol, setSymbol] = useState('');
  const [notes, setNotes] = useState('');
  const [adding, setAdding] = useState(false);
  const [open, setOpen] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!symbol.trim()) return;
    setAdding(true);
    await onAdd(symbol.trim().toUpperCase(), notes.trim());
    setSymbol(''); setNotes(''); setAdding(false); setOpen(false);
  }

  if (!open) return (
    <button onClick={() => setOpen(true)} style={{ width: '100%', padding: '8px', borderRadius: '8px', border: '1px dashed #1e293b', background: 'transparent', color: '#334155', cursor: 'pointer', fontSize: '12px', marginBottom: '8px' }}>
      + Add card
    </button>
  );

  return (
    <form onSubmit={submit} style={{ marginBottom: '8px', padding: '10px 12px', borderRadius: '8px', border: '1px solid rgba(99,102,241,0.25)', background: 'rgba(15,23,42,0.9)' }}>
      <input
        autoFocus
        value={symbol}
        onChange={e => setSymbol(e.target.value)}
        placeholder="Symbol (e.g. AAPL)"
        style={{ width: '100%', background: 'rgba(255,255,255,0.04)', border: '1px solid #1e293b', borderRadius: '6px', padding: '6px 10px', fontSize: '12px', color: '#f1f5f9', outline: 'none', marginBottom: '6px', boxSizing: 'border-box' }}
      />
      <textarea
        value={notes}
        onChange={e => setNotes(e.target.value)}
        placeholder="Notes (optional)"
        rows={2}
        style={{ width: '100%', background: 'rgba(255,255,255,0.04)', border: '1px solid #1e293b', borderRadius: '6px', padding: '6px 10px', fontSize: '11px', color: '#94a3b8', outline: 'none', resize: 'none', boxSizing: 'border-box', fontFamily: 'inherit', marginBottom: '6px' }}
      />
      <div style={{ display: 'flex', gap: '6px' }}>
        <button type="submit" disabled={!symbol.trim() || adding} style={{ flex: 1, padding: '6px', borderRadius: '6px', border: 'none', background: symbol.trim() ? 'linear-gradient(135deg,#4f46e5,#6366f1)' : '#1e293b', color: symbol.trim() ? '#fff' : '#475569', fontSize: '12px', fontWeight: 700, cursor: symbol.trim() ? 'pointer' : 'default' }}>
          {adding ? '…' : 'Add'}
        </button>
        <button type="button" onClick={() => setOpen(false)} style={{ padding: '6px 12px', borderRadius: '6px', border: '1px solid #1e293b', background: 'transparent', color: '#475569', fontSize: '12px', cursor: 'pointer' }}>Cancel</button>
      </div>
    </form>
  );
}

type MarketFilter = 'US' | 'HK';
const isHK = (symbol: string) => /\.(HK|hk)$/.test(symbol) || /^\d{4,5}$/.test(symbol);

/* ── Main ─────────────────────────────────────────────── */
export default function BoardPage() {
  const { data, mutate, isLoading, error } = useSWR<TradePlan[]>('board', () => api.listBoard(), { revalidateOnFocus: false });
  const { data: priceAlerts, mutate: mutateAlerts } = useSWR<PriceAlert[]>('alerts', () => api.listAlerts(), { revalidateOnFocus: false });
  const { data: signalAlerts, mutate: mutateSignalAlerts } = useSWR<SignalAlertItem[]>('signal-alerts', () => api.listSignalAlerts(), { revalidateOnFocus: false });
  const [market, setMarket] = useState<MarketFilter>('US');

  function handleAlertsChange() { mutateAlerts(); mutateSignalAlerts(); }

  const filtered = useMemo(() =>
    (data ?? []).filter(p => market === 'HK' ? isHK(p.symbol) : !isHK(p.symbol)),
    [data, market]
  );

  const byStage = useMemo(() => {
    const m: Record<Stage, TradePlan[]> = { watch: [], planning: [], active: [], closed: [] };
    for (const p of filtered) {
      const s = (p.stage as Stage) in m ? (p.stage as Stage) : 'watch';
      m[s].push(p);
    }
    return m;
  }, [filtered]);

  async function handleStageChange(id: number, stage: Stage) {
    await api.updateBoardPlan(id, { stage });
    mutate();
  }

  async function handleDelete(id: number) {
    await api.deleteBoardPlan(id);
    mutate();
  }

  async function handleAdd(symbol: string, notes: string) {
    await api.createBoardPlan({ symbol, stage: 'watch', notes: notes || null, source: 'manual' });
    mutate();
  }

  const total = filtered.length;
  const usCnt = (data ?? []).filter(p => !isHK(p.symbol)).length;
  const hkCnt = (data ?? []).filter(p => isHK(p.symbol)).length;

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px', flexWrap: 'wrap', gap: '12px' }}>
        <div>
          <h1 style={{ fontSize: '22px', fontWeight: 800, color: '#e2e8f0', marginBottom: '4px' }}>Trade Board</h1>
          <p style={{ fontSize: '12px', color: '#475569' }}>
            {total > 0 ? `${total} idea${total !== 1 ? 's' : ''} · drag-free Kanban` : 'Save game plans and forecast picks here'}
          </p>
        </div>
        <div style={{ display: 'flex', gap: '8px', fontSize: '11px', color: '#334155' }}>
          {STAGES.map(s => (
            <span key={s} style={{ padding: '4px 10px', borderRadius: '6px', background: STAGE_META[s].bg, border: `1px solid ${STAGE_META[s].border}`, color: STAGE_META[s].color, fontWeight: 600 }}>
              {STAGE_META[s].label} {byStage[s].length > 0 && `(${byStage[s].length})`}
            </span>
          ))}
        </div>
      </div>

      {/* Market tabs */}
      <div style={{ display: 'flex', gap: '6px', marginBottom: '20px' }}>
        {(['US', 'HK'] as MarketFilter[]).map(m => (
          <button
            key={m}
            onClick={() => setMarket(m)}
            style={{
              padding: '6px 18px', borderRadius: '8px', fontSize: '12px', fontWeight: 700, cursor: 'pointer',
              border: market === m ? '1px solid #818cf8' : '1px solid #1e293b',
              background: market === m ? 'rgba(129,140,248,0.12)' : 'transparent',
              color: market === m ? '#818cf8' : '#475569',
            }}
          >
            {m} <span style={{ fontSize: '10px', fontWeight: 400, opacity: 0.7 }}>({m === 'US' ? usCnt : hkCnt})</span>
          </button>
        ))}
      </div>

      {isLoading && <div style={{ color: '#475569', fontSize: '13px', padding: '40px 0', textAlign: 'center' }}>Loading board…</div>}

      {error && (
        <div style={{ padding: '12px 16px', borderRadius: '8px', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: '#f87171', fontSize: '12px', marginBottom: '16px' }}>
          Failed to load board: {String(error?.message ?? error)}. Try refreshing the page.
        </div>
      )}

      {!isLoading && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: '16px', alignItems: 'start' }}>
          {STAGES.map(stage => {
            const m = STAGE_META[stage];
            const cards = byStage[stage];
            return (
              <div key={stage} style={{ borderRadius: '12px', border: `1px solid ${m.border}`, background: '#080f1e', overflow: 'hidden' }}>
                {/* Column header */}
                <div style={{ padding: '10px 14px 8px', background: m.bg, borderBottom: `1px solid ${m.border}` }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <span style={{ fontSize: '13px', fontWeight: 700, color: m.color }}>{m.label}</span>
                    <span style={{ fontSize: '11px', color: m.color, opacity: 0.7, background: m.bg, border: `1px solid ${m.border}`, borderRadius: '10px', padding: '1px 7px' }}>{cards.length}</span>
                  </div>
                  <div style={{ fontSize: '10px', color: '#334155', marginTop: '2px' }}>{m.desc}</div>
                </div>

                {/* Cards */}
                <div style={{ padding: '10px' }}>
                  {stage === 'watch' && <AddCardForm onAdd={handleAdd} />}
                  {cards.length === 0 && stage !== 'watch' && (
                    <div style={{ textAlign: 'center', padding: '24px 0', fontSize: '11px', color: '#1e293b' }}>
                      Move cards here as your trade progresses
                    </div>
                  )}
                  {cards.map(plan => (
                    <PlanCard
                      key={plan.id}
                      plan={plan}
                      priceAlerts={(priceAlerts ?? []).filter(a => a.symbol === plan.symbol)}
                      signalAlert={(signalAlerts ?? []).find(a => a.symbol === plan.symbol) ?? null}
                      onStageChange={handleStageChange}
                      onDelete={handleDelete}
                      onAlertsChange={handleAlertsChange}
                    />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {!isLoading && total === 0 && (
        <div style={{ marginTop: '24px', padding: '20px 24px', borderRadius: '10px', background: 'rgba(99,102,241,0.05)', border: '1px solid rgba(99,102,241,0.15)', fontSize: '12px', color: '#475569', lineHeight: 1.7 }}>
          <strong style={{ color: '#818cf8' }}>How to populate your {market} board:</strong>
          <ul style={{ margin: '8px 0 0 16px', padding: 0 }}>
            <li>On any stock detail page — generate a <strong>Game Plan</strong> then click <strong>📌 Save</strong></li>
            <li>On the <Link href="/forecast" style={{ color: '#818cf8' }}>Forecast</Link> page — click <strong>📌 Save to Board</strong> on any pick</li>
            <li>Add manually using the <strong>+ Add card</strong> in the Watch column above</li>
          </ul>
        </div>
      )}
    </div>
  );
}
