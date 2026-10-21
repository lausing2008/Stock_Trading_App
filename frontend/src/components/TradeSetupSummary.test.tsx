// @vitest-environment jsdom
import React from 'react';
import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { setupFixture } from './__fixtures__/tradeSetup';
import TradeSetupSummary from './TradeSetupSummary';

afterEach(cleanup);
it('shows explicit entry, stop, two targets, reward/risk, shares, capital and conditions in stock currency', () => {
  render(<TradeSetupSummary setups={[setupFixture()]} currency="HKD" />);
  expect(screen.getByText('Entry reference')).toBeTruthy(); expect(screen.getByText('Stop loss to assess')).toBeTruthy();
  expect(screen.getByText('Take profit 1')).toBeTruthy(); expect(screen.getByText('Take profit 2')).toBeTruthy();
  expect(screen.getByText(/HKD\s+101\.00.*2\.00R/)).toBeTruthy();
  expect(screen.getByText('400')).toBeTruthy(); expect(screen.getByText(/HKD\s+39,600\.00/)).toBeTruthy();
  expect(screen.getByText('Wait for daily-close confirmation.')).toBeTruthy(); expect(screen.getByText('Earnings date unknown.')).toBeTruthy();
});
it('labels unknown original risk and held shares instead of pretending profit-locked stops are risk-free', () => {
  const s = { ...setupFixture(), id: 'holding', title: 'Held-position management', entry: 95, stop: 98, shares_basis: 'held' as const,
    risk_per_share: null, shares: null, capital: null, cash_at_stop: null, risk_budget: null,
    targets: [{ price: 101, label: 'Structural resistance', reward_risk: null }],
    risk_note: 'Original risk is unknown. Gap risk remains.', recovery_levels: [{ price: 94, label: 'Loss reduction, not profit' }] };
  render(<TradeSetupSummary setups={[s]} currency="USD" />);
  expect(screen.getByText('Original entry cost')).toBeTruthy(); expect(screen.getByText('Enter shares held')).toBeTruthy();
  expect(screen.getByText(/R unknown/)).toBeTruthy(); expect(screen.getByText('Original risk is unknown. Gap risk remains.')).toBeTruthy();
  expect(screen.getByText(/Loss reduction, not profit/)).toBeTruthy();
});
it('preserves blocked/stale states and avoids implying an unknown currency is USD', () => {
  const s = { ...setupFixture(), state: 'blocked' as const, reasons: ['Reward-to-risk is too low.'] };
  const v = render(<TradeSetupSummary setups={[s]} currency={null} />);
  expect(screen.getByText('blocked')).toBeTruthy(); expect(screen.getByText('Reward-to-risk is too low.')).toBeTruthy();
  expect(within(screen.getByLabelText('Pullback entry')).getAllByText(/currency unknown/).length).toBeGreaterThan(0);
  v.rerender(<TradeSetupSummary setups={[{ ...s, state: 'stale' }]} currency="USD" />); expect(screen.getByText('stale')).toBeTruthy();
});
it('shows the playbook branches when present and nothing extra for older plans without one', () => {
  const s = { ...setupFixture(), playbook: [
    { case: 'rises' as const, title: 'If it rises', text: 'Do not chase above 99.67.' },
    { case: 'falls' as const, title: 'If it falls', text: 'A close below 98 cancels the setup.' },
    { case: 'sideways' as const, title: 'If it goes sideways', text: 'No trade between the edges.' },
    { case: 'after_entry' as const, title: 'After you are filled', text: 'Move the stop to the entry.' }] };
  render(<TradeSetupSummary setups={[s]} currency="USD" compact />);
  const book = screen.getByRole('region', { name: 'Pullback entry playbook' });
  for (const t of ['If it rises', 'If it falls', 'If it goes sideways', 'After you are filled']) expect(within(book).getByText(t)).toBeTruthy();
  expect(within(book).getByText('A close below 98 cancels the setup.')).toBeTruthy();
  cleanup();
  render(<TradeSetupSummary setups={[setupFixture()]} currency="USD" compact />);
  expect(screen.queryByRole('region', { name: 'Pullback entry playbook' })).toBeNull();
});
