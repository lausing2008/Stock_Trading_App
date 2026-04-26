import { useState, useEffect, useRef } from 'react';
import type { AppProps } from 'next/app';
import { useRouter } from 'next/router';
import Link from 'next/link';
import '@/styles/globals.css';
import { getSession, logout } from '@/lib/auth';
import { checkAlerts, playNotificationSound } from '@/lib/alerts';
import { loadSettings } from '@/lib/settings';
import NotificationBell from '@/components/NotificationBell';
import { api } from '@/lib/api';

const PUBLIC_PATHS = ['/login'];

export default function App({ Component, pageProps }: AppProps) {
  const router = useRouter();
  const [username, setUsername] = useState<string | null>(null);
  const [checked, setChecked] = useState(false);
  const alertTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    const session = getSession();
    if (session) {
      setUsername(session.username);
    } else if (!PUBLIC_PATHS.includes(router.pathname)) {
      router.replace('/login');
    }
    setChecked(true);
  }, [router.pathname]);

  // Global alert checker — runs every 60 s when logged in
  useEffect(() => {
    if (!username) return;

    async function runCheck() {
      try {
        const [priceList, signalList, rankData] = await Promise.all([
          api.latestPrices(),
          api.allSignals(),
          api.rankings(),
        ]);

        const prices: Record<string, { price: number; change_pct: number | null }> = {};
        for (const p of priceList) prices[p.symbol] = { price: p.price, change_pct: p.change_pct ?? null };

        const signals: Record<string, { signal: string; confidence: number }> = {};
        for (const s of signalList) signals[s.symbol] = { signal: s.signal, confidence: s.confidence };

        const scores: Record<string, { score: number }> = {};
        for (const r of (rankData.rankings ?? [])) scores[r.symbol] = { score: r.score };

        const triggered = checkAlerts(prices, signals, scores);
        if (triggered.length > 0) {
          const settings = loadSettings();
          if (settings.notificationSound) playNotificationSound();
        }
      } catch {
        // silently ignore — network might be unavailable
      }
    }

    runCheck();
    alertTimerRef.current = setInterval(runCheck, 60_000);
    return () => {
      if (alertTimerRef.current) clearInterval(alertTimerRef.current);
    };
  }, [username]);

  function handleLogout() {
    logout();
    setUsername(null);
    if (alertTimerRef.current) clearInterval(alertTimerRef.current);
    router.push('/login');
  }

  if (!checked) return null;

  if (PUBLIC_PATHS.includes(router.pathname)) {
    return <Component {...pageProps} />;
  }

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
            <Link href="/opportunities" style={{ color: '#a78bfa', fontWeight: 600 }}>Opportunities</Link>
            <Link href="/rankings">Rankings</Link>
            <Link href="/watchlist">Watchlist</Link>
            <Link href="/positions">Positions</Link>
            <Link href="/portfolio">Portfolio</Link>
            <Link href="/strategies">Strategies</Link>
            <Link href="/alerts">Alerts</Link>
          </nav>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <NotificationBell />
            <span style={{ fontSize: '12px', color: '#475569' }}>
              👤 <span style={{ color: '#94a3b8' }}>{username}</span>
            </span>
            <Link
              href="/settings"
              style={{
                background: 'transparent', border: '1px solid #1e293b',
                color: '#64748b', padding: '4px 10px', borderRadius: '6px',
                fontSize: '12px', cursor: 'pointer', textDecoration: 'none',
              }}
              title="Settings"
            >
              ⚙
            </Link>
            <button
              onClick={handleLogout}
              style={{
                background: 'transparent', border: '1px solid #1e293b',
                color: '#64748b', padding: '4px 12px', borderRadius: '6px',
                fontSize: '12px', cursor: 'pointer', transition: 'all 0.15s',
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
