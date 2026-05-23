/**
 * Signal Accuracy Tracker page (/signal-accuracy) — measures how often the
 * AI signal engine's BUY and SELL calls predicted the correct direction.
 *
 * Data source: GET /signals/accuracy?lookback_days=N (signal-engine service).
 * For each persisted BUY or SELL signal, the backend joins the close price on
 * the signal date to the close price ~5 trading days later and checks:
 *   BUY correct  → exit price > entry price
 *   SELL correct → exit price < entry price
 * Only signals at least 7 days old are included (needs time to settle).
 *
 * How to read the stats
 * ─────────────────────
 * Overall Accuracy   — % of all evaluated signals that pointed the right way.
 *                      > 50% beats a coin flip; > 60% indicates real signal value.
 * BUY / SELL Accuracy — accuracy split by signal type. Often one direction is
 *                       more reliable than the other.
 * Avg BUY Return     — average price change 5 days after a BUY signal.
 *                      Positive = signals are calling entries at the right time.
 * Avg SELL Return    — shown as the decline after a SELL signal.
 * Profit Factor      — total gain from correct signals ÷ total loss from wrong
 *                      ones. Above 1.5 = good; below 1.0 = signals losing money.
 *
 * Accuracy bar
 * ────────────
 * The horizontal bar has a centre line at 50% (random baseline). Green means
 * above random, yellow means near-random, red means below random.
 *
 * Practical workflow
 * ──────────────────
 * 1. Start with the 90d window (default) for a statistically meaningful sample.
 * 2. Compare 30d vs 90d accuracy — if 30d is higher, the model is improving.
 * 3. Filter by BUY/SELL separately to decide how much weight to give each type.
 * 4. Click "Wrong" to study only the misses — look for sector or market-regime
 *    patterns that cause the signal engine to fail.
 * 5. Type a symbol to see accuracy for a single stock.
 *
 * Filters / sort
 * ──────────────
 * Lookback   — 30d / 60d / 90d / 180d
 * Symbol     — free-text filter on ticker
 * Signal     — ALL / BUY / SELL
 * Outcome    — ALL / CORRECT / WRONG
 * Sort by    — Date (newest first) / Confidence / Return %
 */
import { useState } from 'react';
import useSWR from 'swr';
import Link from 'next/link';
import { api, type SignalAccuracyRow } from '@/lib/api';

const LOOKBACK_OPTIONS = [
  { label: '30d', value: 30 },
  { label: '60d', value: 60 },
  { label: '90d', value: 90 },
  { label: '180d', value: 180 },
];

function pct(n: number | null, digits = 1) {
  return n == null ? '—' : `${n >= 0 ? '+' : ''}${n.toFixed(digits)}%`;
}

function acc(n: number | null) {
  return n == null ? '—' : `${n.toFixed(1)}%`;
}

