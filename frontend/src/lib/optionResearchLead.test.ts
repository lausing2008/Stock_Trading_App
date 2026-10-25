import { expect, it } from 'vitest';
import type { OptionStrategy, OptionStrategyMatrix } from './api';
import { EMPTY_OPTION_PREFERENCES } from './optionSearchPreferences';
import { optionResearchLead } from './optionResearchLead';

function contract(id: string, spread: number, oi: number, strike = 110): OptionStrategy {
  return { name: 'Covered Call', direction: 'neutral', net: 'credit', requires_shares: true,
    legs: [{ action: 'sell', right: 'call', strike, expiry: '2026-02-06', option_symbol: id,
      days_to_expiry: 35, price_source: 'quote_mid', price_per_share: 2, cost_per_contract: 200,
      spread_pct: spread, oi, delta: .3, gamma: .01, theta: null, vega: null }],
    net_per_share: 2, net_per_contract: 200, max_loss_per_contract: 9800,
    max_profit_per_contract: 1200, breakeven: 98, breakeven_move_pct: -2,
    collateral_per_contract: 10000, what_it_does: '', use_when: '' };
}
function candidates(alternatives: OptionStrategy[]) {
  const matrix = { singles: {}, combos: {}, iv_rank: null, iv_regime: 'unknown', recommendation: { primary: null, reason: '' },
    candidate_review: { covered_call: { method_version: 'covered_call_candidates_v2', anchor: 100, expiry: '2026-02-06',
      scope: 'bounded', evaluated_count: alternatives.length, screened_count: alternatives.length, rejected_counts: {},
      original: { strike: 100, option_symbol: 'ORIGINAL', reason: 'two_sided_quote_required' },
      ranking: '', alternatives, actionability: 'research_only' } } } as OptionStrategyMatrix;
  return { matrix, source: matrix.candidate_review!.covered_call };
}
const today = '2026-01-02';
it('chooses a deterministic quote-quality lead, retaining the source index', () => {
  const { matrix, source } = candidates([contract('WIDE', 10, 900), contract('B', 5, 50), contract('A', 5, 50)]);
  expect(optionResearchLead('covered_call', matrix, source, today, 14, EMPTY_OPTION_PREFERENCES)?.index).toBe(2);
  expect(optionResearchLead('covered_call', matrix, source, today, 14, EMPTY_OPTION_PREFERENCES)?.strategy.legs[0].option_symbol).toBe('A');
});
it('does not elevate missing OI, a one-sided quote or incomplete expiration reads', () => {
  const { matrix, source } = candidates([contract('NO-OI', 1, 0), { ...contract('NO-QUOTE', 2, 100), legs: [{ ...contract('NO-QUOTE', 2, 100).legs[0], price_source: 'last_trade' }] }]);
  expect(optionResearchLead('covered_call', matrix, source, today, 14, EMPTY_OPTION_PREFERENCES)).toBeNull();
  source!.alternatives.push(contract('VALID', 3, 1));
  expect(optionResearchLead('covered_call', matrix, source, today, 14, EMPTY_OPTION_PREFERENCES)?.strategy.legs[0].option_symbol).toBe('VALID');
  source!.failed_expiries = ['2026-03-06'];
  expect(optionResearchLead('covered_call', matrix, source, today, 14, EMPTY_OPTION_PREFERENCES)).toBeNull();
});
it('honors holding period and delta preferences before ranking', () => {
  const { matrix, source } = candidates([contract('A', 1, 500), contract('B', 2, 100)]);
  expect(optionResearchLead('covered_call', matrix, source, today, 40, EMPTY_OPTION_PREFERENCES)).toBeNull();
  const prefs = { ...EMPTY_OPTION_PREFERENCES, min_abs_delta: .5 };
  expect(optionResearchLead('covered_call', matrix, source, today, 14, prefs)).toBeNull();
  source!.alternatives[1].legs[0].delta = .6;
  expect(optionResearchLead('covered_call', matrix, source, today, 14, prefs)?.index).toBe(1);
});
