import { useState, useEffect, useRef, useCallback } from 'react';
import type { AppProps } from 'next/app';
import { useRouter } from 'next/router';
import Link from 'next/link';
import useSWR from 'swr';
import '@/styles/globals.css';
import { getSession, logout, getImpersonatedUser, exitImpersonation } from '@/lib/auth';
import { checkAlerts, playNotificationSound } from '@/lib/alerts';
import { confluenceScore } from '@/lib/confluence';
import { loadSettings, getSignalStyle } from '@/lib/settings';
import NotificationBell from '@/components/NotificationBell';
import { api, type Stock } from '@/lib/api';

const PUBLIC_PATHS = ['/login', '/gate'];
const GATE_COOKIE  = 'stockai_gate';
let _configPushed = false;

function hasGateCookie() {
  if (typeof document === 'undefined') return false;
  return document.cookie.split(';').some(c => c.trim().startsWith(`${GATE_COOKIE}=`));
}

// ── Nav group definitions ─────────────────────────────────────────────────────

type NavItem = { label: string; href: string; color?: string; tag?: string };
type NavGroupDef = { label: string; items: NavItem[]; adminOnly?: boolean };

const NAV_GROUPS: NavGroupDef[] = [
  {
    label: 'Markets',
    items: [
      { label: 'Dashboard',    href: '/' },
      { label: 'Heatmap',      href: '/heatmap',      color: '#38bdf8', tag: 'live' },
      { label: 'Rankings',         href: '/rankings' },
      { label: 'Sector Rotation',  href: '/sector-rotation', color: '#38bdf8' },
      { label: 'Forecast',     href: '/forecast',     color: '#4ade80' },
    ],
  },
  {
    label: 'Research',
    items: [
      { label: 'Screener',      href: '/screener' },
      { label: 'Compare',       href: '/compare',       color: '#818cf8' },
      { label: 'Opportunities', href: '/opportunities', color: '#a78bfa' },
      { label: 'Earnings',      href: '/earnings',      color: '#fb923c', tag: 'cal' },
      { label: 'Analyst',       href: '/analyst',       color: '#818cf8' },
      { label: 'Short Squeeze',    href: '/short-squeeze',  color: '#f87171' },
      { label: 'Short Interest',   href: '/short-selling',  color: '#fb923c' },
      { label: 'Research Engine',  href: '/research',       color: '#4ade80', tag: 'ai' },
      { label: 'Event Intelligence', href: '/intelligence', color: '#f59e0b', tag: 'new' },
    ],
  },
  {
    label: 'Portfolio',
    items: [
      { label: 'Watchlist',    href: '/watchlist' },
      { label: 'Positions',    href: '/positions' },
      { label: 'Portfolio',    href: '/portfolio' },
      { label: 'Trade Board',  href: '/board',    color: '#818cf8' },
      { label: 'Journal',      href: '/journal',  color: '#34d399' },
    ],
  },
  {
    label: 'Tools',
    items: [
      { label: 'Strategies',      href: '/strategies' },
      { label: 'Alerts',          href: '/alerts' },
      { label: 'Decision Engine', href: '/decide',   color: '#34d399', tag: 'new' },
      { label: 'Market Regime',   href: '/regime',   color: '#6366f1', tag: 'new' },
      { label: 'Insider Trading', href: '/insider',  color: '#fb923c' },
      { label: 'Congress Trades', href: '/congress', color: '#f97316' },
    ],
  },
  {
    label: 'Admin',
    adminOnly: true,
    items: [
      { label: 'Paper Portfolio',  href: '/paper-portfolio',  color: '#22c55e' },
      { label: 'Entry Gates',      href: '/paper-gates',      color: '#22c55e' },
      { label: 'Signal Accuracy',  href: '/signal-accuracy',  color: '#a78bfa' },
      { label: 'Signal Filters',   href: '/signal-filters',   color: '#f97316' },
      { label: 'Signal Quality',   href: '/signal-quality',   color: '#818cf8', tag: 'new' },
      { label: 'Signal Tuning',    href: '/signal-tuning',    color: '#a78bfa', tag: 'new' },
      { label: 'Trade Performance', href: '/trade-performance', color: '#34d399' },
      { label: 'Signal Log',       href: '/admin-signals',    color: '#f87171' },
      { label: 'System Health',    href: '/admin-health',     color: '#38bdf8' },
      { label: 'Improvements',     href: '/improvements',     color: '#f59e0b', tag: 'new' },
    ],
  },
];

// ── GlobalSearch component ────────────────────────────────────────────────────

