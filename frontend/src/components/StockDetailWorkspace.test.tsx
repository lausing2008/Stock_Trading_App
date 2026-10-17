// @vitest-environment jsdom
import React, { useState } from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
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
