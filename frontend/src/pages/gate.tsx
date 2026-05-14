import { useState, FormEvent } from 'react';
import { useRouter } from 'next/router';
import Head from 'next/head';

export default function GatePage() {
  const router = useRouter();
  const [password, setPassword] = useState('');
  const [error, setError]       = useState('');
  const [loading, setLoading]   = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const res = await fetch('/api/gate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      if (res.ok) {
        const next = (router.query.next as string) || '/';
        router.replace(next);
      } else {
        setError('Incorrect password. Please try again.');
        setPassword('');
      }
    } catch {
      setError('Connection error. Please try again.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <Head><title>StockAI — Access</title></Head>
      <div style={{
        minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: '#060d1a',
      }}>
        <div style={{
          width: '100%', maxWidth: 360,
          background: '#0b1420', border: '1px solid #1e293b',
          borderRadius: 12, padding: '40px 32px',
          boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
        }}>
          {/* Logo / title */}
          <div style={{ textAlign: 'center', marginBottom: 32 }}>
            <div style={{ fontSize: 28, marginBottom: 8 }}>📊</div>
            <h1 style={{ margin: 0, fontSize: 20, fontWeight: 700, color: '#e2e8f0', letterSpacing: '-0.02em' }}>
              StockAI
            </h1>
            <p style={{ margin: '6px 0 0', fontSize: 13, color: '#64748b' }}>
              Enter your access password to continue
            </p>
          </div>

          <form onSubmit={handleSubmit}>
            <input
              type="password"
              placeholder="Password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              autoFocus
              required
              style={{
                width: '100%', boxSizing: 'border-box',
                padding: '10px 14px', borderRadius: 7,
                border: error ? '1px solid #ef4444' : '1px solid #1e293b',
                background: '#0f172a', color: '#e2e8f0',
                fontSize: 14, outline: 'none',
                transition: 'border-color 0.15s',
              }}
            />

            {error && (
              <p style={{ margin: '8px 0 0', fontSize: 12, color: '#ef4444' }}>{error}</p>
            )}

            <button
              type="submit"
              disabled={loading || !password}
              style={{
                marginTop: 16, width: '100%', padding: '10px 0',
                borderRadius: 7, border: 'none', cursor: loading || !password ? 'not-allowed' : 'pointer',
                background: loading || !password ? '#1e293b' : 'linear-gradient(135deg,#6366f1,#818cf8)',
                color: loading || !password ? '#475569' : '#fff',
                fontSize: 14, fontWeight: 600, transition: 'all 0.15s',
              }}
            >
              {loading ? 'Verifying…' : 'Enter'}
            </button>
          </form>
        </div>
      </div>
    </>
  );
}
