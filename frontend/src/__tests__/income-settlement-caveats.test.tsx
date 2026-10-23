// @vitest-environment jsdom
// Review 2026-10-23: an expired position on an unsupported contract shows "settlement unresolved",
// and approximate valuation stays identifiable in portfolio equity and the equity curve.
import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { IncomeCaveats, EquityCurveCaveat, SettlementBadge } from '@/components/IncomeCaveats';
import type { OptionsIncomePortfolioListItem, OptionsIncomePosition } from '@/lib/api';

afterEach(cleanup);

const portfolio = (over: Partial<OptionsIncomePortfolioListItem> = {}) => ({ id: 1, name: 'Income', initial_capital: 50000,
  current_cash: 40000, current_equity: 51000, total_return_pct: 2, open_positions: 3, closed_positions: 7, win_rate_pct: 80,
  assignment_rate_pct: 10, total_premium_collected: 900, is_active: true, config: {}, created_at: null, ...over }) as OptionsIncomePortfolioListItem;
const position = (over: Partial<OptionsIncomePosition>) => ({ id: 1, symbol: 'AMD', strategy: 'CASH_SECURED_PUT', option_symbol: 'AMD1261016P00100000',
  strike: 100, expiry: '2026-10-16', contracts: 1, entry_date: '2026-09-20', underlying_entry_price: 110, delta_at_entry: -0.3,
  premium_per_contract: 150, total_premium_collected: 150, collateral_reserved: 10000, stage: 'open', close_date: null,
  underlying_close_price: null, assigned: null, pnl: null, pct_return_on_collateral: null, close_reason: null,
  live_price: null, is_itm: null, cushion_pct: null, days_to_expiry: -7, ...over }) as OptionsIncomePosition;

describe('settlement state', () => {
  it('an expired unsupported position is visibly unresolved, with its reason', () => {
    render(<SettlementBadge p={position({ settlement_state: 'settlement_unresolved',
      settlement_reason: 'expired; settlement not computed — nonstandard_root_deliverable_unknown: adjusted contract' })} />);
    const badge = screen.getByLabelText('Settlement unresolved');
    expect(badge.textContent).toBe('SETTLEMENT UNRESOLVED');
    expect(badge.getAttribute('title')).toContain('adjusted contract');
  });

  it('an ordinary open position shows no badge', () => {
    const { container } = render(<SettlementBadge p={position({ settlement_state: 'open' })} />);
    expect(container.textContent).toBe('');
  });
});

describe('equity and performance caveats', () => {
  it('the portfolio summary names unresolved settlements and unsupported-contract marks', () => {
    render(<IncomeCaveats p={portfolio({ settlement_unresolved: 1, equity_is_approximate: true, equity_unsupported_contract_marks: 1 })} />);
    const note = screen.getByLabelText('Equity caveats').textContent ?? '';
    expect(note).toContain('1 expired position with settlement unresolved (unsupported contract terms) — return includes only their approximate mark, not a settled outcome');
    expect(note).toContain('equity includes 1 approximate mark on unsupported contract terms');
  });

  it('other approximate marks are still flagged, without claiming unsupported terms', () => {
    render(<IncomeCaveats p={portfolio({ equity_is_approximate: true, equity_unsupported_contract_marks: 0 })} />);
    expect(screen.getByLabelText('Equity caveats').textContent).toBe('≈ equity includes approximate marks');
  });

  it('a fully quoted, fully settled portfolio shows nothing', () => {
    const { container } = render(<IncomeCaveats p={portfolio()} />);
    expect(container.textContent).toBe('');
  });

  it('the equity curve says how many points rest on approximate and unsupported marks', () => {
    render(<EquityCurveCaveat points={[{ is_approximate: false }, { is_approximate: true, unsupported_contract_marks: 0 },
      { is_approximate: true, unsupported_contract_marks: 1 }]} />);
    expect(screen.getByLabelText('Equity curve caveats').textContent)
      .toBe('2 of 3 points rest on approximate marks, 1 on contracts with unsupported terms.');
  });
});
