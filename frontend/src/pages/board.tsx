import { useState, useMemo, useEffect } from 'react';
import { createPortal } from 'react-dom';
import useSWR from 'swr';
import Link from 'next/link';
import { api, type TradePlan, type PriceAlert, type SignalAlertItem } from '@/lib/api';
import { getSignalStyle } from '@/lib/settings';
import { getUsername } from '@/lib/auth';

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

/* ── Fill Modal ──────────────────────────────────────────── */
function FillModal({ defaultPrice, onConfirm, onSkip }: {
  defaultPrice: number | null;
  onConfirm: (fillPrice: number, shares: number | null) => void;
  onSkip: () => void;
}) {
  const [fillInput, setFillInput] = useState(defaultPrice != null ? defaultPrice.toFixed(2) : '');
  const [sharesInput, setSharesInput] = useState('');

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onSkip(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onSkip]);

  function handleConfirm() {
    const price = parseFloat(fillInput);
    if (isNaN(price) || price <= 0) return;
    const shares = sharesInput ? parseFloat(sharesInput) : null;
    onConfirm(price, shares && !isNaN(shares) && shares > 0 ? shares : null);
  }

  const modal = (
    <div
      onClick={onSkip}
      style={{ position: 'fixed', inset: 0, zIndex: 800, background: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{ background: '#0d1424', border: '1px solid rgba(74,222,128,0.3)', borderRadius: '14px', padding: '24px 28px', width: '340px', boxShadow: '0 24px 60px rgba(0,0,0,0.7)' }}
      >
        <div style={{ fontSize: '15px', fontWeight: 800, color: '#e2e8f0', marginBottom: '4px' }}>Record Fill</div>
        <div style={{ fontSize: '12px', color: '#475569', marginBottom: '20px' }}>What price did you actually buy at?</div>

        <label style={{ display: 'block', fontSize: '11px', color: '#64748b', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '6px' }}>Fill Price *</label>
        <input
          autoFocus
          type="number"
          step="0.01"
          placeholder="e.g. 151.50"
          value={fillInput}
          onChange={e => setFillInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleConfirm()}
          style={{ width: '100%', padding: '9px 12px', borderRadius: '8px', border: '1px solid #334155', background: '#060814', color: '#f1f5f9', fontSize: '14px', fontFamily: 'ui-monospace, monospace', outline: 'none', boxSizing: 'border-box', marginBottom: '14px' }}
        />

        <label style={{ display: 'block', fontSize: '11px', color: '#64748b', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '6px' }}>Shares <span style={{ color: '#334155', fontWeight: 400 }}>(optional — enables $ P&L)</span></label>
        <input
          type="number"
          step="1"
          placeholder="e.g. 50"
          value={sharesInput}
          onChange={e => setSharesInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleConfirm()}
          style={{ width: '100%', padding: '9px 12px', borderRadius: '8px', border: '1px solid #1e293b', background: '#060814', color: '#f1f5f9', fontSize: '14px', fontFamily: 'ui-monospace, monospace', outline: 'none', boxSizing: 'border-box', marginBottom: '20px' }}
        />

        <div style={{ display: 'flex', gap: '8px' }}>
          <button
            onClick={handleConfirm}
            disabled={!fillInput || isNaN(parseFloat(fillInput))}
            style={{ flex: 1, padding: '9px', borderRadius: '8px', border: '1px solid rgba(74,222,128,0.3)', background: fillInput ? 'rgba(74,222,128,0.15)' : '#1e293b', color: fillInput ? '#4ade80' : '#334155', fontWeight: 700, fontSize: '13px', cursor: fillInput ? 'pointer' : 'default' }}
          >
            Confirm Fill
          </button>
          <button
            onClick={onSkip}
            style={{ padding: '9px 16px', borderRadius: '8px', border: '1px solid #1e293b', background: 'transparent', color: '#475569', fontSize: '13px', cursor: 'pointer' }}
          >
            Skip
          </button>
        </div>
      </div>
    </div>
  );

  if (typeof document === 'undefined') return null;
  return createPortal(modal, document.body);
}

