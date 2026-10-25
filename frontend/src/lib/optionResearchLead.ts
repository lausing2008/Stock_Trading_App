import type { OptionStrategy, OptionStrategyMatrix } from './api';
import { COMMON_OPTION_KEYS, type CommonOptionKey } from './commonOptionPlans';
import { candidateDte } from './optionCandidateComparison';
import { optionPreferenceBlocker, type OptionSearchPreferences } from './optionSearchPreferences';

type Review = NonNullable<OptionStrategyMatrix['candidate_review']>[CommonOptionKey];
export type OptionResearchLead = { strategy: OptionStrategy; index: number | null; source: Review };

/** A deterministic quote-quality lead within the API's bounded shortlist, not a return forecast. */
export function optionResearchLead(
  key: CommonOptionKey, matrix: OptionStrategyMatrix, source: Review, today: string,
  holdingDays: number | null | undefined, preferences: OptionSearchPreferences,
): OptionResearchLead | null {
  if (!source || source.failed_expiries?.length || !Number.isFinite(source.anchor) || source.anchor <= 0) return null;
  const expected = key.endsWith('call') ? 'call' : 'put';
  const action = key === 'long_call' || key === 'protective_put' ? 'buy' : 'sell';
  const valid = (s: OptionStrategy | undefined) => {
    const leg = s?.legs[0];
    const dte = candidateDte(leg?.expiry, today);
    return !!s && s.legs.length === 1 && !!leg && leg.right === expected && leg.action === action
      && !!leg.option_symbol && Number.isFinite(leg.strike) && leg.strike > 0
      && leg.price_source === 'quote_mid' && Number.isFinite(leg.price_per_share) && leg.price_per_share > 0
      && Number.isFinite(s.net_per_contract) && s.net_per_contract > 0
      && Math.abs(s.net_per_contract - leg.price_per_share * 100) <= .02
      && Number.isFinite(leg.spread_pct) && leg.spread_pct! >= 0 && leg.spread_pct! <= 20
      && Number.isInteger(leg.oi) && leg.oi! > 0 && dte !== null && dte > 0
      && (holdingDays === undefined || (holdingDays !== null && Number.isInteger(holdingDays) && holdingDays >= 1 && dte >= holdingDays + 2))
      && s.max_profit_per_contract !== undefined
      && (s.max_profit_per_contract === null || (Number.isFinite(s.max_profit_per_contract) && s.max_profit_per_contract > 0))
      && !optionPreferenceBlocker(leg, preferences, today);
  };
  const rows = [
    ...(source.original.reason === null && valid(matrix.singles[key]) ? [{ strategy: matrix.singles[key], index: null }] : []),
    ...source.alternatives.map((strategy, index) => ({ strategy, index })).filter(row => valid(row.strategy)),
  ];
  rows.sort((a, b) => {
    const x = a.strategy.legs[0], y = b.strategy.legs[0];
    return x.spread_pct! - y.spread_pct! || y.oi! - x.oi!
      || Math.abs(x.strike - source.anchor) - Math.abs(y.strike - source.anchor)
      || candidateDte(x.expiry, today)! - candidateDte(y.expiry, today)!
      || x.option_symbol!.localeCompare(y.option_symbol!) || (a.index ?? -1) - (b.index ?? -1);
  });
  return rows.length ? { ...rows[0], source } : null;
}

export function allOptionResearchLeads(matrix: OptionStrategyMatrix, today: string, holdingDays: number | null | undefined,
  preferences: OptionSearchPreferences): Partial<Record<CommonOptionKey, OptionResearchLead>> {
  return Object.fromEntries(COMMON_OPTION_KEYS.flatMap(key => {
    const source = matrix.candidate_review?.[key];
    const lead = optionResearchLead(key, matrix, source, today, holdingDays, preferences);
    return lead ? [[key, lead]] : [];
  }));
}
