import { useState, useEffect } from 'react';
import useSWR from 'swr';
import Link from 'next/link';
import {
  loadAlerts, saveAlerts, addAlert, deleteAlert, toggleAlert,
  loadNotifications, clearNotifications, conditionLabel,
  type StockAlert, type ConditionType, type AlertCondition,
} from '@/lib/alerts';
import { loadSettings } from '@/lib/settings';
import { api, type Stock } from '@/lib/api';

const CONDITION_OPTIONS: { value: ConditionType; label: string; hasThreshold: boolean; unit: string }[] = [
  { value: 'price_above',      label: 'Price rises above',     hasThreshold: true,  unit: '$' },
  { value: 'price_below',      label: 'Price falls below',     hasThreshold: true,  unit: '$' },
  { value: 'change_pct_above', label: 'Day gain exceeds',      hasThreshold: true,  unit: '%' },
  { value: 'change_pct_below', label: 'Day loss exceeds',      hasThreshold: true,  unit: '%' },
  { value: 'signal_buy',       label: 'Signal becomes BUY',    hasThreshold: false, unit: ''  },
  { value: 'signal_sell',      label: 'Signal becomes SELL',   hasThreshold: false, unit: ''  },
  { value: 'score_above',      label: 'K-Score rises above',   hasThreshold: true,  unit: ''  },
  { value: 'score_below',      label: 'K-Score falls below',   hasThreshold: true,  unit: ''  },
];

const COOLDOWN_OPTIONS = [
  { value: 15,   label: '15 minutes' },
  { value: 30,   label: '30 minutes' },
  { value: 60,   label: '1 hour' },
  { value: 240,  label: '4 hours' },
  { value: 1440, label: '24 hours' },
];

function relTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

const inp: React.CSSProperties = {
  background: '#0f172a', border: '1px solid #1e293b', borderRadius: '8px',
  padding: '9px 12px', fontSize: '13px', color: '#e2e8f0', outline: 'none',
  width: '100%', boxSizing: 'border-box',
};

const lbl: React.CSSProperties = {
  fontSize: '11px', color: '#64748b', fontWeight: 600,
  textTransform: 'uppercase', letterSpacing: '0.05em', display: 'block', marginBottom: '5px',
};