function PlanCard({ plan, priceAlerts, signalAlert, livePrice, onStageChange, onDelete, onAlertsChange, onExitSaved, onDragStart }: {
  plan: TradePlan;
  priceAlerts: PriceAlert[];
  signalAlert: SignalAlertItem | null;
  livePrice: { price: number; change_pct: number | null } | null;
  onStageChange: (id: number, stage: Stage) => void;
  onDelete: (id: number) => void;
  onAlertsChange: () => void;
  onExitSaved: () => void;
  onDragStart: (id: number) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [alertOpen, setAlertOpen] = useState(false);
  const [exitInput, setExitInput] = useState('');
  const [savingExit, setSavingExit] = useState(false);
  const [editingActive, setEditingActive] = useState(false);
  const [editShares, setEditShares] = useState('');
  const [editFillPrice, setEditFillPrice] = useState('');
  const [editStop, setEditStop] = useState('');
  const [editTarget, setEditTarget] = useState('');
  const [savingEdit, setSavingEdit] = useState(false);
  const meta = STAGE_META[plan.stage as Stage] ?? STAGE_META.watch;
  const gp = plan.game_plan as StoredGamePlan | null;

  const effectiveEntry = plan.actual_entry_price ?? plan.entry_price;
  const pnlPct = plan.exit_price != null && effectiveEntry != null && effectiveEntry > 0
    ? ((plan.exit_price - effectiveEntry) / effectiveEntry) * 100
    : null;
  const dollarPnl = plan.exit_price != null && effectiveEntry != null && plan.shares != null
    ? (plan.exit_price - effectiveEntry) * plan.shares
    : null;

  async function saveExitPrice() {
    const val = parseFloat(exitInput);
    if (isNaN(val) || val <= 0) return;
    setSavingExit(true);
    try {
      await api.updateBoardPlan(plan.id, { exit_price: val });
      // Sync SELL to positions when shares are known
      if (plan.shares != null && plan.shares > 0) {
        try {
          const positions = await api.listPositions();
          const existing = positions.find(p => p.symbol === plan.symbol);
          if (existing && existing.shares > 0) {
            await api.sellPosition(existing.id, { shares: Math.min(plan.shares, existing.shares), price: val });
          }
        } catch { /* best-effort */ }
      }
      setExitInput('');
      onExitSaved();
    } finally {
      setSavingExit(false);
    }
  }

  function openActiveEdit() {
    setEditShares(plan.shares != null ? String(plan.shares) : '');
    setEditFillPrice(plan.actual_entry_price != null ? plan.actual_entry_price.toFixed(2) : '');
    setEditStop(plan.stop_loss != null ? plan.stop_loss.toFixed(2) : '');
    setEditTarget(plan.take_profit != null ? plan.take_profit.toFixed(2) : '');
    setEditingActive(true);
  }

  async function saveActiveEdit() {
    setSavingEdit(true);
    try {
      const updates: Record<string, number> = {};
      const s = parseFloat(editShares);
      const p = parseFloat(editFillPrice);
      const stop = parseFloat(editStop);
      const target = parseFloat(editTarget);
      if (!isNaN(s) && s > 0) updates.shares = s;
      if (!isNaN(p) && p > 0) updates.actual_entry_price = p;
      if (!isNaN(stop) && stop > 0) updates.stop_loss = stop;
      if (!isNaN(target) && target > 0) updates.take_profit = target;
      if (Object.keys(updates).length > 0) await api.updateBoardPlan(plan.id, updates);
      // Sync shares delta to positions
      const newShares = !isNaN(s) && s > 0 ? s : null;
      const oldShares = plan.shares;
      const fillPrice = (!isNaN(p) && p > 0 ? p : null) ?? plan.actual_entry_price;
      if (newShares != null && oldShares != null && newShares !== oldShares && fillPrice != null) {
        try {
          const positions = await api.listPositions();
          const existing = positions.find(pos => pos.symbol === plan.symbol);
          if (existing) {
            const delta = newShares - oldShares;
            if (delta > 0) await api.buyMorePosition(existing.id, { shares: delta, price: fillPrice });
            else await api.sellPosition(existing.id, { shares: Math.abs(delta), price: fillPrice });
          }
        } catch { /* best-effort */ }
      }
      setEditingActive(false);
      onExitSaved();
    } finally {
      setSavingEdit(false);
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
    <div
      draggable
      onDragStart={e => { e.dataTransfer.effectAllowed = 'move'; onDragStart(plan.id); }}
      style={{ borderRadius: '10px', border: `1px solid ${meta.border}`, background: '#0f172a', overflow: 'hidden', marginBottom: '8px', cursor: 'grab' }}
    >
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
        <div style={{ marginBottom: '8px' }}>
          {plan.stage === 'active' && editingActive ? (
            <div style={{ padding: '10px 12px', borderRadius: '8px', background: 'rgba(99,102,241,0.06)', border: '1px solid rgba(99,102,241,0.2)' }}>
              <div style={{ fontSize: '10px', color: '#64748b', fontWeight: 700, letterSpacing: '0.06em', marginBottom: '8px', textTransform: 'uppercase' }}>Edit Position</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px', marginBottom: '8px' }}>
                <div>
                  <label style={{ fontSize: '10px', color: '#475569', display: 'block', marginBottom: '3px' }}>Shares</label>
                  <input type="number" step="1" value={editShares} onChange={e => setEditShares(e.target.value)}
                    style={{ width: '100%', padding: '5px 8px', borderRadius: '5px', border: '1px solid #334155', background: '#060814', color: '#f1f5f9', fontSize: '12px', fontFamily: 'monospace', boxSizing: 'border-box' }} />
                </div>
                <div>
                  <label style={{ fontSize: '10px', color: '#475569', display: 'block', marginBottom: '3px' }}>Fill Price</label>
                  <input type="number" step="0.01" value={editFillPrice} onChange={e => setEditFillPrice(e.target.value)}
                    style={{ width: '100%', padding: '5px 8px', borderRadius: '5px', border: '1px solid #334155', background: '#060814', color: '#4ade80', fontSize: '12px', fontFamily: 'monospace', boxSizing: 'border-box' }} />
                </div>
                <div>
                  <label style={{ fontSize: '10px', color: '#475569', display: 'block', marginBottom: '3px' }}>Stop Loss</label>
                  <input type="number" step="0.01" value={editStop} onChange={e => setEditStop(e.target.value)}
                    style={{ width: '100%', padding: '5px 8px', borderRadius: '5px', border: '1px solid #334155', background: '#060814', color: '#f87171', fontSize: '12px', fontFamily: 'monospace', boxSizing: 'border-box' }} />
                </div>
                <div>
                  <label style={{ fontSize: '10px', color: '#475569', display: 'block', marginBottom: '3px' }}>Take Profit</label>
                  <input type="number" step="0.01" value={editTarget} onChange={e => setEditTarget(e.target.value)}
                    style={{ width: '100%', padding: '5px 8px', borderRadius: '5px', border: '1px solid #334155', background: '#060814', color: '#4ade80', fontSize: '12px', fontFamily: 'monospace', boxSizing: 'border-box' }} />
                </div>
              </div>
              <div style={{ display: 'flex', gap: '6px' }}>
                <button onClick={saveActiveEdit} disabled={savingEdit}
                  style={{ flex: 1, padding: '5px', borderRadius: '5px', border: '1px solid rgba(99,102,241,0.4)', background: 'rgba(99,102,241,0.12)', color: '#818cf8', fontSize: '11px', fontWeight: 700, cursor: 'pointer' }}>
                  {savingEdit ? '…' : 'Save'}
                </button>
                <button onClick={() => setEditingActive(false)}
                  style={{ padding: '5px 12px', borderRadius: '5px', border: '1px solid #1e293b', background: 'transparent', color: '#475569', fontSize: '11px', cursor: 'pointer' }}>
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
              {plan.actual_entry_price != null && (
                <div style={{ fontSize: '11px' }}>
                  <span style={{ color: '#475569' }}>Fill </span>
                  <span style={{ color: '#4ade80', fontWeight: 700, fontFamily: 'monospace' }}>{fmt(plan.actual_entry_price)}</span>
                  {plan.shares != null && <span style={{ color: '#334155' }}> × {plan.shares}</span>}
                </div>
              )}
              {plan.entry_price != null && (
                <div style={{ fontSize: '11px' }}>
                  <span style={{ color: '#475569' }}>{plan.actual_entry_price != null ? 'Plan ' : 'Entry '}</span>
                  <span style={{ color: plan.actual_entry_price != null ? '#334155' : '#818cf8', fontWeight: 700, fontFamily: 'monospace' }}>{fmt(plan.entry_price)}</span>
                </div>
              )}
              {(plan.stop_loss != null || gp?.stop_loss?.price != null) && (
                <div style={{ fontSize: '11px' }}>
                  <span style={{ color: '#475569' }}>Stop </span>
                  <span style={{ color: '#f87171', fontWeight: 700, fontFamily: 'monospace' }}>
                    {fmt(plan.stop_loss ?? gp!.stop_loss!.price)}
                  </span>
                  {plan.stop_loss == null && gp?.stop_loss?.price != null && (
                    <span style={{ fontSize: '9px', color: '#334155', marginLeft: '3px' }}>gp</span>
                  )}
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
              {plan.stage === 'active' && (
                <button onClick={openActiveEdit} title="Edit shares / prices"
                  style={{ marginLeft: 'auto', background: 'none', border: 'none', color: '#334155', cursor: 'pointer', fontSize: '13px', padding: '0 2px', lineHeight: 1, flexShrink: 0 }}>
                  ✎
                </button>
              )}
            </div>
          )}
        </div>

        {/* Closed: P&L outcome */}
        {plan.stage === 'closed' && (
          <div style={{ marginBottom: '8px', padding: '8px 10px', borderRadius: '7px', background: pnlPct != null ? (pnlPct >= 0 ? 'rgba(74,222,128,0.07)' : 'rgba(239,68,68,0.07)') : 'rgba(255,255,255,0.02)', border: `1px solid ${pnlPct != null ? (pnlPct >= 0 ? 'rgba(74,222,128,0.2)' : 'rgba(239,68,68,0.2)') : 'rgba(255,255,255,0.04)'}` }}>
            {pnlPct != null ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <div>
                  <span style={{ fontSize: '20px', fontWeight: 900, fontFamily: 'monospace', color: pnlPct >= 0 ? '#4ade80' : '#f87171' }}>
                    {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%
                  </span>
                  {dollarPnl != null && (
                    <div style={{ fontSize: '11px', fontFamily: 'monospace', color: dollarPnl >= 0 ? '#4ade80' : '#f87171', opacity: 0.8 }}>
                      {dollarPnl >= 0 ? '+' : ''}${Math.abs(dollarPnl).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </div>
                  )}
                </div>
                <div style={{ fontSize: '11px', color: '#475569' }}>
                  <div>Exit <span style={{ color: '#94a3b8', fontFamily: 'monospace' }}>${plan.exit_price!.toFixed(2)}</span></div>
                  {effectiveEntry != null && (
                    <div>
                      {plan.actual_entry_price != null ? 'Fill' : 'Entry'}{' '}
                      <span style={{ color: '#94a3b8', fontFamily: 'monospace' }}>${effectiveEntry.toFixed(2)}</span>
                    </div>
                  )}
                </div>
                <div style={{ marginLeft: 'auto', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '4px' }}>
                  {plan.take_profit != null && effectiveEntry != null && effectiveEntry > 0 && plan.take_profit !== effectiveEntry && plan.exit_price != null && (
                    <div style={{ fontSize: '10px', color: '#475569' }}>
                      {((plan.exit_price! - effectiveEntry) / (plan.take_profit - effectiveEntry) * 100).toFixed(0)}% of target
                    </div>
                  )}
                  {plan.trading_style && (
                    <div style={{
                      fontSize: '9px', fontWeight: 800, padding: '2px 6px', borderRadius: '4px',
                      letterSpacing: '0.06em',
                      color: plan.trading_style === 'SHORT' ? '#f87171' : plan.trading_style === 'LONG' ? '#4ade80' : '#818cf8',
                      background: plan.trading_style === 'SHORT' ? 'rgba(248,113,113,0.12)' : plan.trading_style === 'LONG' ? 'rgba(74,222,128,0.12)' : 'rgba(129,140,248,0.12)',
                      border: `1px solid ${plan.trading_style === 'SHORT' ? 'rgba(248,113,113,0.3)' : plan.trading_style === 'LONG' ? 'rgba(74,222,128,0.3)' : 'rgba(129,140,248,0.3)'}`,
                    }}>
                      {plan.trading_style}
                    </div>
                  )}
                </div>
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
        {plan.stage === 'active' && livePrice && (effectiveEntry != null || plan.stop_loss != null || plan.take_profit != null) && (() => {
          const cur = livePrice.price;
          const entry = effectiveEntry;
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

        {/* Active: position monitor — stop alerts, P&L, trail recommendations */}
        {plan.stage === 'active' && livePrice && effectiveEntry != null && (() => {
          const cur = livePrice.price;
          const stop = plan.stop_loss ?? gp?.stop_loss?.price ?? null;
          const target = plan.take_profit ?? gp?.take_profit?.price ?? null;
          const shares = plan.shares;
          const pnlPct = (cur - effectiveEntry) / effectiveEntry * 100;
          const dollarPnl = shares != null ? (cur - effectiveEntry) * shares : null;
          const dollarRisk = shares != null && stop != null ? (effectiveEntry - stop) * shares : null;

          const stopBreached = stop != null && cur < stop;
          const nearStop = !stopBreached && stop != null && cur <= stop * 1.02;
          const nearTarget = target != null && cur >= target * 0.98;
          const daysInTrade = Math.floor((Date.now() - new Date(plan.created_at).getTime()) / 86400000);
          const stalled = daysInTrade > 15 && Math.abs(pnlPct) < 3;

          const breakEvenSuggestion = pnlPct >= 3 && pnlPct < 5 && stop != null && stop < effectiveEntry;
          const trailSuggestion = pnlPct >= 5;
          const suggestedStop = trailSuggestion ? (cur * 0.97).toFixed(2) : null;

          const hasMonitor = stopBreached || nearStop || nearTarget || stalled || dollarPnl != null || breakEvenSuggestion || trailSuggestion;
          if (!hasMonitor) return null;

          return (
            <div style={{ marginBottom: '8px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
              {stopBreached && stop != null && (
                <div style={{ padding: '5px 10px', borderRadius: '6px', background: 'rgba(239,68,68,0.15)', border: '1px solid rgba(239,68,68,0.4)', color: '#f87171', fontSize: '11px', fontWeight: 700, display: 'flex', gap: '6px', alignItems: 'center' }}>
                  <span>⚠</span><span>STOP BREACHED — ${cur.toFixed(2)} vs stop ${stop.toFixed(2)}</span>
                </div>
              )}
              {nearStop && stop != null && (
                <div style={{ padding: '4px 10px', borderRadius: '6px', background: 'rgba(251,191,36,0.1)', border: '1px solid rgba(251,191,36,0.3)', color: '#fbbf24', fontSize: '10px', fontWeight: 600 }}>
                  Near stop — {((cur / stop - 1) * 100).toFixed(1)}% above stop loss
                </div>
              )}
              {nearTarget && target != null && (
                <div style={{ padding: '4px 10px', borderRadius: '6px', background: 'rgba(74,222,128,0.08)', border: '1px solid rgba(74,222,128,0.25)', color: '#4ade80', fontSize: '10px', fontWeight: 600 }}>
                  Near target — consider scaling out 50%
                </div>
              )}
              {stalled && (
                <div style={{ padding: '4px 10px', borderRadius: '6px', background: 'rgba(148,163,184,0.05)', border: '1px solid rgba(148,163,184,0.12)', color: '#475569', fontSize: '10px', fontWeight: 600 }}>
                  ⏱ Stalled {daysInTrade}d — consider exiting if thesis not playing out
                </div>
              )}
              {(dollarPnl != null || (dollarRisk != null && dollarRisk > 0) || breakEvenSuggestion || trailSuggestion) && (
                <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', padding: '5px 10px', borderRadius: '6px', background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.04)', fontSize: '11px', alignItems: 'center' }}>
                  {dollarPnl != null && (
                    <div>
                      <span style={{ color: '#475569' }}>P&amp;L </span>
                      <span style={{ color: dollarPnl >= 0 ? '#4ade80' : '#f87171', fontWeight: 700, fontFamily: 'monospace' }}>
                        {dollarPnl >= 0 ? '+' : ''}${Math.abs(dollarPnl).toFixed(0)}
                      </span>
                    </div>
                  )}
                  {dollarRisk != null && dollarRisk > 0 && (
                    <div>
                      <span style={{ color: '#475569' }}>Risk </span>
                      <span style={{ color: '#f87171', fontWeight: 700, fontFamily: 'monospace' }}>-${dollarRisk.toFixed(0)}</span>
                    </div>
                  )}
                  {breakEvenSuggestion && (
                    <span style={{ color: '#fbbf24', fontSize: '10px', fontWeight: 600 }}>
                      ↑ Move stop to breakeven (${effectiveEntry.toFixed(2)})
                    </span>
                  )}
                  {trailSuggestion && suggestedStop != null && (
                    <span style={{ color: '#fbbf24', fontSize: '10px', fontWeight: 600 }}>
                      ↑ Trail stop to ${suggestedStop}
                    </span>
                  )}
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
            {(plan.stage === 'planning' || plan.stage === 'active') && (
              <Link
                href={`/research/${plan.symbol}`}
                style={{ padding: '2px 8px', borderRadius: '4px', fontSize: '10px', fontWeight: 600, cursor: 'pointer', border: '1px solid rgba(74,222,128,0.4)', background: 'rgba(74,222,128,0.1)', color: '#4ade80', marginLeft: '2px', textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: '3px' }}
              >
                Research
              </Link>
            )}
            {plan.stage === 'active' && plan.trading_style && (() => {
              const maxDays: Record<string, number> = { SHORT: 5, SWING: 14, LONG: 30, GROWTH: 20 };
              const max = maxDays[plan.trading_style] ?? 14;
              const daysHeld = Math.floor((Date.now() - new Date(plan.updated_at).getTime()) / 86400000);
              const pct = Math.min(1, daysHeld / max);
              const color = pct >= 0.9 ? '#f87171' : pct >= 0.7 ? '#fbbf24' : '#475569';
              return (
                <span title={`~${daysHeld}d in trade · ${plan.trading_style} max ~${max}d`}
                  style={{ fontSize: '9px', fontWeight: 700, color, padding: '2px 6px', borderRadius: '4px', marginLeft: '2px', background: pct >= 0.9 ? 'rgba(248,113,113,0.1)' : 'transparent', border: pct >= 0.9 ? '1px solid rgba(248,113,113,0.25)' : '1px solid transparent' }}>
                  {daysHeld}d
                </span>
              );
            })()}
            {plan.stage === 'active' && (
              <button
                onClick={() => setAlertOpen(o => !o)}
                style={{ padding: '2px 8px', borderRadius: '4px', fontSize: '10px', fontWeight: 600, cursor: 'pointer', border: `1px solid ${hasAlerts ? 'rgba(251,191,36,0.4)' : '#1e293b'}`, background: hasAlerts ? 'rgba(251,191,36,0.1)' : 'transparent', color: hasAlerts ? '#fbbf24' : '#475569', marginLeft: '2px' }}
              >
                🔔 {hasAlerts ? `Alerts (${alertCount})` : 'Set Alerts'}
              </button>
            )}
            {plan.stage === 'active' && plan.trading_style && (
              <span style={{
                fontSize: '9px', fontWeight: 800, padding: '2px 6px', borderRadius: '4px', letterSpacing: '0.06em', marginLeft: '2px',
                color: plan.trading_style === 'SHORT' ? '#f87171' : plan.trading_style === 'LONG' ? '#4ade80' : plan.trading_style === 'GROWTH' ? '#a78bfa' : '#818cf8',
                background: plan.trading_style === 'SHORT' ? 'rgba(248,113,113,0.1)' : plan.trading_style === 'LONG' ? 'rgba(74,222,128,0.1)' : plan.trading_style === 'GROWTH' ? 'rgba(167,139,250,0.1)' : 'rgba(129,140,248,0.1)',
                border: `1px solid ${plan.trading_style === 'SHORT' ? 'rgba(248,113,113,0.25)' : plan.trading_style === 'LONG' ? 'rgba(74,222,128,0.25)' : plan.trading_style === 'GROWTH' ? 'rgba(167,139,250,0.25)' : 'rgba(129,140,248,0.25)'}`,
              }}>
                {plan.trading_style}
              </span>
            )}
            {plan.stage === 'active' && signalAlert?.last_signal && (
              <span style={{
                fontSize: '9px', fontWeight: 700, padding: '2px 6px', borderRadius: '4px', letterSpacing: '0.04em', marginLeft: '2px',
                color: signalAlert.last_signal === 'BUY' ? '#4ade80' : signalAlert.last_signal === 'SELL' ? '#f87171' : signalAlert.last_signal === 'WAIT' ? '#fbbf24' : '#94a3b8',
                background: signalAlert.last_signal === 'BUY' ? 'rgba(74,222,128,0.1)' : signalAlert.last_signal === 'SELL' ? 'rgba(239,68,68,0.1)' : signalAlert.last_signal === 'WAIT' ? 'rgba(251,191,36,0.1)' : 'rgba(148,163,184,0.08)',
                border: `1px solid ${signalAlert.last_signal === 'BUY' ? 'rgba(74,222,128,0.3)' : signalAlert.last_signal === 'SELL' ? 'rgba(239,68,68,0.3)' : signalAlert.last_signal === 'WAIT' ? 'rgba(251,191,36,0.3)' : 'rgba(148,163,184,0.15)'}`,
              }}>
                {signalAlert.last_signal}
              </span>
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
  const u = getUsername();
  const { data, mutate, isLoading, error } = useSWR<TradePlan[]>(`${u}:board`, () => api.listBoard(), { revalidateOnFocus: false });
  const { data: priceAlerts, mutate: mutateAlerts } = useSWR<PriceAlert[]>(`${u}:alerts`, () => api.listAlerts(), { revalidateOnFocus: false });
  const { data: signalAlerts, mutate: mutateSignalAlerts } = useSWR<SignalAlertItem[]>(`${u}:signal-alerts`, () => api.listSignalAlerts(), { revalidateOnFocus: false });
  const [market, setMarket] = useState<MarketFilter>('US');
  const [dragId, setDragId] = useState<number | null>(null);
  const [dragOverStage, setDragOverStage] = useState<Stage | null>(null);
  type FillTarget = { id: number; defaultPrice: number | null };
  const [fillTarget, setFillTarget] = useState<FillTarget | null>(null);
  const [closeConfirmId, setCloseConfirmId] = useState<number | null>(null);
  const [closeExitInput, setCloseExitInput] = useState('');
  const [fillSyncMsg, setFillSyncMsg] = useState('');

  // Fetch live prices for board symbols only, refresh every 60 s
  const boardSymbols = useMemo(() => [...new Set((data ?? []).map(p => p.symbol))], [data]);
  const { data: livePrices } = useSWR(
    boardSymbols.length > 0 ? [`${u}:board-live-prices`, boardSymbols.join(',')] : null,
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
    if (stage === 'active') {
      const plan = (data ?? []).find(p => p.id === id);
      setFillTarget({ id, defaultPrice: livePriceMap[plan?.symbol ?? '']?.price ?? plan?.entry_price ?? null });
      return;
    }
    // UI-5: confirm before marking a trade closed (irreversible PnL record)
    if (stage === 'closed') {
      setCloseConfirmId(id);
      return;
    }
    await api.updateBoardPlan(id, { stage });
    mutate();
  }

  async function handleCloseConfirmed() {
    if (closeConfirmId == null) return;
    const closePlan = (data ?? []).find(p => p.id === closeConfirmId);
    const exitPrice = parseFloat(closeExitInput);
    const updates: Record<string, unknown> = { stage: 'closed' };
    if (!isNaN(exitPrice) && exitPrice > 0) updates.exit_price = exitPrice;
    await api.updateBoardPlan(closeConfirmId, updates);
    // Remove from Positions and credit cash proceeds if the card has a tracked position
    if (closePlan?.symbol) {
      try {
        const positions = await api.listPositions();
        const existing = positions.find(p => p.symbol === closePlan.symbol.toUpperCase());
        if (existing) {
          const isHK = /\.(HK|hk)$/.test(closePlan.symbol) || /^\d{4,5}$/.test(closePlan.symbol);
          const currency: 'USD' | 'HKD' = isHK ? 'HKD' : 'USD';
          if (!isNaN(exitPrice) && exitPrice > 0 && (closePlan.shares ?? 0) > 0) {
            const soldShares = Math.min(closePlan.shares!, existing.shares);
            await api.sellPosition(existing.id, { shares: soldShares, price: exitPrice });
            // Credit proceeds back to cash balance
            try {
              const currentCash = await api.getCash();
              await api.updateCash({
                ...currentCash,
                [currency]: Math.max(0, (currentCash[currency] ?? 0) + soldShares * exitPrice),
              });
            } catch { /* best-effort cash update */ }
          } else {
            await api.removePosition(existing.id);
          }
        }
      } catch { /* best-effort */ }
    }
    setCloseConfirmId(null);
    setCloseExitInput('');
    mutate();
  }

  async function handleFillConfirm(fillPrice: number, shares: number | null) {
    if (!fillTarget) return;
    const activatingPlan = (data ?? []).find(p => p.id === fillTarget.id);
    await api.updateBoardPlan(fillTarget.id, {
      stage: 'active',
      actual_entry_price: fillPrice,
      trading_style: getSignalStyle(),
      ...(shares != null ? { shares } : {}),
    });
    // Auto-sync to Positions page when shares + fill price are provided
    if (shares != null && activatingPlan) {
      try {
        const positions = await api.listPositions();
        const existing = positions.find(p => p.symbol === activatingPlan.symbol.toUpperCase());
        const isHK = /\.(HK|hk)$/.test(activatingPlan.symbol) || /^\d{4,5}$/.test(activatingPlan.symbol);
        const currency: 'USD' | 'HKD' = isHK ? 'HKD' : 'USD';
        if (existing) {
          await api.buyMorePosition(existing.id, { shares, price: fillPrice });
        } else {
          await api.addPosition({ symbol: activatingPlan.symbol, shares, price: fillPrice, currency });
        }
        // Debit purchase cost from cash balance
        try {
          const currentCash = await api.getCash();
          await api.updateCash({
            ...currentCash,
            [currency]: Math.max(0, (currentCash[currency] ?? 0) - shares * fillPrice),
          });
        } catch { /* best-effort cash debit */ }
        setFillSyncMsg(`✓ Added to Positions (${shares} shares @ ${fillPrice})`);
      } catch {
        setFillSyncMsg('⚠ Position sync failed — add manually in Positions');
      }
      setTimeout(() => setFillSyncMsg(''), 6000);
    } else if (shares == null) {
      setFillSyncMsg('No shares entered — open Positions to add manually');
      setTimeout(() => setFillSyncMsg(''), 5000);
    }
    setFillTarget(null);
    mutate();
  }

  async function handleFillSkip() {
    if (!fillTarget) return;
    await api.updateBoardPlan(fillTarget.id, { stage: 'active', trading_style: getSignalStyle() });
    setFillTarget(null);
    mutate();
  }

  function handleDrop(stage: Stage) {
    if (dragId == null) return;
    handleStageChange(dragId, stage);
    setDragId(null);
    setDragOverStage(null);
  }

  async function handleDelete(id: number) {
    const plan = (data ?? []).find(p => p.id === id);
    await api.deleteBoardPlan(id);
    // If active with fill price → also remove from Positions
    if (plan?.stage === 'active' && plan.actual_entry_price != null) {
      try {
        const positions = await api.listPositions();
        const existing = positions.find(p => p.symbol === plan.symbol.toUpperCase());
        if (existing) await api.removePosition(existing.id);
      } catch { /* best-effort */ }
    }
    mutate();
  }

  async function handleAdd(symbol: string, notes: string) {
    await api.createBoardPlan({ symbol, stage: 'watch', notes: notes || null, source: 'manual', trading_style: getSignalStyle() });
    mutate();
  }

  const total = filtered.length;
  const usCnt = (data ?? []).filter(p => !isHK(p.symbol)).length;
  const hkCnt = (data ?? []).filter(p => isHK(p.symbol)).length;

  // Performance summary from closed trades with both entry + exit price
  const perfStats = useMemo(() => {
    const closed = byStage.closed.filter(p => p.exit_price != null && (p.actual_entry_price ?? p.entry_price) != null);
    if (closed.length === 0) return null;
    const returns = closed.map(p => {
      const eff = p.actual_entry_price ?? p.entry_price!;
      return ((p.exit_price! - eff) / eff) * 100;
    });
    const wins = returns.filter(r => r > 0).length;
    const avgReturn = returns.reduce((a, b) => a + b, 0) / returns.length;
    const best = Math.max(...returns);
    const worst = Math.min(...returns);

    // Per-style breakdown
    const styleColors: Record<string, string> = { SHORT: '#f87171', SWING: '#818cf8', LONG: '#4ade80', GROWTH: '#a78bfa', OTHER: '#64748b' };
    const knownStyles = ['SHORT', 'SWING', 'LONG', 'GROWTH'] as const;
    const styleBreakdown = ([...knownStyles, 'OTHER'] as string[])
      .map(style => {
        const group = style === 'OTHER'
          ? closed.filter(p => !p.trading_style || !knownStyles.includes(p.trading_style as typeof knownStyles[number]))
          : closed.filter(p => p.trading_style === style);
        if (group.length === 0) return null;
        const rets = group.map(p => {
          const eff = p.actual_entry_price ?? p.entry_price!;
          return ((p.exit_price! - eff) / eff) * 100;
        });
        const winRate = (rets.filter(r => r > 0).length / rets.length) * 100;
        const avg = rets.reduce((a, b) => a + b, 0) / rets.length;
        return { style, count: group.length, winRate, avg, color: styleColors[style] };
      })
      .filter(Boolean) as { style: string; count: number; winRate: number; avg: number; color: string }[];

    return { count: closed.length, winRate: (wins / closed.length) * 100, avgReturn, best, worst, styleBreakdown };
  }, [byStage.closed]);

  // Portfolio risk — on-demand only (can be slow with many positions)
  const [riskRequested, setRiskRequested] = useState(false);
  const riskPositions = useMemo(
    () => byStage.active.filter(p => p.shares != null && p.shares > 0 && (p.actual_entry_price ?? p.entry_price) != null),
    [byStage.active],
  );
  const riskSymbols = useMemo(() => riskPositions.map(p => p.symbol), [riskPositions]);
  const riskWeights = useMemo(
    () => riskPositions.map(p => p.shares! * (p.actual_entry_price ?? p.entry_price!)),
    [riskPositions],
  );
  const { data: riskData, isLoading: riskLoading } = useSWR(
    riskRequested && riskSymbols.length >= 2 ? [`${u}:portfolio-risk`, riskSymbols.join(','), riskWeights.join(',')] : null,
    () => api.portfolioRisk(riskSymbols, riskWeights),
    { revalidateOnFocus: false, dedupingInterval: 300_000 },
  );

  // Bulk sync: push all active cards → Positions (skips symbols already in positions)
  const [syncMsg, setSyncMsg] = useState<string | null>(null);
  async function syncActivePlans() {
    setSyncMsg('Syncing…');
    try {
      const activePlans = byStage.active.filter(
        p => (p.shares ?? 0) > 0 && (p.actual_entry_price ?? p.entry_price) != null
      );
      if (!activePlans.length) { setSyncMsg('No active cards with fill price/shares'); return; }
      const existingPositions = await api.listPositions();
      const existingSymbols = new Set(existingPositions.map(p => p.symbol.toUpperCase()));
      let added = 0;
      for (const plan of activePlans) {
        if (existingSymbols.has(plan.symbol.toUpperCase())) continue;
        const price = plan.actual_entry_price ?? plan.entry_price!;
        const currency = plan.symbol.endsWith('.HK') ? 'HKD' : 'USD';
        await api.addPosition({ symbol: plan.symbol, shares: plan.shares!, price, currency });
        existingSymbols.add(plan.symbol.toUpperCase());
        added++;
      }
      setSyncMsg(added > 0 ? `Synced ${added} position${added !== 1 ? 's' : ''}` : `Already in sync (${activePlans.length} cards)`);
    } catch { setSyncMsg('Sync failed'); }
    setTimeout(() => setSyncMsg(null), 5000);
  }

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px', flexWrap: 'wrap', gap: '12px' }}>
        <div>
          <h1 style={{ fontSize: '22px', fontWeight: 800, color: '#e2e8f0', marginBottom: '4px' }}>Trade Board</h1>
          <p style={{ fontSize: '12px', color: '#475569' }}>
            {total > 0 ? `${total} idea${total !== 1 ? 's' : ''} · drag cards between columns` : 'Save game plans and forecast picks here'}
          </p>
        </div>
        <div style={{ display: 'flex', gap: '8px', fontSize: '11px', color: '#334155', alignItems: 'center', flexWrap: 'wrap' }}>
          {STAGES.map(s => (
            <span key={s} style={{ padding: '4px 10px', borderRadius: '6px', background: STAGE_META[s].bg, border: `1px solid ${STAGE_META[s].border}`, color: STAGE_META[s].color, fontWeight: 600 }}>
              {STAGE_META[s].label} {byStage[s].length > 0 && `(${byStage[s].length})`}
            </span>
          ))}
          <button
            onClick={syncActivePlans}
            title="Push all active cards (with shares + fill price) to Positions — skips symbols already there"
            style={{ padding: '4px 10px', borderRadius: '6px', background: 'rgba(34,197,94,0.08)', border: '1px solid rgba(34,197,94,0.25)', color: '#4ade80', fontWeight: 600, cursor: 'pointer' }}
          >↑ Sync Active → Positions</button>
          {syncMsg && <span style={{ color: syncMsg.startsWith('Sync') && !syncMsg.includes('failed') ? '#4ade80' : '#f59e0b', fontSize: 11 }}>{syncMsg}</span>}
        </div>
      </div>

      {/* Performance summary */}
      {perfStats && (
        <div style={{ marginBottom: '16px', padding: '12px 16px', borderRadius: '10px', background: '#080f1e', border: '1px solid #1e293b' }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px', alignItems: 'center' }}>
            <span style={{ fontSize: '11px', fontWeight: 700, color: '#475569', marginRight: '4px' }}>Track Record</span>
            <Stat label="Closed" value={`${perfStats.count}`} color="#94a3b8" />
            <Stat label="Win Rate" value={`${perfStats.winRate.toFixed(0)}%`} color={perfStats.winRate >= 50 ? '#4ade80' : '#f87171'} />
            <Stat label="Avg Return" value={`${perfStats.avgReturn >= 0 ? '+' : ''}${perfStats.avgReturn.toFixed(1)}%`} color={perfStats.avgReturn >= 0 ? '#4ade80' : '#f87171'} />
            <Stat label="Best" value={`+${perfStats.best.toFixed(1)}%`} color="#4ade80" />
            <Stat label="Worst" value={`${perfStats.worst.toFixed(1)}%`} color="#f87171" />
          </div>
          {perfStats.styleBreakdown.length > 0 && (
            <div style={{ display: 'flex', gap: '8px', marginTop: '8px', paddingTop: '8px', borderTop: '1px solid #0f172a', flexWrap: 'wrap' }}>
              <span style={{ fontSize: '10px', fontWeight: 700, color: '#334155', alignSelf: 'center', marginRight: '2px' }}>BY STYLE</span>
              {perfStats.styleBreakdown.map(sb => (
                <div key={sb.style} style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '3px 10px', borderRadius: '6px', background: `${sb.color}08`, border: `1px solid ${sb.color}25` }}>
                  <span style={{ fontSize: '10px', fontWeight: 800, color: sb.color }}>{sb.style}</span>
                  <span style={{ fontSize: '10px', color: '#475569' }}>{sb.count} trade{sb.count !== 1 ? 's' : ''}</span>
                  <span style={{ fontSize: '10px', color: sb.winRate >= 50 ? '#4ade80' : '#f87171', fontWeight: 600 }}>{sb.winRate.toFixed(0)}% win</span>
                  <span style={{ fontSize: '10px', color: sb.avg >= 0 ? '#4ade80' : '#f87171', fontFamily: 'monospace' }}>{sb.avg >= 0 ? '+' : ''}{sb.avg.toFixed(1)}%</span>
                </div>
              ))}
            </div>
          )}
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

      {/* TB-5: Active positions live summary bar */}
      {byStage.active.length > 0 && (() => {
        const activeWithEntry = byStage.active.filter(p => (p.actual_entry_price ?? p.entry_price) != null);
        if (activeWithEntry.length === 0) return null;

        let totalPnl = 0, totalRisk = 0, capitalDeployed = 0;
        let pnlCount = 0, riskCount = 0, capitalCount = 0, breachCount = 0, nearTargetCount = 0;
        const styleCounts: Record<string, number> = {};
        for (const p of activeWithEntry) {
          const lp = livePriceMap[p.symbol];
          const entry = p.actual_entry_price ?? p.entry_price!;
          const gpj = p.game_plan as StoredGamePlan | null;
          const stop = p.stop_loss ?? gpj?.stop_loss?.price ?? null;
          const target = p.take_profit ?? gpj?.take_profit?.price ?? null;
          if (lp && p.shares) { totalPnl += (lp.price - entry) * p.shares; pnlCount++; }
          if (stop != null && p.shares) { totalRisk += Math.max(0, (entry - stop) * p.shares); riskCount++; }
          if (p.shares) { capitalDeployed += entry * p.shares; capitalCount++; }
          if (lp && stop != null && lp.price < stop) breachCount++;
          if (lp && target != null && lp.price >= target * 0.98) nearTargetCount++;
          const style = p.trading_style ?? 'OTHER';
          styleCounts[style] = (styleCounts[style] ?? 0) + 1;
        }
        const styleColors: Record<string, string> = { SHORT: '#38bdf8', SWING: '#818cf8', LONG: '#4ade80', GROWTH: '#fb923c', OTHER: '#64748b' };

        return (
          <div style={{ marginBottom: '16px', padding: '8px 14px', borderRadius: '8px', background: '#080f1e', border: '1px solid #1e293b' }}>
            <div style={{ display: 'flex', gap: '14px', flexWrap: 'wrap', alignItems: 'center' }}>
              <span style={{ fontSize: '10px', fontWeight: 700, color: '#334155', letterSpacing: '0.06em' }}>ACTIVE {activeWithEntry.length}</span>
              {pnlCount > 0 && (
                <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                  <span style={{ fontSize: '10px', color: '#475569' }}>Unrealized P&amp;L</span>
                  <span style={{ fontSize: '14px', fontWeight: 800, fontFamily: 'monospace', color: totalPnl >= 0 ? '#4ade80' : '#f87171' }}>
                    {totalPnl >= 0 ? '+' : ''}${Math.abs(totalPnl).toFixed(0)}
                  </span>
                </div>
              )}
              {capitalCount > 0 && (
                <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                  <span style={{ fontSize: '10px', color: '#475569' }}>Deployed</span>
                  <span style={{ fontSize: '13px', fontWeight: 700, fontFamily: 'monospace', color: '#94a3b8' }}>${capitalDeployed.toFixed(0)}</span>
                </div>
              )}
              {riskCount > 0 && (
                <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                  <span style={{ fontSize: '10px', color: '#475569' }}>Total Risk</span>
                  <span style={{ fontSize: '13px', fontWeight: 700, fontFamily: 'monospace', color: '#f87171' }}>-${totalRisk.toFixed(0)}</span>
                </div>
              )}
              {breachCount > 0 && (
                <span style={{ padding: '2px 8px', borderRadius: '4px', fontSize: '10px', fontWeight: 700, color: '#f87171', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)' }}>
                  ⚠ {breachCount} stop breached
                </span>
              )}
              {nearTargetCount > 0 && (
                <span style={{ padding: '2px 8px', borderRadius: '4px', fontSize: '10px', fontWeight: 700, color: '#4ade80', background: 'rgba(74,222,128,0.08)', border: '1px solid rgba(74,222,128,0.25)' }}>
                  ↑ {nearTargetCount} near target
                </span>
              )}
            </div>
            {Object.keys(styleCounts).length > 0 && (
              <div style={{ display: 'flex', gap: '6px', marginTop: '6px', paddingTop: '6px', borderTop: '1px solid #0f172a', flexWrap: 'wrap', alignItems: 'center' }}>
                <span style={{ fontSize: '10px', fontWeight: 700, color: '#1e293b', letterSpacing: '0.06em' }}>BY STYLE</span>
                {Object.entries(styleCounts).map(([style, count]) => (
                  <span key={style} style={{ padding: '2px 8px', borderRadius: '4px', fontSize: '10px', fontWeight: 700, color: styleColors[style] ?? '#64748b', background: `${styleColors[style] ?? '#64748b'}10`, border: `1px solid ${styleColors[style] ?? '#64748b'}25` }}>
                    {style} {count}
                  </span>
                ))}
              </div>
            )}
          </div>
        );
      })()}

      {fillTarget && (
        <FillModal
          defaultPrice={fillTarget.defaultPrice}
          onConfirm={handleFillConfirm}
          onSkip={handleFillSkip}
        />
      )}

      {/* fill sync status toast */}
      {fillSyncMsg && (
        <div style={{ position: 'fixed', bottom: 24, left: '50%', transform: 'translateX(-50%)', zIndex: 2000, background: '#0f172a', border: `1px solid ${fillSyncMsg.startsWith('✓') ? 'rgba(74,222,128,0.4)' : 'rgba(251,191,36,0.4)'}`, borderRadius: 8, padding: '8px 18px', fontSize: 12, color: fillSyncMsg.startsWith('✓') ? '#4ade80' : '#fbbf24', fontWeight: 600, boxShadow: '0 4px 20px rgba(0,0,0,0.5)' }}>
          {fillSyncMsg}
        </div>
      )}

      {/* UI-5: close confirmation modal */}
      {closeConfirmId != null && (() => {
        const closePlan = (data ?? []).find(p => p.id === closeConfirmId);
        const hasPosition = closePlan?.actual_entry_price != null;
        const pnlPreview = (() => {
          const exit = parseFloat(closeExitInput);
          const entry = closePlan?.actual_entry_price ?? closePlan?.entry_price;
          const shares = closePlan?.shares;
          if (!isNaN(exit) && exit > 0 && entry != null && shares != null) {
            const pnl = (exit - entry) * shares;
            return { pnl, pct: ((exit - entry) / entry) * 100 };
          }
          return null;
        })();
        return (
          <div style={{ position: 'fixed', inset: 0, zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '16px' }}>
            <div onClick={() => { setCloseConfirmId(null); setCloseExitInput(''); }} style={{ position: 'absolute', inset: 0, background: 'rgba(6,8,20,0.85)', backdropFilter: 'blur(6px)' }} />
            <div style={{ position: 'relative', zIndex: 10, width: '100%', maxWidth: '380px', borderRadius: '14px', background: 'linear-gradient(160deg,#0d1424 0%,#090e1a 100%)', border: '1px solid rgba(239,68,68,0.3)', boxShadow: '0 24px 48px rgba(0,0,0,0.6)', padding: '24px 24px 20px' }}>
              <div style={{ height: '3px', background: 'linear-gradient(90deg,#ef4444,#f87171)', borderRadius: '2px', marginBottom: '18px' }} />
              <div style={{ fontSize: '15px', fontWeight: 700, color: '#f1f5f9', marginBottom: '8px' }}>
                Close trade{closePlan ? ` · ${closePlan.symbol}` : ''}?
              </div>
              <div style={{ fontSize: '12px', color: '#64748b', marginBottom: '16px', lineHeight: 1.5 }}>
                Moves to <span style={{ color: '#94a3b8' }}>Closed</span>.{hasPosition ? ' Position will be removed from Positions page.' : ''}
              </div>
              <label style={{ display: 'block', fontSize: '11px', color: '#64748b', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '6px' }}>
                Exit price <span style={{ color: '#334155', fontWeight: 400, textTransform: 'none' }}>(optional — logs P&L)</span>
              </label>
              <input
                autoFocus
                type="number" step="0.01" placeholder="e.g. 165.00"
                value={closeExitInput}
                onChange={e => setCloseExitInput(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleCloseConfirmed()}
                style={{ width: '100%', padding: '8px 12px', borderRadius: '7px', border: '1px solid #334155', background: '#060814', color: '#f1f5f9', fontSize: '14px', fontFamily: 'ui-monospace, monospace', outline: 'none', boxSizing: 'border-box', marginBottom: '10px' }}
              />
              {pnlPreview && (
                <div style={{ fontSize: 12, marginBottom: 14, padding: '6px 10px', borderRadius: 6, background: pnlPreview.pnl >= 0 ? 'rgba(74,222,128,0.08)' : 'rgba(239,68,68,0.08)', border: `1px solid ${pnlPreview.pnl >= 0 ? 'rgba(74,222,128,0.2)' : 'rgba(239,68,68,0.2)'}`, color: pnlPreview.pnl >= 0 ? '#4ade80' : '#f87171' }}>
                  P&L: {pnlPreview.pnl >= 0 ? '+' : ''}${Math.abs(pnlPreview.pnl).toFixed(0)} ({pnlPreview.pct >= 0 ? '+' : ''}{pnlPreview.pct.toFixed(1)}%)
                </div>
              )}
              <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                <button onClick={() => { setCloseConfirmId(null); setCloseExitInput(''); }} style={{ padding: '7px 16px', borderRadius: '6px', border: '1px solid #1e293b', background: 'transparent', color: '#64748b', fontSize: '12px', cursor: 'pointer' }}>
                  Cancel
                </button>
                <button onClick={handleCloseConfirmed} style={{ padding: '7px 18px', borderRadius: '6px', border: '1px solid rgba(239,68,68,0.4)', background: 'rgba(239,68,68,0.12)', color: '#f87171', fontSize: '12px', fontWeight: 700, cursor: 'pointer' }}>
                  Close trade
                </button>
              </div>
            </div>
          </div>
        );
      })()}

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
            const isDropTarget = dragOverStage === stage && dragId != null;
            return (
              <div
                key={stage}
                onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; setDragOverStage(stage); }}
                onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragOverStage(null); }}
                onDrop={e => { e.preventDefault(); handleDrop(stage); }}
                style={{
                  borderRadius: '12px',
                  border: `1px solid ${isDropTarget ? m.color : m.border}`,
                  background: isDropTarget ? m.bg : '#080f1e',
                  overflow: 'hidden',
                  transition: 'border-color 0.15s, background 0.15s',
                }}
              >
                {/* Column header */}
                <div style={{ padding: '10px 14px 8px', background: m.bg, borderBottom: `1px solid ${m.border}` }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <span style={{ fontSize: '13px', fontWeight: 700, color: m.color }}>{m.label}</span>
                    <span style={{ fontSize: '11px', color: m.color, opacity: 0.7, background: m.bg, border: `1px solid ${m.border}`, borderRadius: '10px', padding: '1px 7px' }}>{cards.length}</span>
                  </div>
                  <div style={{ fontSize: '10px', color: '#334155', marginTop: '2px' }}>{m.desc}</div>
                </div>

                {/* Cards */}
                <div style={{ padding: '10px', minHeight: '60px' }}>
                  {stage === 'watch' && <AddCardForm onAdd={handleAdd} />}
                  {cards.length === 0 && stage !== 'watch' && (
                    <div style={{ textAlign: 'center', padding: '24px 0', fontSize: '11px', color: isDropTarget ? m.color : '#1e293b', transition: 'color 0.15s' }}>
                      {isDropTarget ? `Drop to move here` : 'Move cards here as your trade progresses'}
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
                      onDragStart={setDragId}
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

      {/* Portfolio Risk Dashboard */}
      {riskData && (
        <div style={{ marginTop: 24, padding: '16px 20px', borderRadius: 12, background: '#080f1e', border: '1px solid #1e293b' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
            <div>
              <h2 style={{ fontSize: 14, fontWeight: 700, color: '#e2e8f0', margin: 0 }}>Portfolio Risk</h2>
              <div style={{ fontSize: 10, color: '#334155', marginTop: 2 }}>
                {riskData.symbols.length} active positions · beta vs {riskData.benchmark}
              </div>
            </div>
            <div style={{ display: 'flex', gap: 10 }}>
              {[
                { label: 'Portfolio β', value: riskData.portfolio_beta.toFixed(2), color: riskData.portfolio_beta > 1.5 ? '#f87171' : riskData.portfolio_beta < 0.8 ? '#4ade80' : '#94a3b8' },
                { label: '1-day VaR 95%', value: `${riskData.var_95_pct.toFixed(1)}%`, color: riskData.var_95_pct > 4 ? '#f87171' : riskData.var_95_pct > 2.5 ? '#fbbf24' : '#4ade80' },
                { label: 'Positions', value: String(riskData.symbols.length), color: '#94a3b8' },
              ].map(s => (
                <div key={s.label} style={{ padding: '6px 12px', borderRadius: 8, background: 'rgba(255,255,255,0.03)', border: '1px solid #1e293b', textAlign: 'center' }}>
                  <div style={{ fontSize: 9, color: '#475569', fontWeight: 600, textTransform: 'uppercase', marginBottom: 2 }}>{s.label}</div>
                  <div style={{ fontSize: 18, fontWeight: 800, color: s.color, lineHeight: 1 }}>{s.value}</div>
                </div>
              ))}
            </div>
          </div>

          {/* Warnings */}
          {riskData.warnings.length > 0 && (
            <div style={{ marginBottom: 16, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {riskData.warnings.map((w, i) => (
                <div key={i} style={{ padding: '4px 10px', borderRadius: 5, background: 'rgba(251,113,133,0.08)', border: '1px solid rgba(251,113,133,0.25)', fontSize: 11, color: '#fca5a5' }}>
                  ⚠ {w}
                </div>
              ))}
            </div>
          )}

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
            {/* Sector pie */}
            <div style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid #1e293b', borderRadius: 8, padding: 14 }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: '#64748b', marginBottom: 10 }}>Sector Concentration</div>
              {(() => {
                const COLORS = ['#818cf8','#4ade80','#fbbf24','#f87171','#38bdf8','#a78bfa','#34d399','#fb923c'];
                const sectors = Object.entries(riskData.sector_weights).sort((a, b) => b[1] - a[1]);
                // Pie chart SVG
                const size = 80, cx = 40, cy = 40, r = 36;
                let cum = 0;
                const slices = sectors.map(([, w], i) => {
                  const start = cum * 2 * Math.PI;
                  cum += w;
                  const end = cum * 2 * Math.PI;
                  const x1 = cx + r * Math.sin(start);
                  const y1 = cy - r * Math.cos(start);
                  const x2 = cx + r * Math.sin(end);
                  const y2 = cy - r * Math.cos(end);
                  const large = (w > 0.5) ? 1 : 0;
                  return { d: `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2} Z`, color: COLORS[i % COLORS.length] };
                });
                return (
                  <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
                    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ flexShrink: 0 }}>
                      {slices.map((s, i) => <path key={i} d={s.d} fill={s.color} opacity={0.85} />)}
                    </svg>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                      {sectors.map(([sec, w], i) => (
                        <div key={sec} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11 }}>
                          <div style={{ width: 8, height: 8, borderRadius: 2, background: COLORS[i % COLORS.length], flexShrink: 0 }} />
                          <span style={{ color: '#94a3b8' }}>{sec}</span>
                          <span style={{ color: '#64748b', marginLeft: 'auto', paddingLeft: 8 }}>{(w * 100).toFixed(0)}%</span>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })()}
            </div>

            {/* Correlation heatmap */}
            <div style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid #1e293b', borderRadius: 8, padding: 14 }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: '#64748b', marginBottom: 10 }}>Return Correlation (30d)</div>
              {(() => {
                const syms = riskData.symbols;
                const corr = riskData.correlation;
                const cellSize = Math.min(36, Math.floor(200 / syms.length));
                const labelColor = (v: number) =>
                  v >= 0.8 ? '#f87171' : v >= 0.5 ? '#fbbf24' : v >= 0 ? '#94a3b8' : '#38bdf8';
                const bgColor = (v: number) => {
                  const abs = Math.abs(v);
                  if (v >= 0.8) return `rgba(248,113,113,${0.1 + abs * 0.3})`;
                  if (v >= 0.5) return `rgba(251,191,36,${0.1 + abs * 0.2})`;
                  if (v >= 0) return `rgba(148,163,184,${abs * 0.15})`;
                  return `rgba(56,189,248,${abs * 0.2})`;
                };
                return (
                  <div style={{ overflowX: 'auto' }}>
                    <div style={{ display: 'inline-block' }}>
                      {/* Header row */}
                      <div style={{ display: 'flex', marginLeft: cellSize + 4 }}>
                        {syms.map(s => (
                          <div key={s} style={{ width: cellSize, textAlign: 'center', fontSize: 9, color: '#475569', fontWeight: 700 }}>
                            {s.length > 5 ? s.slice(0, 4) : s}
                          </div>
                        ))}
                      </div>
                      {corr.map((row, i) => (
                        <div key={i} style={{ display: 'flex', alignItems: 'center', marginTop: 2 }}>
                          <div style={{ width: cellSize, fontSize: 9, color: '#475569', fontWeight: 700, textAlign: 'right', paddingRight: 4, flexShrink: 0 }}>
                            {syms[i].length > 5 ? syms[i].slice(0, 4) : syms[i]}
                          </div>
                          {row.map((v, j) => (
                            <div key={j} style={{
                              width: cellSize, height: cellSize, display: 'flex', alignItems: 'center', justifyContent: 'center',
                              background: i === j ? 'rgba(99,102,241,0.15)' : bgColor(v),
                              fontSize: 9, fontWeight: 700,
                              color: i === j ? '#818cf8' : labelColor(v),
                              borderRadius: 3, marginLeft: 2,
                            }}>
                              {v.toFixed(2)}
                            </div>
                          ))}
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })()}
            </div>
          </div>

          {/* Per-symbol betas */}
          {Object.keys(riskData.betas).length > 0 && (
            <div style={{ marginTop: 12, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {Object.entries(riskData.betas).map(([sym, beta]) => (
                <div key={sym} style={{ padding: '3px 10px', borderRadius: 5, background: 'rgba(255,255,255,0.03)', border: '1px solid #1e293b', fontSize: 11 }}>
                  <span style={{ color: '#64748b' }}>{sym} β </span>
                  <span style={{ color: (beta as number) > 1.3 ? '#f87171' : (beta as number) < 0.7 ? '#4ade80' : '#94a3b8', fontWeight: 700 }}>
                    {(beta as number).toFixed(2)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {riskSymbols.length >= 2 && !riskData && (
        <div style={{ marginTop: 24, padding: '12px 20px', borderRadius: 10, background: 'rgba(99,102,241,0.04)', border: '1px solid #1e293b', display: 'flex', alignItems: 'center', gap: 12 }}>
          {riskLoading ? (
            <span style={{ fontSize: 12, color: '#475569' }}>Computing portfolio risk for {riskSymbols.length} positions… (may take ~20s)</span>
          ) : (
            <>
              <span style={{ fontSize: 12, color: '#475569' }}>{riskSymbols.length} active positions with size data</span>
              <button
                onClick={() => setRiskRequested(true)}
                style={{ padding: '4px 14px', borderRadius: 6, border: '1px solid rgba(99,102,241,0.4)', background: 'rgba(99,102,241,0.1)', color: '#818cf8', fontSize: 12, fontWeight: 600, cursor: 'pointer' }}
              >
                Compute Risk
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
