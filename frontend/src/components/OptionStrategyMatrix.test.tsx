// @vitest-environment jsdom
import React from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import type { OptionStrategy, OptionStrategyMatrix } from '@/lib/api';
import OptionStrategyMatrixPanel from './OptionStrategyMatrix';

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2026-01-02T17:00:00Z')); });
afterEach(() => { cleanup(); vi.useRealTimers(); });
const alternative: OptionStrategy = {
  name: 'Covered Call', direction: 'neutral-to-mildly-bullish', net: 'credit', requires_shares: true,
  legs: [{ action: 'sell', right: 'call', strike: 110, price_per_share: 2.1, cost_per_contract: 210,
    expiry: '2026-02-06', days_to_expiry: 35, price_source: 'quote_mid', spread_pct: 9.5,
    oi: 100, delta: .3, gamma: .02, theta: null, vega: null, option_symbol: 'EXAMPLE-CALL' }],
  net_per_share: 2.1, net_per_contract: 210, max_loss_per_contract: 9790,
  max_profit_per_contract: 1210, breakeven: 97.9, breakeven_move_pct: -2.1,
  collateral_per_contract: 10000, what_it_does: 'Caps the stock upside at 110.', use_when: 'Accept assignment.',
};
function matrix(): OptionStrategyMatrix {
  return { singles: {}, combos: {}, iv_rank: null, iv_regime: 'unknown', recommendation: { primary: null, reason: 'Wait' },
    candidate_review: { covered_call: { method_version: 'covered_call_candidates_v1', anchor: 120, expiry: '2026-02-06',
      scope: 'all_rows_of_selected_call_expiry', evaluated_count: 3, screened_count: 2,
      rejected_counts: { two_sided_quote_required: 1 }, original: { strike: 120, option_symbol: 'EXAMPLE-TARGET', reason: 'two_sided_quote_required' },
      ranking: 'Distance to target; not expected return.', alternatives: [alternative], actionability: 'research_only' } } };
}
const panel = (m = matrix(), asOf = '2026-01-02') => <OptionStrategyMatrixPanel matrix={m} spot={100} stopLoss={95} takeProfit={120} chainAsOf={asOf} />;

it('explains the rejected target and scope without silently selecting a different strike', () => {
  render(panel());
  const selection = screen.getByRole('region', { name: 'Covered call contract selection' });
  expect(selection.textContent).toContain('selected strike $120.00');
  expect(selection.textContent).toContain('missing a usable two-sided quote');
  expect(selection.textContent).toContain('2 of 3 rows');
  expect(selection.textContent).toContain('other expirations are not searched');
  expect((screen.getByLabelText('Covered-call contract to compare') as HTMLSelectElement).value).toBe('original');
  expect(screen.queryByText(/Comparing an explicitly selected alternative/)).toBeNull();
  expect(document.body.textContent).toContain('No usable priced contract at the selected strike');
});

it('requires renewed assignment acceptance after explicitly choosing a comparison', () => {
  render(panel());
  fireEvent.change(screen.getByLabelText('Objective'), { target: { value: 'income' } });
  fireEvent.change(screen.getByLabelText('Unencumbered shares'), { target: { value: '100' } });
  const accept = screen.getByRole('checkbox') as HTMLInputElement;
  fireEvent.click(accept);
  expect(accept.checked).toBe(true);
  fireEvent.change(screen.getByLabelText('Covered-call contract to compare'), { target: { value: '0' } });
  expect(accept.checked).toBe(false);
  expect(screen.queryByText('Preferred strategy to research: Covered Call')).toBeNull();
  expect(screen.getByText(/Stock target remains/).textContent).toContain('$120.00');
  expect(screen.getByText(/Stock target remains/).textContent).toContain('$110.00');
  expect(screen.getByText('$97.90')).toBeTruthy();
  expect(document.body.textContent).toContain('Delta 0.3000');
  expect(document.body.textContent).toContain('Gamma 0.0200');
  expect(document.body.textContent).toContain('Theta unavailable');
  fireEvent.click(accept);
  expect(screen.getByText('Preferred strategy to research: Covered Call')).toBeTruthy();
  expect(document.body.textContent).toContain('Entry status: WAIT');
});

it('discards an alternative and its assignment acknowledgement when a fresh response replaces the matrix', () => {
  const { rerender } = render(panel());
  fireEvent.change(screen.getByLabelText('Covered-call contract to compare'), { target: { value: '0' } });
  fireEvent.click(screen.getByRole('checkbox'));
  rerender(panel(matrix()));
  expect((screen.getByLabelText('Covered-call contract to compare') as HTMLSelectElement).value).toBe('original');
  expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(false);
});

it('does not turn a chosen alternative with stale data into a preferred strategy', () => {
  render(panel(matrix(), '2025-12-01'));
  fireEvent.change(screen.getByLabelText('Covered-call contract to compare'), { target: { value: '0' } });
  fireEvent.change(screen.getByLabelText('Objective'), { target: { value: 'income' } });
  fireEvent.change(screen.getByLabelText('Unencumbered shares'), { target: { value: '100' } });
  fireEvent.click(screen.getByRole('checkbox'));
  expect(screen.queryByText('Preferred strategy to research: Covered Call')).toBeNull();
  expect(document.body.textContent).toContain('more than seven calendar days old');
});

it('explains an empty screen and remains compatible with older responses', () => {
  const m = matrix(); m.candidate_review!.covered_call!.alternatives = [];
  const { rerender } = render(panel(m));
  expect(screen.getByText(/No other contract passed this screen/)).toBeTruthy();
  delete m.candidate_review;
  rerender(panel({ ...m }));
  expect(screen.queryByRole('region', { name: 'Covered call contract selection' })).toBeNull();
  expect(screen.getByText('Wait / no strategy preference yet')).toBeTruthy();
});


it('ages recorded context while the tab stays open without any request', () => {
  render(<OptionStrategyMatrixPanel matrix={matrix()} spot={100} takeProfit={120} chainAsOf="2026-01-02"
    researchContext={{ symbol: 'EXAMPLE', horizon: 'SWING', signal: { symbol: 'EXAMPLE', horizon: 'SWING', signal: 'BUY',
      ts: '2026-01-02T16:00:00Z', reasons: {}, confidence: 99, bullish_probability: .99 } }} />);
  const evidence = screen.getByRole('region', { name: 'Horizon and market context evidence' });
  expect(evidence.textContent).toContain('Record within 72-hour limit');
  vi.setSystemTime(new Date('2026-01-06T17:00:00Z'));
  act(() => vi.advanceTimersByTime(30_000));
  expect(evidence.textContent).toContain('Record older than 72 hours');
  expect(evidence.textContent).toContain('Market-aware preference unavailable');
});
