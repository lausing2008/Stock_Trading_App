// @vitest-environment jsdom
import React from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import type { OptionStrategy, OptionStrategyMatrix } from '@/lib/api';
import OptionStrategyMatrixPanel from './OptionStrategyMatrix';
import { api } from '@/lib/api';

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2026-01-02T17:00:00Z')); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });
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

const searchContext = { symbol: 'EXAMPLE', horizon: 'SWING' };
function searchPanel(m = matrix()) {
  return <OptionStrategyMatrixPanel matrix={m} spot={100} takeProfit={120} chainAsOf="2026-01-02" researchContext={searchContext} />;
}
function searchResult(m = matrix()) {
  return { symbol: 'EXAMPLE', available: true, current_price: 100, chain_as_of: '2026-01-02',
    strategy_matrix: { ...m, candidate_review: { covered_call: { ...m.candidate_review!.covered_call!,
      method_version: 'covered_call_candidates_v2', holding_days: 14, buffer_days: 2,
      searched_expiries: ['2026-02-06', '2026-03-06'], completed_expiries: ['2026-03-06'],
      failed_expiries: ['2026-02-06'], eligible_expiry_count: 8,
      alternatives: [{ ...alternative, legs: [{ ...alternative.legs[0], expiry: '2026-03-06', option_symbol: 'LATER-CALL' }] }] } } } } as Awaited<ReturnType<typeof api.getOptionsGamePlan>>;
}

it('searches only on request and keeps partial results explicit without selecting them', async () => {
  const get = vi.spyOn(api, 'getOptionsGamePlan').mockResolvedValue(searchResult());
  render(searchPanel());
  expect(get).not.toHaveBeenCalled();
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Find expirations for holding period' })));
  expect(get).toHaveBeenCalledTimes(1);
  expect(get).toHaveBeenCalledWith('EXAMPLE', { stopLoss: undefined, takeProfit: 120, holdingDays: 14 });
  expect(screen.getByRole('alert').textContent).toContain('Expiration reads failed: 2026-02-06');
  expect(document.body.textContent).toContain('1 of 2 attempted expirations read; 8 eligible');
  const select = screen.getByLabelText('Covered-call contract to compare');
  expect((select as HTMLSelectElement).value).toBe('original');
  fireEvent.change(select, { target: { value: '0' } });
  expect(screen.getByText(/Comparing an explicitly selected alternative/).textContent).toContain('Stock target remains $120.00');
  fireEvent.change(screen.getByLabelText('Planned holding period (calendar days)'), { target: { value: '90' } });
  expect(screen.queryByText(/Comparing an explicitly selected alternative/)).toBeNull();
  expect(document.body.textContent).toContain('other expirations are not searched');
});

it.each(['failed', 'different-session', 'different-spot'])('retains the original comparison on %s searches', async kind => {
  const result = searchResult();
  if (kind === 'different-session') result.chain_as_of = '2026-01-01';
  if (kind === 'different-spot') result.current_price = 101;
  const get = vi.spyOn(api, 'getOptionsGamePlan');
  if (kind === 'failed') get.mockRejectedValue(new Error('Search unavailable'));
  else get.mockResolvedValue(result);
  render(searchPanel());
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Find expirations for holding period' })));
  expect(screen.getByRole('alert').textContent).toContain('Original comparison remains available');
  expect(document.body.textContent).toContain('other expirations are not searched');
});

it('discards a pending search after holding context changes', async () => {
  let resolve!: (value: Awaited<ReturnType<typeof api.getOptionsGamePlan>>) => void;
  vi.spyOn(api, 'getOptionsGamePlan').mockReturnValue(new Promise(r => { resolve = r; }));
  render(searchPanel());
  fireEvent.click(screen.getByRole('button', { name: 'Find expirations for holding period' }));
  expect((screen.getByRole('button', { name: 'Searching expirations…' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('Planned holding period (calendar days)'), { target: { value: '90' } });
  await act(async () => resolve(searchResult()));
  expect(document.body.textContent).toContain('other expirations are not searched');
  fireEvent.change(screen.getByLabelText('Planned holding period (calendar days)'), { target: { value: '0' } });
  expect((screen.getByRole('button', { name: 'Find expirations for holding period' }) as HTMLButtonElement).disabled).toBe(true);
});

function fourStrategyResult() {
  const result = searchResult();
  const reviews = result.strategy_matrix!.candidate_review!;
  for (const key of ['long_call', 'protective_put', 'cash_secured_put'] as const) {
    const isCall = key === 'long_call';
    const buying = key !== 'cash_secured_put';
    const strategy = { ...alternative, name: key, net: buying ? 'debit' as const : 'credit' as const,
      requires_shares: key === 'protective_put',
      legs: [{ ...alternative.legs[0], action: buying ? 'buy' as const : 'sell' as const,
        right: isCall ? 'call' as const : 'put' as const, expiry: '2026-03-06', option_symbol: key }] };
    reviews[key] = { ...reviews.covered_call!, method_version: 'single_leg_candidates_v1', strategy_key: key,
      failed_expiries: [], completed_expiries: ['2026-02-06', '2026-03-06'], alternatives: [strategy] };
  }
  return result;
}

it('compares all other single-leg strategies explicitly with one request and clears assignment acceptance', async () => {
  const get = vi.spyOn(api, 'getOptionsGamePlan').mockResolvedValue(fourStrategyResult());
  render(searchPanel());
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Find expirations for holding period' })));
  for (const [key, name] of [['long_call', 'Long Call'], ['protective_put', 'Protective Put'], ['cash_secured_put', 'Cash-Secured Put']]) {
    fireEvent.change(screen.getByLabelText('Strategy for contract comparison'), { target: { value: key } });
    const select = screen.getByLabelText(`${name} contract to compare`) as HTMLSelectElement;
    expect(select.value).toBe('original');
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.change(select, { target: { value: '0' } });
    expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(false);
    expect(screen.getByText(/Selected comparisons:/).textContent).toContain(name);
    expect(screen.getByText(/Comparing an explicitly selected alternative/).textContent).toContain('Stock target remains $120.00');
  }
  expect(get).toHaveBeenCalledTimes(1);
  const selections = screen.getByText(/Selected comparisons:/).textContent;
  expect(selections).toContain('Long Call');
  expect(selections).toContain('Protective Put');
  expect(selections).toContain('Cash-Secured Put');
  fireEvent.change(screen.getByLabelText('Planned holding period (calendar days)'), { target: { value: '60' } });
  expect(screen.queryByText(/Selected comparisons:/)).toBeNull();
});

it('expires pending search context when the New York date rolls over', async () => {
  let resolve!: (value: Awaited<ReturnType<typeof api.getOptionsGamePlan>>) => void;
  vi.spyOn(api, 'getOptionsGamePlan').mockReturnValue(new Promise(r => { resolve = r; }));
  render(searchPanel());
  fireEvent.click(screen.getByRole('button', { name: 'Find expirations for holding period' }));
  vi.setSystemTime(new Date('2026-01-03T05:00:01Z'));
  act(() => vi.advanceTimersByTime(30_000));
  await act(async () => resolve(fourStrategyResult()));
  expect(document.body.textContent).toContain('other expirations are not searched');
});
