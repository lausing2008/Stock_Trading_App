/**
 * Trade Performance page (/trade-performance)
 *
 * Shows real trade results by pairing each BUY signal with its next
 * SELL/WAIT exit signal for the same stock.
 *
 * How to read the summary cards
 * ─────────────────────────────
 * Win Rate      — % of closed trades that made money. > 50% beats random.
 * Profit Factor — total profit from winners ÷ total loss from losers.
 *                 > 1.5 = good system; < 1.0 = losing money overall.
 * Avg Return    — average % gain or loss per closed trade.
 * Avg Win/Loss  — typical size of a winning vs losing trade.
 *                 You want avg win > avg loss (favourable risk/reward).
 * Avg Hold Days — how long the system typically stays in a trade.
 *
 * How to read the trades table
 * ────────────────────────────
 * Each row = one trade: entered on BUY signal, exited on SELL/WAIT signal.
 * Open trades have no exit signal yet — current price used as exit.
 * Return is coloured green (profit) or red (loss).
 */
import { useState } from 'react';
import useSWR from 'swr';
import Link from 'next/link';
import { api, type TradePair } from '@/lib/api';

const LOOKBACK_OPTIONS = [
  { label: '90d',  value: 90 },
  { label: '180d', value: 180 },
  { label: '365d', value: 365 },
];

