import { useState, useEffect } from 'react';
import type { AppProps } from 'next/app';
import { useRouter } from 'next/router';
import Link from 'next/link';
import '@/styles/globals.css';
import { getSession, logout } from '@/lib/auth';

const PUBLIC_PATHS = ['/login'];

export default function App({ Component, pageProps }: AppProps) {
  const router = useRouter();
  const [username, setUsername] = useState<string | null>(null);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    const session = getSession();
    if (session) {
      setUsername(session.username);
    } else if (!PUBLIC_PATHS.includes(router.pathname)) {
      router.replace('/login');
    }
    setChecked(true);
  }, [router.pathname]);

  function handleLogout() {
    logout();
    setUsername(null);
    router.push('/login');
  }

  // Don't flash anything until auth state is known
  if (!checked) return null;

  // Public pages render without the shell
  if (PUBLIC_PATHS.includes(router.pathname)) {
    return <Component {...pageProps} />;
  }

  // Guard: redirect in progress
  if (!username) return null;

  return (
    <div>
      <header className="border border-slate-800 bg-slate-900">
        <div className="container-xl" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Link href="/" className="text-lg font-bold">
            <span style={{ color: '#818cf8' }}>Stock</span>AI
          </Link>
          <nav className="flex gap-4 text-sm text-slate-300">
            <Link href="/">Dashboard</Link>
            <Link href="/rankings">Rankings</Link>
            <Link href="/watchlist">Watchlist</Link>
            <Link href="/positions">Positions</Link>
            <Link href="/portfolio">Portfolio</Link>
            <Link href="/strategies">Strategies</Link>
          </nav>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <span style={{ fontSize: '12px', color: '#475569' }}>
              👤 <span style={{ color: '#94a3b8' }}>{username}</span>
            </span>
            <button
              onClick={handleLogout}
              style={{
                background: 'transparent',
                border: '1px solid #1e293b',
                color: '#64748b',
                padding: '4px 12px',
                borderRadius: '6px',
                fontSize: '12px',
                cursor: 'pointer',
                transition: 'all 0.15s',
              }}
            >
              Logout
            </button>
          </div>
        </div>
      </header>
      <main className="container-xl">
        <Component {...pageProps} />
      </main>
    </div>
  );
}
