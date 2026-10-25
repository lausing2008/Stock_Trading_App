import type { OptionStrategy } from '@/lib/api';
export const coveredCall: OptionStrategy = {
  name:'Covered Call',direction:'neutral-to-mildly-bullish',net:'credit',net_per_share:1.08,net_per_contract:108,
  max_loss_per_contract:41711,max_profit_per_contract:6289,breakeven:417.11,breakeven_move_pct:-.26,collateral_per_contract:41819,
  requires_shares:true,what_it_does:'Caps upside at $480.',use_when:'Accept assignment.',
  legs:[{action:'sell',right:'call',strike:480,expiry:'2026-11-20',days_to_expiry:27,price_per_share:1.08,cost_per_contract:108}],
};
export const securedPut: OptionStrategy = {
  name:'Cash-Secured Put',direction:'neutral-to-bullish',net:'credit',net_per_share:14.57,net_per_contract:1457,
  max_loss_per_contract:39043,max_profit_per_contract:1457,breakeven:390.43,breakeven_move_pct:-6.64,collateral_per_contract:40500,
  requires_shares:false,what_it_does:'Buy shares at $405 if assigned.',use_when:'Cash reserved.',
  legs:[{action:'sell',right:'put',strike:405,expiry:'2026-12-04',days_to_expiry:41,price_per_share:14.57,cost_per_contract:1457}],
};
