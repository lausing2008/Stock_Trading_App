import type { OptionStrategy, OptionStrategyMatrix } from './api';

export const COMMON_OPTION_KEYS = ['long_call', 'protective_put', 'covered_call', 'cash_secured_put'] as const;
export type CommonOptionKey = typeof COMMON_OPTION_KEYS[number];
export const COMMON_OPTION_NAMES: Record<CommonOptionKey, string> = {
  long_call: 'Long Call', protective_put: 'Protective Put', covered_call: 'Covered Call', cash_secured_put: 'Cash-Secured Put',
};
export type OptionGoal = 'compare' | 'upside' | 'income' | 'protect' | 'bearish';
export type OptionPlanContext = {
  goal: OptionGoal; shares: number | null; cash: number | null; premiumBudget: number | null;
  acceptAssignment: boolean; stop?: number; target?: number; holdingDays?: number | null;
};
const positive = (n: number | null | undefined): n is number => typeof n === 'number' && Number.isFinite(n) && n > 0;
const usd = (n: number) => `$${n.toFixed(2)}`;

/** Research policies, not optimized parameters or executable order instructions. */
export function commonOptionPlan(key: CommonOptionKey, strategy: OptionStrategy | undefined, context: OptionPlanContext, today: string) {
  const blockers: string[] = [];
  const leg = strategy?.legs[0];
  const expected = { long_call: ['buy', 'call'], protective_put: ['buy', 'put'], covered_call: ['sell', 'call'], cash_secured_put: ['sell', 'put'] }[key];
  if (!strategy || !leg) blockers.push('No priced contract is available.');
  else {
    if (strategy.legs.length !== 1 || leg.action !== expected[0] || leg.right !== expected[1]) blockers.push('Contract does not match this strategy.');
    if (!positive(leg.strike) || !positive(leg.price_per_share) || !positive(strategy.net_per_share) || !positive(strategy.net_per_contract)
      || Math.abs(strategy.net_per_contract - leg.price_per_share * 100) > 0.02) blockers.push('Contract prices are invalid or inconsistent.');
    if (!leg.expiry || !/^\d{4}-\d{2}-\d{2}$/.test(leg.expiry) || !Number.isFinite(Date.parse(leg.expiry)) || leg.expiry <= today) blockers.push('Expiry is missing or has passed; refresh the chain.');
    if (leg.price_source !== 'quote_mid' || leg.spread_pct == null || !Number.isFinite(leg.spread_pct)) blockers.push('A usable two-sided quote and spread are required.');
    else if (leg.spread_pct < 0 || leg.spread_pct > 20) blockers.push('Bid/ask spread exceeds the 20% research comparison limit or is invalid.');
    if (context.holdingDays !== undefined) {
      if (context.holdingDays == null || !Number.isInteger(context.holdingDays) || context.holdingDays < 1 || context.holdingDays > 365) {
        blockers.push('Declare a planned holding period from 1 to 365 calendar days.');
      } else {
        const remaining = (Date.parse(leg.expiry ?? '') - Date.parse(today)) / 86_400_000;
        if (!Number.isFinite(remaining) || remaining < context.holdingDays + 2) {
          blockers.push('Expiry does not cover the planned holding period plus the two-calendar-day research buffer.');
        }
      }
    }
    if (strategy.max_profit_per_contract !== null && (!Number.isFinite(strategy.max_profit_per_contract) || strategy.max_profit_per_contract <= 0)) blockers.push('The maximum payoff is not positive.');
  }
  if (key === 'covered_call' || key === 'protective_put') {
    if (!positive(context.shares) || context.shares < 100) blockers.push('Declare at least 100 unencumbered shares for one standard contract.');
  }
  if (key === 'covered_call' && !context.acceptAssignment) blockers.push('Accept selling the shares at the strike if assigned.');
  if (key === 'cash_secured_put') {
    if (!context.acceptAssignment) blockers.push('Accept buying 100 shares at the strike if assigned.');
    if (!leg || !positive(context.cash) || context.cash < leg.strike * 100) blockers.push('Reserve the full strike × 100 in cash before considering one contract.');
  }
  if ((key === 'long_call' || key === 'protective_put') && (!strategy || !positive(context.premiumBudget) || context.premiumBudget < strategy.net_per_contract)) blockers.push('Declare a premium-loss budget covering the full option debit.');

  const p = leg?.price_per_share;
  const reference = positive(p) ? usd(p) : 'unavailable';
  const entry = key === 'long_call' ? `After an upside confirmation, assess buying the listed call. Archive premium ${reference}/share; reprice using a fresh limit quote.`
    : key === 'protective_put' ? `If protecting an existing holding is the goal, assess buying the listed put. Archive premium ${reference}/share; reprice using a fresh limit quote.`
    : `Only after confirming coverage and assignment acceptance, assess selling the listed ${key === 'covered_call' ? 'call' : 'put'}. Archive credit ${reference}/share; reprice using a fresh limit quote.`;
  const target = key === 'long_call' && positive(p) ? `Illustrative option-premium TP1 ${usd(p * 1.5)}, TP2 ${usd(p * 2)} (+50% / +100% before fees). Recalculate from the actual fill.`
    : key === 'protective_put' ? 'The objective is protection of the stock, not a put-premium profit target. Review the hedge when protection is no longer needed; closing the put removes the floor.'
    : positive(p) ? `Illustrative buy-to-close review at ${usd(p * 0.5)}/share (50% of original credit retained before fees). Recalculate from the actual fill.` : 'No priced target is available.';
  const stop = key === 'long_call' && positive(p) ? `Illustrative premium exit review ${usd(p * 0.5)}/share (−50%); a gap can lose the entire premium. Recalculate from the actual fill.`
    : key === 'protective_put' ? 'Do not apply a call-style premium stop to insurance. If selling the hedge, explicitly reassess the stock downside and replace protection if needed.'
    : 'If the stock thesis fails or assignment is no longer acceptable, review buying back the short option. Loss can exceed the credit; a stop does not remove assignment risk.';
  const underlying = [positive(context.stop) ? `Stock invalidation reference ${usd(context.stop)}.` : 'Stock invalidation reference not available.',
    positive(context.target) ? `Stock target reference ${usd(context.target)}; its horizon may exceed option expiry.` : 'Stock target reference not available.'];
  const steps = [
    'Before entry: verify a fresh two-sided quote, liquidity, standard 100-share deliverable, earnings/ex-dividend dates, permissions and total portfolio risk. These are not verified by this comparison.',
    ...underlying,
    key === 'covered_call' ? 'While open: keep 100 shares unencumbered for each short call. Monitor early assignment, especially around ex-dividend dates; do not sell the covering shares alone.'
      : key === 'cash_secured_put' ? 'While open: keep strike × 100 cash reserved per contract. A falling stock can create a substantial loss and early assignment may occur.'
      : key === 'protective_put' ? 'While open: match the hedge quantity to shares owned. Track expiry and the stock-plus-put result; the put premium can expire worthless.'
      : 'While open: monitor direction, time decay and volatility changes. A correct direction can still lose if the move is too small or late.',
    'Review daily and before earnings or ex-dividend dates. Plan to close before expiration unless exercise or assignment is intentional and funded. Confirm fills with the broker; this screen does not manage orders.',
  ];
  return { blockers, entry, target, stop, steps };
}

