import { useState, useEffect, FormEvent } from 'react';
import { useRouter } from 'next/router';
import { login, resetPassword, getSession } from '@/lib/auth';

type Mode = 'login' | 'reset';

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>('login');

  // Login state
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loginError, setLoginError] = useState('');
  const [loginLoading, setLoginLoading] = useState(false);

  // Reset state
  const [rUser, setRUser] = useState('');
  const [rOld, setROld] = useState('');
  const [rNew, setRNew] = useState('');
  const [rConfirm, setRConfirm] = useState('');
  const [resetMsg, setResetMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    if (getSession()) router.replace('/');
  }, []);

  async function handleLogin(e: FormEvent) {
    e.preventDefault();
    setLoginLoading(true);
    setLoginError('');
    await new Promise(r => setTimeout(r, 350)); // brief delay for UX
    if (login(username, password)) {
      router.replace('/');
    } else {
      setLoginError('Incorrect username or password.');
      setLoginLoading(false);
    }
  }

  function handleReset(e: FormEvent) {
    e.preventDefault();
    setResetMsg(null);
    if (rNew !== rConfirm) {
      setResetMsg({ ok: false, text: 'New passwords do not match.' });
      return;
    }
    if (rNew.length < 4) {
      setResetMsg({ ok: false, text: 'New password must be at least 4 characters.' });
      return;
    }
    const result = resetPassword(rUser, rOld, rNew);
    if (result === 'ok') {
      setResetMsg({ ok: true, text: 'Password updated. You can now log in.' });
      setRUser(''); setROld(''); setRNew(''); setRConfirm('');
      setTimeout(() => { setMode('login'); setResetMsg(null); }, 1800);
    } else if (result === 'wrong_password') {
      setResetMsg({ ok: false, text: 'Current password is incorrect.' });
    } else {
      setResetMsg({ ok: false, text: 'Username not found.' });
    }
  }

  const inputStyle: React.CSSProperties = {
    width: '100%', padding: '11px 14px', borderRadius: '8px',
    border: '1px solid #1e293b', background: '#0f172a',
    color: '#e2e8f0', fontSize: '14px', outline: 'none',
    boxSizing: 'border-box', transition: 'border-color 0.15s',
  };

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'radial-gradient(ellipse at 60% 20%, rgba(99,102,241,0.08) 0%, transparent 60%), #060814',
      padding: '24px',
    }}>
      <div style={{
        width: '100%', maxWidth: '400px',
        background: 'rgba(15,23,42,0.95)',
        border: '1px solid rgba(99,102,241,0.2)',
        borderRadius: '16px',
        boxShadow: '0 25px 60px rgba(0,0,0,0.6), 0 0 0 1px rgba(99,102,241,0.1)',
        overflow: 'hidden',
      }}>
        {/* Accent bar */}
        <div style={{ height: '3px', background: 'linear-gradient(90deg, #4f46e5, #818cf8, #4f46e5)' }} />

        <div style={{ padding: '36px 32px 32px' }}>
          {/* Logo */}
          <div style={{ textAlign: 'center', marginBottom: '32px' }}>
            <div style={{ fontSize: '28px', fontWeight: 800, letterSpacing: '-0.5px' }}>
              <span style={{ color: '#818cf8' }}>Stock</span>
              <span style={{ color: '#f1f5f9' }}>AI</span>
            </div>
            <div style={{ fontSize: '12px', color: '#475569', marginTop: '4px', letterSpacing: '0.05em' }}>
              AI STOCK INTELLIGENCE PLATFORM
            </div>
          </div>

          {/* Tab switcher */}
          <div style={{
            display: 'flex', background: '#0a0f1e', borderRadius: '8px',
            padding: '3px', marginBottom: '28px', border: '1px solid #1e293b',
          }}>
            {(['login', 'reset'] as Mode[]).map(m => (
              <button
                key={m}
                onClick={() => { setMode(m); setLoginError(''); setResetMsg(null); }}
                style={{
                  flex: 1, padding: '8px', borderRadius: '6px', fontSize: '13px',
                  fontWeight: 600, border: 'none', cursor: 'pointer', transition: 'all 0.15s',
                  background: mode === m ? '#1e293b' : 'transparent',
                  color: mode === m ? '#e2e8f0' : '#475569',
                }}
              >
                {m === 'login' ? 'Sign In' : 'Reset Password'}
              </button>
            ))}
          </div>

          {/* Login form */}
          {mode === 'login' && (
            <form onSubmit={handleLogin} style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
              <div>
                <label style={{ fontSize: '12px', color: '#64748b', fontWeight: 600, letterSpacing: '0.04em', display: 'block', marginBottom: '6px' }}>
                  USERNAME
                </label>
                <input
                  type="text"
                  autoComplete="username"
                  autoFocus
                  value={username}
                  onChange={e => setUsername(e.target.value)}
                  placeholder="Enter your username"
                  required
                  style={inputStyle}
                />
              </div>
              <div>
                <label style={{ fontSize: '12px', color: '#64748b', fontWeight: 600, letterSpacing: '0.04em', display: 'block', marginBottom: '6px' }}>
                  PASSWORD
                </label>
                <input
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  placeholder="Enter your password"
                  required
                  style={inputStyle}
                />
              </div>

              {loginError && (
                <div style={{
                  background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.25)',
                  borderRadius: '8px', padding: '10px 14px', fontSize: '13px', color: '#f87171',
                }}>
                  {loginError}
                </div>
              )}

              <button
                type="submit"
                disabled={loginLoading}
                style={{
                  width: '100%', padding: '12px', borderRadius: '8px', border: 'none',
                  background: loginLoading ? 'rgba(79,70,229,0.5)' : 'linear-gradient(135deg, #4f46e5, #6366f1)',
                  color: '#fff', fontSize: '14px', fontWeight: 700,
                  cursor: loginLoading ? 'not-allowed' : 'pointer',
                  transition: 'all 0.15s', marginTop: '4px',
                  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px',
                  boxShadow: loginLoading ? 'none' : '0 4px 16px rgba(79,70,229,0.35)',
                }}
              >
                {loginLoading ? (
                  <>
                    <span style={{ display: 'inline-block', animation: 'spin 0.8s linear infinite', fontSize: '16px' }}>↻</span>
                    Signing in…
                  </>
                ) : 'Sign In'}
              </button>
            </form>
          )}

          {/* Reset password form */}
          {mode === 'reset' && (
            <form onSubmit={handleReset} style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
              {[
                { label: 'USERNAME', value: rUser, set: setRUser, type: 'text', ph: 'Your username' },
                { label: 'CURRENT PASSWORD', value: rOld, set: setROld, type: 'password', ph: 'Current password' },
                { label: 'NEW PASSWORD', value: rNew, set: setRNew, type: 'password', ph: 'New password (min 4 chars)' },
                { label: 'CONFIRM NEW PASSWORD', value: rConfirm, set: setRConfirm, type: 'password', ph: 'Repeat new password' },
              ].map(({ label, value, set, type, ph }) => (
                <div key={label}>
                  <label style={{ fontSize: '11px', color: '#64748b', fontWeight: 600, letterSpacing: '0.04em', display: 'block', marginBottom: '5px' }}>
                    {label}
                  </label>
                  <input
                    type={type}
                    value={value}
                    onChange={e => set(e.target.value)}
                    placeholder={ph}
                    required
                    style={inputStyle}
                  />
                </div>
              ))}

              {resetMsg && (
                <div style={{
                  borderRadius: '8px', padding: '10px 14px', fontSize: '13px',
                  background: resetMsg.ok ? 'rgba(34,197,94,0.08)' : 'rgba(239,68,68,0.08)',
                  border: `1px solid ${resetMsg.ok ? 'rgba(34,197,94,0.25)' : 'rgba(239,68,68,0.25)'}`,
                  color: resetMsg.ok ? '#4ade80' : '#f87171',
                }}>
                  {resetMsg.text}
                </div>
              )}

              <button
                type="submit"
                style={{
                  width: '100%', padding: '11px', borderRadius: '8px',
                  border: '1px solid rgba(99,102,241,0.3)',
                  background: 'rgba(99,102,241,0.15)',
                  color: '#818cf8', fontSize: '14px', fontWeight: 700,
                  cursor: 'pointer', transition: 'all 0.15s', marginTop: '2px',
                }}
              >
                Update Password
              </button>
            </form>
          )}
        </div>

        {/* Footer */}
        <div style={{
          padding: '14px 32px', borderTop: '1px solid #0f172a',
          background: 'rgba(0,0,0,0.2)', textAlign: 'center',
          fontSize: '11px', color: '#334155',
        }}>
          StockAI — Personal trading intelligence platform
        </div>
      </div>

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}
