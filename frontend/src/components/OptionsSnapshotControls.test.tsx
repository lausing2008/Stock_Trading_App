// @vitest-environment jsdom
import React from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '@/lib/api';
import OptionsSnapshotControls from './OptionsSnapshotControls';

import { snapshot } from '@/testFixtures/optionSnapshot';

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-24T15:00:00Z')); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

it('starts no capture automatically and emits only a matching completed snapshot', async () => {
  const refresh=vi.spyOn(api,'refreshOptionsChain').mockResolvedValue({available:true,status:'captured',snapshot});
  const onSnapshot=vi.fn();
  render(<OptionsSnapshotControls symbol="MU" onSnapshot={onSnapshot} />);
  expect(refresh).not.toHaveBeenCalled();
  await act(async () => fireEvent.click(screen.getByRole('button',{name:'Refresh option chain & recalculate'})));
  expect(refresh).toHaveBeenCalledOnce();expect(refresh).toHaveBeenCalledWith('MU');
  expect(onSnapshot).toHaveBeenCalledWith(snapshot);
});

it('prevents double requests while a full capture is pending', async () => {
  let resolve!: (v: Awaited<ReturnType<typeof api.refreshOptionsChain>>) => void;
  const refresh=vi.spyOn(api,'refreshOptionsChain').mockReturnValue(new Promise(r=>{resolve=r;}));
  render(<OptionsSnapshotControls symbol="MU" onSnapshot={vi.fn()} />);
  const button=screen.getByRole('button',{name:'Refresh option chain & recalculate'});
  fireEvent.click(button);fireEvent.click(button);
  expect(refresh).toHaveBeenCalledOnce();expect((button as HTMLButtonElement).disabled).toBe(true);
  await act(async()=>resolve({available:true,status:'captured',snapshot}));
});

it('shows capture and stock times separately, enforces cooldown and ages into expiry', () => {
  render(<OptionsSnapshotControls symbol="MU" snapshot={snapshot} onSnapshot={vi.fn()} />);
  expect(screen.getByRole('status').textContent).toContain('2026-10-23T20:00:00Z');
  expect(screen.getByRole('status').textContent).toContain('2026-10-24T15:00:00Z');
  expect(screen.getByRole('status').textContent).toContain('not a verified quote timestamp');
  expect((screen.getByRole('button',{name:'Refresh available in 60s'}) as HTMLButtonElement).disabled).toBe(true);
  act(()=>vi.advanceTimersByTime(61_000));
  expect((screen.getByRole('button',{name:'Refresh option chain & recalculate'}) as HTMLButtonElement).disabled).toBe(false);
  act(()=>vi.advanceTimersByTime(600_000));
  expect(screen.getByText('Snapshot expired — refresh required.')).toBeTruthy();
});

it('retains previous data with a visible provider failure and honors its retry delay', async () => {
  vi.setSystemTime(new Date('2026-10-24T15:02:00Z'));
  vi.spyOn(api,'refreshOptionsChain').mockResolvedValue({available:false,status:'rate_limited',message:'Provider rate limited.',retry_after:60});
  const onSnapshot=vi.fn();render(<OptionsSnapshotControls symbol="MU" snapshot={snapshot} onSnapshot={onSnapshot} />);
  await act(async()=>fireEvent.click(screen.getByRole('button',{name:'Refresh option chain & recalculate'})));
  expect(onSnapshot).not.toHaveBeenCalled();
  expect(screen.getByRole('alert').textContent).toContain('Previous data remains displayed');
  expect((screen.getByRole('button',{name:'Refresh available in 60s'}) as HTMLButtonElement).disabled).toBe(true);
});

it('rejects the wrong symbol and never silently switches to the archive on errors', async () => {
  vi.spyOn(api,'refreshOptionsChain').mockResolvedValue({available:true,status:'captured',snapshot:{...snapshot,symbol:'AAPL'}});
  const cb=vi.fn();render(<OptionsSnapshotControls symbol="MU" onSnapshot={cb} />);
  await act(async()=>fireEvent.click(screen.getByRole('button',{name:'Refresh option chain & recalculate'})));
  expect(cb).not.toHaveBeenCalled();expect(screen.getByRole('alert')).toBeTruthy();
});

