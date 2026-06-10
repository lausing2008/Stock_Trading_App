import { useState, useEffect, FormEvent } from 'react';
import { useRouter } from 'next/router';
import { login, getSession } from '@/lib/auth';

export default function LoginPage() {
  const router = useRouter();

  // Login state
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loginError, setLoginError] = useState('');
  const [loginLoading, setLoginLoading] = useState(false);

  useEffect(() => {
    if (!router.isReady) return;
    if (getSession()) {
      const raw = router.query.next as string | undefined;
      const next = raw && raw.startsWith('/') && raw !== '/login' && raw !== '/gate' ? raw : '/';
      router.replace(next);
    }
  }, [router.isReady, router.query]);

  async function handleLogin(e: FormEvent) {
    e.preventDefault();
    setLoginLoading(true);
    setLoginError('');
    const ok = await login(username, password);
    if (ok) {
      const raw = router.query.next as string | undefined;
      const next = raw && raw.startsWith('/') && raw !== '/login' && raw !== '/gate' ? raw : '/';
      window.location.href = next;
    } else {
      setLoginError('Incorrect username or password.');
      setLoginLoading(false);
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

          {/* Login form */}
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
