// @vitest-environment jsdom
import React, { useState } from 'react';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import StockDetailWorkspace, { StockDetailWidget } from './StockDetailWorkspace';

function Counter() { const [n, setN] = useState(0); return <button onClick={() => setN(n + 1)}>Count {n}</button>; }
const widgets = <>
  <StockDetailWidget id="chart" title="Chart" wide><Counter /></StockDetailWidget>
  <StockDetailWidget id="plan" title="Plan"><div>Entry 100, stop 98</div></StockDetailWidget>
  <StockDetailWidget id="research" title="Research" collapsed><div>Evidence</div></StockDetailWidget>
</>;
function View({ account = 'alice', includeMissing = false }) {
  return <StockDetailWorkspace storageKey={`${account}:layout`}>
    {widgets.props.children}
    {includeMissing && <StockDetailWidget id="missing" title="Missing">{false}</StockDetailWidget>}
  </StockDetailWorkspace>;
}
function order() { return screen.getAllByRole('region').map(n => n.getAttribute('aria-label')).filter(n => n?.endsWith(' widget')); }
beforeEach(() => localStorage.clear());
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('starts supporting details collapsed and saves expansion through remount', () => {
  const view = render(<View includeMissing />);
  expect(screen.getByRole('button', { name: 'Research' }).getAttribute('aria-expanded')).toBe('false');
  expect(screen.queryByRole('region', { name: 'Missing widget' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Research' })); view.unmount(); render(<View />);
  expect(screen.getByRole('button', { name: 'Research' }).getAttribute('aria-expanded')).toBe('true');
});

it('reorders with accessible controls, preserves live widget state and restores saved order', () => {
  const view = render(<View />); fireEvent.click(screen.getByRole('button', { name: 'Count 0' }));
  fireEvent.click(screen.getByRole('button', { name: 'Arrange widgets' }));
  fireEvent.click(screen.getByRole('button', { name: 'Move Plan earlier' }));
  expect(order()).toEqual(['Plan widget', 'Chart widget', 'Research widget']);
  expect(screen.getByRole('button', { name: 'Count 1' })).toBeTruthy();
  view.unmount(); render(<View />); expect(order()[0]).toBe('Plan widget');
  fireEvent.click(screen.getByRole('button', { name: 'Reset layout' }));
  expect(order()[0]).toBe('Chart widget');
});

it('accepts only internal handle drags while arranging; drops do not submit content', () => {
  render(<View />);
  fireEvent.drop(screen.getByRole('region', { name: 'Plan widget' })); expect(order()[0]).toBe('Chart widget');
  fireEvent.click(screen.getByRole('button', { name: 'Arrange widgets' }));
  const dataTransfer = { effectAllowed: '', setData: vi.fn() };
  fireEvent.dragStart(screen.getByRole('button', { name: 'Drag Chart' }), { dataTransfer });
  const target = screen.getByRole('region', { name: 'Plan widget' }).querySelector('.stock-workspace-widget-header')!;
  fireEvent.dragOver(target);
  fireEvent.drop(target);
  expect(order()[0]).toBe('Plan widget'); expect(dataTransfer.setData).toHaveBeenCalledWith('text/plain', 'chart');
});

it('isolates accounts and handles corrupt or unavailable storage', () => {
  localStorage.setItem('alice:layout', JSON.stringify({ order: ['unknown', 'plan', 'plan'], closed: { research: false } }));
  const view = render(<View />); expect(order()[0]).toBe('Plan widget');
  view.rerender(<View account="bob" />); expect(order()[0]).toBe('Chart widget');
  expect(within(screen.getByRole('region', { name: 'Research widget' })).getByRole('button', { name: 'Research' }).getAttribute('aria-expanded')).toBe('false');
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('No storage'); });
  fireEvent.click(screen.getByRole('button', { name: 'Research' }));
  expect(screen.getByRole('button', { name: 'Research' }).getAttribute('aria-expanded')).toBe('true');
  localStorage.clear(); view.unmount();
  vi.restoreAllMocks(); localStorage.setItem('alice:layout', '{bad json'); render(<View />); expect(order()[0]).toBe('Chart widget');
});

it('persists width and hide preferences without losing mounted widget state', () => {
  const view = render(<View />); fireEvent.click(screen.getByRole('button', { name: 'Count 0' }));
  fireEvent.click(screen.getByRole('button', { name: 'Arrange widgets' }));
  fireEvent.change(screen.getByLabelText('Width for Chart'), { target: { value: 'full' } });
  expect(screen.getByRole('region', { name: 'Chart widget' }).className).toContain('stock-workspace-full');
  fireEvent.click(screen.getByRole('button', { name: 'Hide Chart' }));
  expect(screen.queryByRole('button', { name: 'Count 1' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Show Chart' }));
  expect(screen.getByRole('button', { name: 'Count 1' })).toBeTruthy();
  view.unmount(); render(<View />);
  expect(screen.getByRole('region', { name: 'Chart widget' }).className).toContain('stock-workspace-full');
});

it('imports validated layout files and refuses late results after account changes', async () => {
  const view = render(<View />);
  const json = JSON.stringify({ version: 1, order: ['plan', 'chart'], hidden: { research: true }, widths: { plan: 'full' } });
  await act(async () => fireEvent.change(screen.getByLabelText('Import workspace layout'), { target: { files: [{ size: json.length, text: async () => json }] } }));
  expect(order()[0]).toBe('Plan widget');
  expect(screen.getByRole('region', { name: 'Plan widget' }).className).toContain('stock-workspace-full');
  expect(screen.getByRole('button', { name: 'Show Research' })).toBeTruthy();
  await act(async () => fireEvent.change(screen.getByLabelText('Import workspace layout'), { target: { files: [{ size: 40000 }] } }));
  expect(screen.getByRole('alert').textContent).toContain('32 KB'); expect(order()[0]).toBe('Plan widget');
  let resolve!: (text: string) => void;
  fireEvent.change(screen.getByLabelText('Import workspace layout'), { target: { files: [{ size: 100, text: () => new Promise<string>(r => { resolve = r; }) }] } });
  view.rerender(<View account="bob" />);
  await act(async () => resolve(json));
  expect(order()[0]).toBe('Chart widget'); expect(localStorage.getItem('bob:layout')).toBeNull();
});

it('notifies lazy widgets when expanded, collapsed or hidden', () => {
  const onExpandedChange = vi.fn();
  render(<StockDetailWorkspace storageKey="lazy"><StockDetailWidget id="ownership" title="Ownership" collapsed onExpandedChange={onExpandedChange}><p>Data</p></StockDetailWidget></StockDetailWorkspace>);
  expect(onExpandedChange).toHaveBeenLastCalledWith(false);
  fireEvent.click(screen.getByRole('button', { name: 'Ownership' })); expect(onExpandedChange).toHaveBeenLastCalledWith(true);
  fireEvent.click(screen.getByRole('button', { name: 'Arrange widgets' }));
  fireEvent.click(screen.getByRole('button', { name: 'Hide Ownership' })); expect(onExpandedChange).toHaveBeenLastCalledWith(false);
});

it('keeps an initially empty lazy panel reachable so expansion can load its content', () => {
  function LazyPanel() {
    const [open, setOpen] = useState(false);
    return <StockDetailWorkspace storageKey="lazy-empty"><StockDetailWidget id="dividends" title="Dividends" collapsed onExpandedChange={setOpen}>
      {open && <p>Dividend data requested</p>}
    </StockDetailWidget></StockDetailWorkspace>;
  }
  render(<LazyPanel />);
  expect(screen.queryByText('Dividend data requested')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Dividends' }));
  expect(screen.getByText('Dividend data requested')).toBeTruthy();
});

it('pins the full-width chart/plan and preserves research state through tabs and Focus mode', () => {
  localStorage.setItem('trade', JSON.stringify({ order: ['company', 'game-plan', 'chart'], closed: { chart: true }, hidden: { chart: true }, widths: { chart: 'compact' } }));
  render(<StockDetailWorkspace storageKey="trade" trading>
    <StockDetailWidget id="chart" title="Chart"><p>Chart data</p></StockDetailWidget>
    <StockDetailWidget id="game-plan" title="Plan"><p>Plan data</p></StockDetailWidget>
    <StockDetailWidget id="signal" title="Signal" group="signals"><p>Signal data</p></StockDetailWidget>
    <StockDetailWidget id="technical" title="Technical" group="technicals"><Counter /></StockDetailWidget>
    <StockDetailWidget id="company" title="Company" group="company"><p>Company data</p></StockDetailWidget>
  </StockDetailWorkspace>);
  expect(order().slice(0, 2)).toEqual(['Chart widget', 'Plan widget']);
  expect(screen.getByRole('region', { name: 'Chart widget' }).className).toContain('stock-workspace-full');
  expect(screen.queryByRole('region', { name: 'Technical widget' })).toBeNull();
  fireEvent.click(screen.getByRole('tab', { name: 'Technicals' })); fireEvent.click(screen.getByRole('button', { name: 'Count 0' }));
  fireEvent.click(screen.getByRole('tab', { name: 'Company' })); fireEvent.keyDown(screen.getByRole('tab', { name: 'Company' }), { key: 'ArrowLeft' });
  expect(screen.getByRole('button', { name: 'Count 1' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Focus mode' }));
  expect(screen.queryByRole('tabpanel')).toBeNull(); expect(document.body.style.overflow).toBe('hidden');
  fireEvent.keyDown(document, { key: 'Escape' });
  expect(screen.getByRole('button', { name: 'Count 1' })).toBeTruthy(); expect(document.body.style.overflow).toBe('');
  expect(JSON.parse(localStorage.getItem('trade')!).hidden.chart).toBe(true);
});
