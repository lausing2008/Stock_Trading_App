import type { TradeSetup } from '@/lib/copilot';
export const setupFixture = (id = 'pullback'): TradeSetup => ({
  id, key: `setup.${id}`, title: id === 'breakout' ? 'Breakout entry' : 'Pullback entry', state: 'conditional',
  entry: id === 'breakout' ? 101 : 99, stop: id === 'breakout' ? 99 : 98,
  targets: [{ price: id === 'breakout' ? 105 : 101, label: '2R objective', reward_risk: 2 }, { price: id === 'breakout' ? 107 : 102, label: 'Stretch objective', reward_risk: 3 }],
  risk_per_share: id === 'breakout' ? 2 : 1, risk_budget: 400, shares: id === 'breakout' ? 200 : 400,
  shares_basis: 'risk_budget', capital: id === 'breakout' ? 20200 : 39600, cash_at_stop: -400,
  trigger: 'Wait for daily-close confirmation.', invalidation: 'Daily close below the stop invalidates the setup.',
  earnings: 'Earnings date unknown.', risk_note: 'Sizing does not check buying power.', reasons: [], recovery_levels: [], evidence_ids: ['calc1'],
});
