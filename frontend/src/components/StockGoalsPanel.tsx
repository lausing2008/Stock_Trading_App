import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { StockGoalItem, StockGoalCreateRequest } from '@/lib/api';

/** T286-STOCK-GOALS: self-contained panel for a symbol's user-defined price/share/date goals.
 * Deliberately its own standalone component (not inlined into stock/[symbol].tsx, which is
 * already 4000+ lines and fragile per this repo's own established discipline around that page)
 * — mounted as a new tab there, matching the exact pattern already used for the Research tab
 * (ResearchPage reused directly rather than re-implemented inline). */
export default function StockGoalsPanel({ symbol }: { symbol: string }) {
  const [goals, setGoals] = useState<StockGoalItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [saving, setSaving] = useState(false);

  const [title, setTitle] = useState('');
  const [targetPrice, setTargetPrice] = useState('');
  const [targetShares, setTargetShares] = useState('');
  const [targetDate, setTargetDate] = useState('');
  const [startShares, setStartShares] = useState('0');
  const [notes, setNotes] = useState('');

  const load = () => {
    api.listStockGoals(symbol)
      .then(rows => setGoals(rows))
      .catch(() => setError('Failed to load goals'));
  };

  useEffect(() => { load(); }, [symbol]);

  const resetForm = () => {
    setTitle(''); setTargetPrice(''); setTargetShares(''); setTargetDate('');
    setStartShares('0'); setNotes(''); setShowForm(false);
  };

  const handleCreate = async () => {
    if (!title.trim()) { setError('Title is required'); return; }
    if (!targetPrice && !targetShares && !targetDate) {
      setError('Set at least one of: target price, target shares, target date');
      return;
    }
    setSaving(true);
    setError(null);
    const req: StockGoalCreateRequest = {
      symbol,
      title: title.trim(),
      target_price: targetPrice ? parseFloat(targetPrice) : null,
      target_shares: targetShares ? parseFloat(targetShares) : null,
      target_date: targetDate || null,
      start_shares: startShares ? parseFloat(startShares) : 0,
      notes: notes || null,
    };
    try {
      await api.createStockGoal(req);
      resetForm();
      load();
    } catch {
      setError('Failed to create goal');
    } finally {
      setSaving(false);
    }
  };

  const handleStatusChange = async (id: number, status: string) => {
    try {
      await api.updateStockGoal(id, { status });
      load();
    } catch {
      setError('Failed to update goal');
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await api.deleteStockGoal(id);
      load();
    } catch {
      setError('Failed to delete goal');
    }
  };

  const progressBarColor = (pct: number | null) => {
    if (pct === null) return '#334155';
    if (pct >= 100) return '#4ade80';
    if (pct >= 50) return '#818cf8';
    if (pct < 0) return '#f87171';
    return '#fbbf24';
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h3 style={{ fontSize: '14px', fontWeight: 700, color: '#e2e8f0', margin: 0 }}>
          Goals for {symbol}
        </h3>
        <button
          onClick={() => setShowForm(s => !s)}
          style={{
            padding: '6px 14px', borderRadius: '6px', fontSize: '12px', fontWeight: 700,
            border: '1px solid #4338ca', background: showForm ? 'transparent' : '#4338ca',
            color: '#e2e8f0', cursor: 'pointer',
          }}
        >
          {showForm ? 'Cancel' : '+ New Goal'}
        </button>
      </div>

      {error && (
        <div style={{ fontSize: '12px', color: '#f87171', padding: '8px 10px', background: 'rgba(239,68,68,0.08)', borderRadius: '6px' }}>
          {error}
        </div>
      )}

      {showForm && (
        <div style={{ border: '1px solid #1e293b', borderRadius: '10px', background: '#0f172a', padding: '14px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
          <input
            placeholder="Goal title (e.g. Build 100-share position for dividend income)"
            value={title}
            onChange={e => setTitle(e.target.value)}
            style={{ padding: '8px 10px', borderRadius: '6px', border: '1px solid #1e293b', background: '#020617', color: '#e2e8f0', fontSize: '13px' }}
          />
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            <input
              placeholder="Target price"
              type="number"
              value={targetPrice}
              onChange={e => setTargetPrice(e.target.value)}
              style={{ flex: '1 1 120px', padding: '8px 10px', borderRadius: '6px', border: '1px solid #1e293b', background: '#020617', color: '#e2e8f0', fontSize: '13px' }}
            />
            <input
              placeholder="Target shares"
              type="number"
              value={targetShares}
              onChange={e => setTargetShares(e.target.value)}
              style={{ flex: '1 1 120px', padding: '8px 10px', borderRadius: '6px', border: '1px solid #1e293b', background: '#020617', color: '#e2e8f0', fontSize: '13px' }}
            />
            <input
              placeholder="Target date"
              type="date"
              value={targetDate}
              onChange={e => setTargetDate(e.target.value)}
              style={{ flex: '1 1 140px', padding: '8px 10px', borderRadius: '6px', border: '1px solid #1e293b', background: '#020617', color: '#e2e8f0', fontSize: '13px' }}
            />
          </div>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            <input
              placeholder="Shares already held (optional)"
              type="number"
              value={startShares}
              onChange={e => setStartShares(e.target.value)}
              style={{ flex: '1 1 160px', padding: '8px 10px', borderRadius: '6px', border: '1px solid #1e293b', background: '#020617', color: '#e2e8f0', fontSize: '13px' }}
            />
            <input
              placeholder="Notes (optional)"
              value={notes}
              onChange={e => setNotes(e.target.value)}
              style={{ flex: '2 1 200px', padding: '8px 10px', borderRadius: '6px', border: '1px solid #1e293b', background: '#020617', color: '#e2e8f0', fontSize: '13px' }}
            />
          </div>
          <button
            onClick={handleCreate}
            disabled={saving}
            style={{
              alignSelf: 'flex-start', padding: '7px 16px', borderRadius: '6px', fontSize: '12px',
              fontWeight: 700, border: 'none', background: saving ? '#334155' : '#4338ca',
              color: '#e2e8f0', cursor: saving ? 'default' : 'pointer',
            }}
          >
            {saving ? 'Saving...' : 'Create Goal'}
          </button>
        </div>
      )}

      {goals === null && (
        <div style={{ fontSize: '12px', color: '#64748b' }}>Loading goals...</div>
      )}

      {goals !== null && goals.length === 0 && !showForm && (
        <div style={{ fontSize: '12px', color: '#64748b', padding: '16px', textAlign: 'center' }}>
          No goals set for {symbol} yet. Click "+ New Goal" to add one.
        </div>
      )}

      {goals !== null && goals.map(g => (
        <div
          key={g.id}
          style={{
            border: '1px solid #1e293b', borderRadius: '10px', background: '#0f172a', padding: '14px',
            opacity: g.status === 'active' ? 1 : 0.55,
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '8px' }}>
            <div>
              <div style={{ fontSize: '13px', fontWeight: 700, color: '#e2e8f0' }}>{g.title}</div>
              {g.notes && <div style={{ fontSize: '11px', color: '#64748b', marginTop: '2px' }}>{g.notes}</div>}
            </div>
            <span style={{
              fontSize: '10px', fontWeight: 700, padding: '2px 8px', borderRadius: '4px',
              textTransform: 'uppercase', letterSpacing: '0.03em',
              color: g.status === 'achieved' ? '#4ade80' : g.status === 'cancelled' ? '#64748b' : '#818cf8',
              background: g.status === 'achieved' ? 'rgba(34,197,94,0.1)' : g.status === 'cancelled' ? 'rgba(100,116,139,0.1)' : 'rgba(99,102,241,0.1)',
              whiteSpace: 'nowrap',
            }}>
              {g.status}
            </span>
          </div>

          <div style={{ display: 'flex', gap: '16px', marginTop: '10px', flexWrap: 'wrap', fontSize: '12px', color: '#94a3b8' }}>
            {g.target_price !== null && (
              <div>
                Target: <strong style={{ color: '#e2e8f0' }}>${g.target_price.toFixed(2)}</strong>
                {g.current_price !== null && (
                  <span style={{ color: '#64748b' }}> (current ${g.current_price.toFixed(2)})</span>
                )}
              </div>
            )}
            {g.target_shares !== null && (
              <div>
                Shares: <strong style={{ color: '#e2e8f0' }}>{g.start_shares} → {g.target_shares}</strong>
              </div>
            )}
            {g.days_remaining !== null && (
              <div>
                {g.days_remaining >= 0
                  ? <>Days left: <strong style={{ color: '#e2e8f0' }}>{g.days_remaining}</strong></>
                  : <span style={{ color: '#f87171' }}>Past due by {Math.abs(g.days_remaining)}d</span>}
              </div>
            )}
          </div>

          {g.price_progress_pct !== null && (
            <div style={{ marginTop: '10px' }}>
              <div style={{ height: '6px', borderRadius: '3px', background: '#1e293b', overflow: 'hidden' }}>
                <div style={{
                  height: '100%', width: `${Math.max(0, Math.min(100, g.price_progress_pct))}%`,
                  background: progressBarColor(g.price_progress_pct), transition: 'width 0.3s',
                }} />
              </div>
              <div style={{ fontSize: '11px', color: '#64748b', marginTop: '4px' }}>
                {g.price_progress_pct.toFixed(0)}% of price target
              </div>
            </div>
          )}

          {g.status === 'active' && (
            <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
              <button
                onClick={() => handleStatusChange(g.id, 'achieved')}
                style={{ fontSize: '11px', padding: '4px 10px', borderRadius: '4px', border: '1px solid #16a34a', background: 'transparent', color: '#4ade80', cursor: 'pointer' }}
              >
                Mark Achieved
              </button>
              <button
                onClick={() => handleStatusChange(g.id, 'cancelled')}
                style={{ fontSize: '11px', padding: '4px 10px', borderRadius: '4px', border: '1px solid #334155', background: 'transparent', color: '#94a3b8', cursor: 'pointer' }}
              >
                Cancel
              </button>
              <button
                onClick={() => handleDelete(g.id)}
                style={{ fontSize: '11px', padding: '4px 10px', borderRadius: '4px', border: '1px solid #7f1d1d', background: 'transparent', color: '#f87171', cursor: 'pointer', marginLeft: 'auto' }}
              >
                Delete
              </button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
