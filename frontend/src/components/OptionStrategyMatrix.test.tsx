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
  const accept = screen.getByRole('checkbox', { name: /I accept buying or selling/ }) as HTMLInputElement;
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
  fireEvent.click(screen.getByRole('checkbox', { name: /I accept buying or selling/ }));
  rerender(panel(matrix()));
  expect((screen.getByLabelText('Covered-call contract to compare') as HTMLSelectElement).value).toBe('original');
  expect((screen.getByRole('checkbox', { name: /I accept buying or selling/ }) as HTMLInputElement).checked).toBe(false);
});

it('does not turn a chosen alternative with stale data into a preferred strategy', () => {
  render(panel(matrix(), '2025-12-01'));
  fireEvent.change(screen.getByLabelText('Covered-call contract to compare'), { target: { value: '0' } });
  fireEvent.change(screen.getByLabelText('Objective'), { target: { value: 'income' } });
  fireEvent.change(screen.getByLabelText('Unencumbered shares'), { target: { value: '100' } });
  fireEvent.click(screen.getByRole('checkbox', { name: /I accept buying or selling/ }));
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
    fireEvent.click(screen.getByRole('checkbox', { name: /I accept buying or selling/ }));
    fireEvent.change(select, { target: { value: '0' } });
    expect((screen.getByRole('checkbox', { name: /I accept buying or selling/ }) as HTMLInputElement).checked).toBe(false);
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

function eventPanel(m: OptionStrategyMatrix, retrievedAt: string | null, earningsDate: string | null, exDividendDate = '2026-01-20') {
  return <OptionStrategyMatrixPanel matrix={m} symbol="EXAMPLE" spot={100} chainAsOf="2026-01-02"
    researchContext={searchContext} calendarContext={{ symbol: 'EXAMPLE', retrievedAt, earningsDate, exDividendDate }} />;
}
function eventMatrix() {
  const m = matrix();
  m.singles.long_call = { ...alternative, name: 'Long Call', net: 'debit', requires_shares: false,
    max_loss_per_contract: 210, max_profit_per_contract: null, collateral_per_contract: 210,
    legs: [{ ...alternative.legs[0], action: 'buy' }] };
  return m;
}
it('keeps the earnings filter optional and blocks events before expiry even beyond the planned hold', () => {
  const m = eventMatrix();
  const { rerender } = render(eventPanel(m, '2026-01-02T16:00:00Z', null));
  fireEvent.change(screen.getByLabelText('Objective'), { target: { value: 'upside' } });
  fireEvent.change(screen.getByLabelText('Option premium-loss budget ($)'), { target: { value: '300' } });
  expect(screen.getByText('Goal-fit only: Long Call')).toBeTruthy();
  const filter = screen.getByRole('checkbox', { name: 'Require a current calendar record with reported next earnings after expiration' });
  expect((filter as HTMLInputElement).checked).toBe(false);
  fireEvent.click(filter);
  expect(screen.queryByText('Goal-fit only: Long Call')).toBeNull();
  rerender(eventPanel(m, '2026-01-02T16:00:00Z', '2026-03-01'));
  expect(screen.getByText('Goal-fit only: Long Call')).toBeTruthy();
  rerender(eventPanel(m, '2026-01-02T16:00:00Z', '2026-01-20'));
  expect(screen.queryByText('Goal-fit only: Long Call')).toBeNull();
  expect(document.body.textContent).toContain('Reported after planned hold, but by expiry');
  rerender(eventPanel(m, '2025-12-31T16:00:00Z', '2026-03-01'));
  expect(screen.queryByText('Goal-fit only: Long Call')).toBeNull();
  expect(document.body.textContent).toContain('Calendar retrieved over 24 hours ago');
});
it('ages calendar evidence and removes a filtered preference without making a request', () => {
  const get = vi.spyOn(api, 'getOptionsGamePlan');
  render(eventPanel(eventMatrix(), '2026-01-02T16:00:00Z', '2026-03-01'));
  fireEvent.change(screen.getByLabelText('Objective'), { target: { value: 'upside' } });
  fireEvent.change(screen.getByLabelText('Option premium-loss budget ($)'), { target: { value: '300' } });
  fireEvent.click(screen.getByRole('checkbox', { name: /Require a current calendar record/ }));
  expect(screen.getByText('Goal-fit only: Long Call')).toBeTruthy();
  vi.setSystemTime(new Date('2026-01-03T16:00:01Z'));
  act(() => vi.advanceTimersByTime(30_000));
  expect(screen.queryByText('Goal-fit only: Long Call')).toBeNull();
  expect(get).not.toHaveBeenCalled();
});
it('discloses quote and event provenance gaps independently of retrieval freshness', () => {
  render(eventPanel(matrix(), '2026-01-02T16:00:00Z', '2026-01-09'));
  const evidence = screen.getByRole('region', { name: 'Quote and event evidence' });
  expect(evidence.textContent).toContain('Quote observation time, bid/ask size and reference-spot observation time are unavailable');
  expect(evidence.textContent).toContain('Calendar retrieved within 24 hours');
  expect(evidence.textContent).toContain('issuer confirmation, event time and complete future calendar coverage are unverified');
  expect(evidence.textContent).toContain('Both short calls and short puts can be assigned early');
});

it('sends explicit bounds once, shows acknowledged coverage and clears choices when preferences change', async () => {
  const result = fourStrategyResult();
  const prefs = { min_dte: 35, max_dte: 90, min_abs_delta: .2, max_abs_delta: .4 };
  for (const review of Object.values(result.strategy_matrix!.candidate_review!)) {
    review!.search_preferences = prefs;
    review!.holding_eligible_expiry_count = 12;
  }
  const get = vi.spyOn(api, 'getOptionsGamePlan').mockResolvedValue(result);
  render(searchPanel());
  for (const [label, value] of [['Minimum days to expiry', '35'], ['Maximum days to expiry', '90'], ['Minimum absolute delta', '.2'], ['Maximum absolute delta', '.4']]) {
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  }
  expect(get).not.toHaveBeenCalled();
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Find expirations for holding period' })));
  expect(get).toHaveBeenCalledWith('EXAMPLE', { stopLoss: undefined, takeProfit: 120, holdingDays: 14, searchPreferences: prefs });
  expect(document.body.textContent).toContain('12 expirations fit the holding period');
  fireEvent.change(screen.getByLabelText('Covered-call contract to compare'), { target: { value: '0' } });
  expect(screen.getByText(/Selected comparisons:/)).toBeTruthy();
  fireEvent.click(screen.getByRole('checkbox', { name: /I accept buying or selling/ }));
  fireEvent.change(screen.getByLabelText('Maximum absolute delta'), { target: { value: '.25' } });
  expect(screen.queryByText(/Selected comparisons:/)).toBeNull();
  expect((screen.getByRole('checkbox', { name: /I accept buying or selling/ }) as HTMLInputElement).checked).toBe(false);
  expect(get).toHaveBeenCalledTimes(1);
});
it('refuses unacknowledged preferences and inverted bounds without disguising the old shortlist as filtered', async () => {
  vi.spyOn(api, 'getOptionsGamePlan').mockResolvedValue(searchResult());
  render(searchPanel());
  fireEvent.change(screen.getByLabelText('Minimum absolute delta'), { target: { value: '.2' } });
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Find expirations for holding period' })));
  expect(screen.getByRole('alert').textContent).toContain('preferences were not acknowledged');
  fireEvent.change(screen.getByLabelText('Minimum days to expiry'), { target: { value: '60' } });
  fireEvent.change(screen.getByLabelText('Maximum days to expiry'), { target: { value: '30' } });
  expect((screen.getByRole('button', { name: 'Find expirations for holding period' }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByRole('alert').textContent).toContain('cannot exceed');
});
it('applies preferences to original goal fit and clear restores blank behavior', () => {
  render(eventPanel(eventMatrix(), '2026-01-02T16:00:00Z', '2026-03-01'));
  fireEvent.change(screen.getByLabelText('Objective'), { target: { value: 'upside' } });
  fireEvent.change(screen.getByLabelText('Option premium-loss budget ($)'), { target: { value: '300' } });
  expect(screen.getByText('Goal-fit only: Long Call')).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Minimum absolute delta'), { target: { value: '.6' } });
  expect(screen.queryByText('Goal-fit only: Long Call')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Clear contract preferences' }));
  expect(screen.getByText('Goal-fit only: Long Call')).toBeTruthy();
});

it('discards an in-flight result when contract preferences change without making another request', async () => {
  let resolve!: (value: Awaited<ReturnType<typeof api.getOptionsGamePlan>>) => void;
  const get = vi.spyOn(api, 'getOptionsGamePlan').mockReturnValue(new Promise(r => { resolve = r; }));
  render(searchPanel());
  fireEvent.click(screen.getByRole('button', { name: 'Find expirations for holding period' }));
  fireEvent.change(screen.getByLabelText('Minimum absolute delta'), { target: { value: '.3' } });
  await act(async () => resolve(fourStrategyResult()));
  expect(document.body.textContent).toContain('other expirations are not searched');
  expect(screen.queryByText(/Search preference coverage:/)).toBeNull();
  expect(get).toHaveBeenCalledTimes(1);
});
