import { useState, useEffect } from 'react';
import Link from 'next/link';
import { loadSettings, saveSettings, type AppSettings } from '@/lib/settings';

const inp: React.CSSProperties = {
  background: '#0f172a', border: '1px solid #1e293b', borderRadius: '8px',
  padding: '9px 12px', fontSize: '13px', color: '#e2e8f0', outline: 'none',
  width: '100%', boxSizing: 'border-box',
};

const lbl: React.CSSProperties = {
  fontSize: '11px', color: '#64748b', fontWeight: 600,
  textTransform: 'uppercase', letterSpacing: '0.05em', display: 'block', marginBottom: '5px',
};

const section: React.CSSProperties = {
  borderRadius: '12px', border: '1px solid rgba(99,102,241,0.2)',
  background: 'rgba(15,23,42,0.95)', overflow: 'hidden', marginBottom: '20px',
};

const sectionHeader: React.CSSProperties = {
  padding: '14px 20px', borderBottom: '1px solid #1e293b',
  fontSize: '13px', fontWeight: 700, color: '#94a3b8',
  textTransform: 'uppercase', letterSpacing: '0.06em',
};

const row: React.CSSProperties = {
  display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', padding: '16px 20px',
};

const hint: React.CSSProperties = {
  fontSize: '11px', color: '#334155', marginTop: '4px',
};

