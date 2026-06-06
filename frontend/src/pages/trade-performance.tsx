/**
 * Trade Performance page (/trade-performance)
 *
 * WHAT IT SHOWS
 * ─────────────
 * Every time the system issued a BUY signal, this page tracks what happened
 * next. A "trade" starts on the BUY signal date and ends when the system
 * issues a SELL signal for the same stock and same trading horizon (SHORT/SWING/LONG).
 * WAIT signals are NOT exits — they mean "hold off new entries," not "close position."
 * If there's no SELL yet, the trade is still "Open" and uses today's price as the exit.
 *
 * HOW TO READ THE SUMMARY CARDS
 * ──────────────────────────────
 * Win Rate      — % of CLOSED trades that made money.
 *                 > 50% means more winners than losers.
 *                 50% alone doesn't mean profitable — see Profit Factor.
 *
 * Profit Factor — total profit from all winning trades
 *                 ÷ total loss from all losing trades.
 *                 > 1.0 = system makes money overall
 *                 > 1.5 = good; > 2.0 = excellent
 *                 This is the single most important number.
 *
 * Avg Return    — average % gain or loss per closed trade.
 *                 Positive = system is profitable on average.
 *
 * Avg Win / Avg Loss — typical size of a winning vs losing trade.
 *                 You want Avg Win > Avg Loss (good risk/reward ratio).
 *                 A system with 40% win rate can still be profitable if
 *                 winners average +5% and losers average -1%.
 *
 * Avg Hold Days — how long the system typically holds a trade.
 *                 Short hold = the signals flip quickly.
 *
 * HOW TO READ THE TRADES TABLE
 * ─────────────────────────────
 * Each row = one complete trade:
 *   Entry date  — when the BUY signal was issued
 *   Exit date   — when the next SELL or WAIT signal was issued
 *   Return      — (exit price - entry price) / entry price × 100
 *   Hold days   — calendar days from entry to exit
 *   Exit signal — SELL or OPEN (still holding). WAIT no longer closes trades.
 *
 * DIFFERENCE FROM SIGNAL ACCURACY
 * ─────────────────────────────────
 * Signal Accuracy measures whether a signal pointed the right direction
 * vs today's price — it doesn't care about when you exited.
 * Trade Performance measures actual trade P&L using realistic BUY→SELL pairs.
 * Trade Performance is the harder, more honest number.
 */
import { useState } from 'react';
import useSWR from 'swr';
import Link from 'next/link';
import { api, type TradePair, type EquityPoint } from '@/lib/api';

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

