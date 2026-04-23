import { useState, useEffect, useRef } from 'react';
import { loadNotifications, markAllRead, clearNotifications, getUnreadCount } from '@/lib/alerts';

export default function NotificationBell() {
  const [open, setOpen] = useState(false);
  const [unread, setUnread] = useState(0);
  const [notifications, setNotifications] = useState(() => loadNotifications().slice(0, 30));
  const panelRef = useRef<HTMLDivElement>(null);

  function refresh() {
    setUnread(getUnreadCount());
    setNotifications(loadNotifications().slice(0, 30));
  }

  useEffect(() => {
    refresh();
    window.addEventListener('stockai:notifications', refresh);
    return () => window.removeEventListener('stockai:notifications', refresh);
  }, []);

  // Close panel on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) setOpen(false);
    }
    if (open) document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [open]);

  function handleOpen() {
    setOpen(o => !o);
    if (!open && unread > 0) {
      markAllRead();
      refresh();
    }
  }

  function relTime(iso: string): string {
    const diff = Date.now() - new Date(iso).getTime();
    const m = Math.floor(diff / 60000);
    if (m < 1) return 'just now';
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    return `${Math.floor(h / 24)}d ago`;
  }

  return (
    <div ref={panelRef} style={{ position: 'relative' }}>
      <button
        onClick={handleOpen}
        style={{
          position: 'relative', background: 'transparent',
          border: '1px solid #1e293b', borderRadius: '6px',
          padding: '4px 10px', cursor: 'pointer', fontSize: '15px',
          color: unread > 0 ? '#818cf8' : '#475569', transition: 'all 0.15s',
        }}
        title="Notifications"
      >
        🔔
        {unread > 0 && (
          <span style={{
            position: 'absolute', top: '-6px', right: '-6px',
            background: '#ef4444', color: '#fff',
            fontSize: '10px', fontWeight: 700, borderRadius: '999px',
            minWidth: '16px', height: '16px', lineHeight: '16px',
            textAlign: 'center', padding: '0 3px',
          }}>
            {unread > 99 ? '99+' : unread}
          </span>
        )}
      </button>

      {open && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 8px)', right: 0, zIndex: 2000,
          width: '340px', maxHeight: '480px', display: 'flex', flexDirection: 'column',
          background: '#0f172a', border: '1px solid #1e293b', borderRadius: '12px',
          boxShadow: '0 20px 50px rgba(0,0,0,0.6)',
          overflow: 'hidden',
        }}>
          {/* Header */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 16px', borderBottom: '1px solid #1e293b', flexShrink: 0 }}>
            <span style={{ fontWeight: 700, fontSize: '13px', color: '#e2e8f0' }}>Notifications</span>
            <div style={{ display: 'flex', gap: '8px' }}>
              {notifications.length > 0 && (
                <button onClick={() => { clearNotifications(); refresh(); }} style={{ fontSize: '11px', color: '#475569', background: 'none', border: 'none', cursor: 'pointer' }}>
                  Clear all
                </button>
              )}
            </div>
          </div>

          {/* List */}
          <div style={{ overflowY: 'auto', flex: 1 }}>
            {notifications.length === 0 ? (
              <div style={{ padding: '32px 16px', textAlign: 'center', fontSize: '12px', color: '#334155' }}>
                No notifications yet.<br />Set up alerts to get notified.
              </div>
            ) : (
              notifications.map(n => (
                <div key={n.id} style={{
                  padding: '11px 16px',
                  borderBottom: '1px solid rgba(30,41,59,0.6)',
                  background: n.read ? 'transparent' : 'rgba(99,102,241,0.05)',
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: '8px', alignItems: 'flex-start' }}>
                    <div style={{ display: 'flex', gap: '8px', alignItems: 'flex-start', flex: 1 }}>
                      <span style={{
                        fontSize: '10px', fontWeight: 700, padding: '2px 6px', borderRadius: '4px',
                        background: 'rgba(99,102,241,0.15)', color: '#818cf8', flexShrink: 0, marginTop: '1px',
                      }}>
                        {n.symbol}
                      </span>
                      <span style={{ fontSize: '12px', color: '#cbd5e1', lineHeight: 1.4 }}>{n.message}</span>
                    </div>
                    <span style={{ fontSize: '10px', color: '#334155', flexShrink: 0 }}>{relTime(n.triggeredAt)}</span>
                  </div>
                </div>
              ))
            )}
          </div>

          {/* Footer */}
          <div style={{ padding: '10px 16px', borderTop: '1px solid #1e293b', flexShrink: 0, textAlign: 'center' }}>
            <a href="/alerts" style={{ fontSize: '12px', color: '#4f46e5', textDecoration: 'none' }}>Manage alerts →</a>
          </div>
        </div>
      )}
    </div>
  );
}