it('ignores a late capture after changing symbols or unmounting', async () => {
  let resolve!: (v: Awaited<ReturnType<typeof api.refreshOptionsChain>>) => void;
  vi.spyOn(api,'refreshOptionsChain').mockReturnValue(new Promise(r=>{resolve=r;}));
  const cb=vi.fn();const view=render(<OptionsSnapshotControls symbol="MU" onSnapshot={cb} />);
  fireEvent.click(screen.getByRole('button',{name:'Refresh option chain & recalculate'}));
  view.rerender(<OptionsSnapshotControls symbol="AAPL" onSnapshot={cb} />);
  await act(async()=>resolve({available:true,status:'captured',snapshot}));
  expect(cb).not.toHaveBeenCalled();
  expect(screen.getByRole('button',{name:'Refresh option chain & recalculate'})).toBeTruthy();
  view.unmount();
});

it('returns to archived mode only through an explicit user action', () => {
  const cb=vi.fn();render(<OptionsSnapshotControls symbol="MU" snapshot={snapshot} onSnapshot={cb} />);
  fireEvent.click(screen.getByRole('button',{name:'Use settled archive'}));
  expect(cb).toHaveBeenCalledWith(undefined);
});

it('can refresh one declared expiration when a large all-expiry capture is unsuitable', async()=>{
  const refresh=vi.spyOn(api,'refreshOptionsChain').mockResolvedValue({available:true,status:'captured',snapshot:{...snapshot,capture_expiry:'2026-11-20'}});
  const cb=vi.fn();render(<OptionsSnapshotControls symbol="MU" expiries={['2026-11-20']} onSnapshot={cb} />);
  fireEvent.change(screen.getByLabelText('Refresh expiration scope'),{target:{value:'2026-11-20'}});
  await act(async()=>fireEvent.click(screen.getByRole('button',{name:'Refresh option chain & recalculate'})));
  expect(refresh).toHaveBeenCalledWith('MU','2026-11-20');
  expect(cb).toHaveBeenCalledWith(expect.objectContaining({capture_expiry:'2026-11-20'}));
});

it('backs off an HTTP rate limit without starting an automatic retry', async()=>{
  const refresh=vi.spyOn(api,'refreshOptionsChain').mockRejectedValue(new Error('429 Too Many Requests'));
  render(<OptionsSnapshotControls symbol="MU" onSnapshot={vi.fn()} />);
  await act(async()=>fireEvent.click(screen.getByRole('button',{name:'Refresh option chain & recalculate'})));
  expect((screen.getByRole('button',{name:'Refresh available in 60s'}) as HTMLButtonElement).disabled).toBe(true);
  act(()=>vi.advanceTimersByTime(61_000));
  expect(refresh).toHaveBeenCalledOnce();
  expect(screen.getByRole('button',{name:'Refresh option chain & recalculate'})).toBeTruthy();
});

it('does not apply a capture after unmounting while it is still pending', async()=>{
  let resolve!: (v:Awaited<ReturnType<typeof api.refreshOptionsChain>>)=>void;
  vi.spyOn(api,'refreshOptionsChain').mockReturnValue(new Promise(r=>{resolve=r;}));
  const cb=vi.fn();const view=render(<OptionsSnapshotControls symbol="MU" onSnapshot={cb} />);
  fireEvent.click(screen.getByRole('button',{name:'Refresh option chain & recalculate'}));
  view.unmount();await act(async()=>resolve({available:true,status:'captured',snapshot}));
  expect(cb).not.toHaveBeenCalled();
});

it('does not publish an incomplete response as a successful refresh', async()=>{
  vi.spyOn(api,'refreshOptionsChain').mockResolvedValue({available:true,status:'captured',snapshot:{...snapshot,pagination_complete:false}});
  const cb=vi.fn();render(<OptionsSnapshotControls symbol="MU" onSnapshot={cb} />);
  await act(async()=>fireEvent.click(screen.getByRole('button',{name:'Refresh option chain & recalculate'})));
  expect(cb).not.toHaveBeenCalled();expect(screen.getByRole('alert')).toBeTruthy();
});
