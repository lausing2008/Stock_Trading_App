// @vitest-environment jsdom
import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,it,expect,vi} from 'vitest';
const mocks=vi.hoisted(()=>({request:vi.fn()}));
vi.mock('@/lib/workspaceApi',()=>({workflowRequest:(...args:unknown[])=>mocks.request(...args)}));
vi.mock('@/lib/copilot',()=>({newRequestId:()=> 'close-confirm-001'}));
import DailyCloseAlerts from './DailyCloseAlerts';
afterEach(()=>{cleanup();vi.clearAllMocks();});
it('previews an explicit fixed daily-close rule and saves only on confirm',async()=>{
 mocks.request.mockImplementation((path:string,init?:RequestInit)=>Promise.resolve(init?.method==='POST'?{id:1,symbol:'TEST',threshold:95,condition:'below',active:true}:[]));
 render(<DailyCloseAlerts symbol="TEST" currency="USD"/>);
 await waitFor(()=>expect(mocks.request).toHaveBeenCalledTimes(1));
 fireEvent.change(screen.getByRole('spinbutton'),{target:{value:'95'}});fireEvent.click(screen.getByText('Review close alert'));
 expect(mocks.request).toHaveBeenCalledTimes(1);expect(screen.getByText(/Only sessions closing after enrollment/)).toBeTruthy();
 fireEvent.click(screen.getByText('Confirm daily-close alert'));await waitFor(()=>expect(mocks.request).toHaveBeenCalledTimes(2));
 expect(JSON.parse(mocks.request.mock.calls[1][1].body)).toEqual({symbol:'TEST',threshold:95,condition:'below',origin:'typed',request_id:'close-confirm-001'});
});