export default function SignalAccuracyPage() {
  const [lookback, setLookback] = useState(90);
  const [filterSymbol, setFilterSymbol] = useState('');
  const [signalFilter, setSignalFilter] = useState<'ALL' | 'BUY' | 'SELL'>('ALL');
  const [showOnly, setShowOnly] = useState<'ALL' | 'CORRECT' | 'WRONG'>('ALL');
  const [sortBy, setSortBy] = useState<'date' | 'confidence' | 'pct_change'>('date');

  const { data, isLoading, error } = useSWR(
    ['signal-accuracy', lookback],
    () => api.signalAccuracy(lookback),
    { revalidateOnFocus: false },
  );

  const rows: SignalAccuracyRow[] = (data?.signals ?? []).filter(r => {
    if (filterSymbol && !r.symbol.includes(filterSymbol.toUpperCase())) return false;
    if (signalFilter !== 'ALL' && r.signal !== signalFilter) return false;
    if (showOnly === 'CORRECT' && !r.correct) return false;
    if (showOnly === 'WRONG' && r.correct) return false;
    return true;
  }).sort((a, b) => {
    if (sortBy === 'date') return b.signal_date.localeCompare(a.signal_date);
    if (sortBy === 'confidence') return b.confidence - a.confidence;
    return b.pct_change - a.pct_change;
  });

  const statCard = (label: string, value: string, sub?: string, color?: string) => (
    <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '12px 16px', minWidth: 110 }}>
      <div style={{ fontSize: 22, fontWeight: 700, color: color ?? '#e2e8f0' }}>{value}</div>
      <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>{label}</div>
      {sub && <div style={{ fontSize: 10, color: '#475569', marginTop: 1 }}>{sub}</div>}
    </div>
  );

  const overallColor = data?.overall_accuracy != null
    ? data.overall_accuracy >= 60 ? '#4ade80' : data.overall_accuracy >= 50 ? '#facc15' : '#f87171'
    : undefined;

  return (
    <div style={{ padding: '24px 0' }}>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, color: '#e2e8f0', marginBottom: 4 }}>Signal Accuracy Tracker</h1>
        <p style={{ fontSize: 13, color: '#64748b' }}>
          How often did past BUY/SELL signals predict the correct direction within ~5 trading days?
        </p>
      </div>

      {/* Controls */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 20, alignItems: 'center' }}>
        <div style={{ display: 'flex', gap: 4 }}>
          {LOOKBACK_OPTIONS.map(o => (
            <button key={o.value} onClick={() => setLookback(o.value)}
              style={{ padding: '4px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer', border: '1px solid',
                borderColor: lookback === o.value ? '#6366f1' : '#1e293b',
                background: lookback === o.value ? 'rgba(99,102,241,0.15)' : 'transparent',
                color: lookback === o.value ? '#818cf8' : '#64748b' }}>
              {o.label}
            </button>
          ))}
        </div>
        <input
          value={filterSymbol} onChange={e => setFilterSymbol(e.target.value)}
          placeholder="Filter symbol…"
          style={{ padding: '4px 10px', borderRadius: 6, border: '1px solid #1e293b', background: '#0f172a', color: '#e2e8f0', fontSize: 12, width: 120 }}
        />
        {(['ALL', 'BUY', 'SELL'] as const).map(v => (
          <button key={v} onClick={() => setSignalFilter(v)}
            style={{ padding: '4px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer', border: '1px solid',
              borderColor: signalFilter === v ? (v === 'BUY' ? '#166534' : v === 'SELL' ? '#991b1b' : '#334155') : '#1e293b',
              background: signalFilter === v ? (v === 'BUY' ? 'rgba(22,101,52,0.2)' : v === 'SELL' ? 'rgba(153,27,27,0.2)' : 'rgba(51,65,85,0.2)') : 'transparent',
              color: signalFilter === v ? (v === 'BUY' ? '#4ade80' : v === 'SELL' ? '#f87171' : '#94a3b8') : '#64748b' }}>
            {v}
          </button>
        ))}
        {(['ALL', 'CORRECT', 'WRONG'] as const).map(v => (
          <button key={v} onClick={() => setShowOnly(v)}
            style={{ padding: '4px 10px', borderRadius: 6, fontSize: 11, cursor: 'pointer', border: '1px solid',
              borderColor: showOnly === v ? '#475569' : '#1e293b',
              background: showOnly === v ? 'rgba(71,85,105,0.2)' : 'transparent',
              color: showOnly === v ? '#94a3b8' : '#475569' }}>
            {v}
          </button>
        ))}
      </div>

      {/* Summary cards */}
      {data && (
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 24 }}>
          {statCard('Overall Accuracy', acc(data.overall_accuracy), `${data.total_signals} signals evaluated`, overallColor)}
          {statCard('BUY Accuracy', acc(data.buy_accuracy), `${data.buy_count} BUY signals`, data.buy_accuracy != null && data.buy_accuracy >= 55 ? '#4ade80' : '#f87171')}
          {statCard('SELL Accuracy', acc(data.sell_accuracy), `${data.sell_count} SELL signals`, data.sell_accuracy != null && data.sell_accuracy >= 55 ? '#4ade80' : '#f87171')}
          {statCard('Avg BUY Return', pct(data.avg_buy_return_pct), '5-day avg after BUY', data.avg_buy_return_pct != null && data.avg_buy_return_pct > 0 ? '#4ade80' : '#f87171')}
          {statCard('Avg SELL Return', pct(data.avg_sell_return_pct != null ? -data.avg_sell_return_pct : null), '5-day decline after SELL', data.avg_sell_return_pct != null && data.avg_sell_return_pct < 0 ? '#4ade80' : '#f87171')}
          {statCard('Profit Factor', data.profit_factor != null ? data.profit_factor.toFixed(2) : '—', 'wins / losses magnitude', data.profit_factor != null && data.profit_factor >= 1.5 ? '#4ade80' : '#facc15')}
        </div>
      )}

      {/* Accuracy bar */}
      {data?.overall_accuracy != null && (
        <div style={{ marginBottom: 20 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: '#64748b', marginBottom: 4 }}>
            <span>0%</span><span>50% (random)</span><span>100%</span>
          </div>
          <div style={{ height: 8, borderRadius: 4, background: '#1e293b', overflow: 'hidden', position: 'relative' }}>
            <div style={{ position: 'absolute', left: '50%', top: 0, bottom: 0, width: 1, background: '#334155' }} />
            <div style={{ height: '100%', width: `${data.overall_accuracy}%`, borderRadius: 4,
              background: data.overall_accuracy >= 60 ? '#22c55e' : data.overall_accuracy >= 50 ? '#eab308' : '#ef4444' }} />
          </div>
          <div style={{ fontSize: 11, color: '#475569', marginTop: 4 }}>
            {data.overall_accuracy >= 60 ? 'Above-random accuracy — signals showing predictive value' :
             data.overall_accuracy >= 50 ? 'Near-random — signals slightly better than a coin flip' :
             'Below-random — signals may need recalibration'}
          </div>
        </div>
      )}

      {isLoading && <div style={{ color: '#64748b', textAlign: 'center', padding: 40 }}>Loading signal history…</div>}
      {error && <div style={{ color: '#f87171', padding: 16 }}>Failed to load accuracy data.</div>}

      {/* Signal table */}
      {!isLoading && rows.length > 0 && (
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <div style={{ fontSize: 12, color: '#64748b' }}>{rows.length} signal{rows.length !== 1 ? 's' : ''} shown</div>
            <div style={{ display: 'flex', gap: 6 }}>
              {([['date', 'Date'], ['confidence', 'Confidence'], ['pct_change', 'Return']] as const).map(([k, label]) => (
                <button key={k} onClick={() => setSortBy(k)}
                  style={{ padding: '3px 8px', borderRadius: 4, fontSize: 11, cursor: 'pointer', border: '1px solid',
                    borderColor: sortBy === k ? '#6366f1' : '#1e293b',
                    background: sortBy === k ? 'rgba(99,102,241,0.1)' : 'transparent',
                    color: sortBy === k ? '#818cf8' : '#475569' }}>
                  {label}
                </button>
              ))}
            </div>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid #1e293b' }}>
                  {['Date', 'Symbol', 'Signal', 'Confidence', 'Entry', 'Exit (5d)', 'Return', 'Outcome'].map(h => (
                    <th key={h} style={{ padding: '6px 10px', textAlign: 'left', color: '#64748b', fontWeight: 500 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid #0f172a' }}>
                    <td style={{ padding: '7px 10px', color: '#64748b' }}>{r.signal_date}</td>
                    <td style={{ padding: '7px 10px' }}>
                      <Link href={`/stock/${r.symbol}`} style={{ color: '#818cf8', fontWeight: 600 }}>{r.symbol}</Link>
                      <div style={{ fontSize: 10, color: '#475569' }}>{r.name}</div>
                    </td>
                    <td style={{ padding: '7px 10px' }}>
                      <span style={{ padding: '2px 7px', borderRadius: 4, fontSize: 11, fontWeight: 700,
                        background: r.signal === 'BUY' ? 'rgba(22,101,52,0.3)' : 'rgba(153,27,27,0.3)',
                        color: r.signal === 'BUY' ? '#4ade80' : '#f87171' }}>
                        {r.signal}
                      </span>
                    </td>
                    <td style={{ padding: '7px 10px', color: '#94a3b8' }}>
                      {r.confidence.toFixed(0)}
                      <div style={{ fontSize: 10, color: '#475569' }}>
                        {r.bullish_probability != null ? `${(r.bullish_probability * 100).toFixed(0)}% bull` : ''}
                      </div>
                    </td>
                    <td style={{ padding: '7px 10px', color: '#94a3b8' }}>${r.entry_price.toFixed(2)}</td>
                    <td style={{ padding: '7px 10px', color: '#94a3b8' }}>
                      ${r.exit_price.toFixed(2)}
                      <div style={{ fontSize: 10, color: '#475569' }}>{r.days_held}d later</div>
                    </td>
                    <td style={{ padding: '7px 10px', fontWeight: 600, color: r.pct_change >= 0 ? '#4ade80' : '#f87171' }}>
                      {pct(r.pct_change)}
                    </td>
                    <td style={{ padding: '7px 10px' }}>
                      <span style={{ fontSize: 13 }}>{r.correct ? '✓' : '✗'}</span>
                      <span style={{ fontSize: 11, marginLeft: 4, color: r.correct ? '#4ade80' : '#f87171' }}>
                        {r.correct ? 'Correct' : 'Wrong'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {!isLoading && !error && rows.length === 0 && data && (
        <div style={{ textAlign: 'center', padding: '40px 0', color: '#475569' }}>
          <div style={{ fontSize: 36, marginBottom: 8 }}>📊</div>
          <div>No completed signals in this window.</div>
          <div style={{ fontSize: 12, marginTop: 4 }}>Signals need ~7 days to settle before they're evaluated. Try a longer lookback or refresh signals.</div>
        </div>
      )}
    </div>
  );
}