export default function SettingsPage() {
  const [s, setS] = useState<AppSettings>(loadSettings);
  const [saved, setSaved] = useState(false);

  function update<K extends keyof AppSettings>(key: K, value: AppSettings[K]) {
    setS(prev => ({ ...prev, [key]: value }));
  }

  function handleSave() {
    saveSettings(s);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  return (
    <div style={{ maxWidth: '760px', margin: '0 auto', paddingTop: '8px' }}>

      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: '20px', fontWeight: 800, color: '#f1f5f9' }}>Settings</h1>
          <div style={{ fontSize: '12px', color: '#475569', marginTop: '3px' }}>App configuration and preferences</div>
        </div>
        <button
          onClick={handleSave}
          style={{
            background: saved ? 'rgba(34,197,94,0.15)' : 'linear-gradient(135deg,#4f46e5,#6366f1)',
            border: saved ? '1px solid rgba(34,197,94,0.4)' : 'none',
            color: saved ? '#4ade80' : '#fff',
            padding: '9px 24px', borderRadius: '8px', fontSize: '13px',
            fontWeight: 700, cursor: 'pointer', transition: 'all 0.2s',
          }}
        >
          {saved ? '✓ Saved' : 'Save Settings'}
        </button>
      </div>

      {/* Data & Refresh */}
      <div style={section}>
        <div style={{ height: '3px', background: 'linear-gradient(90deg,#4f46e5,#818cf8,#4f46e5)' }} />
        <div style={sectionHeader}>Data & Refresh</div>
        <div style={row}>
          <div>
            <label style={lbl}>Price Refresh Interval</label>
            <select value={s.priceRefreshInterval} onChange={e => update('priceRefreshInterval', Number(e.target.value))} style={inp}>
              <option value={30}>30 seconds</option>
              <option value={60}>60 seconds</option>
              <option value={120}>2 minutes</option>
              <option value={300}>5 minutes</option>
            </select>
            <div style={hint}>How often live prices are fetched automatically.</div>
          </div>
          <div>
            <label style={lbl}>News Max Age</label>
            <select value={s.newsMaxAgeDays} onChange={e => update('newsMaxAgeDays', Number(e.target.value))} style={inp}>
              <option value={3}>3 days</option>
              <option value={7}>7 days</option>
              <option value={14}>14 days</option>
              <option value={30}>30 days</option>
            </select>
            <div style={hint}>Discard news articles older than this.</div>
          </div>
        </div>
        <div style={{ ...row, paddingTop: 0 }}>
          <div>
            <label style={lbl}>Default Chart Limit</label>
            <select value={s.defaultChartLimit} onChange={e => update('defaultChartLimit', Number(e.target.value))} style={inp}>
              <option value={100}>100 days</option>
              <option value={200}>200 days</option>
              <option value={400}>400 days</option>
              <option value={730}>2 years</option>
            </select>
            <div style={hint}>Number of historical bars shown in price charts.</div>
          </div>
          <div />
        </div>
      </div>

      {/* Notifications */}
      <div style={section}>
        <div style={{ height: '3px', background: 'linear-gradient(90deg,#0ea5e9,#38bdf8,#0ea5e9)' }} />
        <div style={sectionHeader}>Notifications</div>
        <div style={row}>
          <div>
            <label style={lbl}>Notification Sound</label>
            <div style={{ display: 'flex', gap: '8px', marginTop: '4px' }}>
              {[true, false].map(v => (
                <button
                  key={String(v)}
                  onClick={() => update('notificationSound', v)}
                  style={{
                    flex: 1, padding: '9px', borderRadius: '8px', fontSize: '13px',
                    fontWeight: 600, cursor: 'pointer', transition: 'all 0.15s',
                    background: s.notificationSound === v ? (v ? 'rgba(79,70,229,0.25)' : 'rgba(239,68,68,0.15)') : '#0f172a',
                    border: s.notificationSound === v ? (v ? '1px solid rgba(99,102,241,0.5)' : '1px solid rgba(239,68,68,0.3)') : '1px solid #1e293b',
                    color: s.notificationSound === v ? (v ? '#818cf8' : '#f87171') : '#475569',
                  }}
                >
                  {v ? '🔔 On' : '🔕 Off'}
                </button>
              ))}
            </div>
            <div style={hint}>Play a sound when an alert triggers.</div>
          </div>
          <div>
            <label style={lbl}>Default Alert Cooldown</label>
            <select value={s.alertCooldownMinutes} onChange={e => update('alertCooldownMinutes', Number(e.target.value))} style={inp}>
              <option value={15}>15 minutes</option>
              <option value={30}>30 minutes</option>
              <option value={60}>1 hour</option>
              <option value={240}>4 hours</option>
              <option value={1440}>24 hours</option>
            </select>
            <div style={hint}>Default cooldown for new alerts (can override per alert).</div>
          </div>
        </div>
        <div style={{ padding: '0 20px 16px', display: 'flex', alignItems: 'center', gap: '10px' }}>
          <Link href="/alerts" style={{
            fontSize: '12px', color: '#4f46e5', textDecoration: 'none',
            padding: '6px 14px', border: '1px solid rgba(79,70,229,0.3)',
            borderRadius: '6px', background: 'rgba(79,70,229,0.08)',
          }}>
            Manage Alerts →
          </Link>
        </div>
      </div>

      {/* ML & Analysis */}
      <div style={section}>
        <div style={{ height: '3px', background: 'linear-gradient(90deg,#10b981,#34d399,#10b981)' }} />
        <div style={sectionHeader}>ML & Analysis</div>
        <div style={row}>
          <div>
            <label style={lbl}>Default ML Model</label>
            <select value={s.defaultMlModel} onChange={e => update('defaultMlModel', e.target.value)} style={inp}>
              <option value="xgboost">XGBoost (recommended)</option>
              <option value="random_forest">Random Forest</option>
              <option value="gradient_boosting">Gradient Boosting</option>
              <option value="lstm">LSTM (deep learning)</option>
            </select>
            <div style={hint}>Model used for signal generation by default.</div>
          </div>
          <div style={{ display: 'flex', alignItems: 'flex-end' }}>
            <div style={{
              padding: '10px 14px', borderRadius: '8px',
              background: 'rgba(16,185,129,0.08)', border: '1px solid rgba(16,185,129,0.2)',
              fontSize: '12px', color: '#6ee7b7', lineHeight: 1.5, width: '100%',
            }}>
              <strong>XGBoost</strong> is fastest and most accurate for this dataset. LSTM is experimental and may be slow to train.
            </div>
          </div>
        </div>
      </div>

      {/* Account */}
      <div style={section}>
        <div style={{ height: '3px', background: 'linear-gradient(90deg,#f59e0b,#fbbf24,#f59e0b)' }} />
        <div style={sectionHeader}>Account</div>
        <div style={{ padding: '16px 20px', display: 'flex', alignItems: 'center', gap: '12px' }}>
          <Link href="/login?tab=reset" style={{
            fontSize: '13px', color: '#fbbf24', textDecoration: 'none',
            padding: '8px 16px', border: '1px solid rgba(251,191,36,0.3)',
            borderRadius: '8px', background: 'rgba(251,191,36,0.08)',
            fontWeight: 600,
          }}>
            Change Password
          </Link>
          <span style={{ fontSize: '12px', color: '#334155' }}>Update your login credentials</span>
        </div>
      </div>

    </div>
  );
}
