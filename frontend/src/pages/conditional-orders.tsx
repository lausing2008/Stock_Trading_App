import { useEffect, useState } from 'react';
import { useRouter } from 'next/router';
import useSWR from 'swr';
import { getSession } from '@/lib/auth';
import { api } from '@/lib/api';
import type {
  ConditionalOrderCondition, ConditionalOrderCreateRequest, ConditionalOrderItem, PaperPortfolioListItem,
} from '@/lib/api';

const ACTION_TYPES: ConditionalOrderItem['action_type'][] = [
  'buy', 'sell_partial', 'sell_all', 'tighten_stop', 'close_position', 'alert_only',
];

const ACTION_LABEL: Record<ConditionalOrderItem['action_type'], string> = {
  buy: 'Buy',
  sell_partial: 'Sell Partial',
  sell_all: 'Sell All',
  tighten_stop: 'Tighten Stop',
  close_position: 'Close Position',
  alert_only: 'Alert Only',
};

const METRICS: ConditionalOrderCondition['metric'][] = [
  'price', 'rsi', 'volume_ratio', 'signal', 'position_pnl_pct', 'time',
];

const METRIC_LABEL: Record<ConditionalOrderCondition['metric'], string> = {
  price: 'Price',
  rsi: 'RSI',
  volume_ratio: 'Volume Ratio (RVOL)',
  signal: 'AI Signal',
  position_pnl_pct: 'Position P&L %',
  time: 'Time (UTC HH:MM)',
};

const STATUS_COLOR: Record<ConditionalOrderItem['status'], string> = {
  pending: '#64748b',
  triggered: '#22c55e',
  failed: '#f87171',
  expired: '#f59e0b',
  cancelled: '#94a3b8',
};

type ConditionDraft = { metric: ConditionalOrderCondition['metric']; op: 'gte' | 'lte' | 'eq'; value: string };

function emptyCondition(): ConditionDraft {
  return { metric: 'price', op: 'gte', value: '' };
}

const inputStyle: React.CSSProperties = {
  background: '#0d1424', border: '1px solid #1e293b', borderRadius: 6, color: '#e2e8f0',
  padding: '6px 10px', fontSize: '13px', width: '100%',
};

const labelStyle: React.CSSProperties = {
  fontSize: '11px', color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 4, display: 'block',
};

function StatusPill({ status }: { status: ConditionalOrderItem['status'] }) {
  const color = STATUS_COLOR[status];
  return (
    <span style={{
      display: 'inline-block', fontSize: '11px', fontWeight: 700, padding: '2px 9px', borderRadius: 999,
      background: `${color}1a`, color, textTransform: 'uppercase', letterSpacing: '0.03em',
    }}>
      {status}
    </span>
  );
}

