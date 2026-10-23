// Review 2026-10-23 (audit R05 follow-up): settlement and valuation caveats for the options-income page.
import React from 'react';
import type { OptionsIncomePortfolioListItem, OptionsIncomePosition } from '@/lib/api';

/** Review 2026-10-23: equity and return are not fully settled, fully quoted figures when any mark
 * is approximate — especially a mark on a contract whose terms are unsupported — or when an
 * expired position's settlement is unresolved. Said on the summary, not only in a tooltip. */
export function IncomeCaveats({ p }: { p: OptionsIncomePortfolioListItem }) {
  const notes: string[] = [];
  if (p.settlement_unresolved) notes.push(`${p.settlement_unresolved} expired position${p.settlement_unresolved === 1 ? '' : 's'} with settlement unresolved (unsupported contract terms) — return includes only their approximate mark, not a settled outcome`);
  if (p.equity_unsupported_contract_marks) notes.push(`equity includes ${p.equity_unsupported_contract_marks} approximate mark${p.equity_unsupported_contract_marks === 1 ? '' : 's'} on unsupported contract terms`);
  else if (p.equity_is_approximate) notes.push('equity includes approximate marks');
  if (!notes.length) return null;
  return <div role="note" aria-label="Equity caveats" style={{ fontSize: 11, color: '#f59e0b', marginTop: 4 }}>≈ {notes.join(' · ')}</div>;
}

export function EquityCurveCaveat({ points }: { points: { is_approximate?: boolean; unsupported_contract_marks?: number }[] }) {
  const approx = points.filter(pt => pt.is_approximate).length;
  const unsupported = points.filter(pt => (pt.unsupported_contract_marks ?? 0) > 0).length;
  if (!approx) return null;
  return (
    <div role="note" aria-label="Equity curve caveats" style={{ fontSize: 11, color: '#f59e0b', marginTop: 4 }}>
      {approx} of {points.length} points rest on approximate marks{unsupported ? `, ${unsupported} on contracts with unsupported terms` : ''}.
    </div>
  );
}

export function SettlementBadge({ p }: { p: OptionsIncomePosition }) {
  if (p.settlement_state !== 'settlement_unresolved' && p.settlement_state !== 'settlement_pending') return null;
  const unresolved = p.settlement_state === 'settlement_unresolved';
  return (
    <span title={p.settlement_reason ?? undefined} aria-label={unresolved ? 'Settlement unresolved' : 'Settlement pending'}
      style={{ marginLeft: 6, fontSize: 9, fontWeight: 800, padding: '1px 5px', borderRadius: 3, cursor: 'help',
        color: unresolved ? '#f87171' : '#f59e0b', border: `1px solid ${unresolved ? 'rgba(248,113,113,0.4)' : 'rgba(245,158,11,0.4)'}` }}>
      {unresolved ? 'SETTLEMENT UNRESOLVED' : 'SETTLEMENT PENDING'}
    </span>
  );
}

