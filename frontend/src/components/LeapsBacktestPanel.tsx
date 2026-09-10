import { useState } from 'react';
import useSWR from 'swr';
import { api, type LeapsCoverage, type LeapsCompare } from '@/lib/api';

/**
 * T375-LEAPS-BACKTEST — long-dated call backtester for the QQQ family.
 *
 * User request: "can we add QQQ Leap Call in Strategy Backtester with delta, date range,
 * QQQ or QQQM or QLD or TQQQ, strike price, expiration date etc"
 *
 * Every number comes from a REAL captured option chain — no Black-Scholes, no synthetic greeks.
 * Entry pays the ASK and exit receives the BID, and the spread cost is shown rather than buried:
 * crossing at mid would have reported a real 366-day TQQQ hold 20+ points higher than it was.
 *
 * COVERAGE IS SHOWN FIRST, ON PURPOSE. Greeks are sparse by design (UW returns delta only where
 * volume > 0), so a symbol's tradeable-day count is far below its row count — QQQM has 266
 * usable days against TQQQ's 726. A comparison that ignored that would rank symbols over
 * DIFFERENT date ranges while looking authoritative, which is the AUD-RANK-RSPLACEHOLDER shape.
 * So `missing` symbols are named and `comparable: false` is surfaced as a warning, never hidden.
 */

const SYMBOLS = ['QQQ', 'QQQM', 'QLD', 'TQQQ'] as const;

