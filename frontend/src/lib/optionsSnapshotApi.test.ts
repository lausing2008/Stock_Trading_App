// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { api } from './api';
afterEach(()=>{vi.unstubAllGlobals();});
it('routes tables and recalculation/search to the same opaque snapshot instead of the archive',async()=>{
  const fetch=vi.fn().mockResolvedValue({ok:true,status:200,headers:{get:()=>null},json:async()=>({})});vi.stubGlobal('fetch',fetch);
  const id='a'.repeat(32);
  await api.getOptionsChain('MU','2026-11-20',id);
  await api.getOptionsGamePlan('MU',{snapshotId:id,holdingDays:14,searchPreferences:{min_dte:14,max_dte:60,min_abs_delta:null,max_abs_delta:null}});
  expect(fetch.mock.calls[0][0]).toContain(`/stocks/MU/options-snapshot/${id}/chain?expiry=2026-11-20`);
  expect(fetch.mock.calls[1][0]).toContain(`/stocks/MU/options-snapshot/${id}/game-plan?holding_days=14&min_dte=14&max_dte=60`);
});
it('requests capture explicitly and preserves the settled archive paths without an id',async()=>{
  const fetch=vi.fn().mockResolvedValue({ok:true,status:200,headers:{get:()=>null},json:async()=>({})});vi.stubGlobal('fetch',fetch);
  await api.refreshOptionsChain('MU');await api.getOptionsChain('MU');await api.getOptionsGamePlan('MU',{});
  expect(fetch.mock.calls[0][0]).toContain('/stocks/MU/options-refresh');expect(fetch.mock.calls[0][1].method).toBe('POST');
  expect(fetch.mock.calls[1][0]).toContain('/stocks/MU/options-chain');
  expect(fetch.mock.calls[2][0]).toContain('/stocks/MU/options-game-plan');
});