function CreateOrderForm({ portfolios, onCreated }: { portfolios: PaperPortfolioListItem[]; onCreated: () => void }) {
  const [portfolioId, setPortfolioId] = useState<number | ''>('');
  const [symbol, setSymbol] = useState('');
  const [actionType, setActionType] = useState<ConditionalOrderItem['action_type']>('buy');
  const [actionValue, setActionValue] = useState('');
  const [triggerLogic, setTriggerLogic] = useState<'AND' | 'OR'>('AND');
  const [conditions, setConditions] = useState<ConditionDraft[]>([emptyCondition()]);
  const [note, setNote] = useState('');
  const [email, setEmail] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (portfolios.length > 0 && portfolioId === '') setPortfolioId(portfolios[0].id);
  }, [portfolios, portfolioId]);

  const updateCondition = (idx: number, patch: Partial<ConditionDraft>) => {
    setConditions(prev => prev.map((c, i) => (i === idx ? { ...c, ...patch } : c)));
  };

  const removeCondition = (idx: number) => {
    setConditions(prev => (prev.length > 1 ? prev.filter((_, i) => i !== idx) : prev));
  };

  const needsActionValue = actionType === 'tighten_stop' || actionType === 'sell_partial';

  const handleSubmit = async () => {
    setError(null);
    if (portfolioId === '') { setError('Choose a portfolio'); return; }
    if (!symbol.trim()) { setError('Enter a symbol'); return; }
    if (conditions.some(c => !c.value.trim())) { setError('Every condition needs a value'); return; }
    if (needsActionValue && !actionValue.trim()) {
      setError(actionType === 'tighten_stop' ? 'Enter the new stop price' : 'Enter the fraction to sell (0-1)');
      return;
    }

    const req: ConditionalOrderCreateRequest = {
      portfolio_id: Number(portfolioId),
      symbol: symbol.trim().toUpperCase(),
      action_type: actionType,
      trigger_logic: triggerLogic,
      conditions: conditions.map(c => ({
        metric: c.metric, op: c.op,
        value: c.metric === 'signal' ? c.value.trim().toUpperCase() : parseFloat(c.value),
      })),
      action_value: needsActionValue ? parseFloat(actionValue) : null,
      note: note.trim() || null,
      email: email.trim() || null,
    };

    setSaving(true);
    try {
      await api.createConditionalOrder(req);
      setSymbol(''); setActionValue(''); setNote(''); setConditions([emptyCondition()]);
      onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create order');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 12, padding: 20, marginBottom: 24 }}>
      <h3 style={{ fontSize: '14px', fontWeight: 700, color: '#e2e8f0', marginBottom: 16 }}>New Conditional Order</h3>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginBottom: 14 }}>
        <div>
          <label style={labelStyle}>Portfolio</label>
          <select style={inputStyle} value={portfolioId} onChange={e => setPortfolioId(Number(e.target.value))}>
            {portfolios.map(p => (
              <option key={p.id} value={p.id}>{p.name} ({p.market} {p.trading_style})</option>
            ))}
          </select>
        </div>
        <div>
          <label style={labelStyle}>Symbol</label>
          <input style={inputStyle} value={symbol} onChange={e => setSymbol(e.target.value)} placeholder="NVDA" />
        </div>
        <div>
          <label style={labelStyle}>Action</label>
          <select style={inputStyle} value={actionType} onChange={e => setActionType(e.target.value as ConditionalOrderItem['action_type'])}>
            {ACTION_TYPES.map(a => <option key={a} value={a}>{ACTION_LABEL[a]}</option>)}
          </select>
        </div>
      </div>

      {needsActionValue && (
        <div style={{ marginBottom: 14 }}>
          <label style={labelStyle}>
            {actionType === 'tighten_stop' ? 'New Stop Price ($)' : 'Fraction to Sell (0–1)'}
          </label>
          <input style={{ ...inputStyle, maxWidth: 200 }} value={actionValue} onChange={e => setActionValue(e.target.value)}
                 placeholder={actionType === 'tighten_stop' ? '135.00' : '0.5'} type="number" step="any" />
        </div>
      )}

      <div style={{ marginBottom: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <label style={labelStyle}>Trigger Conditions</label>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <span style={{ fontSize: '11px', color: '#64748b' }}>Combine with:</span>
            <select style={{ ...inputStyle, width: 'auto', padding: '4px 8px' }} value={triggerLogic}
                    onChange={e => setTriggerLogic(e.target.value as 'AND' | 'OR')}>
              <option value="AND">ALL must be true (AND)</option>
              <option value="OR">ANY must be true (OR)</option>
            </select>
          </div>
        </div>
        {conditions.map((c, idx) => (
          <div key={idx} style={{ display: 'grid', gridTemplateColumns: '1fr auto 1fr auto', gap: 8, marginBottom: 8, alignItems: 'center' }}>
            <select style={inputStyle} value={c.metric} onChange={e => updateCondition(idx, { metric: e.target.value as ConditionalOrderCondition['metric'] })}>
              {METRICS.map(m => <option key={m} value={m}>{METRIC_LABEL[m]}</option>)}
            </select>
            <select style={{ ...inputStyle, width: 'auto' }} value={c.op} onChange={e => updateCondition(idx, { op: e.target.value as 'gte' | 'lte' | 'eq' })}>
              <option value="gte">&ge;</option>
              <option value="lte">&le;</option>
              <option value="eq">=</option>
            </select>
            <input style={inputStyle} value={c.value} onChange={e => updateCondition(idx, { value: e.target.value })}
                   placeholder={c.metric === 'signal' ? 'BUY' : c.metric === 'time' ? '14:30' : '140'} />
            <button onClick={() => removeCondition(idx)} disabled={conditions.length === 1}
                    style={{ background: 'transparent', border: 'none', color: '#f87171', cursor: conditions.length > 1 ? 'pointer' : 'default', opacity: conditions.length > 1 ? 1 : 0.3, fontSize: '16px' }}>
              &times;
            </button>
          </div>
        ))}
        <button onClick={() => setConditions(prev => [...prev, emptyCondition()])}
                style={{ background: 'transparent', border: '1px dashed #334155', color: '#64748b', borderRadius: 6, padding: '4px 10px', fontSize: '12px', cursor: 'pointer' }}>
          + Add condition
        </button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 14 }}>
        <div>
          <label style={labelStyle}>Note (optional)</label>
          <input style={inputStyle} value={note} onChange={e => setNote(e.target.value)} placeholder="Breakout entry" />
        </div>
        <div>
          <label style={labelStyle}>Email (optional)</label>
          <input style={inputStyle} value={email} onChange={e => setEmail(e.target.value)} placeholder="you@example.com" />
        </div>
      </div>

      {error && <div style={{ color: '#f87171', fontSize: '12.5px', marginBottom: 10 }}>{error}</div>}

      <button onClick={handleSubmit} disabled={saving}
              style={{ background: '#22c55e', border: 'none', color: '#0f172a', fontWeight: 700, borderRadius: 8, padding: '9px 20px', fontSize: '13px', cursor: saving ? 'default' : 'pointer', opacity: saving ? 0.6 : 1 }}>
        {saving ? 'Creating…' : 'Create Order'}
      </button>
    </div>
  );
}

