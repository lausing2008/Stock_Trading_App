// @vitest-environment jsdom
// Call Finder card: the six figures with reasons, scenarios, warnings, the glance strip, the
// target control, and honest unavailable states.
import React from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { SWRConfig } from 'swr';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { CallFinderContract, CallFinderResponse } from '@/lib/api';
const mocks = vi.hoisted(() => ({ getOptionsCallFinder: vi.fn() }));
vi.mock('@/lib/api', () => ({ api: mocks }));
import CallFinderCard from './CallFinderCard';

const contract = (over: Partial<CallFinderContract> = {}): CallFinderContract => ({
  option_symbol: 'XYZ261215C00095000', expiry: '2026-12-15', dte: 35, strike: 95, bid: 8.6, ask: 8.95, mid: 8.775,
  spread_pct: 4, cost_per_contract: 895, breakeven: 103.95, breakeven_move_pct: 3.95, delta: 0.69, gamma: 0.025,
  greeks_estimated: false, oi: 1200, iv_pct: 40,
  scenarios: { target: 0.56, half_move: 0.19, flat: -0.14, down_one_sigma: -0.64, target_twice_as_late: 0.4, late_days: 28 },
  model_prob_above_breakeven: 0.38,
  reasons: { expiration: '35 days to run', strike: 'in the money by 5.0%', delta: 'like 69 shares', gamma: 'delta 0.85 after',
    open_interest: '1,200 contracts', iv: '40% IV against 35% realised' },
  ...over,
});
const response = (over: Partial<CallFinderResponse> = {}): CallFinderResponse => ({
  symbol: 'XYZ', available: true, source_session: '2026-11-09', chain_age_days: 1,
  glance: { price: 100, price_basis: 'x', atm_iv_pct: 40, hv20_pct: 35, iv_rank: 55, iv_rank_as_of: '2026-11-09',
    iv_rank_status: 'ok', implied_move_pct: 12.4, implied_move_expiry: '2026-12-15', events: [{ label: 'Earnings', date: '2026-11-20' }] },
  near: { kind: 'near', available: true, expiry: '2026-12-15', expiry_reason: 'nearest with 30+ days', hold_days: 14,
    target: 107.8, target_basis: 'one implied standard deviation up', rule: 'half the move', pick: contract(),
    aggressive_alternative: contract({ strike: 105, option_symbol: 'XYZ261215C00105000', delta: 0.4 }), eligible_count: 9,
    rejected: {}, wide_quote_fallback: false, warnings: ['Earnings on 2026-11-20 falls inside the holding period'] },
  leaps: { kind: 'leaps', available: false, reason: 'no expiry 270+ days out' },
  assumptions: ['Settled end-of-day quotes'],
  ...over,
});
const renderCard = (props: Partial<React.ComponentProps<typeof CallFinderCard>> = {}) =>
  render(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
    <CallFinderCard symbol="XYZ" holdingDays={14} analystTarget={120} {...props} />
  </SWRConfig>);

beforeEach(() => { mocks.getOptionsCallFinder.mockReset(); mocks.getOptionsCallFinder.mockResolvedValue(response()); });
afterEach(cleanup);

it('shows the six figures, each with its reason, and the scenarios', async () => {
  renderCard();
  const near = await screen.findByTestId('call-finder-near');
  for (const name of ['Expiration', 'Strike', 'Delta', 'Gamma', 'Open interest', 'IV']) expect(within(near).getByText(name)).toBeTruthy();
  expect(near.textContent).toContain('2026-12-15 (35d)');
  expect(near.textContent).toContain('like 69 shares');
  expect(within(near).getByLabelText('near scenarios').textContent).toContain('Half the move: +19%');
  expect(near.textContent).toContain('not a prediction');
});

it('shows warnings, the glance strip and an honest reason for a missing pick', async () => {
  renderCard();
  await screen.findByTestId('call-finder-near');
  expect(screen.getByLabelText('Near-term call warnings').textContent).toContain('Earnings');
  const glance = screen.getByLabelText('Options at a glance').textContent!;
  expect(glance).toContain('IV rank 55/100');
  expect(glance).toContain('±12.4% to 2026-12-15');
  expect(screen.getByLabelText('LEAPS call').textContent).toContain('No pick: no expiry 270+ days out.');
});

it('keeps the aggressive alternative behind a button', async () => {
  renderCard();
  await screen.findByTestId('call-finder-near');
  expect(screen.queryByTestId('call-finder-near-aggressive')).toBeNull();
  fireEvent.click(screen.getByText(/Show aggressive alternative/));
  expect(screen.getByTestId('call-finder-near-aggressive').textContent).toContain('$105.00 call');
});

it('requests with the analyst target when chosen, and reports picks to the page', async () => {
  const onPick = vi.fn();
  renderCard({ onPick });
  await screen.findByTestId('call-finder-near');
  expect(mocks.getOptionsCallFinder).toHaveBeenLastCalledWith('XYZ', { holdingDays: 14, targetPrice: null });
  expect(onPick).toHaveBeenCalledWith({ near: expect.objectContaining({ strike: 95 }), leaps: undefined });
  fireEvent.click(screen.getByText(/Use analyst target/));
  await vi.waitFor(() => expect(mocks.getOptionsCallFinder).toHaveBeenLastCalledWith('XYZ', { holdingDays: 14, targetPrice: 120 }));
});

it('says why when the finder is unavailable', async () => {
  mocks.getOptionsCallFinder.mockResolvedValue({ symbol: 'XYZ', available: false, reason: 'price_basis_unverified', detail: 'no daily close stored' });
  renderCard();
  expect((await screen.findByText(/Call Finder unavailable/)).textContent).toContain('price_basis_unverified (no daily close stored)');
});

it('states what the replay found under the near-term pick only', async () => {
  mocks.getOptionsCallFinder.mockResolvedValue(response({
    leaps: { ...response().near!, kind: 'leaps' },
  }));
  renderCard();
  await screen.findByTestId('call-finder-near');
  const notes = screen.getAllByTestId('call-finder-replay-note');
  expect(notes).toHaveLength(1);
  expect(notes[0].textContent).toContain('lower leverage than the aggressive alternative');
  expect(within(screen.getByLabelText('Near-term call')).getByTestId('call-finder-replay-note')).toBeTruthy();
});
