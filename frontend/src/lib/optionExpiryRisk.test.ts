import {expect,it} from 'vitest';
import {expiryPayoff,lossScopeLabel,shortIncomeKind,shortIncomeScenario} from './optionExpiryRisk';
import {coveredCall,securedPut} from '@/testFixtures/shortOptionRisk';

it('reproduces the user covered-call stock-loss, breakeven and capped-upside examples',()=>{
  for(const [price,total] of [[0,-41711],[350,-6711],[400,-1711],[417.11,0],[418.19,108],[480,6289],[550,6289]]) {
    const r=shortIncomeScenario(coveredCall,418.19,price)!;
    expect(r.total).toBeCloseTo(total,6);
    expect(r.stockOrAssignmentEffect+r.premium).toBeCloseTo(total,6);
  }
  expect(lossScopeLabel(coveredCall)).toBe('Max stock + call loss');
});
it('cash-secured put includes the purchase obligation and retains premium in falling scenarios',()=>{
  for(const [price,total] of [[0,-39043],[350,-4043],[390.43,0],[405,1457],[500,1457]]) {
    const r=shortIncomeScenario(securedPut,418.19,price)!;
    expect(r.total).toBeCloseTo(total,6);
    expect(r.stockOrAssignmentEffect+r.premium).toBeCloseTo(total,6);
  }
  expect(lossScopeLabel(securedPut)).toBe('Max put / assignment loss');
});
it('graph and scenarios use the shown strategy credit rather than a conflicting half-cent leg cost',()=>{
  const s={...coveredCall,net_per_contract:5248,max_loss_per_contract:82507,max_profit_per_contract:5493,
    legs:[{...coveredCall.legs[0],strike:880,price_per_share:52.48,cost_per_contract:5247.5}]};
  expect(expiryPayoff(s,0,877.55)).toBeCloseTo(-82507);
  expect(shortIncomeScenario(s,877.55,0)!.total).toBeCloseTo(-82507);
  expect(expiryPayoff(s,1000,877.55)).toBeCloseTo(5493);
});
it('signed credit collars and debit long calls keep their aggregate premium convention',()=>{
  const collar={...coveredCall,net_per_contract:-100,legs:[
    {...coveredCall.legs[0],right:'put' as const,action:'buy' as const,strike:95,cost_per_contract:100},
    {...coveredCall.legs[0],strike:110,cost_per_contract:200}]};
  expect(expiryPayoff(collar,100,100)).toBe(100);
  expect(expiryPayoff(collar,0,100)).toBe(-400);
  expect(shortIncomeKind(collar)).toBeNull();
  const call={...coveredCall,net:'debit' as const,net_per_contract:100,requires_shares:false,
    legs:[{...coveredCall.legs[0],action:'buy' as const,strike:110,cost_per_contract:99.5}]};
  expect(expiryPayoff(call,0,100)).toBe(-100);
  expect(expiryPayoff(call,120,100)).toBe(900);
  expect(lossScopeLabel(call)).toBe('Max option-debit loss');
});
it('refuses unknown, negative, nonfinite, unsupported and inconsistent short-risk inputs',()=>{
  for(const value of [-1,NaN,Infinity])expect(shortIncomeScenario(coveredCall,418.19,value)).toBeNull();
  for(const reference of [0,-1,NaN,Infinity])expect(shortIncomeScenario(coveredCall,reference,400)).toBeNull();
  expect(shortIncomeScenario({...coveredCall,net_per_contract:NaN},418.19,400)).toBeNull();
  expect(shortIncomeScenario({...coveredCall,max_loss_per_contract:100},418.19,400)).toBeNull();
  expect(shortIncomeScenario({...coveredCall,legs:[{...coveredCall.legs[0],contract_terms:{automatic_payoff_supported:false} as never}]},418.19,400)).toBeNull();
});
it('does not mutate the chosen strategy or invent a purchase cost',()=>{
  const before=JSON.stringify(coveredCall);
  shortIncomeScenario(coveredCall,418.19,400);
  expect(JSON.stringify(coveredCall)).toBe(before);
  expect('cost_basis' in coveredCall).toBe(false);
});

it('preserves the stock-plus-protective-put floor and a debit spread cap',()=>{
  const hedge={...securedPut,net:'debit' as const,requires_shares:true,
    legs:[{...securedPut.legs[0],action:'buy' as const}]};
  expect(expiryPayoff(hedge,0,418.19)).toBeCloseTo(-2776);
  expect(expiryPayoff(hedge,405,418.19)).toBeCloseTo(-2776);
  expect(expiryPayoff(hedge,500,418.19)).toBeCloseTo(6724);
  expect(lossScopeLabel(hedge)).toBe('Max stock + hedge loss');
  const spread={...coveredCall,net:'debit' as const,requires_shares:false,net_per_contract:200,
    legs:[{...coveredCall.legs[0],action:'buy' as const,strike:100},{...coveredCall.legs[0],strike:110}]};
  expect(expiryPayoff(spread,0,100)).toBe(-200);
  expect(expiryPayoff(spread,105,100)).toBe(300);
  expect(expiryPayoff(spread,120,100)).toBe(800);
  expect(lossScopeLabel(spread)).toBe('Max combined loss');
});
