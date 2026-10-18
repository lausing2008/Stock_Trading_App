// @vitest-environment jsdom
import React from 'react';
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,it,expect,vi} from 'vitest';
const api=vi.hoisted(()=>({get:vi.fn(),put:vi.fn()}));
vi.mock('@/lib/workspaceApi',()=>({getWorkspaceLayout:()=>api.get(),putWorkspaceLayout:(...args:unknown[])=>api.put(...args),WorkflowError:class extends Error{constructor(message:string,public status:number){super(message);}}}));
import WorkspaceSyncControls from './WorkspaceSyncControls';
const layout={order:['chart'],hidden:{},closed:{},widths:{}};
beforeEach(()=>{localStorage.clear();vi.clearAllMocks();});afterEach(cleanup);
it('requires opt-in and pauses when device layouts disagree',async()=>{
 api.get.mockResolvedValue({revision:3,layout:{...layout,order:['plan']}});api.put.mockResolvedValue({revision:4,layout});
 render(<WorkspaceSyncControls storageKey="alice" layout={layout} onLoad={vi.fn()}/>);expect(api.get).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('checkbox'));await screen.findByRole('alert');expect(api.put).not.toHaveBeenCalled();
 fireEvent.click(screen.getByText('Replace account layout with this browser'));await waitFor(()=>expect(api.put).toHaveBeenCalledWith(3,layout));
});
it('ignores a remote response after the account component unmounts',async()=>{
 let finish!:(v:unknown)=>void;api.get.mockReturnValue(new Promise(r=>finish=r));const onLoad=vi.fn();
 const v=render(<WorkspaceSyncControls storageKey="alice" layout={layout} onLoad={onLoad}/>);fireEvent.click(screen.getByRole('checkbox'));v.unmount();
 await act(async()=>finish({revision:3,layout:{...layout,order:['plan']}}));expect(api.put).not.toHaveBeenCalled();expect(onLoad).not.toHaveBeenCalled();
});
it('loads a newer account layout on focus when browser edits are already saved',async()=>{
 localStorage.setItem('alice:cloud','1');
 api.get.mockResolvedValueOnce({revision:1,layout});
 const onLoad=vi.fn();
 render(<WorkspaceSyncControls storageKey="alice" layout={layout} onLoad={onLoad}/>);
 await screen.findByText('Account sync ready.');
 const remote={...layout,order:['plan']};api.get.mockResolvedValueOnce({revision:2,layout:remote});
 fireEvent(window,new Event('focus'));
 await waitFor(()=>expect(onLoad).toHaveBeenCalledWith(remote));
 expect(api.put).not.toHaveBeenCalled();
});
