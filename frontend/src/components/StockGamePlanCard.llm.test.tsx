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
const FP = 'f'.repeat(24);
const plan = { symbol: 'AAPL', packet_version: 9, packet_fingerprint: FP, evidence: {}, limitations: [],
  plan: { kind: 'entry', currency: 'USD', price_as_of: '2026-10-20T00:00:00', unsupported: false, position: null, position_refusals: [], unavailable: {},
    parameters: { note: 'n' }, groups: { rules: [{ key: 'rule.e1', display: 'Pullback rule text', meaning: 'm', state: 'OK', evidence_ids: [] }] },
    levels: [{ key: 'level.pullback_stop', price: 98, label: 'stop', role: 'stop', state: 'OK', evidence_ids: [] }], setups: [setupFixture()] } };
const proposal = (state: string, entry = 100) => ({ ...setupFixture(), id: 'llm', key: 'proposal.llm', title: 'LLM-proposed plan', state, kind: 'pullback',
  source: 'llm_proposal', entry, stop: 97, targets: [{ price: 106, label: 'Model-chosen objective', reward_risk: 2 }, { price: 109, label: 'Model-chosen stretch objective', reward_risk: 3 }],
  reasons: state === 'blocked' ? ['Latest close is below a falling 50-bar average; no new long under the trend rule.'] : [],
  derivation: [{ level: 'entry', anchor: 'level.low20', anchor_price: 99, atr_multiple: 0.5, atr: 2, price: entry }], price_as_of: '2026-10-20T00:00:00' });
const reply = (p: object) => ({ message: { content: 'The model reasoning.', model: 'claude-x', status: 'grounded', request_id: 'r1',
  plan_proposal: p, plan_setups: [], selected_setup_id: null, packet_fingerprint: FP, price_as_of: '2026-10-20T00:00:00' } });
beforeEach(() => { Object.values(mocks).forEach(m => m.mockReset()); mocks.getStockPlan.mockResolvedValue(plan); mocks.createBoardPlan.mockResolvedValue({ id: 1 }); });
afterEach(cleanup);
async function openLlm(levels = vi.fn()) {
  render(<StockGamePlanCard symbol="AAPL" onLevelsChange={levels} />);
  fireEvent.click(screen.getByRole('button', { name: 'Build plan' })); await screen.findByText('Pullback rule text');
  fireEvent.click(screen.getByRole('button', { name: 'Select LLM plan' }));
  fireEvent.click(screen.getByRole('button', { name: 'Generate LLM alternative' }));
  return levels;
}
const save = () => screen.getByRole('button', { name: 'Save selected plan to Trade Board' }) as HTMLButtonElement;

it('plots only the selected LLM plan and saves that exact plan with its reasoning, model and snapshot', async () => {
  mocks.askCopilot.mockResolvedValueOnce(reply(proposal('conditional')));
  const levels = await openLlm();
  await screen.findByLabelText('LLM-proposed plan');
  await waitFor(() => expect(levels.mock.calls.at(-1)?.[0].map((l: { price: number }) => l.price)).toEqual([100, 97, 106, 109]));
  fireEvent.click(save()); await screen.findByText(/Saved as a new Trade Board plan/);
  const body = mocks.createBoardPlan.mock.calls[0][0];
  expect(body).toMatchObject({ symbol: 'AAPL', entry_price: 100, stop_loss: 97, take_profit: 106, source: 'gameplan-llm' });
  expect(body.game_plan).toMatchObject({ source: 'llm_proposal', reasoning: 'The model reasoning.', model: 'claude-x', request_id: 'r1',
    price_as_of: '2026-10-20T00:00:00', packet_fingerprint: FP });
  expect(body.game_plan.derivation[0]).toMatchObject({ anchor: 'level.low20', atr_multiple: 0.5 });
  // Switching back plots the rules-based plan, not the LLM levels.
  fireEvent.click(screen.getByRole('button', { name: 'Select rules-based plan' }));
  await waitFor(() => expect(levels.mock.calls.at(-1)?.[0].map((l: { price: number }) => l.price)).toEqual([99, 98, 101, 102]));
});

it('a new comparison never changes a saved plan: it can only be saved as another new plan', async () => {
  mocks.askCopilot.mockResolvedValueOnce(reply(proposal('conditional'))).mockResolvedValueOnce(reply(proposal('conditional', 99.5)));
  await openLlm(); await screen.findByLabelText('LLM-proposed plan');
  fireEvent.click(save()); await screen.findByText(/Saved as a new Trade Board plan/);
  fireEvent.click(screen.getByRole('button', { name: 'Generate another LLM alternative' }));
  await waitFor(() => expect(save().disabled).toBe(false));
  expect(mocks.createBoardPlan).toHaveBeenCalledTimes(1);
  fireEvent.click(save()); await screen.findByText(/Saved as a new Trade Board plan/);
  expect(mocks.createBoardPlan).toHaveBeenCalledTimes(2);
  expect(mocks.createBoardPlan.mock.calls[1][0].entry_price).toBe(99.5);
});

it('disables saving a blocked or rejected proposal and says why', async () => {
  mocks.askCopilot.mockResolvedValueOnce(reply(proposal('blocked')));
  const levels = await openLlm(); await screen.findByLabelText('LLM-proposed plan');
  expect(save().disabled).toBe(true); expect(screen.getByText(/blocked by a mandatory rule/)).toBeTruthy();
  expect(levels.mock.calls.at(-1)?.[0]).toEqual([]);
  cleanup();
  mocks.askCopilot.mockResolvedValueOnce(reply(proposal('rejected')));
  await openLlm(); await screen.findByText(/failed the code checks and cannot be selected or saved/);
  expect(save().disabled).toBe(true);
});
