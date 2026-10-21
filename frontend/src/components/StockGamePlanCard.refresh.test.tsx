// @vitest-environment jsdom
import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ getStockPlan: vi.fn(), draftPlanLevel: vi.fn(), confirmCopilotDraft: vi.fn(), createBoardPlan: vi.fn(),
  askCopilot: vi.fn(), cancelCopilot: vi.fn() }));
vi.mock('@/lib/copilot', () => ({ ...mocks, newRequestId: () => 'request-' + Math.random().toString(36).slice(2) }));
vi.mock('@/lib/api', () => ({ api: { createBoardPlan: mocks.createBoardPlan } }));
vi.mock('@/lib/ai', () => ({ isAiConfigured: () => true, getAiProviderLabel: () => 'Claude' }));
import StockGamePlanCard from './StockGamePlanCard';
import { setupFixture } from './__fixtures__/tradeSetup';
const plan = (fp: string) => ({ symbol: 'AAPL', packet_version: 7, packet_fingerprint: fp, evidence: {}, limitations: [],
  plan: { kind: 'entry', currency: 'USD', price_as_of: '2026-10-20T00:00:00', unsupported: false, position: null, position_refusals: [], unavailable: {},
    parameters: { note: 'n' }, groups: { rules: [{ key: 'rule.e1', display: 'Pullback rule text', meaning: 'm', state: 'OK', evidence_ids: [] }] },
    levels: [{ key: 'level.pullback_stop', price: 98, label: 'stop', role: 'stop', state: 'OK', evidence_ids: [] }], setups: [setupFixture()] } });
beforeEach(() => { Object.values(mocks).forEach(m => m.mockReset()); mocks.cancelCopilot.mockResolvedValue({ cancelled: true, note: '' });
  mocks.getStockPlan.mockResolvedValueOnce(plan('a'.repeat(24))).mockResolvedValueOnce(plan('b'.repeat(24)));
  mocks.askCopilot.mockRejectedValueOnce(Object.assign(new Error('plan changed; refresh it before requesting an LLM alternative'), { status: 409 }))
    .mockReturnValue(new Promise(() => {})); });
afterEach(cleanup);
it('refreshes the rules-based plan on 409 instead of retrying a stale snapshot, and stays on the LLM view', async () => {
  render(<StockGamePlanCard symbol="AAPL" onLevelsChange={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Build plan' })); await screen.findByText('Pullback rule text');
  fireEvent.click(screen.getByRole('button', { name: 'LLM alternative plan' }));
  fireEvent.click(screen.getByRole('button', { name: 'Generate LLM alternative' }));
  await screen.findByText(/New data arrived after the plan was built/);
  await waitFor(() => expect(mocks.getStockPlan).toHaveBeenCalledTimes(2));
  expect(mocks.getStockPlan.mock.calls[1][1]).toEqual(mocks.getStockPlan.mock.calls[0][1]);
  expect(screen.queryByText('plan changed; refresh it before requesting an LLM alternative')).toBeNull();
  fireEvent.click(await screen.findByRole('button', { name: 'Generate LLM alternative' }));
  await waitFor(() => expect(mocks.askCopilot).toHaveBeenCalledTimes(2));
  expect(mocks.askCopilot.mock.calls[0][0].planContext.fingerprint).toBe('a'.repeat(24));
  expect(mocks.askCopilot.mock.calls[1][0].planContext.fingerprint).toBe('b'.repeat(24));
});