export default function AlertsPage() {
  const { data: stocks } = useSWR<Stock[]>('stocks', () => api.listStocks());
  const settings = loadSettings();

  const [alerts, setAlerts] = useState<StockAlert[]>([]);
  const [notifications, setNotifications] = useState(() => loadNotifications().slice(0, 50));

  // Form state
  const [symbol, setSymbol]       = useState('');
  const [condType, setCondType]   = useState<ConditionType>('price_above');
  const [threshold, setThreshold] = useState('');
  const [cooldown, setCooldown]   = useState(settings.alertCooldownMinutes);
  const [saved, setSaved]         = useState(false);

  useEffect(() => {
    setAlerts(loadAlerts());
    function refresh() { setAlerts(loadAlerts()); setNotifications(loadNotifications().slice(0, 50)); }
    window.addEventListener('stockai:notifications', refresh);
    return () => window.removeEventListener('stockai:notifications', refresh);
  }, []);

  const condMeta = CONDITION_OPTIONS.find(o => o.value === condType)!;

  function buildCondition(): AlertCondition {
    const t = parseFloat(threshold);
    switch (condType) {
      case 'signal_buy':  return { type: 'signal_buy' };
      case 'signal_sell': return { type: 'signal_sell' };
      default:            return { type: condType, threshold: t } as AlertCondition;
    }
  }

  function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!symbol) return;
    if (condMeta.hasThreshold && (!threshold || isNaN(parseFloat(threshold)))) return;
    const stock = stocks?.find(s => s.symbol === symbol);
    addAlert({
      symbol,
      name: stock?.name ?? symbol,
      condition: buildCondition(),
      enabled: true,
      cooldownMinutes: cooldown,
    });
    setAlerts(loadAlerts());
    setThreshold('');
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  const sigColor = (s: string) =>
    s === 'BUY' ? '#4ade80' : s === 'SELL' ? '#f87171' : s === 'WAIT' ? '#fb923c' : '#facc15';

  return (
    <div style={{ maxWidth: '900px', margin: '0 auto', paddingTop: '8px' }}>

      {/* Page header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: '20px', fontWeight: 800, color: '#f1f5f9' }}>Alerts</h1>
          <div style={{ fontSize: '12px', color: '#475569', marginTop: '3px' }}>
            {alerts.filter(a => a.enabled).length} active · checked every 60 s
          </div>
        </div>
      </div>

      {/* Create alert form */}
      <div style={{ borderRadius: '12px', border: '1px solid rgba(99,102,241,0.25)', background: 'rgba(15,23,42,0.95)', overflow: 'hidden', marginBottom: '24px' }}>
        <div style={{ height: '3px', background: 'linear-gradient(90deg,#4f46e5,#818cf8,#4f46e5)' }} />
        <div style={{ padding: '20px 24px' }}>
          <h2 style={{ margin: '0 0 18px', fontSize: '14px', fontWeight: 700, color: '#e2e8f0' }}>Create Alert</h2>
          <form onSubmit={handleCreate}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr auto', gap: '12px', alignItems: 'end' }}>

              {/* Stock */}
              <div>
                <label style={lbl}>Stock</label>
                <select value={symbol} onChange={e => setSymbol(e.target.value)} required style={inp}>
                  <option value="">Select stock…</option>
                  {(stocks ?? []).map(s => (
                    <option key={s.symbol} value={s.symbol}>{s.symbol} — {s.name}</option>
                  ))}
                </select>
              </div>

              {/* Condition */}
              <div>
                <label style={lbl}>Condition</label>
                <select value={condType} onChange={e => setCondType(e.target.value as ConditionType)} style={inp}>
                  {CONDITION_OPTIONS.map(o => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </div>

              {/* Threshold */}
              <div>
                {condMeta.hasThreshold ? (
                  <>
                    <label style={lbl}>Threshold {condMeta.unit && `(${condMeta.unit})`}</label>
                    <input
                      type="number" step="any" min="0"
                      value={threshold}
                      onChange={e => setThreshold(e.target.value)}
                      placeholder={condMeta.unit === '$' ? '0.00' : '0'}
                      required
                      style={inp}
                    />
                  </>
                ) : (
                  <>
                    <label style={lbl}>Cooldown</label>
                    <select value={cooldown} onChange={e => setCooldown(Number(e.target.value))} style={inp}>
                      {COOLDOWN_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </>
                )}
              </div>

              {/* Submit */}
              <div>
                <button
                  type="submit"
                  style={{
                    background: saved ? 'rgba(34,197,94,0.2)' : 'linear-gradient(135deg,#4f46e5,#6366f1)',
                    border: saved ? '1px solid rgba(34,197,94,0.4)' : 'none',
                    color: saved ? '#4ade80' : '#fff',
                    padding: '9px 20px', borderRadius: '8px', fontSize: '13px',
                    fontWeight: 700, cursor: 'pointer', whiteSpace: 'nowrap',
                    width: '100%', transition: 'all 0.2s',
                  }}
                >
                  {saved ? '✓ Saved' : '+ Add Alert'}
                </button>
              </div>
            </div>

            {/* Cooldown row (only when threshold is shown) */}
            {condMeta.hasThreshold && (
              <div style={{ marginTop: '10px', display: 'flex', alignItems: 'center', gap: '10px' }}>
                <label style={{ ...lbl, marginBottom: 0 }}>Cooldown:</label>
                <select value={cooldown} onChange={e => setCooldown(Number(e.target.value))}
                  style={{ ...inp, width: 'auto', padding: '5px 10px', fontSize: '12px' }}>
                  {COOLDOWN_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
                <span style={{ fontSize: '11px', color: '#334155' }}>before re-triggering</span>
              </div>
            )}
          </form>
        </div>
      </div>

      {/* Active alerts */}
      <div style={{ marginBottom: '24px' }}>
        <h2 style={{ fontSize: '14px', fontWeight: 700, color: '#94a3b8', marginBottom: '12px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
          Active Alerts ({alerts.length})
        </h2>

        {alerts.length === 0 ? (
          <div style={{ padding: '32px', textAlign: 'center', borderRadius: '10px', border: '1px dashed #1e293b', color: '#334155', fontSize: '13px' }}>
            No alerts yet. Create one above.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {alerts.map(alert => (
              <div key={alert.id} style={{
                display: 'flex', alignItems: 'center', gap: '12px',
                padding: '12px 16px', borderRadius: '10px',
                border: `1px solid ${alert.enabled ? 'rgba(99,102,241,0.2)' : '#1e293b'}`,
                background: alert.enabled ? 'rgba(15,23,42,0.8)' : 'rgba(15,23,42,0.4)',
                opacity: alert.enabled ? 1 : 0.55,
                transition: 'all 0.15s',
              }}>
                {/* Enable toggle */}
                <button
                  onClick={() => { toggleAlert(alert.id); setAlerts(loadAlerts()); }}
                  style={{
                    width: '36px', height: '20px', borderRadius: '10px', border: 'none',
                    cursor: 'pointer', flexShrink: 0, transition: 'background 0.2s',
                    background: alert.enabled ? '#4f46e5' : '#1e293b',
                    position: 'relative',
                  }}
                  title={alert.enabled ? 'Disable' : 'Enable'}
                >
                  <span style={{
                    position: 'absolute', top: '3px',
                    left: alert.enabled ? '19px' : '3px',
                    width: '14px', height: '14px', borderRadius: '50%',
                    background: '#fff', transition: 'left 0.2s',
                  }} />
                </button>

                {/* Symbol */}
                <span style={{ fontSize: '13px', fontWeight: 800, color: '#818cf8', fontFamily: 'monospace', minWidth: '70px' }}>
                  {alert.symbol}
                </span>

                {/* Condition */}
                <span style={{ fontSize: '13px', color: '#cbd5e1', flex: 1 }}>
                  {conditionLabel(alert.condition)}
                </span>

                {/* Cooldown */}
                <span style={{ fontSize: '11px', color: '#334155', flexShrink: 0 }}>
                  {alert.cooldownMinutes >= 60
                    ? `${alert.cooldownMinutes / 60}h cooldown`
                    : `${alert.cooldownMinutes}m cooldown`}
                </span>

                {/* Last triggered */}
                {alert.lastTriggered && (
                  <span style={{ fontSize: '11px', color: '#475569', flexShrink: 0 }}>
                    Last: {relTime(alert.lastTriggered)}
                  </span>
                )}

                {/* Delete */}
                <button
                  onClick={() => { deleteAlert(alert.id); setAlerts(loadAlerts()); }}
                  style={{ background: 'none', border: 'none', color: '#334155', cursor: 'pointer', fontSize: '14px', flexShrink: 0, padding: '2px 4px' }}
                  title="Delete alert"
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Notification history */}
      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
          <h2 style={{ fontSize: '14px', fontWeight: 700, color: '#94a3b8', margin: 0, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            Notification History ({notifications.length})
          </h2>
          {notifications.length > 0 && (
            <button onClick={() => { clearNotifications(); setNotifications([]); }}
              style={{ fontSize: '12px', color: '#475569', background: 'none', border: 'none', cursor: 'pointer' }}>
              Clear history
            </button>
          )}
        </div>

        {notifications.length === 0 ? (
          <div style={{ padding: '32px', textAlign: 'center', borderRadius: '10px', border: '1px dashed #1e293b', color: '#334155', fontSize: '13px' }}>
            No notifications triggered yet.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {notifications.map(n => (
              <div key={n.id} style={{
                display: 'flex', alignItems: 'center', gap: '10px', padding: '10px 14px',
                borderRadius: '8px', background: 'rgba(255,255,255,0.02)', border: '1px solid #1e293b',
              }}>
                <Link href={`/stock/${n.symbol}`} style={{
                  fontSize: '11px', fontWeight: 700, padding: '2px 7px', borderRadius: '4px',
                  background: 'rgba(99,102,241,0.15)', color: '#818cf8', textDecoration: 'none', flexShrink: 0,
                }}>
                  {n.symbol}
                </Link>
                <span style={{ fontSize: '12px', color: '#94a3b8', flex: 1 }}>{n.message}</span>
                <span style={{ fontSize: '11px', color: '#334155', flexShrink: 0 }}>{relTime(n.triggeredAt)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
