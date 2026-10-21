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
const view=()=>render(<SWRConfig value={{provider:()=>new Map(),dedupingInterval:0}}><LittleRzyPanel/></SWRConfig>);
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
it('keeps only email preferences here and links admins to the Little RZY page',async()=>{
 view();await screen.findByLabelText('Morning short-term watchlist digest');
 expect(screen.queryByText('Preview morning candidates')).toBeNull();expect(screen.queryByText('Show RZY research paper ledger')).toBeNull();
 expect(api.rzyDigest).not.toHaveBeenCalled();expect(api.rzyPaper).not.toHaveBeenCalled();
});
