import { useState, useMemo, useEffect } from 'react';
import { createPortal } from 'react-dom';
import useSWR from 'swr';
import Link from 'next/link';
import { api, type TradePlan, type PriceAlert, type SignalAlertItem } from '@/lib/api';

const STAGES = ['watch', 'planning', 'active', 'closed'] as const;
type Stage = typeof STAGES[number];

const STAGE_META: Record<Stage, { label: string; color: string; bg: string; border: string; desc: string }> = {
  watch:    { label: 'Radar',    color: '#94a3b8', bg: 'rgba(148,163,184,0.06)', border: 'rgba(148,163,184,0.15)', desc: 'On radar — shortlisted from screener or forecast' },
  planning: { label: 'Planning', color: '#818cf8', bg: 'rgba(129,140,248,0.06)', border: 'rgba(129,140,248,0.2)',  desc: 'AI plan generated, evaluating entry' },
  active:   { label: 'Active',   color: '#4ade80', bg: 'rgba(74,222,128,0.06)',  border: 'rgba(74,222,128,0.2)',   desc: 'In trade — monitoring' },
  closed:   { label: 'Closed',   color: '#475569', bg: 'rgba(71,85,105,0.05)',   border: 'rgba(71,85,105,0.15)',   desc: 'Trade completed' },
};

const SOURCE_LABEL: Record<string, string> = {
  gameplan: '📋 Game Plan',
  forecast: '🔮 Forecast',
  manual:   '✏️ Manual',
};

function Stat({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '5px', padding: '4px 10px', borderRadius: '6px', background: 'rgba(255,255,255,0.03)', border: '1px solid #1e293b' }}>
      <span style={{ fontSize: '10px', color: '#334155' }}>{label}</span>
      <span style={{ fontSize: '13px', fontWeight: 800, fontFamily: 'monospace', color }}>{value}</span>
    </div>
  );
}

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

