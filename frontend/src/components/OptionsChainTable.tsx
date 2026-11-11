// Full strike matrix for one expiry (T230-DATA-OPTIONS-CHAIN), extracted from the stock page so
// it can be rendered in tests.
//
// Audit R03/R04 (2026-10-22): volume, OI and IV render "—" when unknown, never 0 or 0%. The ITM
// tint appears only when moneyness was measured against the chain session's own close; with no
// such price nothing is tinted and the reference line says why.
import React from 'react';
import type { FieldCount, MoneynessReference, OptionsChainRow } from '@/lib/api';
import { fmtCount, fmtIv, moneynessReferenceLine, missingFieldNote } from '@/lib/optionsFlowStatus';

type Coverage = { calls: Record<'volume' | 'oi' | 'iv', FieldCount>; puts: Record<'volume' | 'oi' | 'iv', FieldCount> } | null | undefined;

// AUD-OPT-CHAIN-GREEKS (2026-11-10): the API has always returned delta and gamma per contract;
// the table never showed them. The provider sends greeks only for contracts that traded that
// session, so a missing one renders "—", never 0.
const fmtGreek = (v: number | null | undefined, digits: number) =>
  typeof v === 'number' && Number.isFinite(v) ? v.toFixed(digits) : '—';
// An ask of 0 is never a real offer; the adapter stores a missing quote as 0.
const fmtAsk = (v: number) => (v > 0 ? v.toFixed(2) : '—');

const isItm = (r?: OptionsChainRow) => Boolean(r && (r.moneyness ? r.moneyness === 'ITM' : r.itm === true));

function sum(a?: FieldCount, b?: FieldCount): FieldCount | null {
  return a && b ? { known: a.known + b.known, total: a.total + b.total } : null;
}

