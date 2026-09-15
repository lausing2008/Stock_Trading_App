import { useState } from 'react';
import useSWR from 'swr';
import { api, type LeapsCoverage, type LeapsCompare, type LeapsRolling } from '@/lib/api';

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

// T395-LEAPS-SYMBOLS: extended from the 4-symbol QQQ family to every symbol with a usable
// captured option-chain history, after the T384 backfill completed (29 symbols, 16,443
// symbol-days, 23.19M rows, zero errors).
//
// ORDERED BY USABLE-LEAPS DAYS, not alphabetically, so the most backtestable names sit first.
// "Usable" means a day carrying a delta-selectable 0.60-0.80 contract at >=330 DTE with a real
// NBBO quote -- greeks are sparse BY DESIGN (UW returns delta only where volume > 0), so raw
// row counts overstate what is actually testable.
//
// QQQM (50% usable) and QLD (82%) are kept ONLY because they are the original T374 comparison
// set the qqq-leaps-playbook page is built around. They are the counter-example, not a
// recommendation -- QLD's thin chain is what produced the T380 "no usable quote" failure.
const SYMBOLS = [
  // 724 days, ~100% usable
  'META', 'TSM', 'GOOG', 'JPM', 'AVGO', 'CRWD', 'NET', 'MU', 'GLD', 'TSLA', 'SMH', 'XLK',
  'CAT', 'RTX', 'DELL', 'HPE', 'GEV', 'SOXX',
  // 549 days
  'SPY', 'QQQ', 'TQQQ', 'QLD', 'QQQM',
  // 130 days -- captured later, shorter history but fully usable
  'AAPL', 'MSFT', 'NVDA', 'AMZN', 'AMD', 'PLTR',
] as const;

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
  // T395: default to a small comparable set, NOT all 30 -- selecting every symbol would fire
  // 30 sequential option-chain backtests on a single click. The user adds what they want.
  const [selected, setSelected] = useState<string[]>(['QQQ', 'TSM', 'GOOG', 'AVGO']);
  const [entryDate, setEntryDate] = useState('2024-10-01');
  const [exitDate, setExitDate] = useState('2025-10-01');
  const [targetDelta, setTargetDelta] = useState(0.80);
  const [minDte, setMinDte] = useState(330);
  const [contracts, setContracts] = useState(1);
  const [result, setResult] = useState<LeapsCompare | null>(null);
  // T385-LEAPS-ROLL: "I set 2 years leaps but I wanna sell before 2 years like half a year or
  // a year, and then repeat." Rolling is a genuinely different strategy, not a preset — it
  // re-strikes at the current price each cycle and pays a full spread every time — so it gets
  // its own mode and its own result shape rather than being folded into the compare table.
  const [mode, setMode] = useState<'hold' | 'roll'>('hold');
  const [holdDays, setHoldDays] = useState(182);
  const [compound, setCompound] = useState(true);
  const [rollResult, setRollResult] = useState<LeapsRolling | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState('');

  const { data: coverage } = useSWR<LeapsCoverage>(
    `leaps-coverage-${selected.join(',')}-${targetDelta}-${minDte}`,
    () => api.leapsCoverage(selected.join(',') || 'QQQ', targetDelta, minDte),
    { revalidateOnFocus: false },
  );

  async function run() {
    if (selected.length === 0) { setError('Pick at least one symbol.'); return; }
    if (exitDate <= entryDate) { setError('Exit date must be after entry date.'); return; }
    setRunning(true); setError(''); setResult(null); setRollResult(null);
    try {
      if (mode === 'roll') {
        // Rolling runs ONE symbol at a time: each symbol produces its own cycle sequence, and
        // stacking several into the compare table would imply a like-for-like ranking across
        // sequences that may have different cycle counts and different skipped windows.
        setRollResult(await api.leapsRolling({
          symbol: selected[0], start_date: entryDate, end_date: exitDate,
          hold_days: holdDays, target_delta: targetDelta, min_dte: minDte,
          contracts, compound,
        }));
        return;
      }
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
        LEAPS Call Backtester
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
            {/* T396: render only the SELECTED symbols. The coverage request is scoped to the
                selection (T395, to avoid a 60s query over 49M rows), so rendering all 29 made
                every unselected symbol show a false "0 days" — META reads 0 while the archive
                holds 724 usable days for it. Showing a wrong number is worse than showing none. */}
            {selected.map(s => {
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
        {/* T385-LEAPS-ROLL: hold_days is INDEPENDENT of Min DTE — Min DTE says how long-dated
            the CONTRACT is, Hold says how long it is HELD. Buying a 730-DTE contract and
            selling after 182 days is the normal case for this mode, not an edge case. */}
        {mode === 'roll' && (
          <>
            <div>
              <label style={lbl}>Hold (days)</label>
              <input type="number" step="1" min="7" max="1095" style={inp}
                     value={holdDays} onChange={e => setHoldDays(parseInt(e.target.value) || 182)} />
            </div>
            <div>
              <label style={lbl}>Sizing</label>
              <select style={inp} value={compound ? 'y' : 'n'}
                      onChange={e => setCompound(e.target.value === 'y')}>
                <option value="y">Reinvest</option>
                <option value="n">Fixed size</option>
              </select>
            </div>
          </>
        )}
      </div>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 12, flexWrap: 'wrap' }}>
        {/* Hold vs Roll. Rolling runs ONE symbol at a time — each produces its own cycle
            sequence, and stacking several into the compare table would imply a like-for-like
            ranking across sequences with different cycle counts and different skipped windows. */}
        {(['hold', 'roll'] as const).map(m => (
          <button key={m} onClick={() => { setMode(m); setResult(null); setRollResult(null); }}
            title={m === 'hold'
              ? 'Buy once, hold to the exit date. Compares every selected symbol.'
              : 'Buy a long-dated LEAPS, sell after the hold period, then repeat. Runs the FIRST selected symbol only — each roll is its own cycle sequence. Pays a full bid/ask round-trip every cycle.'}
            style={{
              padding: '5px 12px', borderRadius: 6, fontSize: 12, fontWeight: 700, cursor: 'pointer',
              background: mode === m ? 'rgba(168,85,247,0.15)' : 'transparent',
              border: `1px solid ${mode === m ? 'rgba(168,85,247,0.45)' : '#1e293b'}`,
              color: mode === m ? '#c084fc' : '#475569',
            }}>{m === 'hold' ? 'Hold' : 'Roll'}</button>
        ))}
        <span style={{ width: 1, height: 20, background: '#1e293b' }} />
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

      {/* T385-LEAPS-ROLL results — its own shape, deliberately not folded into the compare
          table: a roll is a SEQUENCE of cycles, and the spread cost is the number that decides
          whether it beat holding. */}
      {rollResult && (
        <div style={{ marginTop: 16 }}>
          <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', alignItems: 'baseline',
                        padding: '10px 12px', borderRadius: 8, background: 'rgba(168,85,247,0.06)',
                        border: '1px solid rgba(168,85,247,0.25)' }}>
            <div>
              <div style={{ fontSize: 10, color: '#475569', textTransform: 'uppercase' }}>Symbol</div>
              <div style={{ fontSize: 18, fontWeight: 800, color: '#e2e8f0' }}>{rollResult.symbol}</div>
            </div>
            <div>
              {/* T396: a roll runs ONE symbol, and the per-cycle table has no symbol column, so
                  without this the whole result was unattributable. */}
              <div style={{ fontSize: 10, color: '#475569', textTransform: 'uppercase' }}>Total</div>
              <div style={{ fontSize: 18, fontWeight: 800,
                            color: (rollResult.total_return_pct ?? 0) >= 0 ? '#4ade80' : '#f87171' }}>
                {rollResult.total_return_pct != null ? `${rollResult.total_return_pct >= 0 ? '+' : ''}${rollResult.total_return_pct.toFixed(2)}%` : '—'}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 10, color: '#475569', textTransform: 'uppercase' }}>CAGR</div>
              <div style={{ fontSize: 14, fontWeight: 700, color: '#e2e8f0' }}>
                {rollResult.cagr_pct != null ? `${rollResult.cagr_pct.toFixed(2)}%` : '—'}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 10, color: '#475569', textTransform: 'uppercase' }}>Cycles</div>
              <div style={{ fontSize: 14, fontWeight: 700, color: '#e2e8f0' }}>
                {rollResult.cycles_completed} · {rollResult.wins}W/{rollResult.losses}L ({rollResult.win_rate_pct}%)
              </div>
            </div>
            <div title="Every cycle pays a full bid/ask round-trip. This is the cost of rolling versus holding once.">
              <div style={{ fontSize: 10, color: '#475569', textTransform: 'uppercase' }}>Spread paid</div>
              <div style={{ fontSize: 14, fontWeight: 700, color: '#fbbf24' }}>
                ${rollResult.total_spread_cost.toLocaleString()}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 10, color: '#475569', textTransform: 'uppercase' }}>Sizing</div>
              <div style={{ fontSize: 14, fontWeight: 700, color: '#94a3b8' }}>
                {rollResult.compound ? 'Reinvest' : 'Fixed'}
              </div>
            </div>
          </div>

          {rollResult.cycles_skipped.length > 0 && (
            <div style={{ marginTop: 10, padding: '9px 12px', borderRadius: 8, fontSize: 11.5,
                          background: 'rgba(251,191,36,0.08)', border: '1px solid rgba(251,191,36,0.3)', color: '#fbbf24' }}>
              {rollResult.cycles_skipped.length} cycle(s) could not be priced — the total covers only the cycles that ran.
              {rollResult.cycles_skipped.map(c => (
                <div key={c.entry_date} style={{ marginTop: 4, opacity: 0.95 }}>
                  <strong>{c.entry_date} → {c.exit_date}</strong>: {c.reason}
                </div>
              ))}
            </div>
          )}

          <div style={{ overflowX: 'auto', marginTop: 10 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, minWidth: 660 }}>
              <thead>
                <tr>
                  {['#', 'Entry', 'Exit', 'Return', 'Strike', 'Expiry', 'Δ entry', 'Held', 'Spread'].map(h => (
                    <th key={h} style={{ textAlign: 'left', padding: '6px 9px', color: '#64748b', fontWeight: 700,
                                         fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.04em',
                                         borderBottom: '1px solid #1e293b' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rollResult.cycles.map((c, i) => (
                  <tr key={`${c.entry_date}-${i}`}>
                    <td style={{ padding: '6px 9px', borderBottom: '1px solid #131c2e', color: '#475569' }}>{i + 1}</td>
                    <td style={{ padding: '6px 9px', borderBottom: '1px solid #131c2e', color: '#94a3b8' }}>{c.entry_date}</td>
                    <td style={{ padding: '6px 9px', borderBottom: '1px solid #131c2e', color: '#94a3b8' }}>{c.exit_date}</td>
                    <td style={{ padding: '6px 9px', borderBottom: '1px solid #131c2e', fontWeight: 700,
                                 color: (c.return_pct ?? 0) >= 0 ? '#4ade80' : '#f87171' }}>
                      {c.return_pct != null ? `${c.return_pct >= 0 ? '+' : ''}${c.return_pct.toFixed(2)}%` : '—'}
                    </td>
                    <td style={{ padding: '6px 9px', borderBottom: '1px solid #131c2e', color: '#94a3b8' }}>{c.strike ?? '—'}</td>
                    <td style={{ padding: '6px 9px', borderBottom: '1px solid #131c2e', color: '#94a3b8' }}>{c.expiry ?? '—'}</td>
                    <td style={{ padding: '6px 9px', borderBottom: '1px solid #131c2e', color: '#94a3b8' }}>
                      {c.entry_delta != null ? c.entry_delta.toFixed(3) : '—'}
                      {c.delta_relaxed && (
                        <span title="No contract inside the strict ±0.10 delta band could be priced; the nearest available delta was used."
                              style={{ marginLeft: 5, padding: '1px 5px', borderRadius: 4, fontSize: 9, fontWeight: 700,
                                       background: 'rgba(251,191,36,0.15)', color: '#fbbf24', border: '1px solid rgba(251,191,36,0.35)' }}>NEAR</span>
                      )}
                    </td>
                    <td style={{ padding: '6px 9px', borderBottom: '1px solid #131c2e', color: '#94a3b8' }}>{c.days_held}d</td>
                    <td style={{ padding: '6px 9px', borderBottom: '1px solid #131c2e', color: '#fbbf24' }}>
                      ${(c.spread_cost ?? 0).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p style={{ fontSize: 11, color: '#64748b', marginTop: 8 }}>
            Each cycle re-strikes at the price on its own entry date. Rolling pays a full bid/ask
            round-trip every cycle — compare <strong style={{ color: '#fbbf24' }}>Spread paid</strong>
            against a single hold before concluding rolling wins. Past results are not a forecast.
          </p>
        </div>
      )}

      {/* Results */}
      {result && (
        <div style={{ marginTop: 16 }}>
          {/* The honesty banner — never hidden when the comparison is incomplete. */}
          {!result.comparable && (
            <div style={{ padding: '9px 12px', borderRadius: 8, background: 'rgba(251,191,36,0.08)', border: '1px solid rgba(251,191,36,0.3)', fontSize: 11.5, color: '#fbbf24', marginBottom: 10 }}>
              {/* T380-LEAPS-CONTRACTGAP: the old text said "no usable LEAPS quote ... on these
                  dates", which reads as a COVERAGE problem — so the user checked the coverage
                  panel directly above, correctly saw 629 usable QLD days, and the two lines
                  contradicted each other on one screen. The backend now returns a specific
                  per-symbol reason (delta band vs. no capture vs. a contract that stopped
                  being quoted mid-hold); only the backend can see which guard actually fired. */}
              Incomplete comparison — the ranking below covers only the symbols that priced.
              {result.missing.map(sym => (
                <div key={sym} style={{ marginTop: 4, opacity: 0.95 }}>
                  <strong>{sym}</strong>: {result.missing_reasons?.[sym] ?? 'no usable LEAPS quote on these dates.'}
                </div>
              ))}
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
                      <td style={{ padding: '6px 9px', borderBottom: '1px solid #131c2e', color: '#94a3b8', fontVariantNumeric: 'tabular-nums' }}>{r.entry_delta != null ? r.entry_delta.toFixed(3) : '—'}
                        {/* T381-LEAPS-NEARESTDELTA: a relaxed match is a NEARBY delta, not the
                            requested one. Badged rather than footnoted, because the number in
                            this very cell is the thing that differs from what was asked for —
                            an unlabelled 0.802 under a 0.70 target reads as a bug. */}
                        {r.delta_relaxed && (
                          <span title="No contract inside the strict ±0.10 delta band could be priced on these dates; the nearest available delta was used instead. This is a related trade, not the one requested."
                                style={{ marginLeft: 6, padding: '1px 5px', borderRadius: 4, fontSize: 9, fontWeight: 700, background: 'rgba(251,191,36,0.15)', color: '#fbbf24', border: '1px solid rgba(251,191,36,0.35)' }}>
                            NEAR
                          </span>
                        )}</td>
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
