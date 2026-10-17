// @vitest-environment jsdom
import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ getStockPlan: vi.fn(), draftPlanLevel: vi.fn(), confirmCopilotDraft: vi.fn(), createBoardPlan: vi.fn() }));
vi.mock('@/lib/copilot', () => ({ ...mocks, newRequestId: () => 'stable-request-0001' }));
vi.mock('@/lib/api', () => ({ api: { createBoardPlan: mocks.createBoardPlan } }));
vi.mock('@/lib/ai', () => ({ isAiConfigured: () => false, getAiProviderLabel: () => '' }));
import StockGamePlanCard from './StockGamePlanCard';
import { setupFixture } from './__fixtures__/tradeSetup';
const fixture = () => ({ symbol: 'AAPL', packet_version: 7, packet_fingerprint: 'abc'.repeat(8),
  evidence: { calc1: { source: 'stored prices', value: 99 } }, limitations: [],
  plan: { kind: 'entry', currency: 'HKD', price_as_of: '2026-10-15T20:00:00', unsupported: false,
    position: null, position_refusals: [], unavailable: {}, parameters: { note: 'Textbook conventions; not tuned' },
    groups: { rules: [{ key: 'rule.e1', display: 'If a daily close holds above HK$99, consider the pullback rule.', meaning: 'Pullback condition', state: 'OK', evidence_ids: ['calc1'] }], records: [{ key: 'record.swing', display: 'Too few to judge; none verified.', meaning: 'History', state: 'OK', evidence_ids: [] }] },
    levels: [{ key: 'level.low20', price: 99, label: '20-bar low', role: 'support', state: 'OK', evidence_ids: ['calc1'] },
      { key: 'level.high20', price: 101, label: '20-bar high', role: 'resistance', state: 'OK', evidence_ids: ['calc1'] },
      { key: 'level.pullback_stop', price: 98, label: 'Pullback stop candidate', role: 'stop', state: 'OK', evidence_ids: ['calc1'] }] } });
