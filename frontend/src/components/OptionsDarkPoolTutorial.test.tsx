// @vitest-environment jsdom
import React from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
const auth = vi.hoisted(() => ({ getSession: vi.fn(), hasAdvancedAccess: vi.fn(), router: { replace: vi.fn() } }));
vi.mock('next/router', () => ({ useRouter: () => auth.router }));
vi.mock('next/head', () => ({ default: () => null }));
vi.mock('@/lib/auth', () => ({ getSession: auth.getSession, hasAdvancedAccess: auth.hasAdvancedAccess }));
import TutorialPage, { OptionsDarkPoolTutorialContent } from '../pages/options-dark-pool-tutorial';
beforeEach(() => { auth.getSession.mockReset(); auth.hasAdvancedAccess.mockReset(); auth.router.replace.mockReset(); });
afterEach(cleanup);
it('redirects unauthenticated visitors instead of rendering the Learning content', async () => {
  auth.getSession.mockReturnValue(null);
  render(<TutorialPage />);
  await waitFor(() => expect(auth.router.replace).toHaveBeenCalledWith('/login'));
  expect(screen.queryByRole('heading', { level: 1 })).toBeNull();
});
it('keeps the page guard aligned with Advanced Learning access', async () => {
  auth.getSession.mockReturnValue({ username: 'basic', role: 'user', tier: 'basic' });
  auth.hasAdvancedAccess.mockReturnValue(false);
  render(<TutorialPage />);
  await waitFor(() => expect(auth.router.replace).toHaveBeenCalledWith('/'));
  expect(screen.queryByRole('heading', { level: 1 })).toBeNull();
});
it.each(['advanced', 'admin'])('shows the tutorial for permitted %s access', async access => {
  auth.getSession.mockReturnValue({ username: 'test', role: access === 'admin' ? 'admin' : 'user', tier: access === 'admin' ? 'basic' : 'advanced' });
  auth.hasAdvancedAccess.mockReturnValue(true);
  render(<TutorialPage />);
  expect(await screen.findByRole('heading', { name: 'Options & Dark Pool — Feature Tutorial' })).toBeTruthy();
  expect(auth.router.replace).not.toHaveBeenCalled();
});
it('covers the real workflow, uncertainty and navigable topic anchors', () => {
  render(<OptionsDarkPoolTutorialContent />);
  const toc = screen.getByRole('navigation', { name: 'Tutorial topics' });
  for (const link of within(toc).getAllByRole('link')) {
    expect(document.querySelector(link.getAttribute('href')!)).not.toBeNull();
  }
  expect(screen.getByRole('link', { name: 'Open TSM’s Options tab' }).getAttribute('href')).toBe('/stock/TSM?tab=options');

});
it('lets users practice sorting and selecting without changing real stock state', () => {
  render(<OptionsDarkPoolTutorialContent />);
  expect(screen.getByRole('status', { name: 'Practice selection' }).textContent).toContain('Original reference');
  fireEvent.click(within(document.getElementById('practice')!).getByText(/Compare candidate costs, risk and liquidity/));
  fireEvent.change(screen.getByLabelText('Sort returned alternatives'), { target: { value: 'spread' } });
  const table = screen.getByRole('table', { name: 'Returned option candidate comparison' });
  expect(within(table).getAllByRole('row')[2].textContent).toContain('SAMPLE-CALL-105');
  fireEvent.click(screen.getByRole('button', { name: 'Compare alternative 2' }));
  expect(screen.getByRole('status', { name: 'Practice selection' }).textContent).toContain('Alternative 2');
  fireEvent.change(screen.getByLabelText('Sort returned alternatives'), { target: { value: 'oi' } });
  expect(screen.getByRole('status', { name: 'Practice selection' }).textContent).toContain('Alternative 2');
  expect(document.body.textContent).toContain('Made-up example, not TSM quotes');
});
