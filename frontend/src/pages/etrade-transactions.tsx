import { useEffect, useState } from 'react';
import { useRouter } from 'next/router';
import useSWR from 'swr';
import { api, type BrokerConnection, type BrokerOrderHistoryItem } from '@/lib/api';
import { getSession } from '@/lib/auth';

type TaggedOrder = BrokerOrderHistoryItem & {
  connectionId: number;
  connectionName: string;
  env: 'sandbox' | 'prod';
};

type StatusFilter = 'all' | 'open' | 'filled' | 'cancelled' | 'rejected';

const ENV_LABEL: Record<'sandbox' | 'prod', string> = { sandbox: 'Sandbox', prod: 'Live' };
const ENV_COLOR: Record<'sandbox' | 'prod', string> = { sandbox: '#f59e0b', prod: '#4ade80' };

function statusColor(status: string): string {
  const s = status.toLowerCase();
  if (s.includes('reject') || s.includes('cancel')) return '#f87171';
  if (s.includes('fill') || s.includes('execut')) return '#4ade80';
  return '#94a3b8';
}

function fmtMoney(n: number | null): string {
  return n == null ? '—' : `$${n.toFixed(2)}`;
}

export default function EtradeTransactionsPage() {
  const router = useRouter();
  const [authed, setAuthed] = useState(false);
  const [envFilter, setEnvFilter] = useState<'all' | 'sandbox' | 'prod'>('all');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [symbolFilter, setSymbolFilter] = useState('');

  useEffect(() => {
    const session = getSession();
    if (!session) { router.replace('/login'); return; }
    // broker.py's routes are admin-only (T270-ETRADE-PROD-REAL-MONEY) — this whole page exists
    // solely to call api.brokerList()/api.brokerOrderHistory(), both of which now 403 for a
    // non-admin session; redirect rather than render a page that can never show real data.
    if (session.role !== 'admin') { router.replace('/'); return; }
    setAuthed(true);
  }, [router]);

  const { data: connections, isLoading: connectionsLoading } = useSWR(
    authed ? 'broker-connections' : null, () => api.brokerList(), { refreshInterval: 60_000 }
  );

  const etradeConnections = (connections ?? []).filter(
    (c): c is BrokerConnection => c.broker_type === 'etrade' || c.broker_type === 'etrade_sandbox'
  );

  // Fan out one order-history fetch per E*Trade connection (sandbox + prod are two separate
  // BrokerConnection rows — there's no backend endpoint that aggregates across connections),
  // then tag each order with which connection/environment it came from since
  // BrokerOrderHistoryItem itself carries no connection identifier.
  const { data: taggedResults } = useSWR(
    authed && etradeConnections.length ? ['etrade-orders', etradeConnections.map(c => c.id).join(',')] : null,
    async () => {
      const results = await Promise.all(
        etradeConnections.map(async (conn) => {
          try {
            const res = await api.brokerOrderHistory(conn.id, 'all');
            return { conn, orders: res.orders, error: null as string | null };
          } catch (e) {
            const msg = e instanceof Error ? e.message : 'Failed to load';
            return { conn, orders: [] as BrokerOrderHistoryItem[], error: msg.includes('501') ? 'unsupported' : msg };
          }
        })
      );
      return results;
    },
    { refreshInterval: 60_000 }
  );

  const allOrders: TaggedOrder[] = (taggedResults ?? []).flatMap(({ conn, orders }) =>
    orders.map(o => ({
      ...o,
      connectionId: conn.id,
      connectionName: conn.name,
      env: conn.broker_type === 'etrade_sandbox' ? 'sandbox' as const : 'prod' as const,
    }))
  );

  const filtered = allOrders
    .filter(o => envFilter === 'all' || o.env === envFilter)
    .filter(o => statusFilter === 'all' || o.status.toLowerCase().includes(statusFilter))
    .filter(o => !symbolFilter.trim() || o.symbol.toUpperCase().includes(symbolFilter.trim().toUpperCase()))
    .sort((a, b) => (b.placed_at ?? '').localeCompare(a.placed_at ?? ''));

  const unsupportedConns = (taggedResults ?? []).filter(r => r.error === 'unsupported');
  const failedConns = (taggedResults ?? []).filter(r => r.error && r.error !== 'unsupported');

  if (!authed) return null;

  return (
    <div style={{ maxWidth: '1100px', margin: '0 auto', paddingTop: '8px' }}>
      <div style={{ marginBottom: '20px' }}>
        <h1 style={{ margin: 0, fontSize: '20px', fontWeight: 800, color: '#f1f5f9' }}>
          E*Trade Transactions
        </h1>
        <div style={{ fontSize: '12px', color: '#475569', marginTop: '3px' }}>
          All orders across your E*Trade sandbox and live connections, fetched live from E*Trade
          on each refresh (E*Trade's own API returns roughly the last 90 days — nothing is stored
          long-term by this app). Manage connections and re-authorize in{' '}
          <a href="/settings" style={{ color: '#7dd3fc' }}>Settings</a>.
        </div>
      </div>

      {connectionsLoading && (
        <div style={{ padding: '30px', textAlign: 'center', color: '#475569', fontSize: '13px' }}>
          Loading connections…
        </div>
      )}

      {!connectionsLoading && etradeConnections.length === 0 && (
        <div style={{
          padding: '30px', textAlign: 'center', color: '#475569', fontSize: '13px',
          border: '1px solid #1e293b', borderRadius: '12px', background: 'rgba(15,23,42,0.6)',
        }}>
          No E*Trade connections found.{' '}
          <a href="/settings" style={{ color: '#7dd3fc' }}>Add one in Settings</a> (sandbox or live).
        </div>
      )}

      {etradeConnections.length > 0 && (
        <>
          {/* Per-connection status strip */}
          <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', marginBottom: '16px' }}>
            {etradeConnections.map(conn => {
              const env = conn.broker_type === 'etrade_sandbox' ? 'sandbox' as const : 'prod' as const;
              const result = (taggedResults ?? []).find(r => r.conn.id === conn.id);
              return (
                <div
                  key={conn.id}
                  style={{
                    padding: '10px 16px', borderRadius: '10px',
                    border: `1px solid ${ENV_COLOR[env]}40`, background: `${ENV_COLOR[env]}10`,
                    fontSize: '12px', minWidth: '180px',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span style={{
                      fontSize: '10px', fontWeight: 700, color: ENV_COLOR[env],
                      padding: '1px 7px', borderRadius: '4px', background: `${ENV_COLOR[env]}20`,
                    }}>
                      {ENV_LABEL[env]}
                    </span>
                    <span style={{ fontWeight: 600, color: '#e2e8f0' }}>{conn.name}</span>
                  </div>
                  <div style={{ marginTop: '4px', color: '#64748b' }}>
                    {!conn.is_authorized
                      ? <span style={{ color: '#f87171' }}>Not authorized</span>
                      : result?.error === 'unsupported'
                        ? 'Order history unsupported'
                        : result?.error
                          ? <span style={{ color: '#f87171' }}>Failed to load</span>
                          : `${result?.orders.length ?? 0} orders`}
                  </div>
                </div>
              );
            })}
          </div>

          {failedConns.length > 0 && (
            <div style={{ padding: '10px 14px', marginBottom: '12px', borderRadius: '8px', background: 'rgba(248,113,113,0.1)', border: '1px solid rgba(248,113,113,0.3)', fontSize: '12px', color: '#f87171' }}>
              Failed to load order history for: {failedConns.map(f => f.conn.name).join(', ')}.
            </div>
          )}

          {/* Filters */}
          <div style={{
            display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'center',
            marginBottom: '16px', padding: '12px 14px', borderRadius: '10px',
            border: '1px solid #1e293b', background: 'rgba(15,23,42,0.6)',
          }}>
            <select
              value={envFilter}
              onChange={e => setEnvFilter(e.target.value as typeof envFilter)}
              style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: '8px', padding: '7px 10px', fontSize: '12px', color: '#e2e8f0', outline: 'none' }}
            >
              <option value="all">All environments</option>
              <option value="sandbox">Sandbox only</option>
              <option value="prod">Live only</option>
            </select>
            <select
              value={statusFilter}
              onChange={e => setStatusFilter(e.target.value as StatusFilter)}
              style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: '8px', padding: '7px 10px', fontSize: '12px', color: '#e2e8f0', outline: 'none' }}
            >
              <option value="all">All statuses</option>
              <option value="open">Open</option>
              <option value="filled">Filled</option>
              <option value="cancelled">Cancelled</option>
              <option value="rejected">Rejected</option>
            </select>
            <input
              value={symbolFilter}
              onChange={e => setSymbolFilter(e.target.value)}
              placeholder="Filter by symbol"
              style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: '8px', padding: '7px 10px', fontSize: '12px', color: '#e2e8f0', outline: 'none', width: '160px' }}
            />
            <div style={{ fontSize: '11px', color: '#334155', marginLeft: 'auto' }}>
              {filtered.length} of {allOrders.length} orders · refreshes every 60s
            </div>
          </div>

          {unsupportedConns.length > 0 && unsupportedConns.length === etradeConnections.length && (
            <div style={{ padding: '30px', textAlign: 'center', color: '#475569', fontSize: '13px' }}>
              None of your E*Trade connections support order history via API.
            </div>
          )}

          {allOrders.length === 0 && unsupportedConns.length < etradeConnections.length && (
            <div style={{ padding: '30px', textAlign: 'center', color: '#475569', fontSize: '13px' }}>
              No orders found across any connection.
            </div>
          )}

          {filtered.length > 0 && (
            <div style={{ overflowX: 'auto', border: '1px solid #1e293b', borderRadius: '12px' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
                <thead>
                  <tr style={{ background: 'rgba(15,23,42,0.9)', textAlign: 'left' }}>
                    {['Env', 'Account', 'Symbol', 'Side', 'Qty', 'Status', 'Filled Price', 'Placed'].map(h => (
                      <th key={h} style={{ padding: '10px 12px', fontSize: '11px', color: '#64748b', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', borderBottom: '1px solid #1e293b' }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((o, i) => (
                    <tr key={`${o.connectionId}-${o.order_id}-${i}`} style={{ borderBottom: '1px solid #0f172a' }}>
                      <td style={{ padding: '9px 12px' }}>
                        <span style={{ fontSize: '10px', fontWeight: 700, color: ENV_COLOR[o.env], padding: '1px 7px', borderRadius: '4px', background: `${ENV_COLOR[o.env]}20` }}>
                          {ENV_LABEL[o.env]}
                        </span>
                      </td>
                      <td style={{ padding: '9px 12px', color: '#94a3b8' }}>{o.connectionName}</td>
                      <td style={{ padding: '9px 12px', fontWeight: 700, color: '#e2e8f0' }}>{o.symbol}</td>
                      <td style={{ padding: '9px 12px', fontWeight: 600, color: o.side === 'buy' ? '#4ade80' : '#f87171' }}>
                        {o.side.toUpperCase()}
                      </td>
                      <td style={{ padding: '9px 12px', color: '#e2e8f0' }}>{o.qty}</td>
                      <td style={{ padding: '9px 12px', color: statusColor(o.status) }}>
                        {o.status.replace(/_/g, ' ')}
                      </td>
                      <td style={{ padding: '9px 12px', color: '#e2e8f0' }}>{fmtMoney(o.filled_avg_price)}</td>
                      <td style={{ padding: '9px 12px', color: '#64748b' }}>
                        {o.placed_at ? new Date(o.placed_at).toLocaleString() : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