function EquityCurve({ points, spyReturn }: { points: EquityPoint[]; spyReturn: number | null }) {
  if (points.length < 2) return null;
  const W = 760, H = 160, PAD = { t: 12, r: 12, b: 28, l: 52 };
  const iW = W - PAD.l - PAD.r;
  const iH = H - PAD.t - PAD.b;

  const equities = points.map(p => p.equity);
  const minE = Math.min(...equities, 1.0) * 0.995;
  const maxE = Math.max(...equities, 1.0) * 1.005;

  // SPY overlay line
  const spyPoints: {x:number;y:number}[] = [];
  if (spyReturn != null && points.length >= 2) {
    const x0 = 0;
    const x1 = iW;
    const spyEnd = 1 + spyReturn / 100;
    const yS0 = iH - ((1.0 - minE) / (maxE - minE)) * iH;
    const yS1 = iH - ((spyEnd - minE) / (maxE - minE)) * iH;
    spyPoints.push({ x: x0, y: yS0 }, { x: x1, y: yS1 });
  }

  const toX = (i: number) => (i / (points.length - 1)) * iW;
  const toY = (e: number) => iH - ((e - minE) / (maxE - minE)) * iH;

  const linePath = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${toX(i).toFixed(1)},${toY(p.equity).toFixed(1)}`).join(' ');
  const areaPath = linePath + ` L${toX(points.length-1).toFixed(1)},${iH} L0,${iH} Z`;

  // Baseline at equity = 1.0
  const baseY = toY(1.0);

  // Y-axis labels
  const yTicks = [minE, (minE + maxE) / 2, maxE].map(v => ({ y: toY(v), label: ((v - 1) * 100).toFixed(1) + '%' }));

  // X-axis: first and last date
  const firstDate = points[0].date.slice(5);  // MM-DD
  const lastDate  = points[points.length - 1].date.slice(5);
  const midDate   = points[Math.floor(points.length / 2)].date.slice(5);

  const finalEquity = equities[equities.length - 1];
  const lineColor = finalEquity >= 1.0 ? '#4ade80' : '#f87171';
  const areaColor = finalEquity >= 1.0 ? 'rgba(74,222,128,0.08)' : 'rgba(248,113,113,0.08)';

  return (
    <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '12px 16px', marginBottom: 24 }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: '#64748b', marginBottom: 10 }}>Equity Curve</div>
      <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ overflow: 'visible' }}>
        <g transform={`translate(${PAD.l},${PAD.t})`}>
          {/* Y-axis labels */}
          {yTicks.map((t, i) => (
            <text key={i} x={-6} y={t.y + 4} textAnchor="end" fill="#475569" fontSize={9}>{t.label}</text>
          ))}
          {/* Baseline at 0% */}
          <line x1={0} y1={baseY} x2={iW} y2={baseY} stroke="#1e293b" strokeWidth={1} strokeDasharray="4 3" />

          {/* SPY overlay */}
          {spyPoints.length === 2 && (
            <line x1={spyPoints[0].x} y1={spyPoints[0].y} x2={spyPoints[1].x} y2={spyPoints[1].y}
              stroke="#f59e0b" strokeWidth={1.5} strokeDasharray="5 3" opacity={0.7} />
          )}
          {spyPoints.length === 2 && (
            <text x={iW + 4} y={spyPoints[1].y + 4} fill="#f59e0b" fontSize={8} opacity={0.8}>SPY</text>
          )}

          {/* Area fill */}
          <path d={areaPath} fill={areaColor} />
          {/* Line */}
          <path d={linePath} fill="none" stroke={lineColor} strokeWidth={1.8} />

          {/* X-axis labels */}
          <text x={0} y={iH + 16} textAnchor="middle" fill="#475569" fontSize={9}>{firstDate}</text>
          <text x={iW / 2} y={iH + 16} textAnchor="middle" fill="#475569" fontSize={9}>{midDate}</text>
          <text x={iW} y={iH + 16} textAnchor="middle" fill="#475569" fontSize={9}>{lastDate}</text>
        </g>
      </svg>
    </div>
  );
}

const HORIZON_OPTIONS = [
  { label: 'SHORT  (1–5d)',   value: 'SHORT' },
  { label: 'SWING  (5–20d)',  value: 'SWING' },
  { label: 'LONG  (30–90d)', value: 'LONG'  },
];

const MAX_HOLD_DEFAULTS: Record<string, number> = { SHORT: 7, SWING: 25, LONG: 90 };

export default function TradePerformancePage() {
  const [lookback, setLookback]           = useState(180);
  const [horizon, setHorizon]             = useState<'SHORT' | 'SWING' | 'LONG'>('SWING');
  const [waitExits, setWaitExits]         = useState(false);
  const [useMaxHold, setUseMaxHold]       = useState(true);
  const [maxHoldDays, setMaxHoldDays]     = useState<number>(25);
  const [minConfidence, setMinConfidence] = useState(0);
  const [filterSymbol, setFilterSymbol]   = useState('');
  const [statusFilter, setStatusFilter]   = useState<'ALL' | 'closed' | 'open'>('ALL');
  const [outcomeFilter, setOutcomeFilter] = useState<'ALL' | 'WIN' | 'LOSS'>('ALL');
  const [sortBy, setSortBy]               = useState<'date' | 'return' | 'hold'>('date');

  const { data, isLoading, error } = useSWR(
    ['trade-performance', lookback, horizon, waitExits, useMaxHold, maxHoldDays, minConfidence],
    () => api.tradePerformance(lookback, undefined, horizon, {
      waitExits,
      maxHoldDays: useMaxHold ? maxHoldDays : undefined,
      minConfidence: minConfidence > 0 ? minConfidence : undefined,
    }),
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
          Real P&amp;L from BUY → exit signal pairs. Toggle exit rules below to simulate better trade management.
        </p>
      </div>

      {/* Controls */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 20, alignItems: 'center' }}>
        {/* Horizon selector */}
        <div style={{ display: 'flex', gap: 4 }}>
          {HORIZON_OPTIONS.map(o => (
            <button key={o.value} onClick={() => { setHorizon(o.value as 'SHORT' | 'SWING' | 'LONG'); setMaxHoldDays(MAX_HOLD_DEFAULTS[o.value]); }}
              style={{ padding: '4px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer', border: '1px solid',
                borderColor: horizon === o.value ? '#4ade80' : '#1e293b',
                background: horizon === o.value ? 'rgba(74,222,128,0.12)' : 'transparent',
                color: horizon === o.value ? '#4ade80' : '#64748b' }}>
              {o.label}
            </button>
          ))}
        </div>
        <div style={{ width: 1, background: '#1e293b', alignSelf: 'stretch' }} />
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
        <div style={{ width: 1, background: '#1e293b', alignSelf: 'stretch' }} />

        {/* Exit rule toggles */}
        <button
          onClick={() => setWaitExits(v => !v)}
          title="Exit on WAIT signal (same horizon) — cuts losses when momentum fades instead of waiting for a full SELL"
          style={{ padding: '4px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer', border: '1px solid',
            borderColor: waitExits ? '#f59e0b' : '#1e293b',
            background: waitExits ? 'rgba(245,158,11,0.12)' : 'transparent',
            color: waitExits ? '#f59e0b' : '#64748b' }}>
          WAIT exits {waitExits ? 'ON' : 'OFF'}
        </button>

        <button
          onClick={() => setUseMaxHold(v => !v)}
          title={`Time-stop: force-close after ${maxHoldDays}d regardless of signal. Default: SHORT=7d, SWING=25d, LONG=90d`}
          style={{ padding: '4px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer', border: '1px solid',
            borderColor: useMaxHold ? '#818cf8' : '#1e293b',
            background: useMaxHold ? 'rgba(129,140,248,0.12)' : 'transparent',
            color: useMaxHold ? '#818cf8' : '#64748b' }}>
          Max hold {useMaxHold ? `${maxHoldDays}d` : 'OFF'}
        </button>
        {useMaxHold && (
          <input
            type="number" value={maxHoldDays} min={1} max={365}
            onChange={e => setMaxHoldDays(Number(e.target.value))}
            title="Max hold days"
            style={{ width: 54, padding: '4px 6px', borderRadius: 6, border: '1px solid #334155', background: '#0f172a', color: '#818cf8', fontSize: 12 }}
          />
        )}

        <select
          value={minConfidence}
          onChange={e => setMinConfidence(Number(e.target.value))}
          title="Only include BUY signals with confidence ≥ this value. Higher = fewer but higher-quality trades."
          style={{ padding: '4px 8px', borderRadius: 6, border: `1px solid ${minConfidence > 0 ? '#38bdf8' : '#1e293b'}`, background: '#0f172a', color: minConfidence > 0 ? '#38bdf8' : '#64748b', fontSize: 12 }}>
          <option value={0}>Min conf: any</option>
          <option value={40}>Min conf: 40%</option>
          <option value={50}>Min conf: 50%</option>
          <option value={60}>Min conf: 60%</option>
          <option value={70}>Min conf: 70%</option>
        </select>

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
        <>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
            <StatCard label="Win Rate"      value={data.win_rate != null ? `${data.win_rate.toFixed(1)}%` : '—'}
              sub={`${data.closed_trades} closed trades`} color={wrColor} />
            <StatCard label="Profit Factor" value={data.profit_factor != null ? data.profit_factor.toFixed(2) : '—'}
              sub="winners ÷ losers" color={pfColor} />
            <StatCard label="Total Return"  value={pct(data.total_return)}
              sub="compounded equity"
              color={data.total_return != null && data.total_return >= 0 ? '#4ade80' : '#f87171'} />
            <StatCard label="vs SPY"
              value={data.total_return != null && data.spy_return != null
                ? pct(data.total_return - data.spy_return)
                : '—'}
              sub={data.spy_return != null ? `SPY: ${pct(data.spy_return)}` : 'no SPY data'}
              color={data.total_return != null && data.spy_return != null
                ? data.total_return >= data.spy_return ? '#4ade80' : '#f87171'
                : undefined} />
            <StatCard label="Sharpe"
              value={data.sharpe != null ? data.sharpe.toFixed(2) : '—'}
              sub="annualised"
              color={data.sharpe != null ? data.sharpe >= 1.0 ? '#4ade80' : data.sharpe >= 0 ? '#facc15' : '#f87171' : undefined} />
            <StatCard label="Max Drawdown"
              value={data.max_drawdown != null ? `${data.max_drawdown.toFixed(1)}%` : '—'}
              sub="peak-to-trough"
              color={data.max_drawdown != null ? data.max_drawdown > -10 ? '#4ade80' : data.max_drawdown > -20 ? '#facc15' : '#f87171' : undefined} />
            <StatCard label="Calmar"
              value={data.calmar != null ? data.calmar.toFixed(2) : '—'}
              sub="ann. return ÷ drawdown"
              color={data.calmar != null ? data.calmar >= 1.0 ? '#4ade80' : data.calmar >= 0 ? '#facc15' : '#f87171' : undefined} />
          </div>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 24 }}>
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

          {/* Equity curve */}
          {data.equity_curve && data.equity_curve.length >= 2 && (
            <EquityCurve points={data.equity_curve} spyReturn={data.spy_return} />
          )}
        </>
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
