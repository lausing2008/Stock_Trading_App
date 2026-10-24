// @vitest-environment jsdom
import React from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import type { OptionStrategy } from '@/lib/api';
import OptionCandidateComparison from './OptionCandidateComparison';
afterEach(cleanup);
function candidate(id: string, spread = 10): OptionStrategy {
  return { name: 'Covered Call', net: 'credit', net_per_contract: 200, max_loss_per_contract: 9800, max_profit_per_contract: 1200,
    breakeven: 98, collateral_per_contract: 10000, requires_shares: true,
    legs: [{ action: 'sell', right: 'call', strike: 110, expiry: '2026-02-06', option_symbol: id, delta: .3, gamma: .02,
      spread_pct: spread, oi: 0, days_to_expiry: 999, price_per_share: 2, cost_per_contract: 200 }] } as OptionStrategy;
}
function open() { fireEvent.click(screen.getByText(/Compare candidate costs, risk and liquidity/)); }
it('pins an unpriced original reference and never invents prices or identity', () => {
  render(<OptionCandidateComparison originalStrike={120} originalExpiry="2026-02-06" originalSymbol="ORIGINAL" alternatives={[candidate('ALT')]}
    strategyKey="covered_call" today="2026-01-02" selectedIndex={null} onSelect={vi.fn()} />);
  open();
  const rows = within(screen.getByRole('table', { name: 'Returned option candidate comparison' })).getAllByRole('row');
  expect(rows[1].textContent).toContain('Original plan');
  expect(rows[1].textContent).toContain('ORIGINAL');
  expect(rows[1].textContent).toContain('$120.00');
  expect(rows[1].textContent).toContain('Payoff unavailable');
  expect(rows[1].textContent).not.toContain('$0.00');
  expect(rows[2].textContent).toContain('35 calendar days');
  expect(rows[2].textContent).toContain('Includes stock at reference spot');
});
it('selects the original source index after sorting and preserves the explicit selection', () => {
  const choose = vi.fn();
  const props = { original: candidate('ORIGINAL'), originalStrike: 110, originalExpiry: '2026-02-06', alternatives: [candidate('WIDE', 12), candidate('NARROW', 2)], strategyKey: 'covered_call' as const, today: '2026-01-02', selectedIndex: 0, onSelect: choose };
  render(<OptionCandidateComparison {...props} />); open();
  fireEvent.change(screen.getByLabelText('Sort returned alternatives'), { target: { value: 'spread' } });
  expect(choose).not.toHaveBeenCalled();
  const rows = within(screen.getByRole('table')).getAllByRole('row');
  expect(rows[1].textContent).toContain('Original plan');
  expect(rows[2].textContent).toContain('NARROW');
  expect(rows[3].getAttribute('aria-selected')).toBe('true');
  fireEvent.click(screen.getByRole('button', { name: 'Compare alternative 2' }));
  expect(choose).toHaveBeenCalledWith(1);
  fireEvent.click(screen.getByRole('button', { name: 'Compare original plan' }));
  expect(choose).toHaveBeenLastCalledWith(null);
});
it('shows unbounded profit only for the relevant strategies and unknown measurements remain unavailable', () => {
  const value = { ...candidate('PUT'), max_profit_per_contract: null, requires_shares: true, legs: [{ ...candidate('PUT').legs[0], action: 'buy' as const, right: 'put', delta: null, gamma: null, oi: null, spread_pct: null }] };
  const { rerender } = render(<OptionCandidateComparison originalStrike={null} originalExpiry={null} alternatives={[value]} strategyKey="protective_put" today="2026-01-02" selectedIndex={null} onSelect={vi.fn()} />);
  open();
  expect(screen.getByText('Unbounded')).toBeTruthy();
  expect(screen.getByText('Option debit only; shares required separately')).toBeTruthy();
  expect(within(screen.getByRole('table')).getAllByRole('row')[2].textContent).toContain('Unavailable / Unavailable');
  rerender(<OptionCandidateComparison originalStrike={null} originalExpiry={null} alternatives={[value]} strategyKey="cash_secured_put" today="2026-01-02" selectedIndex={null} onSelect={vi.fn()} />);
  expect(screen.queryByText('Unbounded')).toBeNull();
  rerender(<OptionCandidateComparison originalStrike={null} originalExpiry={null} alternatives={[value]} strategyKey="long_call" today="2026-01-02" selectedIndex={null} onSelect={vi.fn()} />);
  expect(screen.queryByText('Unbounded')).toBeNull();
});
