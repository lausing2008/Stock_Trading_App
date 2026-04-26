import { useState } from 'react';
import Link from 'next/link';
import { loadSettings, saveSettings, type AppSettings } from '@/lib/settings';

const inp: React.CSSProperties = {
  background: '#0f172a', border: '1px solid #1e293b', borderRadius: '8px',
  padding: '9px 12px', fontSize: '13px', color: '#e2e8f0', outline: 'none',
  width: '100%', boxSizing: 'border-box',
};

const inpKey: React.CSSProperties = {
  ...inp, fontFamily: 'monospace', letterSpacing: '0.02em',
};

const lbl: React.CSSProperties = {
  fontSize: '11px', color: '#64748b', fontWeight: 600,
  textTransform: 'uppercase', letterSpacing: '0.05em', display: 'block', marginBottom: '5px',
};

const hint: React.CSSProperties = { fontSize: '11px', color: '#334155', marginTop: '4px' };

const section = (accent: string): React.CSSProperties => ({
  borderRadius: '12px', border: '1px solid rgba(99,102,241,0.2)',
  background: 'rgba(15,23,42,0.95)', overflow: 'hidden', marginBottom: '20px',
});

const sectionBar = (gradient: string): React.CSSProperties => ({
  height: '3px', background: gradient,
});

const sectionHead: React.CSSProperties = {
  padding: '14px 20px', borderBottom: '1px solid #1e293b',
  fontSize: '13px', fontWeight: 700, color: '#94a3b8',
  textTransform: 'uppercase', letterSpacing: '0.06em',
};

const grid2: React.CSSProperties = {
  display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', padding: '16px 20px',
};

function Toggle({ on, onChange, disabled }: { on: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <button
      onClick={() => !disabled && onChange(!on)}
      style={{
        width: '44px', height: '24px', borderRadius: '12px', border: 'none',
        cursor: disabled ? 'default' : 'pointer', position: 'relative',
        background: on ? '#4f46e5' : '#1e293b', transition: 'background 0.2s',
        opacity: disabled ? 0.5 : 1, flexShrink: 0,
      }}
    >
      <span style={{
        position: 'absolute', top: '4px', left: on ? '22px' : '4px',
        width: '16px', height: '16px', borderRadius: '50%',
        background: '#fff', transition: 'left 0.2s',
      }} />
    </button>
  );
}

