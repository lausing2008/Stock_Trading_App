import { useState, useEffect, useRef } from 'react';
import type { AppProps } from 'next/app';
import { useRouter } from 'next/router';
import Link from 'next/link';
import '@/styles/globals.css';
import { getSession, logout, getImpersonatedUser, exitImpersonation } from '@/lib/auth';
import { checkAlerts, playNotificationSound } from '@/lib/alerts';
import { confluenceScore } from '@/lib/confluence';
import { loadSettings } from '@/lib/settings';
import NotificationBell from '@/components/NotificationBell';
import { api } from '@/lib/api';

const PUBLIC_PATHS = ['/login', '/gate'];
const GATE_COOKIE  = 'stockai_gate';
const SIDEBAR_W    = 220;
const SIDEBAR_C    = 56; // collapsed width

function hasGateCookie() {
  if (typeof document === 'undefined') return false;
  return document.cookie.split(';').some(c => c.trim().startsWith(`${GATE_COOKIE}=`));
}

// ── Nav definitions ───────────────────────────────────────────────────────────

type NavItem = { label: string; href: string; color?: string; tag?: string; adminOnly?: boolean };
type NavGroupDef = { label: string; icon: string; items: NavItem[] };

const NAV_GROUPS: NavGroupDef[] = [
  {
    label: 'Markets', icon: '◈',
    items: [
      { label: 'Dashboard',  href: '/' },
      { label: 'Rankings',   href: '/rankings' },
      { label: 'Heatmap',    href: '/heatmap',   tag: 'live' },
      { label: 'Forecast',   href: '/forecast' },
    ],
  },
  {
    label: 'Research', icon: '⌖',
    items: [
      { label: 'Screener',        href: '/screener' },
      { label: 'Opportunities',   href: '/opportunities',  color: '#a78bfa' },
      { label: 'Research Engine', href: '/research',       color: '#4ade80', tag: 'ai' },
      { label: 'Earnings',        href: '/earnings',       color: '#fb923c', tag: 'cal' },
      { label: 'Short Squeeze',   href: '/short-squeeze',  color: '#f87171' },
      { label: 'Analyst',         href: '/analyst',        color: '#818cf8' },
    ],
  },
  {
    label: 'Portfolio', icon: '⊡',
    items: [
      { label: 'Watchlist',  href: '/watchlist' },
      { label: 'Trade Board', href: '/board',    color: '#818cf8' },
      { label: 'Positions',  href: '/positions' },
      { label: 'Portfolio',  href: '/portfolio' },
      { label: 'Journal',    href: '/journal',   color: '#34d399' },
    ],
  },
  {
    label: 'Analytics', icon: '⎔',
    items: [
      { label: 'Signal Accuracy',   href: '/signal-accuracy',   color: '#a78bfa' },
      { label: 'Trade Performance', href: '/trade-performance',  color: '#34d399' },
      { label: 'Strategies',        href: '/strategies' },
      { label: 'Alerts',            href: '/alerts' },
      { label: 'Insider / Congress', href: '/insider',           color: '#fb923c' },
      { label: 'Improvements',      href: '/improvements',       color: '#f59e0b', tag: 'new', adminOnly: true },
    ],
  },
];

// ── NavGroup component ────────────────────────────────────────────────────────