/* ── Alert Modal ─────────────────────────────────────────── */
function AlertModal({ plan, priceAlerts, signalAlert, suggestions, onClose, onAlertsChange }: {
  plan: TradePlan;
  priceAlerts: PriceAlert[];
  signalAlert: SignalAlertItem | null;
  suggestions: Suggestion[];
  onClose: () => void;
  onAlertsChange: () => void;
}) {
  const [threshold, setThreshold] = useState('');
  const [condition, setCondition] = useState<'above' | 'below'>('above');
  const [addingAlert, setAddingAlert] = useState(false);
  const [settingAll, setSettingAll] = useState(false);
  const [togglingSignal, setTogglingSignal] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(() => new Set(suggestions.map((_, i) => i)));

  const existingThresholds = new Set(priceAlerts.map(a => `${a.condition}:${a.threshold}`));
  const totalAlerts = priceAlerts.length + (signalAlert ? 1 : 0);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

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

  const pendingCount = [...selected].filter(i => suggestions[i] && !existingThresholds.has(`${suggestions[i].condition}:${suggestions[i].price}`)).length;

  const modal = (
    <div
      onClick={onClose}
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.65)', zIndex: 9999, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '16px' }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{ width: '460px', maxWidth: '100%', maxHeight: '90vh', overflowY: 'auto', borderRadius: '14px', border: '1px solid rgba(251,191,36,0.3)', background: '#0d1829', boxShadow: '0 25px 60px rgba(0,0,0,0.7)' }}
      >
        {/* Header */}
        <div style={{ position: 'sticky', top: 0, padding: '16px 20px', background: '#0d1829', borderBottom: '1px solid rgba(251,191,36,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', zIndex: 1 }}>
          <div>
            <div style={{ fontSize: '16px', fontWeight: 800, color: '#fbbf24' }}>🔔 Alerts — {plan.symbol}</div>
            {totalAlerts > 0 && <div style={{ fontSize: '11px', color: '#475569', marginTop: '2px' }}>{totalAlerts} active alert{totalAlerts !== 1 ? 's' : ''}</div>}
          </div>
          <button onClick={onClose} style={{ background: 'rgba(255,255,255,0.06)', border: '1px solid #1e293b', color: '#94a3b8', cursor: 'pointer', fontSize: '16px', width: '32px', height: '32px', borderRadius: '8px', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>✕</button>
        </div>

        <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: '20px' }}>

          {/* Signal alert */}
          <section>
            <div style={{ fontSize: '11px', color: '#475569', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: '10px' }}>AI Signal Alert</div>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 16px', borderRadius: '10px', background: signalAlert ? 'rgba(129,140,248,0.08)' : 'rgba(255,255,255,0.02)', border: `1px solid ${signalAlert ? 'rgba(129,140,248,0.3)' : '#1e293b'}` }}>
              <div>
                <div style={{ fontSize: '14px', color: '#e2e8f0', fontWeight: 600 }}>📡 Signal change notification</div>
                <div style={{ fontSize: '12px', color: '#475569', marginTop: '3px' }}>Email me when BUY / SELL / HOLD changes</div>
              </div>
              <button
                onClick={handleToggleSignal}
                disabled={togglingSignal}
                style={{ flexShrink: 0, marginLeft: '16px', padding: '8px 18px', borderRadius: '8px', cursor: 'pointer', fontWeight: 700, fontSize: '13px', border: `1px solid ${signalAlert ? 'rgba(129,140,248,0.5)' : '#334155'}`, background: signalAlert ? 'rgba(129,140,248,0.2)' : 'rgba(255,255,255,0.04)', color: signalAlert ? '#818cf8' : '#64748b' }}
              >
                {togglingSignal ? '…' : signalAlert ? '🔔 On' : '🔕 Off'}
              </button>
            </div>
          </section>

          {/* Price levels */}
          {suggestions.length > 0 && (
            <section>
              <div style={{ fontSize: '11px', color: '#475569', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: '10px' }}>Price Levels from Game Plan</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '12px' }}>
                {suggestions.map((s, i) => {
                  const alreadySet = existingThresholds.has(`${s.condition}:${s.price}`);
                  const isSel = selected.has(i);
                  return (
                    <div
                      key={i}
                      onClick={() => !alreadySet && setSelected(prev => { const n = new Set(prev); isSel ? n.delete(i) : n.add(i); return n; })}
                      style={{ display: 'flex', alignItems: 'center', gap: '12px', padding: '12px 14px', borderRadius: '10px', cursor: alreadySet ? 'default' : 'pointer', border: `1px solid ${alreadySet ? 'rgba(74,222,128,0.35)' : isSel ? `${s.color}55` : '#1e293b'}`, background: alreadySet ? 'rgba(74,222,128,0.06)' : isSel ? `${s.color}10` : 'rgba(255,255,255,0.02)' }}
                    >
                      <div style={{ flexShrink: 0, width: '20px', height: '20px', borderRadius: '5px', border: `2px solid ${alreadySet ? '#4ade80' : isSel ? s.color : '#334155'}`, background: alreadySet ? 'rgba(74,222,128,0.25)' : isSel ? `${s.color}35` : 'transparent', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '12px', color: alreadySet ? '#4ade80' : s.color }}>
                        {(alreadySet || isSel) ? '✓' : ''}
                      </div>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <span style={{ fontSize: '14px', color: s.color, fontWeight: 700 }}>{s.label}</span>
                          <span style={{ fontSize: '11px', color: '#475569', background: 'rgba(255,255,255,0.05)', padding: '2px 7px', borderRadius: '4px' }}>{s.condition === 'above' ? '↑ rises above' : '↓ drops below'}</span>
                        </div>
                        {s.rationale && <div style={{ fontSize: '12px', color: '#475569', marginTop: '3px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.rationale}</div>}
                      </div>
                      <span style={{ flexShrink: 0, fontSize: '15px', fontFamily: 'ui-monospace, monospace', color: alreadySet ? '#4ade80' : '#e2e8f0', fontWeight: 700 }}>${s.price.toFixed(2)}</span>
                    </div>
                  );
                })}
              </div>
              <button
                onClick={handleSetAll}
                disabled={settingAll || pendingCount === 0}
                style={{ width: '100%', padding: '10px', borderRadius: '10px', border: 'none', background: pendingCount > 0 ? 'linear-gradient(135deg,rgba(251,191,36,0.3),rgba(251,191,36,0.18))' : '#1e293b', color: pendingCount > 0 ? '#fbbf24' : '#334155', fontSize: '14px', fontWeight: 700, cursor: pendingCount > 0 ? 'pointer' : 'default' }}
              >
                {settingAll ? 'Setting alerts…' : pendingCount > 0 ? `Set ${pendingCount} Alert${pendingCount !== 1 ? 's' : ''}` : 'All price alerts set ✓'}
              </button>
            </section>
          )}

          {/* Custom alert */}
          <section>
            <div style={{ fontSize: '11px', color: '#475569', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: '10px' }}>Custom Price Alert</div>
            <div style={{ display: 'flex', gap: '8px' }}>
              <select value={condition} onChange={e => setCondition(e.target.value as 'above' | 'below')} style={{ fontSize: '13px', background: '#0f172a', border: '1px solid #1e293b', color: '#94a3b8', borderRadius: '8px', padding: '9px 10px', cursor: 'pointer', flexShrink: 0 }}>
                <option value="above">↑ Above</option>
                <option value="below">↓ Below</option>
              </select>
              <input
                type="number"
                value={threshold}
                onChange={e => setThreshold(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') handleAddPriceAlert(); }}
                placeholder="Enter price…"
                style={{ flex: 1, fontSize: '14px', background: 'rgba(255,255,255,0.04)', border: '1px solid #1e293b', borderRadius: '8px', padding: '9px 12px', color: '#f1f5f9', outline: 'none', minWidth: 0 }}
              />
              <button onClick={handleAddPriceAlert} disabled={!threshold || addingAlert} style={{ fontSize: '13px', padding: '9px 16px', borderRadius: '8px', border: 'none', background: threshold ? 'rgba(251,191,36,0.22)' : '#1e293b', color: threshold ? '#fbbf24' : '#334155', cursor: threshold ? 'pointer' : 'default', fontWeight: 700, whiteSpace: 'nowrap', flexShrink: 0 }}>
                {addingAlert ? '…' : '+ Add'}
              </button>
            </div>
          </section>

          {/* Active alerts — price + signal together so count matches badge */}
          {totalAlerts > 0 && (
            <section>
              <div style={{ fontSize: '11px', color: '#475569', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: '10px' }}>Active Alerts ({totalAlerts})</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                {signalAlert && (
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px', borderRadius: '9px', background: 'rgba(129,140,248,0.06)', border: '1px solid rgba(129,140,248,0.25)' }}>
                    <div>
                      <span style={{ fontSize: '13px', color: '#818cf8', fontWeight: 700 }}>📡 Signal alert</span>
                      <span style={{ fontSize: '12px', color: '#475569', marginLeft: '8px' }}>· notifies on BUY/SELL/HOLD change</span>
                    </div>
                    <button onClick={async () => { await api.deleteSignalAlert(signalAlert.id); onAlertsChange(); }} style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.2)', color: '#f87171', cursor: 'pointer', fontSize: '12px', padding: '4px 10px', borderRadius: '6px', fontWeight: 600 }}>Remove</button>
                  </div>
                )}
                {priceAlerts.map(a => (
                  <div key={a.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px', borderRadius: '9px', background: a.triggered ? 'rgba(74,222,128,0.06)' : 'rgba(255,255,255,0.02)', border: `1px solid ${a.triggered ? 'rgba(74,222,128,0.25)' : '#1e293b'}` }}>
                    <div>
                      <span style={{ fontSize: '14px', fontFamily: 'ui-monospace, monospace', color: a.triggered ? '#4ade80' : '#e2e8f0', fontWeight: 700 }}>
                        {a.condition === 'above' ? '↑' : '↓'} ${Number(a.threshold).toFixed(2)}
                      </span>
                      {a.note && <span style={{ fontSize: '12px', color: '#475569', marginLeft: '8px' }}>· {a.note}</span>}
                      {a.triggered && <span style={{ fontSize: '12px', color: '#4ade80', marginLeft: '8px' }}>✓ Triggered</span>}
                    </div>
                    <button onClick={async () => { await api.deleteAlert(a.id); onAlertsChange(); }} style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.2)', color: '#f87171', cursor: 'pointer', fontSize: '12px', padding: '4px 10px', borderRadius: '6px', fontWeight: 600 }}>Remove</button>
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  );

  if (typeof document === 'undefined') return null;
  return createPortal(modal, document.body);
}

function PlanCard({ plan, priceAlerts, signalAlert, livePrice, onStageChange, onDelete, onAlertsChange, onExitSaved }: {
  plan: TradePlan;
  priceAlerts: PriceAlert[];
  signalAlert: SignalAlertItem | null;
  livePrice: { price: number; change_pct: number | null } | null;
  onStageChange: (id: number, stage: Stage) => void;
  onDelete: (id: number) => void;
  onAlertsChange: () => void;
  onExitSaved: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [alertOpen, setAlertOpen] = useState(false);
  const [exitInput, setExitInput] = useState('');
  const [savingExit, setSavingExit] = useState(false);
  const meta = STAGE_META[plan.stage as Stage] ?? STAGE_META.watch;
  const gp = plan.game_plan as StoredGamePlan | null;

  const pnlPct = plan.exit_price != null && plan.entry_price != null && plan.entry_price > 0
    ? ((plan.exit_price - plan.entry_price) / plan.entry_price) * 100
    : null;

  async function saveExitPrice() {
    const val = parseFloat(exitInput);
    if (isNaN(val) || val <= 0) return;
    setSavingExit(true);
    try {
      await api.updateBoardPlan(plan.id, { exit_price: val });
      setExitInput('');
      onExitSaved();
    } finally {
      setSavingExit(false);
    }
  }

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

  // Close modal when card leaves Active
  useMemo(() => { if (plan.stage !== 'active') setAlertOpen(false); }, [plan.stage]);

  const hasAlerts = priceAlerts.length > 0 || !!signalAlert;
  const alertCount = priceAlerts.length + (signalAlert ? 1 : 0);

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
          {/* Live price + day change */}
          {livePrice && (
            <div style={{ textAlign: 'right', marginLeft: '8px' }}>
              <div style={{ fontSize: '15px', fontWeight: 800, fontFamily: 'ui-monospace, monospace', color: '#f1f5f9' }}>
                ${livePrice.price.toFixed(2)}
              </div>
              {livePrice.change_pct != null && (
                <div style={{ fontSize: '11px', fontWeight: 600, color: livePrice.change_pct >= 0 ? '#4ade80' : '#f87171' }}>
                  {livePrice.change_pct >= 0 ? '+' : ''}{livePrice.change_pct.toFixed(2)}%
                </div>
              )}
            </div>
          )}
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

        {/* Alert modal (portal) */}
        {alertOpen && (
          <AlertModal
            plan={plan}
            priceAlerts={priceAlerts}
            signalAlert={signalAlert}
            suggestions={suggestions}
            onClose={() => setAlertOpen(false)}
            onAlertsChange={onAlertsChange}
          />
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

        {/* Closed: P&L outcome */}
        {plan.stage === 'closed' && (
          <div style={{ marginBottom: '8px', padding: '8px 10px', borderRadius: '7px', background: pnlPct != null ? (pnlPct >= 0 ? 'rgba(74,222,128,0.07)' : 'rgba(239,68,68,0.07)') : 'rgba(255,255,255,0.02)', border: `1px solid ${pnlPct != null ? (pnlPct >= 0 ? 'rgba(74,222,128,0.2)' : 'rgba(239,68,68,0.2)') : 'rgba(255,255,255,0.04)'}` }}>
            {pnlPct != null ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <span style={{ fontSize: '20px', fontWeight: 900, fontFamily: 'monospace', color: pnlPct >= 0 ? '#4ade80' : '#f87171' }}>
                  {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%
                </span>
                <div style={{ fontSize: '11px', color: '#475569' }}>
                  <div>Exit <span style={{ color: '#94a3b8', fontFamily: 'monospace' }}>${plan.exit_price!.toFixed(2)}</span></div>
                  {plan.entry_price != null && <div>Entry <span style={{ color: '#94a3b8', fontFamily: 'monospace' }}>${plan.entry_price.toFixed(2)}</span></div>}
                </div>
                {plan.take_profit != null && plan.entry_price != null && plan.entry_price > 0 && plan.take_profit !== plan.entry_price && plan.exit_price != null && (
                  <div style={{ marginLeft: 'auto', fontSize: '10px', color: '#475569' }}>
                    {((plan.exit_price! - plan.entry_price) / (plan.take_profit - plan.entry_price) * 100).toFixed(0)}% of target
                  </div>
                )}
              </div>
            ) : (
              <div>
                <div style={{ fontSize: '10px', color: '#475569', marginBottom: '5px' }}>Record exit price to track P&L</div>
                <div style={{ display: 'flex', gap: '5px', alignItems: 'center' }}>
                  <input
                    type="number"
                    placeholder="Exit price"
                    value={exitInput}
                    onChange={e => setExitInput(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && saveExitPrice()}
                    style={{ width: '100px', padding: '3px 8px', borderRadius: '5px', border: '1px solid #1e293b', background: '#0b1020', color: '#e2e8f0', fontSize: '12px', fontFamily: 'monospace' }}
                  />
                  <button
                    onClick={saveExitPrice}
                    disabled={savingExit || !exitInput}
                    style={{ padding: '3px 10px', borderRadius: '5px', fontSize: '11px', fontWeight: 600, border: '1px solid rgba(99,102,241,0.4)', background: 'rgba(99,102,241,0.1)', color: '#818cf8', cursor: 'pointer' }}
                  >
                    {savingExit ? '…' : 'Save'}
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Active: distance from key levels */}
        {plan.stage === 'active' && livePrice && (plan.entry_price != null || plan.stop_loss != null || plan.take_profit != null) && (() => {
          const cur = livePrice.price;
          const entry = plan.entry_price;
          const stop = plan.stop_loss ?? (gp?.stop_loss?.price ?? null);
          const target = plan.take_profit ?? (gp?.take_profit?.price ?? null);
          const pct = (ref: number) => ((cur - ref) / ref * 100);
          return (
            <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginBottom: '8px', padding: '7px 10px', borderRadius: '7px', background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.04)' }}>
              {entry != null && (
                <div style={{ fontSize: '11px' }}>
                  <span style={{ color: '#475569' }}>vs Entry </span>
                  <span style={{ color: pct(entry) >= 0 ? '#4ade80' : '#f87171', fontWeight: 700 }}>
                    {pct(entry) >= 0 ? '+' : ''}{pct(entry).toFixed(1)}%
                  </span>
                </div>
              )}
              {stop != null && (
                <div style={{ fontSize: '11px' }}>
                  <span style={{ color: '#475569' }}>vs Stop </span>
                  <span style={{ color: pct(stop) >= 0 ? '#4ade80' : '#f87171', fontWeight: 700 }}>
                    {pct(stop) >= 0 ? '+' : ''}{pct(stop).toFixed(1)}%
                  </span>
                </div>
              )}
              {target != null && (
                <div style={{ fontSize: '11px' }}>
                  <span style={{ color: '#475569' }}>vs Target </span>
                  <span style={{ color: '#94a3b8', fontWeight: 700 }}>
                    {pct(target) >= 0 ? '+' : ''}{pct(target).toFixed(1)}%
                  </span>
                </div>
              )}
            </div>
          );
        })()}

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
            {plan.stage === 'planning' && (
              <Link
                href={`/research/${plan.symbol}`}
                style={{ padding: '2px 8px', borderRadius: '4px', fontSize: '10px', fontWeight: 600, cursor: 'pointer', border: '1px solid rgba(74,222,128,0.4)', background: 'rgba(74,222,128,0.1)', color: '#4ade80', marginLeft: '2px', textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: '3px' }}
              >
                Research
              </Link>
            )}
            {plan.stage === 'active' && (
              <button
                onClick={() => setAlertOpen(o => !o)}
                style={{ padding: '2px 8px', borderRadius: '4px', fontSize: '10px', fontWeight: 600, cursor: 'pointer', border: `1px solid ${hasAlerts ? 'rgba(251,191,36,0.4)' : '#1e293b'}`, background: hasAlerts ? 'rgba(251,191,36,0.1)' : 'transparent', color: hasAlerts ? '#fbbf24' : '#475569', marginLeft: '2px' }}
              >
                🔔 {hasAlerts ? `Alerts (${alertCount})` : 'Set Alerts'}
              </button>
            )}
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

  // Fetch live prices for board symbols only, refresh every 60 s
  const boardSymbols = useMemo(() => [...new Set((data ?? []).map(p => p.symbol))], [data]);
  const { data: livePrices } = useSWR(
    boardSymbols.length > 0 ? ['board-live-prices', boardSymbols.join(',')] : null,
    () => api.latestPricesFor(boardSymbols),
    { refreshInterval: 60_000, revalidateOnFocus: false },
  );
  const livePriceMap = useMemo(() => {
    const m: Record<string, { price: number; change_pct: number | null }> = {};
    for (const lp of livePrices ?? []) m[lp.symbol] = { price: lp.price, change_pct: lp.change_pct };
    return m;
  }, [livePrices]);

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

  // Performance summary from closed trades with both entry + exit price
  const perfStats = useMemo(() => {
    const closed = byStage.closed.filter(p => p.entry_price != null && p.exit_price != null && p.entry_price > 0);
    if (closed.length === 0) return null;
    const returns = closed.map(p => ((p.exit_price! - p.entry_price!) / p.entry_price!) * 100);
    const wins = returns.filter(r => r > 0).length;
    const avgReturn = returns.reduce((a, b) => a + b, 0) / returns.length;
    const best = Math.max(...returns);
    const worst = Math.min(...returns);
    return { count: closed.length, winRate: (wins / closed.length) * 100, avgReturn, best, worst };
  }, [byStage.closed]);

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

      {/* Performance summary */}
      {perfStats && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px', marginBottom: '16px', padding: '10px 16px', borderRadius: '10px', background: '#080f1e', border: '1px solid #1e293b', alignItems: 'center' }}>
          <span style={{ fontSize: '11px', fontWeight: 700, color: '#475569', marginRight: '4px' }}>Track Record</span>
          <Stat label="Closed" value={`${perfStats.count}`} color="#94a3b8" />
          <Stat label="Win Rate" value={`${perfStats.winRate.toFixed(0)}%`} color={perfStats.winRate >= 50 ? '#4ade80' : '#f87171'} />
          <Stat label="Avg Return" value={`${perfStats.avgReturn >= 0 ? '+' : ''}${perfStats.avgReturn.toFixed(1)}%`} color={perfStats.avgReturn >= 0 ? '#4ade80' : '#f87171'} />
          <Stat label="Best" value={`+${perfStats.best.toFixed(1)}%`} color="#4ade80" />
          <Stat label="Worst" value={`${perfStats.worst.toFixed(1)}%`} color="#f87171" />
        </div>
      )}

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
                      livePrice={livePriceMap[plan.symbol] ?? null}
                      onStageChange={handleStageChange}
                      onDelete={handleDelete}
                      onAlertsChange={handleAlertsChange}
                      onExitSaved={mutate}
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