function SourceRow({
  label, subtitle, on, onChange, disabled, children,
}: {
  label: string; subtitle: string; on: boolean;
  onChange: (v: boolean) => void; disabled?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <div style={{ padding: '14px 20px', borderBottom: '1px solid #0f172a' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px' }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: '13px', fontWeight: 600, color: '#e2e8f0', display: 'flex', alignItems: 'center', gap: '8px' }}>
            {label}
            {disabled && <span style={{ fontSize: '10px', color: '#475569', fontWeight: 400, padding: '1px 6px', borderRadius: '4px', background: '#1e293b' }}>always on</span>}
          </div>
          <div style={{ fontSize: '12px', color: '#475569', marginTop: '2px' }}>{subtitle}</div>
        </div>
        <Toggle on={on} onChange={onChange} disabled={disabled} />
      </div>
      {on && children && (
        <div style={{ marginTop: '10px' }}>{children}</div>
      )}
    </div>
  );
}

export default function SettingsPage() {
  const [s, setS] = useState<AppSettings>(loadSettings);
  const [saved, setSaved] = useState(false);
  const [showKeys, setShowKeys] = useState<Record<string, boolean>>({});
  const [aiTestState, setAiTestState] = useState<'idle' | 'loading' | 'ok' | 'error'>('idle');
  const [aiTestMsg, setAiTestMsg] = useState('');

  function update<K extends keyof AppSettings>(key: K, value: AppSettings[K]) {
    setS(prev => ({ ...prev, [key]: value }));
  }

  function handleSave() {
    saveSettings(s);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  async function testAiConnection() {
    setAiTestState('loading');
    setAiTestMsg('');
    try {
      const base = process.env.NEXT_PUBLIC_API_URL ?? '/api';
      const res = await fetch(`${base}/ai/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider: s.aiProvider,
          model: s.aiProvider === 'claude' ? s.claudeModel : s.deepseekModel,
          api_key: s.aiProvider === 'claude' ? s.claudeApiKey : s.deepseekApiKey,
          messages: [{ role: 'user', content: 'Reply with exactly: OK' }],
          max_tokens: 10,
        }),
      });
      if (res.ok) {
        setAiTestState('ok');
        setAiTestMsg('Connection successful ✓');
      } else {
        const err = await res.json().catch(() => ({}));
        setAiTestState('error');
        setAiTestMsg(err.detail || `Error ${res.status}`);
      }
    } catch (e) {
      setAiTestState('error');
      setAiTestMsg(e instanceof Error ? e.message : 'Network error');
    }
  }

  function toggleKeyVisible(k: string) {
    setShowKeys(prev => ({ ...prev, [k]: !prev[k] }));
  }

  function KeyInput({ id, value, onChange, placeholder }: { id: string; value: string; onChange: (v: string) => void; placeholder?: string }) {
    return (
      <div style={{ position: 'relative' }}>
        <input
          type={showKeys[id] ? 'text' : 'password'}
          value={value}
          onChange={e => onChange(e.target.value)}
          placeholder={placeholder ?? 'sk-…'}
          style={{ ...inpKey, paddingRight: '48px' }}
        />
        <button
          onClick={() => toggleKeyVisible(id)}
          style={{
            position: 'absolute', right: '10px', top: '50%', transform: 'translateY(-50%)',
            background: 'none', border: 'none', color: '#475569', cursor: 'pointer', fontSize: '12px',
          }}
        >
          {showKeys[id] ? 'hide' : 'show'}
        </button>
      </div>
    );
  }

  const aiProviderOptions: { value: AppSettings['aiProvider']; label: string; color: string; desc: string }[] = [
    { value: 'none', label: 'Disabled', color: '#475569', desc: 'No AI analysis' },
    { value: 'claude', label: 'Claude (Anthropic)', color: '#818cf8', desc: 'Most capable, great at reasoning' },
    { value: 'deepseek', label: 'DeepSeek', color: '#34d399', desc: 'Fast & cost-effective' },
  ];

  const CLAUDE_MODELS = [
    { value: 'claude-opus-4-7', label: 'Claude Opus 4.7 (most capable)' },
    { value: 'claude-sonnet-4-6', label: 'Claude Sonnet 4.6 (recommended)' },
    { value: 'claude-haiku-4-5-20251001', label: 'Claude Haiku 4.5 (fastest)' },
  ];

  const DEEPSEEK_MODELS = [
    { value: 'deepseek-chat', label: 'DeepSeek Chat (recommended)' },
    { value: 'deepseek-reasoner', label: 'DeepSeek Reasoner (R1)' },
  ];

  return (
    <div style={{ maxWidth: '760px', margin: '0 auto', paddingTop: '8px' }}>

      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: '20px', fontWeight: 800, color: '#f1f5f9' }}>Settings</h1>
          <div style={{ fontSize: '12px', color: '#475569', marginTop: '3px' }}>App configuration, data sources, and AI integration</div>
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

      {/* ── Data Sources ───────────────────────────────────────────────── */}
      <div style={section('#4f46e5')}>
        <div style={sectionBar('linear-gradient(90deg,#4f46e5,#818cf8,#4f46e5)')} />
        <div style={sectionHead}>
          Stock Price Data Sources
        </div>

        <SourceRow
          label="yfinance"
          subtitle="Free · Yahoo Finance — real-time prices, OHLCV history, fundamentals. Primary source."
          on={s.dataSourceYfinance}
          onChange={v => update('dataSourceYfinance', v)}
          disabled
        />

        <SourceRow
          label="Alpha Vantage"
          subtitle="Free tier (25 req/day) · US equities historical OHLCV. Requires API key."
          on={s.dataSourceAlphaVantage}
          onChange={v => update('dataSourceAlphaVantage', v)}
        >
          <div>
            <label style={lbl}>Alpha Vantage API Key</label>
            <KeyInput
              id="av"
              value={s.alphaVantageApiKey}
              onChange={v => update('alphaVantageApiKey', v)}
              placeholder="Enter your Alpha Vantage API key"
            />
            <div style={hint}>
              Get a free key at{' '}
              <span style={{ color: '#818cf8' }}>alphavantage.co</span>
              {' '}· Used for historical OHLCV when yfinance data is incomplete.
            </div>
          </div>
        </SourceRow>

        <SourceRow
          label="Polygon.io"
          subtitle="Free tier (5 req/min) · US equities, multiple timeframes. Requires API key."
          on={s.dataSourcePolygon}
          onChange={v => update('dataSourcePolygon', v)}
        >
          <div>
            <label style={lbl}>Polygon.io API Key</label>
            <KeyInput
              id="poly"
              value={s.polygonApiKey}
              onChange={v => update('polygonApiKey', v)}
              placeholder="Enter your Polygon.io API key"
            />
            <div style={hint}>
              Get a free key at{' '}
              <span style={{ color: '#818cf8' }}>polygon.io</span>
              {' '}· Used as an alternative/supplement for US stock OHLCV data.
            </div>
          </div>
        </SourceRow>
      </div>

      {/* ── News Sources ───────────────────────────────────────────────── */}
      <div style={section('#0ea5e9')}>
        <div style={sectionBar('linear-gradient(90deg,#0ea5e9,#38bdf8,#0ea5e9)')} />
        <div style={sectionHead}>News Sources</div>

        <SourceRow
          label="Yahoo Finance News (yfinance)"
          subtitle="Free · Stock-specific news from Yahoo Finance. Best for US equities."
          on={s.newsSourceYfinance}
          onChange={v => update('newsSourceYfinance', v)}
        />

        <SourceRow
          label="Google News RSS"
          subtitle="Free · Broad news coverage via Google News RSS feed. Essential for HK stocks."
          on={s.newsSourceGoogleNews}
          onChange={v => update('newsSourceGoogleNews', v)}
        />

        <div style={{ padding: '10px 20px', fontSize: '11px', color: '#334155' }}>
          Sentiment scoring (VADER) runs on all articles from enabled sources.
          At least one source must remain active. Changes apply on next news fetch.
        </div>
      </div>

      {/* ── AI Assistant ───────────────────────────────────────────────── */}
      <div style={section('#a78bfa')}>
        <div style={sectionBar('linear-gradient(90deg,#a78bfa,#c4b5fd,#a78bfa)')} />
        <div style={sectionHead}>AI Assistant</div>

        {/* Provider selector */}
        <div style={{ padding: '16px 20px', borderBottom: '1px solid #1e293b' }}>
          <label style={lbl}>AI Provider</label>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            {aiProviderOptions.map(opt => (
              <button
                key={opt.value}
                onClick={() => update('aiProvider', opt.value)}
                style={{
                  padding: '10px 16px', borderRadius: '10px', cursor: 'pointer',
                  textAlign: 'left', transition: 'all 0.15s', flex: 1, minWidth: '160px',
                  background: s.aiProvider === opt.value ? `${opt.color}15` : 'rgba(15,23,42,0.6)',
                  border: s.aiProvider === opt.value ? `1px solid ${opt.color}50` : '1px solid #1e293b',
                }}
              >
                <div style={{ fontSize: '13px', fontWeight: 700, color: s.aiProvider === opt.value ? opt.color : '#94a3b8', marginBottom: '2px' }}>
                  {opt.label}
                </div>
                <div style={{ fontSize: '11px', color: '#475569' }}>{opt.desc}</div>
              </button>
            ))}
          </div>
        </div>

        {/* Claude config */}
        {s.aiProvider === 'claude' && (
          <div style={{ padding: '16px 20px', borderBottom: '1px solid #1e293b' }}>
            <div style={grid2}>
              <div>
                <label style={lbl}>Claude API Key</label>
                <KeyInput
                  id="claude"
                  value={s.claudeApiKey}
                  onChange={v => update('claudeApiKey', v)}
                  placeholder="sk-ant-…"
                />
                <div style={hint}>
                  Get your key at{' '}
                  <span style={{ color: '#818cf8' }}>console.anthropic.com</span>
                </div>
              </div>
              <div>
                <label style={lbl}>Model</label>
                <select value={s.claudeModel} onChange={e => update('claudeModel', e.target.value)} style={inp}>
                  {CLAUDE_MODELS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
                </select>
                <div style={hint}>Sonnet 4.6 offers the best balance of speed and intelligence.</div>
              </div>
            </div>
          </div>
        )}

        {/* DeepSeek config */}
        {s.aiProvider === 'deepseek' && (
          <div style={{ padding: '16px 20px', borderBottom: '1px solid #1e293b' }}>
            <div style={grid2}>
              <div>
                <label style={lbl}>DeepSeek API Key</label>
                <KeyInput
                  id="ds"
                  value={s.deepseekApiKey}
                  onChange={v => update('deepseekApiKey', v)}
                  placeholder="sk-…"
                />
                <div style={hint}>
                  Get your key at{' '}
                  <span style={{ color: '#34d399' }}>platform.deepseek.com</span>
                </div>
              </div>
              <div>
                <label style={lbl}>Model</label>
                <select value={s.deepseekModel} onChange={e => update('deepseekModel', e.target.value)} style={inp}>
                  {DEEPSEEK_MODELS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
                </select>
                <div style={hint}>DeepSeek Chat is fast and affordable for stock analysis.</div>
              </div>
            </div>
          </div>
        )}

        {/* Test connection */}
        {s.aiProvider !== 'none' && (
          <div style={{ padding: '14px 20px', display: 'flex', alignItems: 'center', gap: '12px' }}>
            <button
              onClick={testAiConnection}
              disabled={aiTestState === 'loading'}
              style={{
                padding: '8px 18px', borderRadius: '8px', fontSize: '13px', fontWeight: 600,
                cursor: aiTestState === 'loading' ? 'not-allowed' : 'pointer',
                background: 'rgba(167,139,250,0.12)', border: '1px solid rgba(167,139,250,0.3)',
                color: '#c4b5fd', transition: 'all 0.15s',
              }}
            >
              {aiTestState === 'loading' ? '⟳ Testing…' : 'Test Connection'}
            </button>
            {aiTestMsg && (
              <span style={{ fontSize: '12px', color: aiTestState === 'ok' ? '#4ade80' : '#f87171' }}>
                {aiTestMsg}
              </span>
            )}
            <span style={{ fontSize: '11px', color: '#334155', marginLeft: 'auto' }}>
              AI analysis is available on stock detail pages once configured.
            </span>
          </div>
        )}

        {s.aiProvider === 'none' && (
          <div style={{ padding: '14px 20px', fontSize: '12px', color: '#334155' }}>
            Select a provider above to enable AI-powered stock analysis and chat on stock detail pages.
          </div>
        )}
      </div>

      {/* ── Data & Refresh ──────────────────────────────────────────────── */}
      <div style={section('#4f46e5')}>
        <div style={sectionBar('linear-gradient(90deg,#10b981,#34d399,#10b981)')} />
        <div style={sectionHead}>Data & Refresh</div>
        <div style={grid2}>
          <div>
            <label style={lbl}>Price Refresh Interval</label>
            <select value={s.priceRefreshInterval} onChange={e => update('priceRefreshInterval', Number(e.target.value))} style={inp}>
              <option value={30}>30 seconds</option>
              <option value={60}>60 seconds</option>
              <option value={120}>2 minutes</option>
              <option value={300}>5 minutes</option>
            </select>
            <div style={hint}>How often live prices auto-refresh on dashboards.</div>
          </div>
          <div>
            <label style={lbl}>News Max Age</label>
            <select value={s.newsMaxAgeDays} onChange={e => update('newsMaxAgeDays', Number(e.target.value))} style={inp}>
              <option value={3}>3 days</option>
              <option value={7}>7 days</option>
              <option value={14}>14 days</option>
              <option value={30}>30 days</option>
            </select>
            <div style={hint}>Discard articles older than this from the yfinance feed.</div>
          </div>
        </div>
        <div style={{ ...grid2, paddingTop: 0 }}>
          <div>
            <label style={lbl}>Default Chart Limit</label>
            <select value={s.defaultChartLimit} onChange={e => update('defaultChartLimit', Number(e.target.value))} style={inp}>
              <option value={100}>100 days</option>
              <option value={200}>200 days</option>
              <option value={400}>400 days</option>
              <option value={730}>2 years</option>
            </select>
            <div style={hint}>Default number of historical bars in price charts.</div>
          </div>
          <div />
        </div>
      </div>

      {/* ── Notifications ──────────────────────────────────────────────── */}
      <div style={section('#0ea5e9')}>
        <div style={sectionBar('linear-gradient(90deg,#0ea5e9,#38bdf8,#0ea5e9)')} />
        <div style={sectionHead}>Notifications</div>
        <div style={grid2}>
          <div>
            <label style={lbl}>Notification Sound</label>
            <div style={{ display: 'flex', gap: '8px', marginTop: '4px' }}>
              {([true, false] as const).map(v => (
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
          </div>
        </div>
        <div style={{ padding: '0 20px 16px' }}>
          <Link href="/alerts" style={{
            fontSize: '12px', color: '#4f46e5', textDecoration: 'none',
            padding: '6px 14px', border: '1px solid rgba(79,70,229,0.3)',
            borderRadius: '6px', background: 'rgba(79,70,229,0.08)',
          }}>
            Manage Alerts →
          </Link>
        </div>
      </div>

      {/* ── ML & Analysis ──────────────────────────────────────────────── */}
      <div style={section('#10b981')}>
        <div style={sectionBar('linear-gradient(90deg,#f59e0b,#fbbf24,#f59e0b)')} />
        <div style={sectionHead}>ML & Analysis</div>
        <div style={grid2}>
          <div>
            <label style={lbl}>Default ML Model</label>
            <select value={s.defaultMlModel} onChange={e => update('defaultMlModel', e.target.value)} style={inp}>
              <option value="xgboost">XGBoost (recommended)</option>
              <option value="random_forest">Random Forest</option>
              <option value="gradient_boosting">Gradient Boosting</option>
              <option value="lstm">LSTM (deep learning)</option>
            </select>
          </div>
          <div style={{ display: 'flex', alignItems: 'flex-end' }}>
            <div style={{
              padding: '10px 14px', borderRadius: '8px', fontSize: '12px',
              background: 'rgba(16,185,129,0.08)', border: '1px solid rgba(16,185,129,0.2)',
              color: '#6ee7b7', lineHeight: 1.5, width: '100%',
            }}>
              <strong>XGBoost</strong> is fastest and most accurate for this dataset. LSTM is experimental and may be slow.
            </div>
          </div>
        </div>
      </div>

      {/* ── Account ──────────────────────────────────────────────── */}
      <div style={section('#f59e0b')}>
        <div style={sectionBar('linear-gradient(90deg,#f59e0b,#fbbf24,#f59e0b)')} />
        <div style={sectionHead}>Account</div>
        <div style={{ padding: '16px 20px', display: 'flex', alignItems: 'center', gap: '12px' }}>
          <Link href="/login?tab=reset" style={{
            fontSize: '13px', color: '#fbbf24', textDecoration: 'none',
            padding: '8px 16px', border: '1px solid rgba(251,191,36,0.3)',
            borderRadius: '8px', background: 'rgba(251,191,36,0.08)', fontWeight: 600,
          }}>
            Change Password
          </Link>
          <span style={{ fontSize: '12px', color: '#334155' }}>Update your login credentials</span>
        </div>
      </div>

    </div>
  );
}
