// @vitest-environment jsdom
import React from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { OptionsGamePlan, OptionStrategy } from '@/lib/api';
const mocks = vi.hoisted(() => ({ getOptionsGamePlan: vi.fn() }));
vi.mock('@/lib/api', () => ({ api: mocks }));
import OptionsGamePlanCard from './OptionsGamePlanCard';
import { COMMON_OPTION_KEYS, COMMON_OPTION_NAMES } from '@/lib/commonOptionPlans';
const props = { symbol: 'AAPL', currentPrice: 100, stopLoss: undefined, takeProfit: undefined, signal: 'BUY' };
function packet(symbol = 'AAPL'): OptionsGamePlan {
  const singles = Object.fromEntries(COMMON_OPTION_KEYS.map(key => {
    const sell = key === 'covered_call' || key === 'cash_secured_put';
    const s: OptionStrategy = { name: COMMON_OPTION_NAMES[key], direction: 'bullish', net: sell ? 'credit' : 'debit',
      legs: [{ action: sell ? 'sell' : 'buy', right: key === 'long_call' || key === 'covered_call' ? 'call' : 'put', strike: 100, price_per_share: 2, cost_per_contract: 200, expiry: '2099-12-20', days_to_expiry: 30, spread_pct: 10, price_source: 'quote_mid' }],
      net_per_share: 2, net_per_contract: 200, max_loss_per_contract: 200, max_profit_per_contract: sell ? 200 : null, breakeven: 102, breakeven_move_pct: 2,
      requires_shares: key === 'covered_call' || key === 'protective_put', collateral_per_contract: 10000, what_it_does: 'Example structure', use_when: 'When it fits' };
    return [key, s];
  }));
  return { symbol, available: true, current_price: 100, chain_as_of: CHAIN_AS_OF, protective_put: null, covered_call: null,
    strategy_matrix: { singles, combos: {}, recommendation: { primary: null, reason: '' }, iv_rank: null, iv_regime: 'unknown' } };
}
// Fixed clock. The card compares the chain date with today's New York date; a fixture built from
// `new Date().toISOString()` (a UTC date) read as "in the future" from 20:00 ET to midnight and the
// preference test failed every evening. 23:30 ET is that window, so it stays covered.
const NOW = new Date('2026-10-22T03:30:00Z');            // 2026-10-21 23:30 America/New_York
const CHAIN_AS_OF = '2026-10-21T20:05:00Z';               // settled chain observed the same ET day
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] }); vi.setSystemTime(NOW);   // only Date: async findBy* timers stay real
  mocks.getOptionsGamePlan.mockReset(); mocks.getOptionsGamePlan.mockResolvedValue(packet());
});
afterEach(() => { cleanup(); vi.useRealTimers(); });
it('fetches and shows all four plans even without a stock stop or target', async () => {
  render(<OptionsGamePlanCard {...props} />);
  await screen.findByRole('region', { name: 'Four common option strategies' });
  expect(mocks.getOptionsGamePlan).toHaveBeenCalledWith('AAPL', { stopLoss: undefined, takeProfit: undefined });
  for (const key of COMMON_OPTION_KEYS) expect(screen.getByText(COMMON_OPTION_NAMES[key], { exact: true })).toBeTruthy();
  expect(screen.getAllByText('Playbook — before entry, while open & exit')).toHaveLength(4);
  fireEvent.change(screen.getByLabelText('Objective'), { target: { value: 'upside' } });
  fireEvent.change(screen.getByLabelText('Option premium-loss budget ($)'), { target: { value: '400' } });
  await screen.findByText('Preferred strategy to research: Long Call');
  expect(screen.getByText(/Entry status: WAIT/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Objective'), { target: { value: 'bearish' } });
  expect(screen.queryByText('Preferred strategy to research: Long Call')).toBeNull();
});
it('shows four unavailable cards and playbooks when the feed has no priced plan', async () => {
  mocks.getOptionsGamePlan.mockResolvedValue({ symbol: 'AAPL', available: false, reason: 'options_feed_unavailable' });
  render(<OptionsGamePlanCard {...props} />); await screen.findByText('Options Game Plan — data unavailable');
  for (const key of COMMON_OPTION_KEYS) expect(screen.getByRole('heading', { name: COMMON_OPTION_NAMES[key] })).toBeTruthy();
  expect(screen.getAllByText('Playbook — before entry, while open & exit')).toHaveLength(4);
});
it('ignores a late response for a previous symbol', async () => {
  let resolve!: (p: OptionsGamePlan) => void;
  mocks.getOptionsGamePlan.mockReturnValueOnce(new Promise(r => { resolve = r; }));
  mocks.getOptionsGamePlan.mockResolvedValueOnce(packet('MSFT'));
  const view = render(<OptionsGamePlanCard {...props} />);
  view.rerender(<OptionsGamePlanCard {...props} symbol="MSFT" />);
  await screen.findByRole('region', { name: 'Four common option strategies' });
  await act(async () => { resolve(packet()); });
  expect(screen.getByRole('region', { name: 'Four common option strategies' })).toBeTruthy();
});

it('qualifies a preference with settled-price provenance and refuses last-trade-only candidates', async () => {
  const data = packet();
  for (const strategy of Object.values(data.strategy_matrix!.singles)) {
    if (strategy) strategy.legs[0].price_source = 'last_trade';
  }
  mocks.getOptionsGamePlan.mockResolvedValue(data);
  render(<OptionsGamePlanCard {...props} />);
  await screen.findByRole('region', { name: 'Four common option strategies' });
  fireEvent.change(screen.getByLabelText('Objective'), { target: { value: 'upside' } });
  fireEvent.change(screen.getByLabelText('Option premium-loss budget ($)'), { target: { value: '400' } });
  expect(screen.getByText(/research illustrations using settled provider prices/)).toBeTruthy();
  expect(screen.getByText(/Entry status: WAIT for fresh quotes/)).toBeTruthy();
  expect(screen.getByText('Wait / no strategy preference yet')).toBeTruthy();
  expect(screen.queryByText('Preferred strategy to research: Long Call')).toBeNull();
});
it('keeps unusable-quote reasons visible when both legacy contracts are absent', async () => {
  mocks.getOptionsGamePlan.mockResolvedValue({ symbol: 'AAPL', available: true, current_price: 100,
    protective_put: null, covered_call: null,
    protective_put_unavailable: 'crossed quote', covered_call_unavailable: 'one-sided quote' });
  render(<OptionsGamePlanCard {...props} />);
  const warning = await screen.findByText(/quote unusable/);
  expect(warning.getAttribute('title')).toContain('crossed quote');
  expect(warning.getAttribute('title')).toContain('one-sided quote');
  expect(screen.getByText('Options Game Plan', { exact: true })).toBeTruthy();
});