beforeEach(() => { Object.values(mocks).forEach(m => m.mockReset()); mocks.getStockPlan.mockResolvedValue(fixture()); mocks.createBoardPlan.mockResolvedValue({ id: 1 }); });
afterEach(cleanup);
async function build() { fireEvent.click(screen.getByRole('button', { name: 'Build plan' })); await screen.findByText('If a daily close holds above HK$99, consider the pullback rule.'); }
it('loads code-written rules without an AI key, publishes numeric chart levels and expands evidence', async () => {
  const levels = vi.fn(); render(<StockGamePlanCard symbol="AAPL" onLevelsChange={levels} />); await build();
  expect(mocks.getStockPlan.mock.calls[0].slice(0, 2)).toEqual(['AAPL', { kind: 'entry' }]);
  expect(levels.mock.calls.at(-1)?.[0].map((x: { price: number }) => x.price)).toEqual([99, 101, 98]);
  fireEvent.click(screen.getByText('Evidence')); expect(screen.getByLabelText('Plan evidence').textContent).toContain('stored prices');
  fireEvent.click(screen.getByRole('checkbox')); expect(levels.mock.calls.at(-1)?.[0]).toEqual([]);
});
it('sends the held position and supplied stop as typed numeric inputs', async () => {
  render(<StockGamePlanCard symbol="AAPL" onLevelsChange={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('Plan for'), { target: { value: 'holding' } });
  for (const [key, value] of Object.entries({ entry: '100', shares: '10', stop: '98', risk: '400' })) fireEvent.change(screen.getByLabelText(key), { target: { value } });
  await build(); expect(mocks.getStockPlan.mock.calls[0][1]).toEqual({ kind: 'holding', entry: 100, shares: 10, stop: 98, risk: 400 });
});
it('freezes a draft first and saves only after explicit confirmation', async () => {
  const draft = { id: 'd1', kind: 'price_alert', arguments: { symbol: 'AAPL', condition: 'below', threshold: 98 }, summary: 'Observed price at or below HK$98', side_effects: ['Sends email'], rule_notes: ['Intraday crossing is not a daily-close condition.'], expires_at: new Date(Date.now() + 600000).toISOString(), status: 'proposed' };
  mocks.draftPlanLevel.mockResolvedValue({ message: { id: 8, drafts: [draft] } });
  mocks.confirmCopilotDraft.mockResolvedValue({ draft: { ...draft, status: 'saved' } });
  render(<StockGamePlanCard symbol="AAPL" onLevelsChange={vi.fn()} />); await build();
  fireEvent.click(screen.getByText('Draft alert: Pullback stop candidate'));
  await screen.findByText('Intraday crossing is not a daily-close condition.'); expect(mocks.confirmCopilotDraft).not.toHaveBeenCalled();
  expect(mocks.draftPlanLevel).toHaveBeenCalledWith('AAPL', { kind: 'entry' }, 'level.pullback_stop', 'abc'.repeat(8), 'stable-request-0001');
  fireEvent.click(screen.getByRole('button', { name: /^Save$/ })); await waitFor(() => expect(mocks.confirmCopilotDraft).toHaveBeenCalledWith(8, 'd1'));
});
it('saves a compatible board snapshot from backend values with currency and provenance', async () => {
  render(<StockGamePlanCard symbol="AAPL" onLevelsChange={vi.fn()} />); await build();
  fireEvent.click(screen.getByText('Save plan to Trade Board')); await screen.findByText('Saved to Trade Board');
  expect(mocks.createBoardPlan.mock.calls[0][0]).toMatchObject({ entry_price: 99, stop_loss: 98, take_profit: 101, game_plan: { currency: 'HKD', packet_fingerprint: 'abc'.repeat(8), entries: [{ condition: 'below', price: 99 }, { condition: 'above', price: 101 }] } });
});
it('never plots stale values or offers stale-level alert drafts', async () => {
  const p = fixture(); p.plan.levels.forEach(x => { x.state = 'STALE'; }); mocks.getStockPlan.mockResolvedValue(p);
  const levels = vi.fn(); render(<StockGamePlanCard symbol="AAPL" onLevelsChange={levels} />); await build();
  expect(levels.mock.calls.at(-1)?.[0]).toEqual([]); expect(screen.queryByText('Draft alert: Pullback stop candidate')).toBeNull();
  expect((screen.getByText('Save plan to Trade Board') as HTMLButtonElement).disabled).toBe(true);
});
it('ignores a completed request from the symbol that was left', async () => {
  let resolve!: (p: ReturnType<typeof fixture>) => void; mocks.getStockPlan.mockReturnValue(new Promise(r => { resolve = r; }));
  const levels = vi.fn(); const view = render(<StockGamePlanCard key="AAPL" symbol="AAPL" onLevelsChange={levels} />);
  fireEvent.click(screen.getByText('Build plan')); view.rerender(<StockGamePlanCard key="MSFT" symbol="MSFT" onLevelsChange={levels} />);
  await act(async () => { resolve(fixture()); }); expect(screen.queryByText('If a daily close holds above HK$99, consider the pullback rule.')).toBeNull();
  expect(levels.mock.calls.every(([lines]) => lines.length === 0)).toBe(true);
});

it('shows both detailed setups and saves the selected breakout with matching entry, stop and take profit', async () => {
  const p = fixture(); mocks.getStockPlan.mockResolvedValue({ ...p, plan: { ...p.plan, setups: [setupFixture(), setupFixture('breakout')] } });
  render(<StockGamePlanCard symbol="AAPL" onLevelsChange={vi.fn()} />); await build();
  expect(screen.getByLabelText('Pullback entry')).toBeTruthy(); expect(screen.queryByLabelText('Breakout entry')).toBeNull();
  fireEvent.change(screen.getByLabelText('Setup to save'), { target: { value: 'breakout' } });
  expect(screen.getByLabelText('Breakout entry')).toBeTruthy(); expect(screen.queryByLabelText('Pullback entry')).toBeNull();
  fireEvent.click(screen.getByText('Save plan to Trade Board')); await screen.findByText('Saved to Trade Board');
  expect(mocks.createBoardPlan.mock.calls[0][0]).toMatchObject({ entry_price: 101, stop_loss: 99, take_profit: 105,
    game_plan: { setup: { id: 'breakout' }, entries: [{ price: 101, condition: 'above' }] } });
});
it('does not save a blocked setup even when reference prices are fresh', async () => {
  const p = fixture(); mocks.getStockPlan.mockResolvedValue({ ...p, plan: { ...p.plan, setups: [{ ...setupFixture(), state: 'blocked', reasons: ['Wait for a new base.'] }] } });
  render(<StockGamePlanCard symbol="AAPL" onLevelsChange={vi.fn()} />); await build();
  expect((screen.getByText('Save plan to Trade Board') as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText('Wait for a new base.')).toBeTruthy(); expect(mocks.createBoardPlan).not.toHaveBeenCalled();
});

it('starts with a compact rules view and opens the model perspective only on selection', async () => {
  const p = fixture(); mocks.getStockPlan.mockResolvedValue({ ...p, plan: { ...p.plan, setups: [setupFixture()] } });
  render(<StockGamePlanCard symbol="AAPL" onLevelsChange={vi.fn()} />); await build();
  expect(screen.getByText('Show rules, history & evidence').closest('details')?.open).toBe(false);
  expect(screen.getByText('Create price-alert drafts').closest('details')?.open).toBe(false);
  expect(screen.getByText('Sizing, risk & supporting levels').closest('details')?.open).toBe(false);
  expect(screen.queryByRole('button', { name: 'Generate LLM alternative' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'LLM alternative plan' }));
  expect(screen.getByRole('button', { name: 'Generate LLM alternative' })).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Save plan to Trade Board' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Rules-based plan' }));
  expect(screen.getByRole('button', { name: 'Save plan to Trade Board' })).toBeTruthy();
});
