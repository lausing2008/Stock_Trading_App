import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { SrWatchItem } from '@/lib/api';

/** SR-WATCH-PROXIMITY-ALERT: a compact toggle button for the stock detail page's own
 * Support & Resistance card. Watching a symbol gets a one-shot email once price gets close
 * (within an ATR-scaled band) to its nearest support or resistance level — "come look and
 * decide whether to buy/sell yourself," never an automated trade signal. Re-arms once price
 * moves out of the band and approaches again, unlike SqueezeWatch's own permanent one-shot
 * revert — see check_sr_watch_reverts() (scheduler.py) for the full proximity/dedup logic.
 *
 * Self-contained (its own SWR-free fetch), matching StockGoalsPanel's own established
 * pattern for keeping stock/[symbol].tsx from growing further — it already sits at 4000+
 * lines, and this repo's own CLAUDE.md flags large edits to that page as fragile. */
export default function SrWatchButton({ symbol }: { symbol: string }) {
  const [watch, setWatch] = useState<SrWatchItem | null | undefined>(undefined); // undefined = loading
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [atrMultiplier, setAtrMultiplier] = useState('1.0');

  const load = () => {
    api.listSrWatches()
      .then(rows => setWatch(rows.find(w => w.symbol === symbol) ?? null))
      .catch(() => setError('Failed to load watch status'));
  };

  useEffect(() => { load(); }, [symbol]);

  const handleAdd = async () => {
    const mult = parseFloat(atrMultiplier);
    if (!mult || mult <= 0) {
      setError('ATR multiplier must be a positive number');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await api.addSrWatch({ symbol, atr_multiplier: mult });
      setWatch(created);
      setShowSettings(false);
    } catch {
      setError('Failed to add watch');
    } finally {
      setBusy(false);
    }
  };

  const handleRemove = async () => {
    if (!watch) return;
    setBusy(true);
    setError(null);
    try {
      await api.removeSrWatch(watch.id);
      setWatch(null);
    } catch {
      setError('Failed to remove watch');
    } finally {
      setBusy(false);
    }
  };

  if (watch === undefined) return null; // still loading — avoid a flash of the wrong state

  return (
    <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px solid #1e293b' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        {watch ? (
          <button
            onClick={handleRemove}
            disabled={busy}
            title="Stop tracking — no more proximity alerts for this symbol"
            style={{
              padding: '3px 9px', borderRadius: 5, fontSize: 10.5, fontWeight: 700,
              cursor: busy ? 'wait' : 'pointer',
              border: '1px solid rgba(56,189,248,0.4)', background: 'rgba(56,189,248,0.12)',
              color: '#38bdf8',
            }}
          >
            🔔 Watching ({watch.atr_multiplier}x ATR)
          </button>
        ) : (
          <button
            onClick={() => setShowSettings(s => !s)}
            disabled={busy}
            title="Get an email the moment price gets close to a computed support/resistance level"
            style={{
              padding: '3px 9px', borderRadius: 5, fontSize: 10.5, fontWeight: 700,
              cursor: busy ? 'wait' : 'pointer',
              border: '1px solid #1e293b', background: 'transparent', color: '#94a3b8',
            }}
          >
            🔕 Watch this level
          </button>
        )}
        {watch?.currently_near && (
          <span style={{ fontSize: 10, color: '#f59e0b' }}>
            ● currently near {watch.last_alert_level_kind ?? 'a level'}
          </span>
        )}
      </div>

      {!watch && showSettings && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 6 }}>
          <label style={{ fontSize: 10, color: '#64748b' }}>Alert within</label>
          <input
            type="number" step="0.5" min="0.1" value={atrMultiplier}
            onChange={e => setAtrMultiplier(e.target.value)}
            style={{ width: 48, padding: '2px 4px', fontSize: 10.5, background: '#0d1424', border: '1px solid #1e293b', borderRadius: 4, color: '#e2e8f0' }}
          />
          <label style={{ fontSize: 10, color: '#64748b' }}>x ATR(14)</label>
          <button
            onClick={handleAdd}
            disabled={busy}
            style={{ padding: '2px 8px', borderRadius: 4, fontSize: 10.5, fontWeight: 700, cursor: busy ? 'wait' : 'pointer', border: '1px solid rgba(56,189,248,0.4)', background: 'rgba(56,189,248,0.12)', color: '#38bdf8' }}
          >
            Save
          </button>
        </div>
      )}

      {error && <div style={{ fontSize: 10, color: '#f87171', marginTop: 4 }}>{error}</div>}
    </div>
  );
}
