// @vitest-environment jsdom
import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach,beforeEach,expect,it,vi } from 'vitest';
import { SWRConfig } from 'swr';
const api=vi.hoisted(()=>({rzyPreferences:vi.fn(),saveRzyPreferences:vi.fn(),rzyDigest:vi.fn(),rzyPaper:vi.fn()}));
vi.mock('@/lib/api',()=>({api}));
import LittleRzyPanel from './LittleRzyPanel';
const prefs={pattern_email:false,morning_digest:false,timeframes:['5m','15m','1h']};
beforeEach(()=>{Object.values(api).forEach(m=>m.mockReset());api.rzyPreferences.mockResolvedValue(prefs);api.saveRzyPreferences.mockImplementation(async x=>x);});
afterEach(cleanup);
const view=(paperOnly=false)=>render(<SWRConfig value={{provider:()=>new Map(),dedupingInterval:0}}><LittleRzyPanel paperOnly={paperOnly}/></SWRConfig>);
it('starts both channels off and writes only an explicit selected preference',async()=>{
 view();const digest=await screen.findByLabelText('Morning short-term watchlist digest');
 expect((digest as HTMLInputElement).checked).toBe(false);
 fireEvent.click(digest);await screen.findByText('Preferences saved.');
 expect(api.saveRzyPreferences).toHaveBeenCalledWith({...prefs,morning_digest:true});
 expect(api.rzyDigest).not.toHaveBeenCalled();
});
it('keeps the prior setting visible after a failed save and never displays a successful opt-in',async()=>{
 api.saveRzyPreferences.mockRejectedValue(new Error('Offline'));view();
 fireEvent.click(await screen.findByLabelText('Little RZY continuation and reversal-watch emails'));
 await screen.findByText('Offline');expect((screen.getByLabelText('Little RZY continuation and reversal-watch emails') as HTMLInputElement).checked).toBe(false);
 expect(screen.queryByText('Preferences saved.')).toBeNull();
});
it('previews no-candidate and exclusion reasons without sending mail or enabling a channel',async()=>{
 api.rzyDigest.mockResolvedValue({candidates:[],exclusions:[{symbol:'TEST',timeframe:'5m',reason:'stale'}],note:'Prior completed data only'});
 view();fireEvent.click(screen.getByText('Preview morning candidates'));
 await screen.findByText('No qualifying candidates. Do not force a trade.');expect(screen.getByText('TEST 5m: stale')).toBeTruthy();
 expect(api.saveRzyPreferences).not.toHaveBeenCalled();
});
it('exposes an independent research paper ledger without writing existing portfolio balances',async()=>{
 api.rzyPaper.mockResolvedValue({note:'Research only',positions:[{symbol:'TEST',timeframe:'5m',state:'CLOSED',cancelled:true,reason:'data_unverified'}]});
 view(true);expect(api.rzyPreferences).not.toHaveBeenCalled();
 fireEvent.click(screen.getByText('Show RZY research paper ledger'));await screen.findByText(/data_unverified/);
 expect(api.saveRzyPreferences).not.toHaveBeenCalled();
});
