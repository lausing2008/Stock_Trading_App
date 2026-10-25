import React, { useState } from 'react';
import type { OptionStrategy } from '@/lib/api';
import { lossScopeLabel } from '@/lib/optionExpiryRisk';
import type { CommonOptionKey } from '@/lib/commonOptionPlans';
import { candidateDte, compareCandidateRows, nonnegativeMeasurement, type CandidateSort } from '@/lib/optionCandidateComparison';

const money = (value: unknown) => nonnegativeMeasurement(value) === null ? 'Unavailable'
  : `$${(value as number).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const greek = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(4) : 'Unavailable';
const cell: React.CSSProperties = { padding: 7, textAlign: 'left', verticalAlign: 'top', borderBottom: '1px solid #334155' };
export default function OptionCandidateComparison({ original, originalStrike, originalExpiry, originalSymbol, alternatives, strategyKey, today, selectedIndex, onSelect }: {
  original?: OptionStrategy; originalStrike: number | null; originalExpiry: string | null;
  originalSymbol?: string | null;
  alternatives: OptionStrategy[]; strategyKey: CommonOptionKey; today: string;
  selectedIndex: number | null; onSelect: (index: number | null) => void;
}) {
  const [sort, setSort] = useState<CandidateSort>('server');
  const alternativesSorted = compareCandidateRows(alternatives, sort, today);
  const rows = [{ sourceIndex: null, strategy: original, dte: candidateDte(original?.legs[0]?.expiry ?? originalExpiry, today) }, ...alternativesSorted];
  return <details style={{ marginTop: 12 }}>
    <summary>Compare candidate costs, risk and liquidity ({alternatives.length} returned alternatives)</summary>
    <p>The original plan is pinned first as a reference. Sorts apply only to the returned shortlist, after server screening and truncation. Unknown measurements sort last. No row is a best-return recommendation, and sorting never selects a contract.</p>
    <label>Sort returned alternatives<select aria-label="Sort returned alternatives" value={sort} onChange={event => setSort(event.target.value as CandidateSort)}>
      <option value="server">Server shortlist order</option><option value="spread">Narrowest displayed spread</option>
      <option value="oi">Highest measured open interest</option><option value="capital">Lowest stated capital</option><option value="expiry">Earliest expiry</option>
    </select></label>
    <div style={{ overflowX: 'auto' }}><table aria-label="Returned option candidate comparison" style={{ width: '100%', fontSize: 12 }}>
      <thead><tr>{['Reference / choice', 'Contract', 'Expiry / calendar DTE', 'Reference debit / credit', 'Maximum loss', 'Maximum profit', 'Expiry breakeven', 'Stated capital', 'Shown spread (rounded)', 'Open interest', 'Contract delta / gamma'].map(label => <th key={label} style={cell}>{label}</th>)}</tr></thead>
      <tbody>{rows.map(row => {
        const strategy = row.strategy, leg = strategy?.legs[0], isOriginal = row.sourceIndex === null;
        const strike = leg?.strike ?? (isOriginal ? originalStrike : null);
        const expiry = leg?.expiry ?? (isOriginal ? originalExpiry : null);
        const chosen = row.sourceIndex === selectedIndex;
        const oi = nonnegativeMeasurement(leg?.oi), spread = nonnegativeMeasurement(leg?.spread_pct);
        const unlimited = strategy?.max_profit_per_contract === null && strategy.legs.length === 1 && leg?.action === 'buy'
          && ((strategyKey === 'long_call' && leg.right === 'call') || (strategyKey === 'protective_put' && leg.right === 'put'));
        return <tr key={isOriginal ? 'original' : row.sourceIndex} aria-selected={chosen}>
          <td style={cell}><strong>{isOriginal ? 'Original plan' : `Alternative ${row.sourceIndex! + 1}`}</strong><br />
            <button type="button" disabled={chosen || (!isOriginal && strategy?.legs.length !== 1)}
              aria-label={isOriginal ? 'Compare original plan' : `Compare alternative ${row.sourceIndex! + 1}`}
              onClick={() => onSelect(row.sourceIndex)}>{chosen ? 'Comparing' : 'Compare'}</button>
            {!strategy && <div>Payoff unavailable</div>}</td>
          <td style={cell}>{money(strike)} {leg?.right ?? (strategyKey === 'long_call' || strategyKey === 'covered_call' ? 'call' : 'put')}<br />{leg?.option_symbol ?? (isOriginal ? originalSymbol : null) ?? 'Contract identity unavailable'}{leg && 'last_tape_time' in leg && <><br />Last trade: {leg.last_tape_time ?? 'unavailable'} · quote time unavailable</>}</td>
          <td style={cell}>{expiry ?? 'Unavailable'}<br />{row.dte === null ? 'DTE unavailable' : `${row.dte} calendar days${row.dte < 0 ? ' — expired' : row.dte === 0 ? ' — same day' : ''}`}</td>
          <td style={cell}>{strategy ? `${money(strategy.net_per_contract)} ${strategy.net}` : 'Unavailable'}</td>
          <td style={cell}>{money(strategy?.max_loss_per_contract)}{strategy && <div>{lossScopeLabel(strategy)}{strategyKey === 'covered_call' || strategyKey === 'cash_secured_put' ? ' · stock reaches $0' : ''}</div>}</td><td style={cell}>{unlimited ? 'Unbounded' : money(strategy?.max_profit_per_contract)}</td>
          <td style={cell}>{money(strategy?.breakeven)}</td><td style={cell}>{strategy && strategy.collateral_per_contract > 0 ? money(strategy.collateral_per_contract) : 'Unavailable'}{strategy?.requires_shares && <div>{strategyKey === 'covered_call' ? 'Includes stock at reference spot; shares required' : 'Option debit only; shares required separately'}</div>}</td>
          <td style={cell}>{spread === null ? 'Unavailable' : `${spread.toFixed(1)}%`}</td><td style={cell}>{oi === null || !Number.isInteger(oi) ? 'Unavailable' : oi.toLocaleString()}</td>
          <td style={cell}>{greek(leg?.delta)} / {greek(leg?.gamma)}</td>
        </tr>;
      })}</tbody>
    </table></div>
    <p>One standard contract at the displayed reference prices, before fees, slippage and taxes. Spread sorting uses the rounded value supplied by the API, not its backend unrounded screen. Stock legs use the reference spot, not your cost basis. Covered-call capital includes stock; protective-put capital is the option debit and excludes the separately required shares. Greeks are contract estimates, not combined position exposures or profit probabilities. Quote time/size, deliverables and full event coverage remain unverified. Existing eligibility checks and WAIT entry status still apply.</p>
  </details>;
}