function GlobalSearch() {
  const router = useRouter();
  const { data: stocks } = useSWR<Stock[]>('stocks-all', () => api.listStocks(), { revalidateOnFocus: false });
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [focused, setFocused] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const q = query.trim().toUpperCase();
  const results = q.length < 1 ? [] : (stocks ?? []).filter(s =>
    s.symbol.startsWith(q) || s.symbol.includes(q) || s.name.toUpperCase().includes(q)
  ).slice(0, 8);

  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if ((e.key === 'k' && (e.metaKey || e.ctrlKey)) || e.key === '/') {
        // Only open on '/' when not in an input/textarea
        if (e.key === '/' && document.activeElement?.tagName.match(/^(INPUT|TEXTAREA|SELECT)$/)) return;
        e.preventDefault();
        inputRef.current?.focus();
        setOpen(true);
      }
      if (e.key === 'Escape') { setOpen(false); setQuery(''); inputRef.current?.blur(); }
    }
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, []);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  function select(symbol: string) {
    setQuery('');
    setOpen(false);
    inputRef.current?.blur();
    router.push(`/stock/${symbol}`);
  }

  return (
    <div ref={containerRef} style={{ position: 'relative' }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: '6px',
        background: focused ? 'rgba(99,102,241,0.08)' : '#0f172a',
        border: `1px solid ${focused ? 'rgba(99,102,241,0.4)' : '#1e293b'}`,
        borderRadius: '8px', padding: '5px 10px',
        transition: 'all 0.15s', width: focused ? '220px' : '160px',
      }}>
        <span style={{ color: '#475569', fontSize: '13px', flexShrink: 0 }}>⌕</span>
        <input
          ref={inputRef}
          value={query}
          onChange={e => { setQuery(e.target.value); setOpen(true); }}
          onFocus={() => { setFocused(true); setOpen(true); }}
          onBlur={() => setFocused(false)}
          placeholder="Search stocks…"
          style={{
            background: 'none', border: 'none', outline: 'none',
            color: '#e2e8f0', fontSize: '12px', width: '100%', minWidth: 0,
          }}
        />
        {!focused && (
          <span style={{ fontSize: '10px', color: '#334155', flexShrink: 0, fontFamily: 'monospace' }}>⌘K</span>
        )}
        {query && (
          <button onClick={() => { setQuery(''); setOpen(false); }} style={{ background: 'none', border: 'none', color: '#475569', cursor: 'pointer', padding: '0 2px', fontSize: '12px' }}>✕</button>
        )}
      </div>

      {open && results.length > 0 && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 6px)', left: 0,
          minWidth: '260px', background: '#0d1424',
          border: '1px solid #1e293b', borderRadius: '10px',
          boxShadow: '0 16px 40px rgba(0,0,0,0.6)',
          overflow: 'hidden', zIndex: 9999,
        }}>
          {results.map((s, i) => (
            <button
              key={s.symbol}
              onMouseDown={() => select(s.symbol)}
              style={{
                display: 'flex', alignItems: 'center', gap: '10px',
                width: '100%', padding: '9px 14px', background: 'none',
                border: 'none', borderBottom: i < results.length - 1 ? '1px solid #0f172a' : 'none',
                cursor: 'pointer', textAlign: 'left',
                transition: 'background 0.1s',
              }}
              className="search-result-row"
            >
              <span style={{ fontFamily: 'monospace', fontSize: '13px', fontWeight: 800, color: '#818cf8', minWidth: '52px' }}>{s.symbol}</span>
              <span style={{ fontSize: '12px', color: '#64748b', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{s.name}</span>
              <span style={{ fontSize: '10px', color: '#334155', flexShrink: 0 }}>{s.market}</span>
            </button>
          ))}
        </div>
      )}
      {open && q.length > 0 && results.length === 0 && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 6px)', left: 0,
          minWidth: '200px', background: '#0d1424',
          border: '1px solid #1e293b', borderRadius: '10px',
          padding: '12px 16px', zIndex: 9999,
          fontSize: '12px', color: '#475569',
          boxShadow: '0 16px 40px rgba(0,0,0,0.6)',
        }}>
          No stocks found for &ldquo;{query}&rdquo;
        </div>
      )}
    </div>
  );
}


// ── NavGroup component ────────────────────────────────────────────────────────

