import { useState, useEffect } from 'react';
import useSWR from 'swr';
import { api, type PriceAlert, type Stock } from '@/lib/api';

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
  const { data: stocks } = useSWR<Stock[]>('stocks-all', () => api.listStocks());
  const { data: alerts, mutate } = useSWR<PriceAlert[]>('alerts', () => api.listAlerts(), { refreshInterval: 30000 });

  const [symbol, setSymbol]       = useState('');
  const [condition, setCondition] = useState<'above' | 'below'>('above');
  const [threshold, setThreshold] = useState('');
  const [email, setEmail]         = useState('');
  const [note, setNote]           = useState('');
  const [saving, setSaving]       = useState(false);
  const [saved, setSaved]         = useState(false);
  const [error, setError]         = useState('');

  useEffect(() => {
    const saved = typeof window !== 'undefined' ? localStorage.getItem('stockai_alert_email') : null;
    if (saved) setEmail(saved);
  }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!symbol || !threshold || !email) return;
    setSaving(true);
    setError('');
    try {
      await api.createAlert({ symbol, condition, threshold: parseFloat(threshold), email, note: note || undefined });
      localStorage.setItem('stockai_alert_email', email);
      await mutate();
      setThreshold('');
      setNote('');
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to create alert');
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: number) {
    try {
      await api.deleteAlert(id);
      await mutate();
    } catch {}
  }

  const active   = (alerts ?? []).filter(a => !a.triggered);
  const fired    = (alerts ?? []).filter(a => a.triggered);

  return (
    <div style={{ maxWidth: '900px', margin: '0 auto', paddingTop: '8px' }}>

      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: '20px', fontWeight: 800, color: '#f1f5f9' }}>Price Alerts</h1>
          <div style={{ fontSize: '12px', color: '#475569', marginTop: '3px' }}>
            {active.length} active · email sent when price is hit · checked every minute
          </div>
        </div>
      </div>

      {/* Create form */}
      <div style={{ borderRadius: '12px', border: '1px solid rgba(99,102,241,0.25)', background: 'rgba(15,23,42,0.95)', overflow: 'hidden', marginBottom: '24px' }}>
        <div style={{ height: '3px', background: 'linear-gradient(90deg,#4f46e5,#818cf8,#4f46e5)' }} />
        <div style={{ padding: '20px 24px' }}>
          <h2 style={{ margin: '0 0 18px', fontSize: '14px', fontWeight: 700, color: '#e2e8f0' }}>Create Alert</h2>
          <form onSubmit={handleCreate}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 160px 1fr 1fr', gap: '12px', alignItems: 'end' }}>

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
                <select value={condition} onChange={e => setCondition(e.target.value as 'above' | 'below')} style={inp}>
                  <option value="above">Price rises above</option>
                  <option value="below">Price falls below</option>
                </select>
              </div>

              <div>
                <label style={lbl}>Threshold ($)</label>
                <input
                  type="number" step="any" min="0"
                  value={threshold}
                  onChange={e => setThreshold(e.target.value)}
                  placeholder="0.00"
                  required
                  style={inp}
                />
              </div>

              <div>
                <label style={lbl}>Email</label>
                <input
                  type="email"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  required
                  style={inp}
                />
              </div>
            </div>

            <div style={{ marginTop: '10px', display: 'flex', gap: '12px', alignItems: 'end' }}>
              <div style={{ flex: 1 }}>
                <label style={lbl}>Note (optional)</label>
                <input
                  type="text"
                  value={note}
                  onChange={e => setNote(e.target.value)}
                  placeholder="e.g. buy target"
                  style={inp}
                />
              </div>
              <button
                type="submit"
                disabled={saving}
                style={{
                  background: saved ? 'rgba(34,197,94,0.2)' : 'linear-gradient(135deg,#4f46e5,#6366f1)',
                  border: saved ? '1px solid rgba(34,197,94,0.4)' : 'none',
                  color: saved ? '#4ade80' : '#fff',
                  padding: '9px 24px', borderRadius: '8px', fontSize: '13px',
                  fontWeight: 700, cursor: saving ? 'not-allowed' : 'pointer',
                  whiteSpace: 'nowrap', opacity: saving ? 0.6 : 1,
                }}
              >
                {saved ? '✓ Saved' : saving ? 'Saving…' : '+ Add Alert'}
              </button>
            </div>

            {error && (
              <div style={{ marginTop: '8px', fontSize: '12px', color: '#f87171' }}>{error}</div>
            )}
          </form>
        </div>
      </div>

      {/* Active alerts */}
      <div style={{ marginBottom: '24px' }}>
        <h2 style={{ fontSize: '14px', fontWeight: 700, color: '#94a3b8', marginBottom: '12px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
          Active ({active.length})
        </h2>

        {active.length === 0 ? (
          <div style={{ padding: '32px', textAlign: 'center', borderRadius: '10px', border: '1px dashed #1e293b', color: '#334155', fontSize: '13px' }}>
            No active alerts. Create one above.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {active.map(alert => (
              <div key={alert.id} style={{
                display: 'flex', alignItems: 'center', gap: '12px',
                padding: '12px 16px', borderRadius: '10px',
                border: '1px solid rgba(99,102,241,0.2)',
                background: 'rgba(15,23,42,0.8)',
              }}>
                <span style={{ fontSize: '13px', fontWeight: 800, color: '#818cf8', fontFamily: 'monospace', minWidth: '70px' }}>
                  {alert.symbol}
                </span>
                <span style={{ fontSize: '13px', color: '#cbd5e1', flex: 1 }}>
                  Price {alert.condition === 'above' ? 'rises above' : 'falls below'} <strong style={{ color: '#f1f5f9' }}>${alert.threshold}</strong>
                </span>
                {alert.note && (
                  <span style={{ fontSize: '11px', color: '#475569', fontStyle: 'italic' }}>{alert.note}</span>
                )}
                <span style={{ fontSize: '11px', color: '#475569' }}>{alert.email}</span>
                <span style={{ fontSize: '11px', color: '#334155' }}>{relTime(alert.created_at)}</span>
                <button
                  onClick={() => handleDelete(alert.id)}
                  style={{ background: 'none', border: 'none', color: '#334155', cursor: 'pointer', fontSize: '14px', flexShrink: 0, padding: '2px 4px' }}
                  title="Delete alert"
                >✕</button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Triggered alerts */}
      {fired.length > 0 && (
        <div>
          <h2 style={{ fontSize: '14px', fontWeight: 700, color: '#94a3b8', marginBottom: '12px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            Triggered ({fired.length})
          </h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {fired.map(alert => (
              <div key={alert.id} style={{
                display: 'flex', alignItems: 'center', gap: '12px', padding: '10px 16px',
                borderRadius: '10px', border: '1px solid #1e293b',
                background: 'rgba(15,23,42,0.4)', opacity: 0.7,
              }}>
                <span style={{ fontSize: '12px', fontWeight: 700, color: '#22c55e', minWidth: '70px', fontFamily: 'monospace' }}>
                  ✓ {alert.symbol}
                </span>
                <span style={{ fontSize: '12px', color: '#64748b', flex: 1 }}>
                  Price {alert.condition === 'above' ? 'rose above' : 'fell below'} ${alert.threshold}
                </span>
                {alert.note && (
                  <span style={{ fontSize: '11px', color: '#334155', fontStyle: 'italic' }}>{alert.note}</span>
                )}
                <span style={{ fontSize: '11px', color: '#334155' }}>
                  {alert.triggered_at ? relTime(alert.triggered_at) : ''}
                </span>
                <button
                  onClick={() => handleDelete(alert.id)}
                  style={{ background: 'none', border: 'none', color: '#1e293b', cursor: 'pointer', fontSize: '14px', flexShrink: 0, padding: '2px 4px' }}
                  title="Delete"
                >✕</button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