function pct(n: number | null, digits = 1) {
  if (n == null) return '—';
  return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}%`;
}

function StatCard({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '12px 16px', minWidth: 120 }}>
      <div style={{ fontSize: 22, fontWeight: 700, color: color ?? '#e2e8f0' }}>{value}</div>
      <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>{label}</div>
      {sub && <div style={{ fontSize: 10, color: '#475569', marginTop: 1 }}>{sub}</div>}
    </div>
  );
}

function Badge({ text, color, bg }: { text: string; color: string; bg: string }) {
  return (
    <span style={{ padding: '2px 8px', borderRadius: 4, fontSize: 11, fontWeight: 700, color, background: bg }}>
      {text}
    </span>
  );
}

export default function TradePerformancePage() {
  const [lookback, setLookback]         = useState(180);
  const [filterSymbol, setFilterSymbol] = useState('');
  const [statusFilter, setStatusFilter] = useState<'ALL' | 'closed' | 'open'>('ALL');
  const [outcomeFilter, setOutcomeFilter] = useState<'ALL' | 'WIN' | 'LOSS'>('ALL');
  const [sortBy, setSortBy]             = useState<'date' | 'return' | 'hold'>('date');

  const { data, isLoading, error } = useSWR(
    ['trade-performance', lookback],
    () => api.tradePerformance(lookback),
    { revalidateOnFocus: false },
  );

  const trades: TradePair[] = (data?.trades ?? []).filter(t => {
    if (filterSymbol && !t.symbol.includes(filterSymbol.toUpperCase())) return false;
    if (statusFilter !== 'ALL' && t.status !== statusFilter) return false;
    if (outcomeFilter === 'WIN' && !t.win) return false;
    if (outcomeFilter === 'LOSS' && t.win) return false;
    return true;
  }).sort((a, b) => {
    if (sortBy === 'return') return b.pct_return - a.pct_return;
    if (sortBy === 'hold')   return b.hold_days - a.hold_days;
    return b.entry_date.localeCompare(a.entry_date);
  });

  const pfColor = data?.profit_factor == null ? undefined
    : data.profit_factor >= 1.5 ? '#4ade80'
    : data.profit_factor >= 1.0 ? '#facc15'
    : '#f87171';

  const wrColor = data?.win_rate == null ? undefined
    : data.win_rate >= 55 ? '#4ade80'
    : data.win_rate >= 45 ? '#facc15'
    : '#f87171';

  return (
    <div style={{ padding: '24px 0' }}>
      {/* Header */}
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, color: '#e2e8f0', marginBottom: 4 }}>Trade Performance</h1>
        <p style={{ fontSize: 13, color: '#64748b' }}>
          Real P&amp;L from BUY → SELL/WAIT signal pairs. Each row is one complete trade.
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
        {(['ALL', 'closed', 'open'] as const).map(v => (
          <button key={v} onClick={() => setStatusFilter(v)}
            style={{ padding: '4px 10px', borderRadius: 6, fontSize: 12, cursor: 'pointer', border: '1px solid',
              borderColor: statusFilter === v ? '#475569' : '#1e293b',
              background: statusFilter === v ? 'rgba(71,85,105,0.2)' : 'transparent',
              color: statusFilter === v ? '#94a3b8' : '#475569' }}>
            {v === 'ALL' ? 'All Trades' : v === 'closed' ? 'Closed' : 'Open'}
          </button>
        ))}
        {(['ALL', 'WIN', 'LOSS'] as const).map(v => (
          <button key={v} onClick={() => setOutcomeFilter(v)}
            style={{ padding: '4px 10px', borderRadius: 6, fontSize: 12, cursor: 'pointer', border: '1px solid',
              borderColor: outcomeFilter === v ? (v === 'WIN' ? '#166534' : v === 'LOSS' ? '#991b1b' : '#334155') : '#1e293b',
              background: outcomeFilter === v ? (v === 'WIN' ? 'rgba(22,101,52,0.2)' : v === 'LOSS' ? 'rgba(153,27,27,0.2)' : 'rgba(51,65,85,0.2)') : 'transparent',
              color: outcomeFilter === v ? (v === 'WIN' ? '#4ade80' : v === 'LOSS' ? '#f87171' : '#94a3b8') : '#64748b' }}>
            {v}
          </button>
        ))}
      </div>

      {/* Summary cards */}
      {data && (
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 24 }}>
          <StatCard label="Win Rate"      value={data.win_rate != null ? `${data.win_rate.toFixed(1)}%` : '—'}
            sub={`${data.closed_trades} closed trades`} color={wrColor} />
          <StatCard label="Profit Factor" value={data.profit_factor != null ? data.profit_factor.toFixed(2) : '—'}
            sub="winners ÷ losers" color={pfColor} />
          <StatCard label="Avg Return"    value={pct(data.avg_return_pct)}
            sub="per closed trade"
            color={data.avg_return_pct != null && data.avg_return_pct > 0 ? '#4ade80' : '#f87171'} />
          <StatCard label="Avg Win"       value={pct(data.avg_win_pct)}
            sub="typical winning trade" color="#4ade80" />
          <StatCard label="Avg Loss"      value={pct(data.avg_loss_pct)}
            sub="typical losing trade" color="#f87171" />
          <StatCard label="Avg Hold"      value={data.avg_hold_days != null ? `${data.avg_hold_days.toFixed(0)}d` : '—'}
            sub="days per trade" />
          <StatCard label="Open Trades"   value={String(data.open_trades)}
            sub="awaiting exit signal" color="#818cf8" />
        </div>
      )}

      {/* Profit factor interpretation */}
      {data?.profit_factor != null && (
        <div style={{ marginBottom: 20, padding: '10px 14px', borderRadius: 6, border: '1px solid #1e293b', background: '#0f172a', fontSize: 12, color: '#94a3b8' }}>
          <strong style={{ color: pfColor }}>Profit Factor {data.profit_factor.toFixed(2)}</strong>
          {' — '}
          {data.profit_factor >= 2.0 ? 'Excellent. Winning trades make 2× more than losing trades cost.' :
           data.profit_factor >= 1.5 ? 'Good. System is profitable with a comfortable margin.' :
           data.profit_factor >= 1.0 ? 'Marginal. Profitable but signal quality could improve.' :
           'Below 1.0 — losing trades are outweighing winners. Review signal thresholds.'}
        </div>
      )}

      {/* Per-symbol breakdown */}
      {data?.by_symbol && data.by_symbol.length > 0 && (
        <div style={{ marginBottom: 28 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: '#94a3b8', marginBottom: 10 }}>By Symbol</div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid #1e293b' }}>
                  {['Symbol', 'Trades', 'Win Rate', 'Avg Return', 'Avg Hold'].map(h => (
                    <th key={h} style={{ padding: '6px 12px', textAlign: 'left', color: '#64748b', fontWeight: 500 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.by_symbol.map(s => (
                  <tr key={s.symbol} style={{ borderBottom: '1px solid #0f172a' }}>
                    <td style={{ padding: '7px 12px' }}>
                      <Link href={`/stock/${s.symbol}`} style={{ color: '#818cf8', fontWeight: 600 }}>{s.symbol}</Link>
                    </td>
                    <td style={{ padding: '7px 12px', color: '#94a3b8' }}>{s.trades}</td>
                    <td style={{ padding: '7px 12px', fontWeight: 600,
                      color: s.win_rate >= 55 ? '#4ade80' : s.win_rate >= 45 ? '#facc15' : '#f87171' }}>
                      {s.win_rate.toFixed(1)}%
                    </td>
                    <td style={{ padding: '7px 12px', fontWeight: 600,
                      color: s.avg_return >= 0 ? '#4ade80' : '#f87171' }}>
                      {pct(s.avg_return)}
                    </td>
                    <td style={{ padding: '7px 12px', color: '#64748b' }}>{s.avg_hold_days.toFixed(0)}d</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Trade list */}
      {isLoading && <div style={{ color: '#64748b', textAlign: 'center', padding: 40 }}>Loading trades…</div>}
      {error   && <div style={{ color: '#f87171', padding: 16 }}>Failed to load trade data.</div>}

      {!isLoading && trades.length > 0 && (
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <div style={{ fontSize: 12, color: '#64748b' }}>{trades.length} trade{trades.length !== 1 ? 's' : ''} shown</div>
            <div style={{ display: 'flex', gap: 6 }}>
              {([['date', 'Entry Date'], ['return', 'Return'], ['hold', 'Hold Days']] as const).map(([k, label]) => (
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
                  {['Symbol', 'Entry Date', 'Exit Date', 'Entry Price', 'Exit Price', 'Return', 'Hold', 'Exit Signal', 'Result'].map(h => (
                    <th key={h} style={{ padding: '6px 10px', textAlign: 'left', color: '#64748b', fontWeight: 500 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {trades.map((t, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid #0f172a',
                    background: t.status === 'open' ? 'rgba(99,102,241,0.03)' : 'transparent' }}>
                    <td style={{ padding: '7px 10px' }}>
                      <Link href={`/stock/${t.symbol}`} style={{ color: '#818cf8', fontWeight: 600 }}>{t.symbol}</Link>
                      <div style={{ fontSize: 10, color: '#475569' }}>{t.name}</div>
                    </td>
                    <td style={{ padding: '7px 10px', color: '#64748b' }}>
                      {t.entry_date}
                      <div style={{ fontSize: 10, color: '#475569' }}>conf {t.entry_confidence.toFixed(0)}</div>
                    </td>
                    <td style={{ padding: '7px 10px', color: '#64748b' }}>{t.exit_date}</td>
                    <td style={{ padding: '7px 10px', color: '#94a3b8' }}>${t.entry_price.toFixed(2)}</td>
                    <td style={{ padding: '7px 10px', color: '#94a3b8' }}>${t.exit_price.toFixed(2)}</td>
                    <td style={{ padding: '7px 10px', fontWeight: 700,
                      color: t.pct_return >= 0 ? '#4ade80' : '#f87171' }}>
                      {pct(t.pct_return)}
                    </td>
                    <td style={{ padding: '7px 10px', color: '#64748b' }}>{t.hold_days}d</td>
                    <td style={{ padding: '7px 10px' }}>
                      {t.status === 'open'
                        ? <Badge text="OPEN" color="#818cf8" bg="rgba(99,102,241,0.15)" />
                        : <Badge text={t.exit_signal}
                            color={t.exit_signal === 'SELL' ? '#f87171' : '#facc15'}
                            bg={t.exit_signal === 'SELL' ? 'rgba(153,27,27,0.3)' : 'rgba(202,138,4,0.2)'} />
                      }
                    </td>
                    <td style={{ padding: '7px 10px' }}>
                      {t.status === 'open'
                        ? <span style={{ fontSize: 11, color: '#64748b' }}>In progress</span>
                        : t.win
                          ? <span style={{ color: '#4ade80', fontWeight: 600 }}>✓ Win</span>
                          : <span style={{ color: '#f87171', fontWeight: 600 }}>✗ Loss</span>
                      }
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {!isLoading && !error && trades.length === 0 && data && (
        <div style={{ textAlign: 'center', padding: '40px 0', color: '#475569' }}>
          <div style={{ fontSize: 36, marginBottom: 8 }}>📈</div>
          <div>No trades found for this filter.</div>
          <div style={{ fontSize: 12, marginTop: 4 }}>Try a longer lookback or remove filters. Trades need at least one BUY signal followed by a SELL or WAIT.</div>
        </div>
      )}
    </div>
  );
}
