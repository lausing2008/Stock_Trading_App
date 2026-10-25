import type { OptionStrategy } from './api';

/** Expiry arithmetic uses the same aggregate debit/credit displayed on the card.
 * Leg costs may retain half-cent mids while the displayed strategy is rounded differently. */
export function expiryPayoff(strategy: OptionStrategy, expiryStock: number, referenceStock: number): number {
  let pnl = (strategy.net === 'credit' ? 1 : -1) * Math.abs(strategy.net_per_contract);
  if (strategy.requires_shares) pnl += (expiryStock - referenceStock) * 100;
  for (const leg of strategy.legs) {
    const intrinsic = leg.right === 'call' ? Math.max(0, expiryStock - leg.strike) : Math.max(0, leg.strike - expiryStock);
    pnl += (leg.action === 'buy' ? 1 : -1) * intrinsic * 100;
  }
  return pnl;
}

export function shortIncomeKind(strategy: OptionStrategy): 'covered_call' | 'cash_secured_put' | null {
  if (strategy.net !== 'credit' || strategy.legs.length !== 1 || strategy.legs[0].action !== 'sell') return null;
  const leg = strategy.legs[0];
  if (leg.right === 'call' && strategy.requires_shares) return 'covered_call';
  if (leg.right === 'put' && !strategy.requires_shares) return 'cash_secured_put';
  return null;
}

export function lossScopeLabel(strategy: OptionStrategy): string {
  const kind = shortIncomeKind(strategy);
  if (kind === 'covered_call') return 'Max stock + call loss';
  if (kind === 'cash_secured_put') return 'Max put / assignment loss';
  if (strategy.legs.length === 1 && strategy.legs[0].action === 'buy') {
    return strategy.requires_shares ? 'Max stock + hedge loss' : 'Max option-debit loss';
  }
  return 'Max combined loss';
}

export function shortIncomeScenario(strategy: OptionStrategy, referenceStock: number, expiryStock: number) {
  const kind = shortIncomeKind(strategy), leg = strategy.legs[0];
  if (!kind || !Number.isFinite(referenceStock) || referenceStock <= 0 || !Number.isFinite(expiryStock) || expiryStock < 0
      || !Number.isFinite(leg.strike) || leg.strike <= 0 || !Number.isFinite(strategy.net_per_contract)
      || strategy.net_per_contract < 0 || strategy.net_per_contract > (kind === 'covered_call' ? referenceStock : leg.strike) * 100
      || leg.contract_terms?.automatic_payoff_supported === false) return null;
  if (typeof strategy.max_loss_per_contract !== 'number' || !Number.isFinite(strategy.max_loss_per_contract)
      || typeof strategy.max_profit_per_contract !== 'number' || !Number.isFinite(strategy.max_profit_per_contract)
      || Math.abs(strategy.max_loss_per_contract + expiryPayoff(strategy, 0, referenceStock)) > .011
      || Math.abs(strategy.max_profit_per_contract - expiryPayoff(strategy, leg.strike, referenceStock)) > .011) return null;
  const premium = strategy.net_per_contract;
  const effect = kind === 'covered_call'
    ? (Math.min(expiryStock, leg.strike) - referenceStock) * 100
    : -Math.max(0, leg.strike - expiryStock) * 100;
  return { kind, premium, stockOrAssignmentEffect: effect, total: expiryPayoff(strategy, expiryStock, referenceStock) };
}
