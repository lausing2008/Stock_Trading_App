// @vitest-environment jsdom
import React from 'react';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
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
  for (const key of COMMON_OPTION_KEYS) expect(within(document.querySelector('.common-option-strategy-grid') as HTMLElement).getByText(COMMON_OPTION_NAMES[key], { exact: true })).toBeTruthy();
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

it('binds holding-period/expiry checks to the selected horizon without refetching the chain', async () => {
  const p = packet();
  for (const s of Object.values(p.strategy_matrix!.singles)) {
    s.legs[0].expiry = '2026-10-30'; s.legs[0].days_to_expiry = 999;
  }
  mocks.getOptionsGamePlan.mockResolvedValue(p);
  const source = { symbol: 'AAPL', horizon: 'SHORT', signal: 'BUY' as const, confidence: 99, bullish_probability: .99,
    ts: '2026-10-22T03:00:00Z', reasons: { market_regime: 'bear', eps_revision_direction: 1 } };
  const { rerender } = render(<OptionsGamePlanCard {...props} researchContext={{ symbol: 'AAPL', horizon: 'SHORT', signal: source }} />);
  await screen.findByRole('region', { name: 'Horizon and market context evidence' });
  expect((screen.getByLabelText('Planned holding period (calendar days)') as HTMLInputElement).value).toBe('7');
  fireEvent.change(screen.getByLabelText('Objective'), { target: { value: 'upside' } });
  fireEvent.change(screen.getByLabelText('Option premium-loss budget ($)'), { target: { value: '400' } });
  expect(screen.getByText('Goal-fit only: Long Call')).toBeTruthy();
  expect(screen.getByText(/Market-aware preference unavailable/)).toBeTruthy();
  expect(screen.getByText(/Recorded signal and regime conflict/)).toBeTruthy();
  rerender(<OptionsGamePlanCard {...props} researchContext={{ symbol: 'AAPL', horizon: 'SWING', signal: { ...source, horizon: 'SWING' } }} />);
  expect((screen.getByLabelText('Planned holding period (calendar days)') as HTMLInputElement).value).toBe('14');
  expect(screen.queryByText('Goal-fit only: Long Call')).toBeNull();
  expect(screen.getAllByText(/Expiry does not cover/).length).toBeGreaterThan(0);
  expect(mocks.getOptionsGamePlan).toHaveBeenCalledTimes(1);
  fireEvent.change(screen.getByLabelText('Planned holding period (calendar days)'), { target: { value: '3' } });
  expect(screen.getByText('Goal-fit only: Long Call')).toBeTruthy();
  rerender(<OptionsGamePlanCard {...props} researchContext={{ symbol: 'AAPL', horizon: 'SHORT', signal: source }} />);
  expect((screen.getByLabelText('Planned holding period (calendar days)') as HTMLInputElement).value).toBe('7');
});

it('uses the selected horizon default and hides mismatched signal reasons', async () => {
  const signal = { symbol: 'OTHER', horizon: 'LONG', signal: 'BUY' as const, confidence: 99, bullish_probability: .99,
    ts: '2026-10-22T03:00:00Z', reasons: { market_regime: 'bull' } };
  render(<OptionsGamePlanCard {...props} researchContext={{ symbol: 'AAPL', horizon: 'LONG', signal }} />);
  await screen.findByRole('region', { name: 'Horizon and market context evidence' });
  expect(screen.getAllByText(/Wrong symbol or horizon/).length).toBeGreaterThan(0);
  expect((screen.getByLabelText('Planned holding period (calendar days)') as HTMLInputElement).value).toBe('28');
  expect(screen.queryByRole('cell', { name: 'bull' })).toBeNull();
});

it('keeps a stable archived basis searchable when the original preset contracts cannot be priced', async () => {
  const initial = packet();
  const covered = initial.strategy_matrix!.singles.covered_call;
  initial.available = false;
  initial.strategy_matrix!.singles = {};
  initial.strategy_matrix!.candidate_review = { covered_call: {
    method_version: 'covered_call_candidates_v1', anchor: 100, expiry: '2099-12-20',
    scope: 'all_rows_of_selected_call_expiry', evaluated_count: 1, screened_count: 0,
    rejected_counts: { two_sided_quote_required: 1 }, original: { strike: 100, option_symbol: null, reason: 'two_sided_quote_required' },
    ranking: 'Research only', alternatives: [], actionability: 'research_only',
  } };
  const searched = packet();
  searched.strategy_matrix!.candidate_review = { covered_call: { ...initial.strategy_matrix!.candidate_review.covered_call!,
    method_version: 'covered_call_candidates_v2', holding_days: 14, buffer_days: 2,
    searched_expiries: ['2099-12-20'], completed_expiries: ['2099-12-20'], eligible_expiry_count: 1,
    alternatives: [covered],
  } };
  mocks.getOptionsGamePlan.mockResolvedValueOnce(initial).mockResolvedValueOnce(searched);
  render(<OptionsGamePlanCard {...props} researchContext={{ symbol: 'AAPL', horizon: 'SWING' }} />);
  await screen.findByText('Options Game Plan — original preset contracts unavailable');
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Find expirations for holding period' })));
  fireEvent.change(screen.getByLabelText('Covered-call contract to compare'), { target: { value: '0' } });
  expect(screen.getByText(/Comparing an explicitly selected alternative/)).toBeTruthy();
  expect(mocks.getOptionsGamePlan).toHaveBeenLastCalledWith('AAPL', { stopLoss: undefined, takeProfit: undefined, holdingDays: 14 });
});

it('renders a near-spot covered call without inventing a missing stock forecast target', async () => {
  const p = packet();
  delete p.strategy_matrix;
  p.covered_call = { expiry: '2026-11-20', days_to_expiry: 30, in_target_window: true,
    strike: 100, mid_price: 2, credit_pct_of_position: 2, credit_per_contract: 200,
    effective_cap_price: 102, iv: 30, oi: 100, reference_take_profit: null };
  mocks.getOptionsGamePlan.mockResolvedValue(p);
  render(<OptionsGamePlanCard {...props} />);
  expect(await screen.findByText('💰 Covered Call — collect income')).toBeTruthy();
  expect(screen.queryByText(/separate stock forecast/)).toBeNull();
});