function NavGroup({ group, currentPath, userRole }: { group: NavGroupDef; currentPath: string; userRole: string | null }) {
  const items = group.items;
  const [open, setOpen] = useState(false);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  const isActive = items.some(item =>
    item.href === '/' ? currentPath === '/' : currentPath.startsWith(item.href)
  );

  function enter() {
    if (closeTimer.current) clearTimeout(closeTimer.current);
    setOpen(true);
  }

  function leave() {
    closeTimer.current = setTimeout(() => setOpen(false), 120);
  }

  return (
    <div
      ref={ref}
      onMouseEnter={enter}
      onMouseLeave={leave}
      style={{ position: 'relative' }}
    >
      {/* Group label */}
      <button
        onClick={() => setOpen((o: boolean) => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: '4px',
          background: 'none', border: 'none', cursor: 'pointer',
          padding: '6px 2px',
          fontSize: '13px',
          fontWeight: isActive ? 700 : 500,
          color: isActive ? '#e2e8f0' : '#94a3b8',
          borderBottom: isActive ? '2px solid #6366f1' : '2px solid transparent',
          transition: 'color 0.15s, border-color 0.15s',
          lineHeight: 1,
        }}
      >
        {group.label}
        <span style={{
          fontSize: '9px', color: isActive ? '#818cf8' : '#475569',
          transform: open ? 'rotate(180deg)' : 'none',
          transition: 'transform 0.15s',
          display: 'inline-block',
          marginTop: '1px',
        }}>▾</span>
      </button>

      {/* Dropdown panel */}
      {open && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 6px)', left: '50%',
          transform: 'translateX(-50%)',
          minWidth: '180px',
          background: '#0d1424',
          border: '1px solid #1e293b',
          borderRadius: '10px',
          boxShadow: '0 16px 40px rgba(0,0,0,0.6)',
          padding: '6px',
          zIndex: 9999,
          animation: 'dropIn 0.12s ease',
        }}>
          {items.map(item => {
            const isCurrent = item.href === '/'
              ? currentPath === '/'
              : currentPath.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={() => setOpen(false)}
                style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  gap: '8px',
                  padding: '8px 12px',
                  borderRadius: '6px',
                  fontSize: '13px',
                  fontWeight: isCurrent ? 700 : 400,
                  color: isCurrent ? '#e2e8f0' : (item.color ?? '#94a3b8'),
                  background: isCurrent ? 'rgba(99,102,241,0.12)' : 'transparent',
                  textDecoration: 'none',
                  transition: 'background 0.1s',
                  whiteSpace: 'nowrap',
                }}
                className={isCurrent ? '' : 'nav-dd-item'}
              >
                <span>{item.label}</span>
                <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                  {item.tag && (
                    <span style={{
                      fontSize: '9px', fontWeight: 700, padding: '1px 5px',
                      borderRadius: '3px', textTransform: 'uppercase', letterSpacing: '0.05em',
                      background: 'rgba(99,102,241,0.15)', color: '#818cf8',
                    }}>{item.tag}</span>
                  )}
                  {isCurrent && <span style={{ width: '5px', height: '5px', borderRadius: '50%', background: '#6366f1', display: 'inline-block' }} />}
                </span>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Main App ──────────────────────────────────────────────────────────────────

export default function App({ Component, pageProps }: AppProps) {
  const router = useRouter();
  // Initialise synchronously from localStorage to prevent blank-flash on page load.
  // getSession() is a pure localStorage read — safe to call during render.
  const [username, setUsername] = useState<string | null>(() =>
    typeof window !== 'undefined' ? (getSession()?.username ?? null) : null
  );
  const [role, setRole] = useState<string | null>(() =>
    typeof window !== 'undefined' ? (getSession()?.role ?? null) : null
  );
  const [checked, setChecked] = useState<boolean>(() => {
    if (typeof window === 'undefined') return false;
    const path = window.location.pathname;
    return PUBLIC_PATHS.includes(path) || Boolean(getSession());
  });
  const [impersonating, setImpersonating] = useState<string | null>(() =>
    typeof window !== 'undefined' ? getImpersonatedUser() : null
  );
  const alertTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [freshness, setFreshness] = useState<{ hours_ago: number | null; status: string } | null>(null);

  useEffect(() => {
    if (!username) return; // Only poll freshness when logged in to avoid 401s on public pages
    let mounted = true;
    function poll() {
      api.dataFreshness().then(f => { if (mounted) setFreshness(f); }).catch(() => {});
    }
    poll();
    const t = setInterval(poll, 5 * 60 * 1000); // refresh every 5 min
    return () => { mounted = false; clearInterval(t); };
  }, [username]);

  useEffect(() => {
    if (PUBLIC_PATHS.includes(router.pathname)) {
      setChecked(true);
      return;
    }

    async function doCheck() {
      // If the user has a valid JWT session, skip the gate — they've already authenticated
      const session = getSession();
      if (session) {
        setUsername(session.username);
        setRole(session.role);
        setImpersonating(getImpersonatedUser());
        const settings = loadSettings();
        if (!_configPushed && (settings.polygonApiKey || settings.alphaVantageApiKey)) {
          _configPushed = true;
          api.pushConfig({
            polygon_api_key: settings.polygonApiKey || undefined,
            alpha_vantage_api_key: settings.alphaVantageApiKey || undefined,
            quiver_api_key: settings.quiverApiKey || undefined,
          }).catch(() => {});
        }
        setChecked(true);
        return;
      }

      // Gate check only for unauthenticated visitors
      if (!hasGateCookie()) {
        try {
          const r = await fetch('/api/gate');
          const { enabled } = await r.json() as { enabled: boolean };
          if (enabled) {
            setChecked(true);
            router.replace(`/gate?next=${encodeURIComponent(router.pathname)}`);
            return;
          }
        } catch {
          // If the gate API is unreachable, let the user through
        }
      }

      const next = router.pathname === '/login' ? '/' : router.pathname;
      router.replace(`/login?next=${encodeURIComponent(next)}`);
      setChecked(true);
    }

    doCheck();
  }, [router.pathname]);

  // Global alert checker — runs every 60 s when logged in
  useEffect(() => {
    if (!username) return;

    async function runCheck() {
      try {
        const globalStyle = getSignalStyle();

        // Fetch base market data and watchlist metadata in parallel
        const [priceList, rankData, watchlists] = await Promise.all([
          api.latestPrices(),
          api.rankings(),
          api.listWatchlists().catch(() => [] as Awaited<ReturnType<typeof api.listWatchlists>>),
        ]);

        // Build symbol→style map from watchlists that have a trading style override
        const symbolStyleMap: Record<string, string> = {};
        const styledLists = watchlists.filter(wl => wl.trading_style);
        if (styledLists.length > 0) {
          const itemArrays = await Promise.all(
            styledLists.map(wl => api.listWatchlist(wl.id).catch(() => [] as Awaited<ReturnType<typeof api.listWatchlist>>))
          );
          itemArrays.forEach((items, i) => {
            const style = styledLists[i].trading_style!;
            items.forEach(item => { symbolStyleMap[item.symbol] = style; });
          });
        }

        // Fetch signals for each distinct style actually needed (usually just 1-2)
        const stylesNeeded = new Set<string>([globalStyle, ...Object.values(symbolStyleMap)]);
        const signalsByStyle: Record<string, Awaited<ReturnType<typeof api.allSignals>>> = {};
        await Promise.all([...stylesNeeded].map(async style => {
          signalsByStyle[style] = await api.allSignals(style).catch(() => []);
        }));

        const prices: Record<string, { price: number; change_pct: number | null }> = {};
        for (const p of priceList) prices[p.symbol] = { price: p.price, change_pct: p.change_pct ?? null };

        // Each symbol gets the signal from its applicable style
        const signals: Record<string, { signal: string; confidence: number; style?: string }> = {};
        for (const [style, sigs] of Object.entries(signalsByStyle)) {
          for (const s of sigs) {
            const applicableStyle = symbolStyleMap[s.symbol] ?? globalStyle;
            if (style === applicableStyle) {
              signals[s.symbol] = { signal: s.signal, confidence: s.confidence, style };
            }
          }
        }

        const scores: Record<string, { score: number }> = {};
        for (const r of (rankData.rankings ?? [])) if (r.score != null) scores[r.symbol] = { score: r.score };

        const confluences: Record<string, { score: number }> = {};
        for (const r of (rankData.rankings ?? [])) {
          const sig = signals[r.symbol];
          confluences[r.symbol] = { score: confluenceScore(r, sig) };
        }

        const triggered = checkAlerts(prices, signals, scores, confluences);
        if (triggered.length > 0) {
          const settings = loadSettings();
          if (settings.notificationSound) playNotificationSound();
        }
      } catch {
        // silently ignore
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
    if (alertTimerRef.current) clearInterval(alertTimerRef.current);
    window.location.href = '/login';
  }

  function handleExitImpersonation() {
    exitImpersonation();
    window.location.href = '/settings';
  }

  if (!checked) return null;

  if (PUBLIC_PATHS.includes(router.pathname)) {
    return <Component {...pageProps} />;
  }

  if (!username) return null;

  return (
    <div>
      {impersonating && (
        <div style={{ position: 'sticky', top: 0, zIndex: 600, background: '#7c3aed', color: '#fff', fontSize: '12px', padding: '6px 16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span>👁 Viewing as <strong>{impersonating}</strong> — all actions are performed as this user</span>
          <button
            onClick={handleExitImpersonation}
            style={{ background: 'rgba(255,255,255,0.2)', border: '1px solid rgba(255,255,255,0.4)', color: '#fff', borderRadius: '5px', padding: '2px 12px', fontSize: '11px', cursor: 'pointer', fontWeight: 700 }}
          >
            ← Return to Admin
          </button>
        </div>
      )}

      <header
        className="border border-slate-800 bg-slate-900"
        style={{ position: 'sticky', top: impersonating ? '33px' : 0, zIndex: 500 }}
      >
        <div
          className="container-xl"
          style={{ display: 'flex', alignItems: 'center', gap: '32px', height: '52px' }}
        >
          {/* Logo */}
          <Link href="/" style={{ textDecoration: 'none', flexShrink: 0 }}>
            <span style={{ fontSize: '17px', fontWeight: 800, color: '#818cf8', letterSpacing: '-0.02em' }}>Stock</span>
            <span style={{ fontSize: '17px', fontWeight: 800, color: '#e2e8f0', letterSpacing: '-0.02em' }}>AI</span>
          </Link>

          {/* Group nav */}
          <nav style={{ display: 'flex', alignItems: 'center', gap: '2px', flex: 1 }}>
            {NAV_GROUPS.filter(g => !g.adminOnly || role === 'admin').map(group => (
              <NavGroup key={group.label} group={group} currentPath={router.pathname} userRole={role} />
            ))}
          </nav>

          {/* Global search */}
          <GlobalSearch />

          {/* Right side controls */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
            {freshness && freshness.hours_ago != null && (
              <span title={`Last price ingest: ${freshness.hours_ago}h ago`} style={{
                fontSize: '10px', fontWeight: 700, padding: '2px 7px', borderRadius: '4px',
                background: freshness.status === 'fresh' ? 'rgba(74,222,128,0.1)' : freshness.status === 'stale' ? 'rgba(251,191,36,0.1)' : 'rgba(248,113,113,0.1)',
                border: `1px solid ${freshness.status === 'fresh' ? 'rgba(74,222,128,0.3)' : freshness.status === 'stale' ? 'rgba(251,191,36,0.3)' : 'rgba(248,113,113,0.3)'}`,
                color: freshness.status === 'fresh' ? '#4ade80' : freshness.status === 'stale' ? '#fbbf24' : '#f87171',
                cursor: 'default', letterSpacing: '0.02em',
              }}>
                {freshness.hours_ago < 1 ? '<1h' : `${freshness.hours_ago.toFixed(0)}h`} ago
              </span>
            )}
            <NotificationBell />
            <div style={{ width: '1px', height: '20px', background: '#1e293b' }} />
            <span style={{ fontSize: '12px', color: '#64748b', display: 'flex', alignItems: 'center', gap: '5px' }}>
              <span style={{ color: '#94a3b8' }}>{username}</span>
              {role === 'admin' && (
                <span style={{ fontSize: '9px', color: '#fb7185', padding: '1px 5px', borderRadius: '3px', background: 'rgba(225,29,72,0.15)', border: '1px solid rgba(225,29,72,0.3)', fontWeight: 700, letterSpacing: '0.04em' }}>
                  ADMIN
                </span>
              )}
            </span>
            <Link
              href="/settings"
              title="Settings"
              style={{ background: 'transparent', border: '1px solid #1e293b', color: '#475569', padding: '4px 10px', borderRadius: '6px', fontSize: '13px', textDecoration: 'none', lineHeight: 1 }}
            >⚙</Link>
            <button
              onClick={handleLogout}
              style={{ background: 'transparent', border: '1px solid #1e293b', color: '#475569', padding: '4px 12px', borderRadius: '6px', fontSize: '12px', cursor: 'pointer' }}
            >
              Logout
            </button>
          </div>
        </div>
      </header>

      <main className="container-xl">
        <Component {...pageProps} />
      </main>

      <style>{`
        @keyframes dropIn {
          from { opacity: 0; transform: translateX(-50%) translateY(-6px); }
          to   { opacity: 1; transform: translateX(-50%) translateY(0); }
        }
        .nav-dd-item:hover { background: rgba(255,255,255,0.05) !important; }
        .search-result-row:hover { background: rgba(99,102,241,0.08) !important; }
      `}</style>
    </div>
  );
}
