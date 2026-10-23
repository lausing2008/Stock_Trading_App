import { expect, it } from 'vitest';
import { calculatorUnavailableReason, expiredSinceLine, expiryCandidateNote, isCurrentExpiry } from './optionsFlowStatus';

it('cannot re-enable a server-refused product before its nominal expiry close', () => {
  const scope = {historical_expiries:['2026-11-20'],current_expiries:[],expiries:[{
    expiry:'2026-11-20',historical:true,current:false,reason:'unsupported_product_settlement',last_trading_instant:'2026-11-20T21:00:00Z'}]};
  const before = Date.parse('2026-11-20T14:00:00Z');
  expect(isCurrentExpiry('2026-11-20',scope,before,true)).toBe(false);
  expect(expiryCandidateNote('2026-11-20',scope,before,true)).toContain('unsupported settlement');
  expect(expiredSinceLine(scope,before)).not.toContain('have expired');
  expect(calculatorUnavailableReason({available:true,current_expiries:[],expiry_scope:scope},[])).toContain('not supported');
});

it('still expires a supported contract when an open tab crosses the close', () => {
  const scope={historical_expiries:['2026-11-20'],current_expiries:['2026-11-20'],expiries:[{
    expiry:'2026-11-20',historical:true,current:true,reason:null,last_trading_instant:'2026-11-20T21:00:00Z'}]};
  expect(isCurrentExpiry('2026-11-20',scope,Date.parse('2026-11-20T20:59:59Z'))).toBe(true);
  expect(isCurrentExpiry('2026-11-20',scope,Date.parse('2026-11-20T21:00:00Z'))).toBe(false);
});