function SidebarGroup({
  group, currentPath, userRole, collapsed, defaultOpen,
}: {
  group: NavGroupDef; currentPath: string; userRole: string | null;
  collapsed: boolean; defaultOpen: boolean;
}) {
  const items = group.items.filter(i => !i.adminOnly || userRole === 'admin');
  const [open, setOpen] = useState(defaultOpen);
  const isGroupActive = items.some(i =>
    i.href === '/' ? currentPath === '/' : currentPath.startsWith(i.href)
  );

  return (
    <div style={{ marginBottom: 2 }}>
      {/* Group header */}
      <button
        onClick={() => !collapsed && setOpen(o => !o)}
        title={collapsed ? group.label : undefined}
        style={{
          width: '100%', display: 'flex', alignItems: 'center',
          gap: 8, padding: collapsed ? '8px 0' : '6px 12px',
          justifyContent: collapsed ? 'center' : 'space-between',
          background: 'none', border: 'none', cursor: collapsed ? 'default' : 'pointer',
          borderRadius: 6,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{
            fontSize: 14, width: 20, textAlign: 'center',
            color: isGroupActive ? '#818cf8' : '#475569',
            flexShrink: 0,
          }}>{group.icon}</span>
          {!collapsed && (
            <span style={{
              fontSize: 11, fontWeight: 700, textTransform: 'uppercase',
              letterSpacing: '0.07em',
              color: isGroupActive ? '#94a3b8' : '#374151',
            }}>{group.label}</span>
          )}
        </div>
        {!collapsed && (
          <span style={{
            fontSize: 10, color: '#374151',
            transform: open ? 'rotate(0deg)' : 'rotate(-90deg)',
            transition: 'transform 0.15s', display: 'inline-block',
          }}>▾</span>
        )}
      </button>

      {/* Items */}
      {(collapsed ? false : open) && (
        <div style={{ paddingBottom: 4 }}>
          {items.map(item => {
            const active = item.href === '/'
              ? currentPath === '/'
              : currentPath.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  padding: '6px 12px 6px 20px',
                  borderRadius: 6,
                  fontSize: 13,
                  fontWeight: active ? 600 : 400,
                  color: active ? '#e2e8f0' : (item.color ?? '#94a3b8'),
                  background: active ? 'rgba(99,102,241,0.12)' : 'transparent',
                  borderLeft: active ? '2px solid #6366f1' : '2px solid transparent',
                  textDecoration: 'none',
                  transition: 'background 0.1s, color 0.1s',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}
                className="sidebar-item"
              >
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{item.label}</span>
                {item.tag && (
                  <span style={{
                    fontSize: 9, fontWeight: 700, padding: '1px 5px',
                    borderRadius: 3, textTransform: 'uppercase', letterSpacing: '0.05em',
                    background: 'rgba(99,102,241,0.15)', color: '#818cf8',
                    flexShrink: 0, marginLeft: 4,
                  }}>{item.tag}</span>
                )}
              </Link>
            );
          })}
        </div>
      )}

      {/* Collapsed: show active item indicators as dots */}
      {collapsed && items.map(item => {
        const active = item.href === '/'
          ? currentPath === '/'
          : currentPath.startsWith(item.href);
        if (!active) return null;
        return (
          <Link key={item.href} href={item.href} title={item.label} style={{
            display: 'flex', justifyContent: 'center', padding: '3px 0',
          }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#6366f1', display: 'inline-block' }} />
          </Link>
        );
      })}
    </div>
  );
}

// ── Main App ──────────────────────────────────────────────────────────────────