function fmtPct(v: number | null | undefined) {
  if (v == null) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
}
function fmtMoney(v: number | null | undefined) {
  if (v == null) return '—';
  return `${v < 0 ? '-' : ''}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}
const col = (v: number | null | undefined) =>
  v == null ? '#64748b' : v >= 0 ? '#22c55e' : '#f87171';

export default function LeapsBacktestPanel() {
  const [selected, setSelected] = useState<string[]>([...SYMBOLS]);
  const [entryDate, setEntryDate] = useState('2024-10-01');
  const [exitDate, setExitDate] = useState('2025-10-01');
  const [targetDelta, setTargetDelta] = useState(0.80);
  const [minDte, setMinDte] = useState(330);
  const [contracts, setContracts] = useState(1);
  const [result, setResult] = useState<LeapsCompare | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState('');

  const { data: coverage } = useSWR<LeapsCoverage>(
    `leaps-coverage-${targetDelta}-${minDte}`,
    () => api.leapsCoverage(SYMBOLS.join(','), targetDelta, minDte),
    { revalidateOnFocus: false },
  );

  async function run() {
    if (selected.length === 0) { setError('Pick at least one symbol.'); return; }
    if (exitDate <= entryDate) { setError('Exit date must be after entry date.'); return; }
    setRunning(true); setError(''); setResult(null);
    try {
      setResult(await api.leapsCompare({
        symbols: selected.join(','), entry_date: entryDate, exit_date: exitDate,
        target_delta: targetDelta, min_dte: minDte, contracts,
      }));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Backtest failed.');
    } finally {
      setRunning(false);
    }
  }

  const lbl = { fontSize: 10, color: '#475569', textTransform: 'uppercase' as const, letterSpacing: '0.05em', display: 'block', marginBottom: 3 };
  const inp = { background: '#0d1424', border: '1px solid #1e293b', borderRadius: 6, color: '#e2e8f0', padding: '6px 8px', fontSize: 12, width: '100%' };

  return (
    <div style={{ marginTop: 28, padding: '18px 20px', borderRadius: 12, background: '#0d1424', border: '1px solid #1e293b' }}>
      <h2 style={{ fontSize: 15, fontWeight: 800, color: '#e2e8f0', margin: 0 }}>
        LEAPS Call Backtester — QQQ Family
      </h2>
      <div style={{ fontSize: 11.5, color: '#64748b', marginTop: 5, lineHeight: 1.6 }}>
        Replays <strong style={{ color: '#94a3b8' }}>real captured option chains</strong> — no
        Black-Scholes, no synthetic greeks. Buys at the ask, sells at the bid, and shows the
        spread cost.
      </div>

      {/* Coverage — deliberately shown BEFORE any result */}
      {coverage && (
        <div style={{ marginTop: 12, padding: '10px 12px', borderRadius: 8, background: 'rgba(148,163,184,0.05)', border: '1px solid #1e293b' }}>
          <div style={{ fontSize: 10, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 6 }}>
            Data coverage at delta {targetDelta.toFixed(2)} · ≥{minDte} DTE
          </div>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', fontSize: 11.5 }}>
            {SYMBOLS.map(s => {
              const c = coverage.by_symbol[s];
              return (
                <div key={s}>
                  <span style={{ color: '#cbd5e1', fontWeight: 700 }}>{s}</span>{' '}
                  <span style={{ color: c && c.days > 0 ? '#94a3b8' : '#f87171' }}>
                    {c ? `${c.days} days` : '0 days'}
                  </span>
                  {c?.oldest && <span style={{ color: '#475569' }}> · from {c.oldest}</span>}
                </div>
              );
            })}
          </div>
          <div style={{ fontSize: 11, color: coverage.comparison_supported ? '#64748b' : '#fbbf24', marginTop: 6 }}>
            {coverage.common_days} days have all four available.
            {!coverage.comparison_supported &&
              ` Below ${coverage.min_days_for_comparison} — treat cross-symbol rankings as indicative only.`}
          </div>
        </div>
      )}

      {/* Controls */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(120px,1fr))', gap: 10, marginTop: 14 }}>
        <div>
          <label style={lbl}>Entry date</label>
          <input type="date" style={inp} value={entryDate} onChange={e => setEntryDate(e.target.value)} />
        </div>
        <div>
          <label style={lbl}>Exit date</label>
          <input type="date" style={inp} value={exitDate} onChange={e => setExitDate(e.target.value)} />
        </div>
        <div>
          <label style={lbl}>Target delta</label>
          <input type="number" step="0.05" min="0.05" max="0.99" style={inp}
                 value={targetDelta} onChange={e => setTargetDelta(parseFloat(e.target.value) || 0.8)} />
        </div>
        <div>
          <label style={lbl}>Min DTE</label>
          <input type="number" step="30" min="30" max="1400" style={inp}
                 value={minDte} onChange={e => setMinDte(parseInt(e.target.value) || 330)} />
        </div>
        <div>
          <label style={lbl}>Contracts</label>
          <input type="number" step="1" min="1" max="1000" style={inp}
                 value={contracts} onChange={e => setContracts(parseInt(e.target.value) || 1)} />
        </div>
      </div>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 12, flexWrap: 'wrap' }}>
        {SYMBOLS.map(s => {
          const on = selected.includes(s);
          return (
            <button key={s} onClick={() => setSelected(p => on ? p.filter(x => x !== s) : [...p, s])}
              style={{
                padding: '5px 12px', borderRadius: 6, fontSize: 12, fontWeight: 700, cursor: 'pointer',
                background: on ? 'rgba(56,189,248,0.15)' : 'transparent',
                border: `1px solid ${on ? 'rgba(56,189,248,0.4)' : '#1e293b'}`,
                color: on ? '#38bdf8' : '#475569',
              }}>{s}</button>
          );
        })}
        <button onClick={run} disabled={running}
          style={{
            marginLeft: 'auto', padding: '7px 20px', borderRadius: 7, fontSize: 12.5, fontWeight: 700,
            cursor: running ? 'default' : 'pointer', background: running ? '#1e293b' : 'rgba(34,197,94,0.18)',
            border: `1px solid ${running ? '#1e293b' : 'rgba(34,197,94,0.4)'}`,
            color: running ? '#64748b' : '#22c55e',
          }}>{running ? 'Running…' : 'Run backtest'}</button>
      </div>

      {error && <div style={{ fontSize: 12, color: '#f87171', marginTop: 10 }}>{error}</div>}

      {/* Results */}
      {result && (
        <div style={{ marginTop: 16 }}>
          {/* The honesty banner — never hidden when the comparison is incomplete. */}
          {!result.comparable && (
            <div style={{ padding: '9px 12px', borderRadius: 8, background: 'rgba(251,191,36,0.08)', border: '1px solid rgba(251,191,36,0.3)', fontSize: 11.5, color: '#fbbf24', marginBottom: 10 }}>
              Incomplete comparison — no usable LEAPS quote for{' '}
              <strong>{result.missing.join(', ')}</strong> on these dates. The ranking below
              covers only the symbols that priced.
            </div>
          )}
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, minWidth: 640 }}>
              <thead>
                <tr>
                  {['Symbol', 'Return', 'P&L', 'Cost', 'Strike', 'Expiry', 'Δ entry', 'DTE', 'Held', 'Spread'].map(h => (
                    <th key={h} style={{ textAlign: 'left', padding: '6px 9px', color: '#64748b', fontWeight: 700, fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.04em', borderBottom: '1px solid #1e293b' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.ranking.map(sym => {
                  const r = result.results[sym];
                  return (
                    <tr key={sym}>
                      <td style={{ padding: '6px 9px', borderBottom: '1px solid #131c2e', fontWeight: 700, color: '#e2e8f0' }}>{sym}</td>
                      <td style={{ padding: '6px 9px', borderBottom: '1px solid #131c2e', fontWeight: 700, color: col(r.return_pct), fontVariantNumeric: 'tabular-nums' }}>{fmtPct(r.return_pct)}</td>
                      <td style={{ padding: '6px 9px', borderBottom: '1px solid #131c2e', color: col(r.pnl), fontVariantNumeric: 'tabular-nums' }}>{fmtMoney(r.pnl)}</td>
                      <td style={{ padding: '6px 9px', borderBottom: '1px solid #131c2e', color: '#94a3b8', fontVariantNumeric: 'tabular-nums' }}>{fmtMoney(r.cost)}</td>
                      <td style={{ padding: '6px 9px', borderBottom: '1px solid #131c2e', color: '#94a3b8' }}>{r.strike ?? '—'}</td>
                      <td style={{ padding: '6px 9px', borderBottom: '1px solid #131c2e', color: '#94a3b8' }}>{r.expiry ?? '—'}</td>
                      <td style={{ padding: '6px 9px', borderBottom: '1px solid #131c2e', color: '#94a3b8', fontVariantNumeric: 'tabular-nums' }}>{r.entry_delta != null ? r.entry_delta.toFixed(3) : '—'}</td>
                      <td style={{ padding: '6px 9px', borderBottom: '1px solid #131c2e', color: '#94a3b8', fontVariantNumeric: 'tabular-nums' }}>{r.dte_at_entry ?? '—'}</td>
                      <td style={{ padding: '6px 9px', borderBottom: '1px solid #131c2e', color: '#94a3b8', fontVariantNumeric: 'tabular-nums' }}>{r.days_held}d</td>
                      {/* Spread is a REAL cost on a LEAPS — shown, not folded into the return. */}
                      <td style={{ padding: '6px 9px', borderBottom: '1px solid #131c2e', color: '#64748b', fontVariantNumeric: 'tabular-nums' }}>{fmtMoney(r.spread_cost)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {result.ranking.length > 0 && (
            <div style={{ fontSize: 11, color: '#475569', marginTop: 8, lineHeight: 1.6 }}>
              Exit priced at the nearest quote on or before the requested date.{' '}
              {result.results[result.ranking[0]].exit_date !== result.results[result.ranking[0]].exit_date_requested && (
                <>Actual exit {result.results[result.ranking[0]].exit_date} (requested{' '}
                {result.results[result.ranking[0]].exit_date_requested}).{' '}</>
              )}
              Past results are not a forecast — leveraged ETFs decay, and a LEAPS adds theta on top.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
