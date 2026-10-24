import { expect, it } from 'vitest';
import type { OptionStrategy, OptionStrategyMatrix } from './api';
import { COMMON_OPTION_KEYS, commonOptionPlan, recommendCommonOption, type CommonOptionKey, type OptionPlanContext } from './commonOptionPlans';
const today = '2026-10-18';
const context: OptionPlanContext = { goal: 'upside', shares: 100, cash: 10000, premiumBudget: 500, acceptAssignment: true, stop: 90, target: 120 };
function strategy(key: CommonOptionKey): OptionStrategy {
  const sell = key === 'covered_call' || key === 'cash_secured_put';
  return { name: key, direction: 'bullish', net: sell ? 'credit' : 'debit',
    legs: [{ action: sell ? 'sell' : 'buy', right: key === 'long_call' || key === 'covered_call' ? 'call' : 'put', strike: 100,
      price_per_share: 2, cost_per_contract: 200, expiry: '2026-11-20', days_to_expiry: 33, spread_pct: 10, price_source: 'quote_mid' }],
    net_per_share: 2, net_per_contract: 200, max_loss_per_contract: 200, max_profit_per_contract: sell ? 200 : null,
    breakeven: 102, breakeven_move_pct: 2, requires_shares: key === 'covered_call' || key === 'protective_put', collateral_per_contract: 10000,
    what_it_does: 'Example strategy', use_when: 'When the objective fits' };
}
function matrix(): OptionStrategyMatrix { return { singles: Object.fromEntries(COMMON_OPTION_KEYS.map(k => [k, strategy(k)])), combos: {}, recommendation: { primary: null, reason: '' }, iv_rank: null, iv_regime: 'unknown' }; }
const recommend = (over: Partial<OptionPlanContext> = {}, m = matrix(), asof: string | null = '2026-10-16') => recommendCommonOption(m, { ...context, ...over }, today, asof);
it('selects by objective and explains unknown volatility without claiming an edge', () => {
  expect(recommend().primary).toBe('long_call'); expect(recommend().ivNote).toContain('unavailable');
  expect(recommend({ goal: 'income' }).primary).toBe('covered_call');
  expect(recommend({ goal: 'protect' }).primary).toBe('protective_put');
  expect(recommend({ goal: 'compare' }).primary).toBeNull();
});
it('compares rich premium only when rank is known, with a cash-backed bullish alternative', () => {
  const m = matrix(); m.iv_rank = 80;
  expect(recommend({}, m).primary).toBe('cash_secured_put');
  expect(recommend({ acceptAssignment: false }, m).primary).toBe('long_call');
  m.iv_rank = NaN; expect(recommend({}, m).primary).toBe('long_call');
});
it('never recommends a bullish trade against a bearish objective without shares', () => {
  expect(recommend({ goal: 'bearish', shares: 0 }).primary).toBeNull();
  expect(recommend({ goal: 'bearish' }).primary).toBe('protective_put');
});
it('enforces coverage, premium budget, assignment acceptance and reserved strike cash', () => {
  expect(recommend({ goal: 'income', shares: 99, cash: 9999 }).primary).toBeNull();
  expect(recommend({ goal: 'income', shares: 0 }).primary).toBe('cash_secured_put');
  expect(recommend({ goal: 'income', acceptAssignment: false }).primary).toBeNull();
  expect(recommend({ premiumBudget: 199, cash: 0 }).primary).toBeNull();
  expect(recommend({ goal: 'protect', premiumBudget: null }).primary).toBeNull();
});
it.each([null, '2026-10-01', '2026-10-19', 'garbled'])('waits on an unknown, old or future chain date: %s', asof => {
  expect(recommend({}, matrix(), asof).primary).toBeNull();
});
it.each(['last_trade', 'none', undefined])('refuses a missing two-sided quote source: %s', source => {
  const s = strategy('long_call'); s.legs[0].price_source = source;
  expect(commonOptionPlan('long_call', s, context, today).blockers.length).toBeGreaterThan(0);
});
it('rejects wide spreads, expired contracts and invalid/inconsistent premiums', () => {
  for (const change of [{ spread_pct: 21 }, { spread_pct: null }, { expiry: today }, { expiry: '2026-99-99' }, { price_per_share: NaN }, { price_per_share: 3 }]) {
    const s = strategy('long_call'); Object.assign(s.legs[0], change);
    expect(commonOptionPlan('long_call', s, context, today).blockers.length).toBeGreaterThan(0);
  }
});
it('calculates labelled illustrative call exits without calling the stop a maximum loss', () => {
  const p = commonOptionPlan('long_call', strategy('long_call'), context, today);
  expect(p.target).toContain('$3.00'); expect(p.target).toContain('$4.00');
  expect(p.stop).toContain('$1.00'); expect(p.stop).toContain('entire premium');
});
it('does not apply a premium stop or speculative profit target to a protective hedge', () => {
  const p = commonOptionPlan('protective_put', strategy('protective_put'), context, today);
  expect(p.target).toContain('not a put-premium profit target'); expect(p.stop).toContain('Do not apply');
  expect(p.steps.join(' ')).toContain('horizon may exceed option expiry');
});
it('keeps missing strategies explainable and never chooses an unavailable contract', () => {
  const m = matrix(); delete m.singles.long_call;
  expect(recommend({ cash: 0 }, m).primary).toBeNull();
  expect(recommend({}, m).plans.long_call.blockers).toContain('No priced contract is available.');
});

it('honors the original candidate screen even when rounded spread would appear acceptable', () => {
  const m = matrix(); m.singles.covered_call.legs[0].spread_pct = 20;
  m.candidate_review = { covered_call: { method_version: 'covered_call_candidates_v1', anchor: 100,
    expiry: '2026-11-20', scope: 'all_rows_of_selected_call_expiry', evaluated_count: 1, screened_count: 0,
    rejected_counts: { spread_exceeds_20_percent: 1 },
    original: { strike: 100, option_symbol: null, reason: 'spread_exceeds_20_percent' },
    ranking: 'Not expected return.', alternatives: [], actionability: 'research_only' } };
  const result = recommend({ goal: 'income', cash: 0 }, m);
  expect(result.primary).toBeNull();
  expect(result.plans.covered_call.blockers.join(' ')).toContain('failed the research screen');
});

it('requires expiry to cover the editable holding period plus two calendar days using current dates', () => {
  const s = strategy('long_call'); s.legs[0].days_to_expiry = 999;
  expect(commonOptionPlan('long_call', s, { ...context, holdingDays: 31 }, today).blockers).toEqual([]);
  expect(commonOptionPlan('long_call', s, { ...context, holdingDays: 32 }, today).blockers.join(' ')).toContain('two-calendar-day research buffer');
});
it.each([null, 0, -1, 1.5, 366, NaN, Infinity])('rejects an invalid declared holding period: %s', holdingDays => {
  const p = commonOptionPlan('long_call', strategy('long_call'), { ...context, holdingDays }, today);
  expect(p.blockers.join(' ')).toContain('1 to 365 calendar days');
});
it.each(COMMON_OPTION_KEYS)('applies holding/expiry checks to %s', key => {
  expect(commonOptionPlan(key, strategy(key), { ...context, holdingDays: 60 }, today).blockers.join(' ')).toContain('planned holding period');
});