export default function App({ Component, pageProps }: AppProps) {
  const router = useRouter();
  const [username, setUsername] = useState<string | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const [checked, setChecked] = useState(false);
  const [impersonating, setImpersonating] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const alertTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Persist sidebar collapse state
  useEffect(() => {
    const saved = typeof localStorage !== 'undefined' && localStorage.getItem('stockai_sidebar_collapsed');
    if (saved === 'true') setCollapsed(true);
  }, []);
  function toggleCollapse() {
    setCollapsed(c => {
      const next = !c;
      if (typeof localStorage !== 'undefined') localStorage.setItem('stockai_sidebar_collapsed', String(next));
      return next;
    });
  }

  useEffect(() => {
    if (PUBLIC_PATHS.includes(router.pathname)) { setChecked(true); return; }
    async function doCheck() {
      if (!hasGateCookie()) {
        try {
          const r = await fetch('/api/gate');
          const { enabled } = await r.json() as { enabled: boolean };
          if (enabled) { router.replace(`/gate?next=${encodeURIComponent(router.pathname)}`); return; }
        } catch {}
      }
      const session = getSession();
      if (session) {
        setUsername(session.username);
        setRole(session.role);
        setImpersonating(getImpersonatedUser());
        const settings = loadSettings();
        if (settings.polygonApiKey || settings.alphaVantageApiKey) {
          api.pushConfig({
            polygon_api_key: settings.polygonApiKey || undefined,
            alpha_vantage_api_key: settings.alphaVantageApiKey || undefined,
            quiver_api_key: settings.quiverApiKey || undefined,
          }).catch(() => {});
        }
      } else {
        router.replace('/login');
      }
      setChecked(true);
    }
    doCheck();
  }, [router.pathname]);

  useEffect(() => {
    if (!username) return;
    async function runCheck() {
      try {
        const [priceList, signalList, rankData] = await Promise.all([
          api.latestPrices(), api.allSignals(), api.rankings(),
        ]);
        const prices: Record<string, { price: number; change_pct: number | null }> = {};
        for (const p of priceList) prices[p.symbol] = { price: p.price, change_pct: p.change_pct ?? null };
        const signals: Record<string, { signal: string; confidence: number }> = {};
        for (const s of signalList) signals[s.symbol] = { signal: s.signal, confidence: s.confidence };
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
      } catch {}
    }
    runCheck();
    alertTimerRef.current = setInterval(runCheck, 60_000);
    return () => { if (alertTimerRef.current) clearInterval(alertTimerRef.current); };
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
  if (PUBLIC_PATHS.includes(router.pathname)) return <Component {...pageProps} />;
  if (!username) return null;

  const sw = collapsed ? SIDEBAR_C : SIDEBAR_W;

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>

      {/* ── Left Sidebar ──────────────────────────────────────────────────── */}
      <aside style={{
        position: 'fixed', top: 0, left: 0, bottom: 0,
        width: sw, background: '#080e1c',
        borderRight: '1px solid #0f1d33',
        display: 'flex', flexDirection: 'column',
        zIndex: 500,
        transition: 'width 0.2s ease',
        overflow: 'hidden',
      }}>

        {/* Logo + collapse toggle */}
        <div style={{
          display: 'flex', alignItems: 'center',
          justifyContent: collapsed ? 'center' : 'space-between',
          padding: collapsed ? '16px 0' : '14px 14px 14px 16px',
          borderBottom: '1px solid #0f1d33',
          flexShrink: 0,
        }}>
          {!collapsed && (
            <Link href="/" style={{ textDecoration: 'none' }}>
              <span style={{ fontSize: 16, fontWeight: 800, color: '#818cf8', letterSpacing: '-0.02em' }}>Stock</span>
              <span style={{ fontSize: 16, fontWeight: 800, color: '#e2e8f0', letterSpacing: '-0.02em' }}>AI</span>
            </Link>
          )}
          {collapsed && (
            <Link href="/" style={{ textDecoration: 'none' }}>
              <span style={{ fontSize: 16, fontWeight: 800, color: '#818cf8' }}>S</span>
            </Link>
          )}
          <button
            onClick={toggleCollapse}
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            style={{
              background: 'none', border: 'none', cursor: 'pointer',
              color: '#374151', fontSize: 12, padding: '4px 6px',
              borderRadius: 4, lineHeight: 1,
              display: 'flex', alignItems: 'center',
            }}
            className="sidebar-toggle"
          >
            {collapsed ? '▶' : '◀'}
          </button>
        </div>

        {/* Impersonation banner inside sidebar */}
        {impersonating && !collapsed && (
          <div style={{
            padding: '6px 12px', background: 'rgba(124,58,237,0.2)',
            borderBottom: '1px solid rgba(124,58,237,0.3)',
            fontSize: 11, color: '#c4b5fd', flexShrink: 0,
          }}>
            👁 As <strong>{impersonating}</strong>
            <button
              onClick={handleExitImpersonation}
              style={{ marginLeft: 8, background: 'none', border: 'none', color: '#a78bfa', cursor: 'pointer', fontSize: 11, textDecoration: 'underline', padding: 0 }}
            >exit</button>
          </div>
        )}

        {/* Nav groups — scrollable */}
        <nav style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', padding: collapsed ? '8px 0' : '8px 8px', scrollbarWidth: 'thin', scrollbarColor: '#1e293b transparent' }}>
          {NAV_GROUPS.map(group => (
            <SidebarGroup
              key={group.label}
              group={group}
              currentPath={router.pathname}
              userRole={role}
              collapsed={collapsed}
              defaultOpen={true}
            />
          ))}
        </nav>

        {/* Bottom: notification, user, settings, logout */}
        <div style={{
          borderTop: '1px solid #0f1d33', padding: collapsed ? '10px 0' : '10px 12px',
          flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 4,
        }}>
          {/* Notification bell */}
          <div style={{ display: 'flex', justifyContent: collapsed ? 'center' : 'flex-start', padding: collapsed ? '4px 0' : '2px 4px' }}>
            <NotificationBell />
          </div>

          {!collapsed && (
            <>
              {/* User info */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 4px' }}>
                <div style={{
                  width: 26, height: 26, borderRadius: '50%',
                  background: 'rgba(99,102,241,0.2)', border: '1px solid rgba(99,102,241,0.3)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 11, fontWeight: 700, color: '#818cf8', flexShrink: 0,
                }}>
                  {username?.[0]?.toUpperCase()}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12, color: '#94a3b8', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{username}</div>
                  {role === 'admin' && (
                    <div style={{ fontSize: 9, color: '#fb7185', letterSpacing: '0.04em', fontWeight: 700 }}>ADMIN</div>
                  )}
                </div>
              </div>

              {/* Settings + Logout */}
              <div style={{ display: 'flex', gap: 6, padding: '2px 4px' }}>
                <Link
                  href="/settings"
                  style={{
                    flex: 1, textAlign: 'center', padding: '5px 0',
                    borderRadius: 5, fontSize: 11, color: '#475569',
                    border: '1px solid #0f1d33', textDecoration: 'none',
                    background: 'rgba(255,255,255,0.02)',
                  }}
                  className="sidebar-bottom-btn"
                >⚙ Settings</Link>
                <button
                  onClick={handleLogout}
                  style={{
                    flex: 1, padding: '5px 0',
                    borderRadius: 5, fontSize: 11, color: '#475569',
                    border: '1px solid #0f1d33', cursor: 'pointer',
                    background: 'rgba(255,255,255,0.02)',
                  }}
                  className="sidebar-bottom-btn"
                >↩ Logout</button>
              </div>
            </>
          )}

          {/* Collapsed: icon buttons */}
          {collapsed && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6 }}>
              <Link href="/settings" title="Settings" style={{ color: '#475569', fontSize: 14, padding: '4px', display: 'block' }}>⚙</Link>
              <button onClick={handleLogout} title="Logout" style={{ background: 'none', border: 'none', color: '#475569', fontSize: 14, cursor: 'pointer', padding: '4px' }}>↩</button>
            </div>
          )}
        </div>
      </aside>

      {/* ── Main content ─────────────────────────────────────────────────── */}
      <div style={{ marginLeft: sw, flex: 1, minWidth: 0, transition: 'margin-left 0.2s ease' }}>
        {/* Optional impersonation top banner when expanded */}
        {impersonating && (
          <div style={{
            position: 'sticky', top: 0, zIndex: 400,
            background: '#7c3aed', color: '#fff', fontSize: 12,
            padding: '6px 20px', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          }}>
            <span>👁 Viewing as <strong>{impersonating}</strong> — all actions are performed as this user</span>
            <button
              onClick={handleExitImpersonation}
              style={{ background: 'rgba(255,255,255,0.2)', border: '1px solid rgba(255,255,255,0.4)', color: '#fff', borderRadius: 5, padding: '2px 12px', fontSize: 11, cursor: 'pointer', fontWeight: 700 }}
            >← Return to Admin</button>
          </div>
        )}
        <main style={{ padding: '20px 24px', maxWidth: 1400 }}>
          <Component {...pageProps} />
        </main>
      </div>

      <style>{`
        .sidebar-item:hover { background: rgba(255,255,255,0.04) !important; }
        .sidebar-toggle:hover { color: #94a3b8 !important; background: rgba(255,255,255,0.05) !important; }
        .sidebar-bottom-btn:hover { border-color: #1e293b !important; color: #94a3b8 !important; background: rgba(255,255,255,0.04) !important; }
        nav::-webkit-scrollbar { width: 3px; }
        nav::-webkit-scrollbar-track { background: transparent; }
        nav::-webkit-scrollbar-thumb { background: #1e293b; border-radius: 2px; }
      `}</style>
    </div>
  );
}