export default function OptionsChainTable({ calls, puts, moneynessReference, moneynessUnknownReason, fieldCoverage, picks, nearStrikesOf, onShowAll }: {
  calls: OptionsChainRow[]; puts: OptionsChainRow[];
  moneynessReference?: MoneynessReference | null; moneynessUnknownReason?: string | null; fieldCoverage?: Coverage;
  /** Call Finder: option_symbol -> badge label for the picked contracts. */
  picks?: Record<string, string>;
  /** When set, only strikes within 15% of this price are shown, with a control to show all. */
  nearStrikesOf?: number | null; onShowAll?: () => void;
}) {
  const allStrikes = Array.from(new Set([...calls.map(c => c.strike), ...puts.map(p => p.strike)])).sort((a, b) => a - b);
  const pickedStrikes = new Set(calls.filter(c => c.option_symbol && picks?.[c.option_symbol]).map(c => c.strike));
  const strikes = nearStrikesOf && nearStrikesOf > 0
    ? allStrikes.filter(k => Math.abs(k - nearStrikesOf) / nearStrikesOf <= 0.15 || pickedStrikes.has(k))
    : allStrikes;
  const hidden = allStrikes.length - strikes.length;
  const notes = fieldCoverage ? [
    missingFieldNote('Volume', sum(fieldCoverage.calls.volume, fieldCoverage.puts.volume)),
    missingFieldNote('Open interest', sum(fieldCoverage.calls.oi, fieldCoverage.puts.oi)),
    missingFieldNote('IV', sum(fieldCoverage.calls.iv, fieldCoverage.puts.iv)),
  ].filter(Boolean) : [];
  const cell = (align: 'left' | 'right', color: string): React.CSSProperties => ({ padding: '4px 8px', textAlign: align, color });
  return (
    <>
      <p aria-label="Chain moneyness reference" style={{ margin: '0 0 6px', fontSize: 11, color: '#64748b' }}>
        {moneynessReferenceLine(moneynessReference, moneynessUnknownReason)}
        {moneynessReference ? ' Strikes estimated to be in the money are tinted.' : ''}
      </p>
      {notes.length > 0 && (
        <p aria-label="Chain field coverage" style={{ margin: '0 0 6px', fontSize: 11, color: '#94a3b8' }}>
          {notes.join(' · ')} (shown as —; unknown is not zero).
        </p>
      )}
      {hidden > 0 && (
        <p aria-label="Chain strike filter" style={{ margin: '0 0 6px', fontSize: 11, color: '#64748b' }}>
          Showing strikes within 15% of the price; {hidden} more hidden.{' '}
          {onShowAll && <button type="button" onClick={onShowAll} style={{ fontSize: 11, background: 'transparent', border: 'none', color: '#38bdf8', cursor: 'pointer', padding: 0 }}>Show all strikes</button>}
        </p>
      )}
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #1e293b' }}>
              {['Bid', 'Ask', 'Last', 'Vol', 'OI', 'IV', 'Delta', 'Gamma'].map(h => (
                <th key={`c-${h}`} style={{ padding: '4px 8px', textAlign: 'right', color: '#4ade80', fontWeight: 500 }}>{h}</th>
              ))}
              <th style={{ padding: '4px 12px', textAlign: 'center', color: '#475569', fontWeight: 700 }}>Strike</th>
              {['Bid', 'Ask', 'Last', 'Vol', 'OI', 'IV', 'Delta', 'Gamma'].map(h => (
                <th key={`p-${h}`} style={{ padding: '4px 8px', textAlign: 'left', color: '#f87171', fontWeight: 500 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {strikes.map(strike => {
              const c = calls.find(x => x.strike === strike);
              const p = puts.find(x => x.strike === strike);
              const cTint = isItm(c) ? '#4ade80' : '#94a3b8';
              const pTint = isItm(p) ? '#f87171' : '#94a3b8';
              return (
                <tr key={strike} data-strike={strike} style={{ borderBottom: '1px solid #0f172a' }}>
                  <td data-itm={isItm(c) || undefined} style={cell('right', cTint)}>{c ? c.bid.toFixed(2) : '—'}</td>
                  <td style={cell('right', cTint)}>{c ? fmtAsk(c.ask) : '—'}</td>
                  <td style={cell('right', '#94a3b8')}>{c ? c.last_price.toFixed(2) : '—'}</td>
                  <td style={cell('right', '#94a3b8')}>{c ? fmtCount(c.volume) : '—'}</td>
                  <td style={cell('right', '#64748b')}>{c ? fmtCount(c.oi) : '—'}</td>
                  <td style={cell('right', '#64748b')}>{c ? fmtIv(c.iv) : '—'}</td>
                  <td style={cell('right', '#64748b')}>{c ? fmtGreek(c.delta, 2) : '—'}</td>
                  <td style={cell('right', '#64748b')}>{c ? fmtGreek(c.gamma, 3) : '—'}</td>
                  <td style={{ padding: '4px 12px', textAlign: 'center', color: '#e2e8f0', fontWeight: 700, whiteSpace: 'nowrap' }}>
                    ${strike}
                    {c?.option_symbol && picks?.[c.option_symbol] && (
                      <span data-pick={picks[c.option_symbol]} style={{ marginLeft: 6, fontSize: 9, padding: '1px 5px', borderRadius: 4, background: 'rgba(56,189,248,0.18)', color: '#38bdf8' }}>
                        {picks[c.option_symbol]}
                      </span>
                    )}
                  </td>
                  <td data-itm={isItm(p) || undefined} style={cell('left', pTint)}>{p ? p.bid.toFixed(2) : '—'}</td>
                  <td style={cell('left', pTint)}>{p ? fmtAsk(p.ask) : '—'}</td>
                  <td style={cell('left', '#94a3b8')}>{p ? p.last_price.toFixed(2) : '—'}</td>
                  <td style={cell('left', '#94a3b8')}>{p ? fmtCount(p.volume) : '—'}</td>
                  <td style={cell('left', '#64748b')}>{p ? fmtCount(p.oi) : '—'}</td>
                  <td style={cell('left', '#64748b')}>{p ? fmtIv(p.iv) : '—'}</td>
                  <td style={cell('left', '#64748b')}>{p ? fmtGreek(p.delta, 2) : '—'}</td>
                  <td style={cell('left', '#64748b')}>{p ? fmtGreek(p.gamma, 3) : '—'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
