import { useState, useEffect, useRef } from 'react';
import Link from 'next/link';
import { loadSettings, saveSettings, type AppSettings } from '@/lib/settings';
import { getSession, changePassword, startImpersonation } from '@/lib/auth';
import { api, type AppUser, type BrokerConnection, type BrokerType } from '@/lib/api';
import { storage } from '@/lib/storage';
import { isPushSupported, getExistingSubscription, enablePushNotifications, disablePushNotifications } from '@/lib/push';

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

  const session = getSession();
  const isAdmin = session?.role === 'admin';

  // Feature flags (admin-controlled)
  const [brokerEnabled, setBrokerEnabled] = useState(false);
  const [featureFlagSaving, setFeatureFlagSaving] = useState(false);

  // T230-ALERTING-PUSH-NOTIFICATIONS
  const [pushSupported, setPushSupported] = useState(false);
  const [pushSubscribed, setPushSubscribed] = useState(false);
  const [pushBusy, setPushBusy] = useState(false);
  const [pushError, setPushError] = useState('');

  useEffect(() => {
    setPushSupported(isPushSupported());
    getExistingSubscription().then(sub => setPushSubscribed(!!sub)).catch(() => {});
  }, []);

  async function togglePush() {
    setPushBusy(true);
    setPushError('');
    try {
      const result = pushSubscribed ? await disablePushNotifications() : await enablePushNotifications();
      if (result.ok) {
        setPushSubscribed(!pushSubscribed);
      } else {
        setPushError(result.error || 'Something went wrong.');
      }
    } catch (e) {
      setPushError(e instanceof Error ? e.message : 'Something went wrong.');
    } finally {
      setPushBusy(false);
    }
  }

  useEffect(() => {
    api.getFeatureFlags().then(f => setBrokerEnabled(f.broker_enabled)).catch(() => {});
  }, []);

  async function handleToggleBroker(val: boolean) {
    setFeatureFlagSaving(true);
    try {
      await api.pushConfig({ broker_enabled: val });
      setBrokerEnabled(val);
    } catch { /* ignore */ } finally {
      setFeatureFlagSaving(false);
    }
  }

  // Profile email
  const [profileEmail, setProfileEmail] = useState('');
  const [emailMsg, setEmailMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [emailSaving, setEmailSaving] = useState(false);
  const [syncingEmail, setSyncingEmail] = useState(false);

  useEffect(() => {
    api.getMe().then(u => { if (u.email) setProfileEmail(u.email); }).catch(() => {});
  }, []);

  async function handleSaveEmail(e: React.FormEvent) {
    e.preventDefault();
    setEmailSaving(true);
    setEmailMsg(null);
    try {
      await api.updateProfile({ email: profileEmail.trim() || undefined });
      if (profileEmail.trim()) localStorage.setItem('stockai_alert_email', profileEmail.trim());
      // Auto-sync to all alerts after saving
      if (profileEmail.trim()) {
        try { await api.syncAlertEmail(); } catch {}
      }
      setEmailMsg({ ok: true, text: 'Email saved and synced to all alerts.' });
      setTimeout(() => setEmailMsg(null), 3000);
    } catch {
      setEmailMsg({ ok: false, text: 'Failed to save email.' });
    } finally {
      setEmailSaving(false);
    }
  }

  async function handleSyncEmail() {
    setSyncingEmail(true);
    setEmailMsg(null);
    try {
      const r = await api.syncAlertEmail();
      setEmailMsg({ ok: true, text: `Synced to ${r.price_alerts_updated} price alert${r.price_alerts_updated !== 1 ? 's' : ''} and ${r.signal_alerts_updated} signal alert${r.signal_alerts_updated !== 1 ? 's' : ''}.` });
      setTimeout(() => setEmailMsg(null), 4000);
    } catch (err: any) {
      setEmailMsg({ ok: false, text: err?.message || 'Sync failed — make sure you have an email saved.' });
    } finally {
      setSyncingEmail(false);
    }
  }

  // Change-password (from settings)
  const [cpOld, setCpOld] = useState('');
  const [cpNew, setCpNew] = useState('');
  const [cpConfirm, setCpConfirm] = useState('');
  const [cpMsg, setCpMsg] = useState<{ ok: boolean; text: string } | null>(null);

  // User management (admin)
  const [users, setUsers] = useState<AppUser[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);
  const [newUsername, setNewUsername] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newRole, setNewRole] = useState<'user' | 'admin'>('user');
  const [createMsg, setCreateMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [resetTarget, setResetTarget] = useState('');
  const [resetPwd, setResetPwd] = useState('');
  const [resetMsg, setResetMsg] = useState<{ ok: boolean; text: string } | null>(null);

  // Broker accounts
  const [brokers, setBrokers] = useState<BrokerConnection[]>([]);
  const [brokerLoading, setBrokerLoading] = useState(false);
  const [brokerMsg, setBrokerMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [newBrokerType, setNewBrokerType] = useState<BrokerType>('etrade_sandbox');
  const [newBrokerName, setNewBrokerName] = useState('');
  const [newBrokerKey, setNewBrokerKey] = useState('');
  const [newBrokerSecret, setNewBrokerSecret] = useState('');
  const [newBrokerAcctNum, setNewBrokerAcctNum] = useState('');
  const [oauthUrl, setOauthUrl] = useState<Record<number, string>>({});
  const [oauthVerifier, setOauthVerifier] = useState<Record<number, string>>({});
  const [brokerAccount, setBrokerAccount] = useState<Record<number, { equity: number; cash: number; buying_power: number } | null>>({});

  useEffect(() => {
    if (!session) return;
    api.brokerList().then(setBrokers).catch(() => {});
  }, []);

  async function handleCreateBroker(e: React.FormEvent) {
    e.preventDefault();
    setBrokerLoading(true);
    setBrokerMsg(null);
    try {
      const payload: Parameters<typeof api.brokerCreate>[0] = {
        name: newBrokerName.trim(),
        broker_type: newBrokerType,
      };
      if (newBrokerType === 'etrade' || newBrokerType === 'etrade_sandbox') {
        payload.consumer_key    = newBrokerKey.trim();
        payload.consumer_secret = newBrokerSecret.trim();
      } else {
        payload.account_number = newBrokerAcctNum.trim();
      }
      const conn = await api.brokerCreate(payload);
      setBrokers(prev => [...prev, conn]);
      setNewBrokerName(''); setNewBrokerKey(''); setNewBrokerSecret(''); setNewBrokerAcctNum('');
      setBrokerMsg({ ok: true, text: 'Broker connection added.' });
      setTimeout(() => setBrokerMsg(null), 3000);
    } catch (err: unknown) {
      setBrokerMsg({ ok: false, text: err instanceof Error ? err.message : 'Failed to add broker.' });
    } finally {
      setBrokerLoading(false);
    }
  }

  async function handleDeleteBroker(id: number) {
    if (!confirm('Remove this broker connection?')) return;
    try {
      await api.brokerDelete(id);
      setBrokers(prev => prev.filter(b => b.id !== id));
    } catch {
      setBrokerMsg({ ok: false, text: 'Failed to remove broker.' });
    }
  }

  async function handleOAuthStart(id: number) {
    try {
      const res = await api.brokerOAuthStart(id);
      setOauthUrl(prev => ({ ...prev, [id]: res.authorize_url }));
    } catch (err: unknown) {
      setBrokerMsg({ ok: false, text: err instanceof Error ? err.message : 'OAuth start failed.' });
    }
  }

  async function handleOAuthComplete(id: number) {
    const verifier = (oauthVerifier[id] || '').trim();
    if (!verifier) return;
    try {
      await api.brokerOAuthComplete(id, verifier);
      const updated = await api.brokerList();
      setBrokers(updated);
      setOauthUrl(prev => { const n = { ...prev }; delete n[id]; return n; });
      setOauthVerifier(prev => { const n = { ...prev }; delete n[id]; return n; });
      setBrokerMsg({ ok: true, text: 'E*Trade authorized successfully.' });
      setTimeout(() => setBrokerMsg(null), 3000);
    } catch (err: unknown) {
      setBrokerMsg({ ok: false, text: err instanceof Error ? err.message : 'OAuth failed.' });
    }
  }

  async function handleLoadAccount(id: number) {
    try {
      const acct = await api.brokerAccount(id);
      setBrokerAccount(prev => ({ ...prev, [id]: { equity: acct.equity, cash: acct.cash_available, buying_power: acct.buying_power } }));
    } catch (err: unknown) {
      setBrokerMsg({ ok: false, text: err instanceof Error ? err.message : 'Failed to load account.' });
    }
  }

  // Admin shared AI key
  const [sharedKeyMsg, setSharedKeyMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [sharedKeyLoading, setSharedKeyLoading] = useState(false);

  async function handlePushSharedKey() {
    if (s.aiProvider === 'none') return;
    setSharedKeyLoading(true);
    setSharedKeyMsg(null);
    try {
      const payload: Parameters<typeof api.pushConfig>[0] = {};
      if (s.aiProvider === 'claude') {
        if (s.claudeApiKey.trim()) payload.claude_api_key = s.claudeApiKey.trim();
        if (s.claudeModel) payload.claude_model = s.claudeModel;
      } else {
        if (s.deepseekApiKey.trim()) payload.deepseek_api_key = s.deepseekApiKey.trim();
        if (s.deepseekModel) payload.deepseek_model = s.deepseekModel;
      }
      await api.pushConfig(payload);
      setSharedKeyMsg({ ok: true, text: 'Shared key saved — all users can now use AI features.' });
      setTimeout(() => setSharedKeyMsg(null), 4000);
    } catch {
      setSharedKeyMsg({ ok: false, text: 'Failed to save shared key.' });
    } finally {
      setSharedKeyLoading(false);
    }
  }

  // Import / Export
  const [ioStatus, setIoStatus]   = useState<{ ok: boolean; text: string } | null>(null);
  const [exporting, setExporting] = useState(false);
  const [importing, setImporting] = useState(false);
  const importRef = useRef<HTMLInputElement>(null);

  async function handleExport() {
    setExporting(true);
    setIoStatus(null);
    try {
      const [lists, alerts] = await Promise.all([api.listWatchlists(), api.listAlerts()]);
      const watchlists = await Promise.all(
        lists.map(async l => ({
          id: l.id, name: l.name,
          symbols: (await api.listWatchlist(l.id)).map(s => ({ symbol: s.symbol, market: s.market, currency: s.currency })),
        }))
      );
      const bundle = {
        version: 1,
        exportedAt: new Date().toISOString(),
        exportedBy: session?.username ?? 'unknown',
        watchlists,
        positions: JSON.parse(storage.getItem('positions') ?? '[]'),
        trades:    JSON.parse(storage.getItem('trades')    ?? '{}'),
        cash:      JSON.parse(storage.getItem('positions_cash') ?? '{"USD":0,"HKD":0}'),
        alerts:    alerts.map(a => ({ symbol: a.symbol, condition: a.condition, threshold: a.threshold, note: a.note })),
      };
      const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
      const url  = URL.createObjectURL(blob);
      const tag  = document.createElement('a');
      tag.href = url; tag.download = `stockai-export-${new Date().toISOString().slice(0,10)}.json`; tag.click();
      URL.revokeObjectURL(url);
      setIoStatus({ ok: true, text: `Exported ${watchlists.reduce((n, l) => n + l.symbols.length, 0)} stocks, ${bundle.positions.length} positions, ${bundle.alerts.length} alerts.` });
    } catch (e) {
      setIoStatus({ ok: false, text: e instanceof Error ? e.message : 'Export failed.' });
    } finally {
      setExporting(false);
    }
  }

  async function handleImport(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = '';
    setImporting(true);
    setIoStatus(null);
    try {
      const text   = await file.text();
      const bundle = JSON.parse(text);
      if (!bundle.version || !bundle.watchlists) throw new Error('Invalid export file.');

      // ── Step 1: ensure every symbol exists in the stocks table ──
      // addStock is idempotent (returns "exists" if already there) and triggers
      // background price ingestion. Run in parallel batches of 8.
      const allSymbols: string[] = [
        ...new Set(
          (bundle.watchlists as { symbols: { symbol: string }[] }[])
            .flatMap(l => l.symbols.map(s => s.symbol))
        ),
      ];
      setIoStatus({ ok: true, text: `Adding ${allSymbols.length} stocks to database… (this may take a minute)` });
      const BATCH = 8;
      for (let i = 0; i < allSymbols.length; i += BATCH) {
        await Promise.allSettled(allSymbols.slice(i, i + BATCH).map(sym => api.addStock(sym)));
      }

      // ── Step 2: restore watchlists ──
      let addedStocks = 0, skippedStocks = 0;
      const existingLists = await api.listWatchlists();
      for (const exportedList of bundle.watchlists as { name: string; symbols: { symbol: string }[] }[]) {
        let target = existingLists.find(l => l.name === exportedList.name);
        if (!target) target = await api.createWatchlist(exportedList.name);
        const existing = new Set((await api.listWatchlist(target.id)).map(s => s.symbol));
        await Promise.allSettled(
          exportedList.symbols.map(async s => {
            if (existing.has(s.symbol)) { skippedStocks++; return; }
            try { await api.addToWatchlist(s.symbol, target!.id); addedStocks++; }
            catch { skippedStocks++; }
          })
        );
      }

      // ── Step 3: restore alerts ──
      let addedAlerts = 0;
      const alertEmail = typeof window !== 'undefined' ? (localStorage.getItem('stockai_alert_email') ?? '') : '';
      if ((bundle.alerts as unknown[])?.length && alertEmail) {
        await Promise.allSettled(
          (bundle.alerts as { symbol: string; condition: string; threshold: number; note?: string }[]).map(async a => {
            try {
              await api.createAlert({ symbol: a.symbol, condition: a.condition, threshold: a.threshold, email: alertEmail, note: a.note });
              addedAlerts++;
            } catch { /* stock may not exist or alert already set */ }
          })
        );
      }

      // ── Step 4: positions & cash (localStorage) ──
      if (bundle.positions?.length) {
        const cur: { id: string; symbol: string }[] = JSON.parse(storage.getItem('positions') ?? '[]');
        const curSymbols = new Set(cur.map(p => p.symbol));
        const toAdd = (bundle.positions as { id: string; symbol: string }[]).filter(p => !curSymbols.has(p.symbol));
        storage.setItem('positions', JSON.stringify([...cur, ...toAdd]));
      }
      if (bundle.trades) {
        const cur: Record<string, unknown[]> = JSON.parse(storage.getItem('trades') ?? '{}');
        for (const [id, t] of Object.entries(bundle.trades)) {
          if (!cur[id]) cur[id] = t as unknown[];
        }
        storage.setItem('trades', JSON.stringify(cur));
      }
      if (bundle.cash) {
        const cur = JSON.parse(storage.getItem('positions_cash') ?? '{"USD":0,"HKD":0}');
        storage.setItem('positions_cash', JSON.stringify({ USD: cur.USD || bundle.cash.USD || 0, HKD: cur.HKD || bundle.cash.HKD || 0 }));
      }

      const parts = [`${addedStocks} stocks added to watchlist`];
      if (skippedStocks) parts.push(`${skippedStocks} already there`);
      if (addedAlerts)   parts.push(`${addedAlerts} alerts restored`);
      const posCount = (bundle.positions ?? []).length;
      if (posCount)      parts.push(`${posCount} positions merged`);
      parts.push('Price data ingesting in background — refresh in a few minutes.');
      setIoStatus({ ok: true, text: parts.join(' · ') });
    } catch (err) {
      setIoStatus({ ok: false, text: err instanceof Error ? err.message : 'Import failed — check the file format.' });
    } finally {
      setImporting(false);
    }
  }

  useEffect(() => {
    if (isAdmin) {
      setUsersLoading(true);
      api.listUsers().then(setUsers).catch(() => {}).finally(() => setUsersLoading(false));
    }
  }, [isAdmin]);

  async function handleChangePassword(e: React.FormEvent) {
    e.preventDefault();
    setCpMsg(null);
    if (cpNew !== cpConfirm) { setCpMsg({ ok: false, text: 'New passwords do not match.' }); return; }
    if (cpNew.length < 4) { setCpMsg({ ok: false, text: 'New password must be at least 4 characters.' }); return; }
    const result = await changePassword(cpOld, cpNew);
    if (result === 'ok') {
      setCpMsg({ ok: true, text: 'Password changed successfully.' });
      setCpOld(''); setCpNew(''); setCpConfirm('');
    } else if (result === 'wrong_password') {
      setCpMsg({ ok: false, text: 'Current password is incorrect.' });
    } else {
      setCpMsg({ ok: false, text: 'Server error. Please try again.' });
    }
  }

  async function handleCreateUser(e: React.FormEvent) {
    e.preventDefault();
    setCreateMsg(null);
    try {
      const u = await api.createUser(newUsername, newPassword, newRole);
      setUsers(prev => [...prev, u]);
      setNewUsername(''); setNewPassword('');
      setCreateMsg({ ok: true, text: `User "${u.username}" created.` });
    } catch (err: any) {
      setCreateMsg({ ok: false, text: err.message ?? 'Failed to create user.' });
    }
  }

  async function handleDeleteUser(username: string) {
    if (!confirm(`Delete user "${username}"? This cannot be undone.`)) return;
    try {
      await api.deleteUser(username);
      setUsers(prev => prev.filter(u => u.username !== username));
    } catch {}
  }

  async function handleToggleUser(username: string) {
    try {
      const res = await api.toggleUser(username);
      setUsers(prev => prev.map(u => u.username === username ? { ...u, is_active: res.is_active } : u));
    } catch {}
  }

  async function handleAdminReset(e: React.FormEvent) {
    e.preventDefault();
    setResetMsg(null);
    if (!resetTarget) { setResetMsg({ ok: false, text: 'Select a user.' }); return; }
    if (resetPwd.length < 4) { setResetMsg({ ok: false, text: 'Password must be at least 4 characters.' }); return; }
    try {
      await api.adminResetPassword(resetTarget, resetPwd);
      setResetMsg({ ok: true, text: `Password reset for "${resetTarget}".` });
      setResetPwd('');
    } catch {
      setResetMsg({ ok: false, text: 'Failed to reset password.' });
    }
  }

  function update<K extends keyof AppSettings>(key: K, value: AppSettings[K]) {
    setS(prev => ({ ...prev, [key]: value }));
  }

  function handleSave() {
    saveSettings(s);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
    api.pushConfig({
      polygon_api_key: s.polygonApiKey || undefined,
      alpha_vantage_api_key: s.alphaVantageApiKey || undefined,
      quiver_api_key: s.quiverApiKey || undefined,
    }).catch(() => {});
  }

  async function testAiConnection() {
    setAiTestState('loading');
    setAiTestMsg('');
    try {
      const base = process.env.NEXT_PUBLIC_API_URL ?? '/api';
      const token = typeof window !== 'undefined' ? localStorage.getItem('stockai_jwt')?.trim() : null;
      const res = await fetch(`${base}/ai/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
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

      {/* ── Congressional & Insider Trading ────────────────────────────── */}
      <div style={section('#fb923c')}>
        <div style={sectionBar('linear-gradient(90deg,#fb923c,#fdba74,#fb923c)')} />
        <div style={sectionHead}>Congressional & Insider Trading</div>

        <div style={{ padding: '16px 20px', borderBottom: '1px solid #1e293b' }}>
          <label style={lbl}>Quiver Quantitative API Key</label>
          <KeyInput
            id="quiver"
            value={s.quiverApiKey}
            onChange={v => update('quiverApiKey', v)}
            placeholder="Enter your Quiver Quantitative API key"
          />
          <div style={hint}>
            Free key at{' '}
            <span style={{ color: '#fb923c' }}>quiverquant.com</span>
            {' '}· Powers the Congressional Trade Tracker page with real-time STOCK Act disclosures.
          </div>
        </div>
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

      {/* ── Trading Style ──────────────────────────────────────────────── */}
      <div style={section('#6366f1')}>
        <div style={sectionBar('linear-gradient(90deg,#6366f1,#a78bfa,#ec4899,#a78bfa,#6366f1)')} />
        <div style={sectionHead}>Trading Style — AI Signal Horizon</div>
        <div style={{ padding: '16px 20px' }}>
          <label style={lbl}>Select your default trading style</label>
          <div style={{ display: 'flex', gap: '10px', marginTop: '6px' }}>
            {([
              {
                value: 'SHORT' as const,
                label: 'Short Term',
                horizon: '1 – 5 days',
                color: '#f87171',
                desc: 'Pure technical analysis. No earnings or news filters. Tight momentum thresholds. Best for day/swing trades on volatile small-caps.',
              },
              {
                value: 'SWING' as const,
                label: 'Swing Trade',
                horizon: '5 – 20 days',
                color: '#818cf8',
                desc: 'Balanced TA + momentum + mild regime filter. Standard earnings & news compression. The recommended default for most stocks.',
              },
              {
                value: 'LONG' as const,
                label: 'Long Term',
                horizon: '30 – 90 days',
                color: '#4ade80',
                desc: 'Fundamentals-heavy. K-Score boost/penalty applied. Strong weekly alignment required. Filters out noise for position trades.',
              },
              {
                value: 'GROWTH' as const,
                label: 'Growth / Momentum',
                horizon: 'Momentum · no weekly gate',
                color: '#a78bfa',
                desc: 'Relaxed thresholds for high-volatility AI/tech stocks. SMA20>SMA50 replaces SMA50>SMA200. ML bar 0.60. No RS compression. Best for NVDA, PLTR, AI-sector names.',
              },
            ]).map(opt => (
              <button
                key={opt.value}
                onClick={() => update('tradingStyle', opt.value)}
                style={{
                  flex: 1, padding: '14px 16px', borderRadius: '12px', cursor: 'pointer',
                  textAlign: 'left', transition: 'all 0.15s',
                  background: s.tradingStyle === opt.value ? `${opt.color}12` : 'rgba(15,23,42,0.6)',
                  border: s.tradingStyle === opt.value ? `1px solid ${opt.color}55` : '1px solid #1e293b',
                  boxShadow: s.tradingStyle === opt.value ? `0 0 0 1px ${opt.color}22` : 'none',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                  <div style={{ fontSize: '14px', fontWeight: 800, color: s.tradingStyle === opt.value ? opt.color : '#94a3b8' }}>
                    {opt.label}
                  </div>
                  {s.tradingStyle === opt.value && (
                    <span style={{ fontSize: '9px', fontWeight: 700, padding: '2px 6px', borderRadius: '4px', background: `${opt.color}20`, color: opt.color, letterSpacing: '0.06em' }}>
                      ACTIVE
                    </span>
                  )}
                </div>
                <div style={{ fontSize: '11px', fontWeight: 600, color: s.tradingStyle === opt.value ? opt.color : '#475569', marginBottom: '8px' }}>
                  {opt.horizon}
                </div>
                <div style={{ fontSize: '11px', color: '#475569', lineHeight: 1.5 }}>
                  {opt.desc}
                </div>
              </button>
            ))}
          </div>
          <div style={{ ...hint, marginTop: '10px' }}>
            This controls which AI signal criteria apply across all pages (Dashboard, Rankings, Watchlist, Screener, etc.).
            SHORT uses pure TA — ideal for volatile small-caps where fundamentals are unreliable.
            Signals are pre-computed for all 3 styles; switching style takes effect immediately.
          </div>
        </div>
      </div>

      {/* ── Position Sizing ────────────────────────────────────────────── */}
      <div style={section('#34d399')}>
        <div style={sectionBar('linear-gradient(90deg,#34d399,#6ee7b7,#34d399)')} />
        <div style={sectionHead}>Position Sizing — ATR-Based Risk Management</div>
        <div style={{ padding: '16px 20px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
          <div>
            <label style={lbl}>Account Size (USD)</label>
            <input
              type="number" min={0} step={1000}
              value={s.accountSize || ''}
              onChange={e => update('accountSize', parseFloat(e.target.value) || 0)}
              placeholder="e.g. 50000"
              style={{ width: '100%', padding: '8px 10px', borderRadius: '8px', border: '1px solid #1e293b', background: '#020617', color: '#e2e8f0', fontSize: '13px' }}
            />
            <div style={hint}>Total portfolio size used to compute share quantity.</div>
          </div>
          <div>
            <label style={lbl}>Risk Per Trade (%)</label>
            <input
              type="number" min={0.1} max={5} step={0.1}
              value={s.riskPctPerTrade || ''}
              onChange={e => update('riskPctPerTrade', parseFloat(e.target.value) || 1)}
              placeholder="e.g. 1"
              style={{ width: '100%', padding: '8px 10px', borderRadius: '8px', border: '1px solid #1e293b', background: '#020617', color: '#e2e8f0', fontSize: '13px' }}
            />
            <div style={hint}>% of account to risk on each trade. 1–2% is professional standard.</div>
          </div>
        </div>
        <div style={{ padding: '0 20px 14px', fontSize: '11px', color: '#334155', lineHeight: 1.6 }}>
          Used on stock detail pages to show ATR-based stop-loss price, recommended share quantity, and risk/reward ratio.
          Stop placed at <strong style={{ color: '#4ade80' }}>entry − 2 × ATR(14)</strong>.
          Position size = <strong style={{ color: '#4ade80' }}>(account × risk%) ÷ (entry − stop)</strong>.
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

        {/* Admin: share key with all users */}
        {isAdmin && s.aiProvider !== 'none' && (
          <div style={{ padding: '14px 20px', borderTop: '1px solid #1e293b', background: 'rgba(167,139,250,0.04)' }}>
            <label style={lbl}>
              Shared Server Key
              <span style={{ fontSize: '10px', color: '#a78bfa', fontWeight: 400, marginLeft: '8px', padding: '2px 8px', border: '1px solid rgba(167,139,250,0.3)', borderRadius: '4px', background: 'rgba(167,139,250,0.1)' }}>Admin only</span>
            </label>
            <div style={{ fontSize: '12px', color: '#475569', marginBottom: '10px' }}>
              Push your configured {s.aiProvider === 'claude' ? 'Claude' : 'DeepSeek'} key + model to the server so users without their own key (e.g. lauwing2) can use AI features.
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
              <button
                onClick={handlePushSharedKey}
                disabled={sharedKeyLoading}
                style={{
                  padding: '8px 18px', borderRadius: '8px', fontSize: '13px', fontWeight: 600,
                  cursor: sharedKeyLoading ? 'not-allowed' : 'pointer',
                  background: 'rgba(167,139,250,0.15)', border: '1px solid rgba(167,139,250,0.4)',
                  color: '#c4b5fd', transition: 'all 0.15s',
                }}
              >
                {sharedKeyLoading ? '⟳ Saving…' : 'Share my key with all users'}
              </button>
              {sharedKeyMsg && (
                <span style={{ fontSize: '12px', color: sharedKeyMsg.ok ? '#4ade80' : '#f87171' }}>
                  {sharedKeyMsg.text}
                </span>
              )}
            </div>
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

        {/* T230-ALERTING-PUSH-NOTIFICATIONS: near-instant browser push, alongside email */}
        <div style={{ padding: '0 20px 16px', borderTop: '1px solid #1e293b', marginTop: '4px', paddingTop: '16px' }}>
          <label style={lbl}>Push Notifications</label>
          <div style={hint}>
            Get signal and price alerts pushed straight to this browser — seconds instead of the
            5–15 minute email delay.
          </div>
          {!pushSupported ? (
            <div style={{ ...hint, color: '#f59e0b', marginTop: '8px' }}>
              This browser doesn&apos;t support push notifications.
            </div>
          ) : (
            <div style={{ marginTop: '10px' }}>
              <button
                onClick={togglePush}
                disabled={pushBusy}
                style={{
                  padding: '9px 16px', borderRadius: '8px', fontSize: '13px', fontWeight: 600,
                  cursor: pushBusy ? 'default' : 'pointer', opacity: pushBusy ? 0.6 : 1,
                  background: pushSubscribed ? 'rgba(239,68,68,0.15)' : 'rgba(34,197,94,0.15)',
                  border: pushSubscribed ? '1px solid rgba(239,68,68,0.3)' : '1px solid rgba(34,197,94,0.3)',
                  color: pushSubscribed ? '#f87171' : '#4ade80',
                }}
              >
                {pushBusy ? 'Working…' : pushSubscribed ? '🔕 Disable push notifications' : '🔔 Enable push notifications'}
              </button>
              {pushError && <div style={{ ...hint, color: '#f87171', marginTop: '8px' }}>{pushError}</div>}
            </div>
          )}
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
        <div style={sectionHead}>Account — {session?.username}</div>

        {/* Email */}
        <form onSubmit={handleSaveEmail} style={{ padding: '16px 20px', borderBottom: '1px solid #1e293b', display: 'flex', gap: '12px', alignItems: 'flex-end' }}>
          <div style={{ flex: 1 }}>
            <label style={lbl}>Alert Email</label>
            <input
              type="email"
              value={profileEmail}
              onChange={e => setProfileEmail(e.target.value)}
              placeholder="you@example.com"
              style={inp}
            />
            <div style={hint}>Price &amp; signal alert emails are sent here. Saving auto-syncs to all your existing alerts.</div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', paddingBottom: '2px' }}>
            {emailMsg && (
              <span style={{ fontSize: '12px', color: emailMsg.ok ? '#4ade80' : '#f87171' }}>{emailMsg.text}</span>
            )}
            <button
              type="submit"
              disabled={emailSaving || syncingEmail}
              style={{ padding: '8px 18px', borderRadius: '8px', border: 'none', background: '#f59e0b', color: '#0f172a', fontSize: '13px', fontWeight: 700, cursor: (emailSaving || syncingEmail) ? 'not-allowed' : 'pointer', opacity: (emailSaving || syncingEmail) ? 0.6 : 1 }}
            >
              {emailSaving ? 'Saving…' : 'Save Email'}
            </button>
            <button
              type="button"
              onClick={handleSyncEmail}
              disabled={syncingEmail || emailSaving}
              style={{ padding: '8px 18px', borderRadius: '8px', border: '1px solid #334155', background: 'transparent', color: '#94a3b8', fontSize: '13px', fontWeight: 600, cursor: (syncingEmail || emailSaving) ? 'not-allowed' : 'pointer', opacity: (syncingEmail || emailSaving) ? 0.6 : 1 }}
            >
              {syncingEmail ? 'Syncing…' : 'Sync to all alerts'}
            </button>
          </div>
        </form>

        <form onSubmit={handleChangePassword} style={{ padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <div style={grid2}>
            <div>
              <label style={lbl}>Current Password</label>
              <input type="password" value={cpOld} onChange={e => setCpOld(e.target.value)} required placeholder="Current password" style={inp} />
            </div>
            <div />
            <div>
              <label style={lbl}>New Password</label>
              <input type="password" value={cpNew} onChange={e => setCpNew(e.target.value)} required placeholder="New password" style={inp} />
            </div>
            <div>
              <label style={lbl}>Confirm New Password</label>
              <input type="password" value={cpConfirm} onChange={e => setCpConfirm(e.target.value)} required placeholder="Repeat new password" style={inp} />
            </div>
          </div>
          {cpMsg && (
            <div style={{
              borderRadius: '8px', padding: '9px 14px', fontSize: '13px',
              background: cpMsg.ok ? 'rgba(34,197,94,0.08)' : 'rgba(239,68,68,0.08)',
              border: `1px solid ${cpMsg.ok ? 'rgba(34,197,94,0.25)' : 'rgba(239,68,68,0.25)'}`,
              color: cpMsg.ok ? '#4ade80' : '#f87171',
            }}>{cpMsg.text}</div>
          )}
          <div>
            <button type="submit" style={{
              padding: '9px 22px', borderRadius: '8px', fontSize: '13px', fontWeight: 700, cursor: 'pointer',
              background: 'rgba(251,191,36,0.15)', border: '1px solid rgba(251,191,36,0.3)', color: '#fbbf24',
            }}>
              Change Password
            </button>
          </div>
        </form>
      </div>

      {/* ── Broker Accounts (only when feature enabled) ───────────── */}
      {brokerEnabled && <div style={section('#22d3ee')}>
        <div style={sectionBar('linear-gradient(90deg,#22d3ee,#67e8f9,#22d3ee)')} />
        <div style={sectionHead}>Broker Accounts</div>

        {/* Existing connections */}
        {brokers.length > 0 && (
          <div style={{ padding: '0 20px 8px' }}>
            {brokers.map(b => {
              const isEtrade = b.broker_type === 'etrade' || b.broker_type === 'etrade_sandbox';
              const typeLabel: Record<string, string> = {
                etrade:          'E*Trade Live',
                etrade_sandbox:  'E*Trade Sandbox',
                fidelity_manual: 'Fidelity (Manual)',
              };
              const acct = brokerAccount[b.id];
              return (
                <div key={b.id} style={{ background: '#0f172a', borderRadius: 8, padding: '12px 14px', marginBottom: 10, border: '1px solid #1e293b' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                    <span style={{ fontSize: 13, fontWeight: 700, color: '#e2e8f0' }}>{b.name}</span>
                    <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 4, background: 'rgba(34,211,238,0.1)', border: '1px solid rgba(34,211,238,0.25)', color: '#22d3ee' }}>
                      {typeLabel[b.broker_type] ?? b.broker_type}
                    </span>
                    {b.is_authorized
                      ? <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 4, background: 'rgba(74,222,128,0.1)', border: '1px solid rgba(74,222,128,0.25)', color: '#4ade80' }}>✓ Authorized</span>
                      : <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 4, background: 'rgba(251,191,36,0.1)', border: '1px solid rgba(251,191,36,0.25)', color: '#fbbf24' }}>⚠ Not authorized</span>
                    }
                    {b.account_id && <span style={{ fontSize: 11, color: '#475569' }}>#{b.account_id}</span>}
                  </div>

                  {/* E*Trade OAuth flow */}
                  {isEtrade && !b.is_authorized && (
                    <div style={{ marginBottom: 8 }}>
                      {!oauthUrl[b.id] ? (
                        <button onClick={() => handleOAuthStart(b.id)} style={{ padding: '6px 14px', borderRadius: 6, fontSize: 12, fontWeight: 700, cursor: 'pointer', background: 'rgba(34,211,238,0.1)', border: '1px solid rgba(34,211,238,0.3)', color: '#22d3ee' }}>
                          Authorize with E*Trade →
                        </button>
                      ) : (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                          <a href={oauthUrl[b.id]} target="_blank" rel="noreferrer" style={{ fontSize: 12, color: '#22d3ee', wordBreak: 'break-all' }}>
                            1. Click here to authorize on E*Trade ↗
                          </a>
                          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                            <input
                              placeholder="2. Enter the PIN/verifier E*Trade showed you"
                              value={oauthVerifier[b.id] || ''}
                              onChange={e => setOauthVerifier(prev => ({ ...prev, [b.id]: e.target.value }))}
                              style={{ ...inpKey, maxWidth: 300 }}
                            />
                            <button onClick={() => handleOAuthComplete(b.id)} style={{ padding: '6px 14px', borderRadius: 6, fontSize: 12, fontWeight: 700, cursor: 'pointer', background: 'rgba(74,222,128,0.1)', border: '1px solid rgba(74,222,128,0.3)', color: '#4ade80', whiteSpace: 'nowrap' }}>
                              Complete
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Reconnect (renew token, once per trading day) */}
                  {isEtrade && b.is_authorized && (
                    <button onClick={() => api.brokerReconnect(b.id).then(() => setBrokerMsg({ ok: true, text: 'Session renewed.' })).catch(err => setBrokerMsg({ ok: false, text: String(err) }))}
                      style={{ padding: '5px 12px', borderRadius: 6, fontSize: 11, fontWeight: 600, cursor: 'pointer', background: 'rgba(34,211,238,0.08)', border: '1px solid rgba(34,211,238,0.2)', color: '#67e8f9', marginRight: 6 }}>
                      Renew Today&apos;s Session
                    </button>
                  )}

                  {/* Load account balance */}
                  {b.is_authorized && (
                    <button onClick={() => handleLoadAccount(b.id)} style={{ padding: '5px 12px', borderRadius: 6, fontSize: 11, fontWeight: 600, cursor: 'pointer', background: 'rgba(99,102,241,0.08)', border: '1px solid rgba(99,102,241,0.2)', color: '#818cf8', marginRight: 6 }}>
                      Load Balance
                    </button>
                  )}
                  <button onClick={() => handleDeleteBroker(b.id)} style={{ padding: '5px 12px', borderRadius: 6, fontSize: 11, fontWeight: 600, cursor: 'pointer', background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)', color: '#f87171' }}>
                    Remove
                  </button>

                  {acct && (
                    <div style={{ display: 'flex', gap: 16, marginTop: 8, fontSize: 12, color: '#94a3b8' }}>
                      <span>Equity: <strong style={{ color: '#e2e8f0' }}>${acct.equity.toLocaleString(undefined, { maximumFractionDigits: 0 })}</strong></span>
                      <span>Cash: <strong style={{ color: '#e2e8f0' }}>${acct.cash.toLocaleString(undefined, { maximumFractionDigits: 0 })}</strong></span>
                      <span>Buying power: <strong style={{ color: '#e2e8f0' }}>${acct.buying_power.toLocaleString(undefined, { maximumFractionDigits: 0 })}</strong></span>
                    </div>
                  )}

                  {b.broker_type === 'fidelity_manual' && (
                    <p style={{ fontSize: 11, color: '#475569', marginTop: 6, marginBottom: 0 }}>
                      Fidelity does not provide a public trading API. The app will show trade instructions that you execute manually in Fidelity&apos;s platform.
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* Add new connection */}
        <form onSubmit={handleCreateBroker} style={{ padding: '4px 20px 16px' }}>
          <label style={lbl}>Add Broker Connection</label>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
            <div style={{ flex: '0 0 auto' }}>
              <label style={{ ...lbl, marginBottom: 4 }}>Broker</label>
              <select value={newBrokerType} onChange={e => setNewBrokerType(e.target.value as BrokerType)} style={{ ...inp, width: 'auto', minWidth: 180 }}>
                <option value="etrade_sandbox">E*Trade Sandbox (paper)</option>
                <option value="etrade">E*Trade Live</option>
                <option value="fidelity_manual">Fidelity (Manual)</option>
              </select>
            </div>
            <div style={{ flex: '1 1 160px' }}>
              <label style={{ ...lbl, marginBottom: 4 }}>Display Name</label>
              <input value={newBrokerName} onChange={e => setNewBrokerName(e.target.value)} required placeholder="e.g. My E*Trade" style={inp} />
            </div>
          </div>
          {(newBrokerType === 'etrade' || newBrokerType === 'etrade_sandbox') && (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
              <div style={{ flex: '1 1 200px' }}>
                <label style={{ ...lbl, marginBottom: 4 }}>Consumer Key</label>
                <input value={newBrokerKey} onChange={e => setNewBrokerKey(e.target.value)} required placeholder="From E*Trade developer portal" style={inpKey} />
              </div>
              <div style={{ flex: '1 1 200px' }}>
                <label style={{ ...lbl, marginBottom: 4 }}>Consumer Secret</label>
                <input type="password" value={newBrokerSecret} onChange={e => setNewBrokerSecret(e.target.value)} required placeholder="Consumer secret" style={inpKey} />
              </div>
            </div>
          )}
          {newBrokerType === 'fidelity_manual' && (
            <div style={{ flex: '1 1 200px', marginBottom: 8 }}>
              <label style={{ ...lbl, marginBottom: 4 }}>Account Number</label>
              <input value={newBrokerAcctNum} onChange={e => setNewBrokerAcctNum(e.target.value)} placeholder="e.g. Z12345678" style={inp} />
            </div>
          )}
          <p style={hint}>
            {newBrokerType === 'etrade_sandbox'
              ? 'Register at developer.etrade.com → Create a sandbox app → copy Consumer Key + Secret here. After saving, click "Authorize" to complete OAuth.'
              : newBrokerType === 'etrade'
              ? 'Use production Consumer Key + Secret from developer.etrade.com. Tokens expire daily — click "Renew Today\'s Session" each trading morning.'
              : 'No API credentials needed. Trade instructions will be shown for manual execution in Fidelity\'s platform.'}
          </p>
          <button type="submit" disabled={brokerLoading} style={{ padding: '8px 18px', borderRadius: 8, fontSize: 13, fontWeight: 700, cursor: 'pointer', background: 'rgba(34,211,238,0.1)', border: '1px solid rgba(34,211,238,0.3)', color: '#22d3ee' }}>
            {brokerLoading ? 'Saving…' : '+ Add Broker'}
          </button>
          {brokerMsg && (
            <span style={{ marginLeft: 12, fontSize: 12, color: brokerMsg.ok ? '#4ade80' : '#f87171' }}>
              {brokerMsg.text}
            </span>
          )}
        </form>
      </div>}

      {/* ── User Management (admin only) ───────────────────────────── */}
      {/* ── Import / Export ── */}
      <div style={section('#0ea5e9')}>
        <div style={sectionBar('linear-gradient(90deg,#0ea5e9,#38bdf8,#0ea5e9)')} />
        <div style={sectionHead}>Import / Export</div>
        <div style={{ padding: '20px' }}>
          <p style={{ margin: '0 0 16px', fontSize: '13px', color: '#64748b', lineHeight: 1.6 }}>
            Export your watchlists, positions, and cash balances to a JSON file. Import the file on any account or system to restore your data.
          </p>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
            {/* Export */}
            <div style={{ borderRadius: '10px', border: '1px solid rgba(14,165,233,0.2)', background: 'rgba(14,165,233,0.04)', padding: '16px' }}>
              <div style={{ fontSize: '13px', fontWeight: 700, color: '#e2e8f0', marginBottom: '6px' }}>⬇ Export</div>
              <div style={{ fontSize: '12px', color: '#475569', marginBottom: '14px', lineHeight: 1.5 }}>
                Downloads a <code style={{ color: '#94a3b8', background: '#1e293b', padding: '1px 5px', borderRadius: '4px' }}>.json</code> file containing all your watchlists (with stock symbols), positions, trade history, and cash balances.
              </div>
              <button onClick={handleExport} disabled={exporting}
                style={{ padding: '9px 20px', borderRadius: '8px', border: 'none', background: 'linear-gradient(135deg,#0284c7,#0ea5e9)', color: '#fff', fontSize: '13px', fontWeight: 600, cursor: exporting ? 'wait' : 'pointer', opacity: exporting ? 0.7 : 1 }}>
                {exporting ? 'Exporting…' : 'Download Export'}
              </button>
            </div>

            {/* Import */}
            <div style={{ borderRadius: '10px', border: '1px solid rgba(14,165,233,0.2)', background: 'rgba(14,165,233,0.04)', padding: '16px' }}>
              <div style={{ fontSize: '13px', fontWeight: 700, color: '#e2e8f0', marginBottom: '6px' }}>⬆ Import</div>
              <div style={{ fontSize: '12px', color: '#475569', marginBottom: '14px', lineHeight: 1.5 }}>
                Select a previously exported <code style={{ color: '#94a3b8', background: '#1e293b', padding: '1px 5px', borderRadius: '4px' }}>.json</code> file. Stocks not already in your watchlist will be added. Positions are merged (existing ones are kept).
              </div>
              <input ref={importRef} type="file" accept=".json,application/json" style={{ display: 'none' }} onChange={handleImport} />
              <button onClick={() => importRef.current?.click()} disabled={importing}
                style={{ padding: '9px 20px', borderRadius: '8px', border: '1px solid rgba(14,165,233,0.4)', background: 'transparent', color: '#38bdf8', fontSize: '13px', fontWeight: 600, cursor: importing ? 'wait' : 'pointer', opacity: importing ? 0.7 : 1 }}>
                {importing ? 'Importing…' : 'Select File to Import'}
              </button>
            </div>
          </div>

          {ioStatus && (
            <div style={{ marginTop: '14px', padding: '10px 14px', borderRadius: '8px', background: ioStatus.ok ? 'rgba(34,197,94,0.08)' : 'rgba(239,68,68,0.08)', border: `1px solid ${ioStatus.ok ? 'rgba(34,197,94,0.3)' : 'rgba(239,68,68,0.3)'}`, fontSize: '13px', color: ioStatus.ok ? '#4ade80' : '#f87171' }}>
              {ioStatus.ok ? '✓ ' : '✕ '}{ioStatus.text}
            </div>
          )}

          <div style={{ marginTop: '14px', fontSize: '11px', color: '#334155', lineHeight: 1.6 }}>
            <strong style={{ color: '#475569' }}>What is included:</strong> Watchlists (all lists + symbols) · Positions + trade history · Cash balances (USD / HKD)<br />
            <strong style={{ color: '#475569' }}>Not included:</strong> Price alerts (require email re-entry on the target account) · Signal alert subscriptions · Browser notifications
          </div>
        </div>
      </div>

      {/* ── Feature Flags (admin only) ─────────────────────────────── */}
      {isAdmin && (
        <div style={section('#6366f1')}>
          <div style={sectionBar('linear-gradient(90deg,#6366f1,#818cf8,#6366f1)')} />
          <div style={sectionHead}>
            Feature Flags
            <span style={{ fontSize: '10px', color: '#818cf8', fontWeight: 400, marginLeft: '8px', padding: '2px 8px', border: '1px solid rgba(129,140,248,0.3)', borderRadius: '4px', background: 'rgba(99,102,241,0.1)' }}>Admin only</span>
          </div>
          <div style={{ padding: '14px 20px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
              <div style={{ flex: 1, minWidth: 200 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: '#e2e8f0' }}>Broker Integration</div>
                <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>
                  Show E*Trade / Fidelity broker connection UI in Settings and Paper Portfolio pages.
                  Turn off to hide from all users until ready for production use.
                </div>
              </div>
              <Toggle on={brokerEnabled} onChange={val => handleToggleBroker(val)} disabled={featureFlagSaving} />
              <span style={{ fontSize: 11, color: brokerEnabled ? '#4ade80' : '#475569', fontWeight: 600 }}>
                {brokerEnabled ? 'On' : 'Off'}
              </span>
            </div>
          </div>
        </div>
      )}

      {isAdmin && (
        <div style={section('#e11d48')}>
          <div style={sectionBar('linear-gradient(90deg,#e11d48,#fb7185,#e11d48)')} />
          <div style={sectionHead}>User Management <span style={{ fontSize: '10px', color: '#fb7185', fontWeight: 400, marginLeft: '8px', padding: '2px 8px', border: '1px solid rgba(251,113,133,0.3)', borderRadius: '4px', background: 'rgba(225,29,72,0.1)' }}>Admin only</span></div>

          {/* User list */}
          <div style={{ padding: '14px 20px', borderBottom: '1px solid #1e293b' }}>
            <label style={lbl}>Current Users</label>
            {usersLoading ? (
              <div style={{ fontSize: '13px', color: '#475569' }}>Loading…</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginTop: '8px' }}>
                {users.map(u => (
                  <div key={u.id} style={{
                    display: 'flex', alignItems: 'center', gap: '10px', padding: '8px 12px',
                    borderRadius: '8px', background: '#0a0f1e', border: '1px solid #1e293b',
                  }}>
                    <span style={{ flex: 1, fontSize: '13px', fontWeight: 600, color: u.is_active ? '#e2e8f0' : '#475569' }}>
                      {u.username}
                    </span>
                    <span style={{
                      fontSize: '10px', padding: '2px 7px', borderRadius: '4px', fontWeight: 700,
                      background: u.role === 'admin' ? 'rgba(251,113,133,0.15)' : 'rgba(99,102,241,0.15)',
                      color: u.role === 'admin' ? '#fb7185' : '#818cf8',
                      border: u.role === 'admin' ? '1px solid rgba(251,113,133,0.3)' : '1px solid rgba(99,102,241,0.3)',
                    }}>{u.role.toUpperCase()}</span>
                    <span style={{
                      fontSize: '10px', padding: '2px 7px', borderRadius: '4px',
                      background: u.is_active ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)',
                      color: u.is_active ? '#4ade80' : '#f87171',
                    }}>{u.is_active ? 'Active' : 'Disabled'}</span>
                    {u.username !== session?.username && (
                      <>
                        <button onClick={() => handleToggleUser(u.username)} style={{
                          fontSize: '11px', padding: '4px 10px', borderRadius: '6px', cursor: 'pointer',
                          background: 'transparent', border: '1px solid #1e293b', color: '#64748b',
                        }}>{u.is_active ? 'Disable' : 'Enable'}</button>
                        <button onClick={() => handleDeleteUser(u.username)} style={{
                          fontSize: '11px', padding: '4px 10px', borderRadius: '6px', cursor: 'pointer',
                          background: 'transparent', border: '1px solid rgba(239,68,68,0.3)', color: '#f87171',
                        }}>Delete</button>
                        {u.is_active && (
                          <button onClick={async () => {
                            try {
                              const result = await api.impersonate(u.username);
                              startImpersonation(result.token);
                              window.location.href = '/';
                            } catch {
                              alert(`Failed to switch to ${u.username}`);
                            }
                          }} style={{
                            fontSize: '11px', padding: '4px 10px', borderRadius: '6px', cursor: 'pointer',
                            background: 'rgba(124,58,237,0.15)', border: '1px solid rgba(124,58,237,0.4)', color: '#a78bfa',
                          }}>Switch to</button>
                        )}
                      </>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Create user */}
          <div style={{ padding: '14px 20px', borderBottom: '1px solid #1e293b' }}>
            <label style={lbl}>Create New User</label>
            <form onSubmit={handleCreateUser} style={{ display: 'flex', gap: '10px', marginTop: '8px', flexWrap: 'wrap', alignItems: 'flex-end' }}>
              <div style={{ flex: 2, minWidth: '120px' }}>
                <label style={{ ...lbl, marginBottom: '4px' }}>Username</label>
                <input value={newUsername} onChange={e => setNewUsername(e.target.value)} required placeholder="e.g. john" style={inp} />
              </div>
              <div style={{ flex: 2, minWidth: '120px' }}>
                <label style={{ ...lbl, marginBottom: '4px' }}>Password</label>
                <input type="password" value={newPassword} onChange={e => setNewPassword(e.target.value)} required placeholder="Min 4 chars" style={inp} />
              </div>
              <div style={{ flex: 1, minWidth: '100px' }}>
                <label style={{ ...lbl, marginBottom: '4px' }}>Role</label>
                <select value={newRole} onChange={e => setNewRole(e.target.value as 'user' | 'admin')} style={inp}>
                  <option value="user">User</option>
                  <option value="admin">Admin</option>
                </select>
              </div>
              <button type="submit" style={{
                padding: '9px 18px', borderRadius: '8px', fontSize: '13px', fontWeight: 700, cursor: 'pointer',
                background: 'rgba(225,29,72,0.15)', border: '1px solid rgba(225,29,72,0.3)', color: '#fb7185',
                flexShrink: 0,
              }}>
                + Create
              </button>
            </form>
            {createMsg && (
              <div style={{
                marginTop: '8px', borderRadius: '8px', padding: '8px 12px', fontSize: '13px',
                background: createMsg.ok ? 'rgba(34,197,94,0.08)' : 'rgba(239,68,68,0.08)',
                border: `1px solid ${createMsg.ok ? 'rgba(34,197,94,0.25)' : 'rgba(239,68,68,0.25)'}`,
                color: createMsg.ok ? '#4ade80' : '#f87171',
              }}>{createMsg.text}</div>
            )}
          </div>

          {/* Admin reset another user's password */}
          <div style={{ padding: '14px 20px' }}>
            <label style={lbl}>Reset Another User&apos;s Password</label>
            <form onSubmit={handleAdminReset} style={{ display: 'flex', gap: '10px', marginTop: '8px', flexWrap: 'wrap', alignItems: 'flex-end' }}>
              <div style={{ flex: 2, minWidth: '120px' }}>
                <label style={{ ...lbl, marginBottom: '4px' }}>User</label>
                <select value={resetTarget} onChange={e => setResetTarget(e.target.value)} style={inp}>
                  <option value="">Select user…</option>
                  {users.filter(u => u.username !== session?.username).map(u => (
                    <option key={u.id} value={u.username}>{u.username}</option>
                  ))}
                </select>
              </div>
              <div style={{ flex: 2, minWidth: '140px' }}>
                <label style={{ ...lbl, marginBottom: '4px' }}>New Password</label>
                <input type="password" value={resetPwd} onChange={e => setResetPwd(e.target.value)} required placeholder="New password" style={inp} />
              </div>
              <button type="submit" style={{
                padding: '9px 18px', borderRadius: '8px', fontSize: '13px', fontWeight: 700, cursor: 'pointer',
                background: 'rgba(225,29,72,0.1)', border: '1px solid rgba(225,29,72,0.3)', color: '#fb7185',
                flexShrink: 0,
              }}>
                Reset
              </button>
            </form>
            {resetMsg && (
              <div style={{
                marginTop: '8px', borderRadius: '8px', padding: '8px 12px', fontSize: '13px',
                background: resetMsg.ok ? 'rgba(34,197,94,0.08)' : 'rgba(239,68,68,0.08)',
                border: `1px solid ${resetMsg.ok ? 'rgba(34,197,94,0.25)' : 'rgba(239,68,68,0.25)'}`,
                color: resetMsg.ok ? '#4ade80' : '#f87171',
              }}>{resetMsg.text}</div>
            )}
          </div>
        </div>
      )}

    </div>
  );
}