export function recommendCommonOption(matrix: OptionStrategyMatrix, context: OptionPlanContext, today: string, chainAsOf?: string | null) {
  const plans = Object.fromEntries(COMMON_OPTION_KEYS.map(k => [k, commonOptionPlan(k, matrix.singles[k], context, today)])) as Record<CommonOptionKey, ReturnType<typeof commonOptionPlan>>;
  for (const key of COMMON_OPTION_KEYS) {
    if (matrix.singles[key] && matrix.candidate_review?.[key]?.original.reason) {
      plans[key].blockers.push(`The original ${key === 'covered_call' ? 'covered-call' : COMMON_OPTION_NAMES[key]} candidate failed the research screen. Choose a screened alternative or refresh its evidence.`);
    }
  }
  const rank = matrix.iv_rank;
  const rich = typeof rank === 'number' && Number.isFinite(rank) && rank >= 70 && rank <= 100;
  const order: CommonOptionKey[] = context.goal === 'upside' ? (rich ? ['cash_secured_put', 'long_call'] : ['long_call', 'cash_secured_put'])
    : context.goal === 'income' ? ['covered_call', 'cash_secured_put'] : context.goal === 'protect' || context.goal === 'bearish' ? ['protective_put'] : [];
  const asOf = chainAsOf?.slice(0, 10);
  const age = asOf ? (Date.parse(today) - Date.parse(asOf)) / 86_400_000 : NaN;
  const snapshotUsable = Number.isFinite(age) && age >= 0 && age <= 7;
  const primary = snapshotUsable ? order.find(k => plans[k].blockers.length === 0) ?? null : null;
  const reason = !snapshotUsable ? 'Wait: the chain observation date is unknown, in the future or more than seven calendar days old. Refresh contract evidence.'
    : context.goal === 'compare' ? 'Choose an objective and declare the relevant holdings and budget to get a conditional preference.'
    : !primary ? 'Wait: none of these four strategies matches the objective and declared coverage, budget and quote checks. Review the reasons on each card.'
    : primary === 'protective_put' ? 'Protection fits an existing stock holding and preserves its upside. A bearish view without shares has no fit among these four; a protective put is not a standalone bearish bet.'
    : primary === 'covered_call' ? 'Income fits the declared 100-share coverage and willingness to sell at the strike. The trade caps upside and keeps most stock downside.'
    : primary === 'cash_secured_put' ? `The declared cash reserve and assignment acceptance support getting paid to wait for shares.${rich ? ' IV rank is at least 70, so selling premium is preferred in this research policy.' : ''} This remains bullish exposure, not downside protection.`
    : `A long call fits the upside objective and declared premium-loss budget, with loss limited to the debit.${rich ? ' Premium is rich; this is the remaining eligible fit, not evidence of cheap pricing.' : ' It preserves uncapped upside without committing to stock assignment.'}`;
  return { primary, reason, plans, ivNote: typeof rank === 'number' && Number.isFinite(rank) && rank >= 0 && rank <= 100 ? `IV rank ${rank.toFixed(0)}/100; heuristic structure preference, not a measured edge.` : 'IV rank unavailable; no cheap/rich volatility claim is used.' };
}