function OrderRow({ order, onCancel }: { order: ConditionalOrderItem; onCancel: (id: number) => void }) {
  const condSummary = order.conditions
    .map(c => `${METRIC_LABEL[c.metric]} ${c.op === 'gte' ? '≥' : c.op === 'lte' ? '≤' : '='} ${c.value}`)
    .join(order.trigger_logic === 'OR' ? '  OR  ' : '  AND  ');

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '90px 1fr 140px 100px 1fr auto', gap: 12, alignItems: 'center', padding: '12px 0', borderBottom: '1px solid #1e293b', fontSize: '13px' }}>
      <strong style={{ color: '#e2e8f0' }}>{order.symbol}</strong>
      <span style={{ color: '#94a3b8' }}>{condSummary}</span>
      <span style={{ color: '#cbd5e1' }}>{ACTION_LABEL[order.action_type]}{order.action_value != null ? ` (${order.action_value})` : ''}</span>
      <StatusPill status={order.status} />
      <span style={{ color: '#64748b', fontSize: '12px' }}>{order.status_reason ?? '—'}</span>
      {order.status === 'pending' ? (
        <button onClick={() => onCancel(order.id)}
                style={{ background: 'transparent', border: '1px solid #f87171', color: '#f87171', borderRadius: 6, padding: '3px 10px', fontSize: '11.5px', cursor: 'pointer' }}>
          Cancel
        </button>
      ) : (
        <span />
      )}
    </div>
  );
}

export default function ConditionalOrdersPage() {
  const router = useRouter();
  const [authed, setAuthed] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string>('');

  useEffect(() => {
    const session = getSession();
    if (!session) { router.replace('/login'); return; }
    setAuthed(true);
  }, [router]);

  const { data: portfolios } = useSWR(authed ? 'paper-list-co' : null, () => api.paperList());
  const { data, mutate } = useSWR(
    authed ? ['conditional-orders', statusFilter] : null,
    () => api.listConditionalOrders(statusFilter ? { status: statusFilter } : undefined),
    { refreshInterval: 15_000 },
  );

  if (!authed) return null;

  const orders = data?.orders ?? [];

  const handleCancel = async (id: number) => {
    try {
      await api.cancelConditionalOrder(id);
      mutate();
    } catch {
      /* swallow — a stale row on next poll is harmless */
    }
  };

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '24px 24px 60px' }}>
      <div style={{ marginBottom: '24px' }}>
        <h1 style={{ fontSize: '22px', fontWeight: 800, color: '#e2e8f0', marginBottom: '6px' }}>Conditional Orders</h1>
        <p style={{ fontSize: '13px', color: '#64748b' }}>
          Single-hop &ldquo;if TRIGGER then ACTION&rdquo; orders on a paper portfolio&apos;s own symbol. See the{' '}
          <a href="/conditional-orders-guide" style={{ color: '#38bdf8', textDecoration: 'none' }}>full guide</a> for
          how each trigger metric and action works.
        </p>
      </div>

      <CreateOrderForm portfolios={portfolios ?? []} onCreated={() => mutate()} />

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <h3 style={{ fontSize: '14px', fontWeight: 700, color: '#e2e8f0' }}>Your Orders</h3>
        <select style={{ ...inputStyle, width: 'auto' }} value={statusFilter} onChange={e => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          <option value="pending">Pending</option>
          <option value="triggered">Triggered</option>
          <option value="failed">Failed</option>
          <option value="expired">Expired</option>
          <option value="cancelled">Cancelled</option>
        </select>
      </div>

      <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 12, padding: '4px 20px' }}>
        {orders.length === 0 ? (
          <div style={{ padding: '24px 0', textAlign: 'center', color: '#64748b', fontSize: '13px' }}>
            No conditional orders yet — create one above.
          </div>
        ) : (
          orders.map(o => <OrderRow key={o.id} order={o} onCancel={handleCancel} />)
        )}
      </div>
    </div>
  );
}
