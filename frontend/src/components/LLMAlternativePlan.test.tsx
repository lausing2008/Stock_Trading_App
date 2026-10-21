// @vitest-environment jsdom
import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ askCopilot: vi.fn(), cancelCopilot: vi.fn(), newRequestId: vi.fn(), configured: true }));
vi.mock('@/lib/copilot', () => mocks);
vi.mock('@/lib/ai', () => ({ isAiConfigured: () => mocks.configured, getAiProviderLabel: () => 'Claude' }));
vi.mock('./CopilotPanel', () => ({ CopilotAnswer: ({ m }: { m: { content: string } }) => <div>{m.content}</div> }));
import LLMAlternativePlan from './LLMAlternativePlan';
import type { TradeSetup } from '@/lib/copilot';
import { setupFixture } from './__fixtures__/tradeSetup';
const fingerprint = 'abc'.repeat(8);
const props = { symbol: 'AAPL', fingerprint, inputs: { kind: 'holding' as const, entry: 100, shares: 10, stop: 98, risk: 400 }, unavailable: false };
const answer = (status = 'grounded') => ({ message: { content: status === 'rejected' ? 'Stored-data fallback' : 'One reading favors waiting.', model: 'actual-model-name', status, selected_setup_id: 'wait', plan_setups: [] as TradeSetup[], packet_fingerprint: fingerprint } });
beforeEach(() => {
  Object.values(mocks).forEach(m => { if (typeof m === 'function') m.mockReset(); }); mocks.configured = true;
  let i = 0; mocks.newRequestId.mockImplementation(() => `request-000${++i}`);
  mocks.cancelCopilot.mockResolvedValue({}); mocks.askCopilot.mockResolvedValue(answer());
});
afterEach(cleanup);
it('makes no eager model call, then binds typed inputs and displays actual model provenance', async () => {
  render(<LLMAlternativePlan {...props} />); expect(mocks.askCopilot).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('Generate LLM alternative')); await screen.findByText('One reading favors waiting.');
  expect(mocks.askCopilot.mock.calls[0][0]).toMatchObject({ symbol: 'AAPL', requestId: 'request-0001', planContext: { ...props.inputs, fingerprint } });
  expect(screen.getByText('Model: actual-model-name')).toBeTruthy();
  expect(screen.getByText(/agreement is not independent confirmation/)).toBeTruthy();
});
it('keeps the paid generation disabled without a provider or fresh supported plan', () => {
  mocks.configured = false; const v = render(<LLMAlternativePlan {...props} />);
  expect((screen.getByText('Generate LLM alternative') as HTMLButtonElement).disabled).toBe(true);
  mocks.configured = true; v.rerender(<LLMAlternativePlan {...props} unavailable />);
  expect((screen.getByText('Generate LLM alternative') as HTMLButtonElement).disabled).toBe(true);
  expect(mocks.askCopilot).not.toHaveBeenCalled();
});
it('marks a rejected model answer as fallback rather than a second plan', async () => {
  mocks.askCopilot.mockResolvedValue(answer('rejected')); render(<LLMAlternativePlan {...props} />);
  fireEvent.click(screen.getByText('Generate LLM alternative')); await screen.findByText('Stored-data fallback');
  expect(screen.getByRole('alert').textContent).toContain('not a second plan');
});
it('refuses a response from a different evidence snapshot', async () => {
  const out = answer(); out.message.packet_fingerprint = 'changed'; mocks.askCopilot.mockResolvedValue(out);
  render(<LLMAlternativePlan {...props} />); fireEvent.click(screen.getByText('Generate LLM alternative'));
  await screen.findByRole('alert'); expect(screen.queryByText('One reading favors waiting.')).toBeNull();
});
const err = (message: string, status?: number) => Object.assign(new Error(message), status === undefined ? {} : { status });
async function tick(ms = 4000) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }
it('recovers a dropped connection by re-asking the same request id, then uses a new id for a new generation', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    mocks.askCopilot.mockRejectedValueOnce(err('NetworkError when attempting to fetch resource.'));
    render(<LLMAlternativePlan {...props} />); fireEvent.click(screen.getByText('Generate LLM alternative'));
    await screen.findByText(/Connection dropped while the model was answering/);
    await tick(); await screen.findByText('One reading favors waiting.');
    expect(mocks.askCopilot.mock.calls.slice(0, 2).map(([x]) => x.requestId)).toEqual(['request-0001', 'request-0001']);
    expect(screen.queryByText(/NetworkError/)).toBeNull();
    fireEvent.click(screen.getByText('Generate another LLM alternative'));
    await waitFor(() => expect(mocks.askCopilot).toHaveBeenCalledTimes(3));
    expect(mocks.askCopilot.mock.calls[2][0].requestId).toBe('request-0002');
  } finally { vi.useRealTimers(); }
});
it('waits while the server is still answering and never treats that 409 as a changed plan', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    const onPlanChanged = vi.fn();
    mocks.askCopilot.mockRejectedValueOnce(err('NetworkError when attempting to fetch resource.'))
      .mockRejectedValueOnce(err('this request is already being answered', 409)).mockResolvedValueOnce(answer());
    render(<LLMAlternativePlan {...props} onPlanChanged={onPlanChanged} />); fireEvent.click(screen.getByText('Generate LLM alternative'));
    await tick(); await tick(); await screen.findByText('One reading favors waiting.');
    expect(mocks.askCopilot).toHaveBeenCalledTimes(3);
    expect(new Set(mocks.askCopilot.mock.calls.map(([x]) => x.requestId))).toEqual(new Set(['request-0001']));
    expect(onPlanChanged).not.toHaveBeenCalled();
  } finally { vi.useRealTimers(); }
});
it('does not retry an error that re-asking cannot fix', async () => {
  mocks.askCopilot.mockRejectedValueOnce(err('daily copilot limit reached (20 answers per UTC day)', 429));
  render(<LLMAlternativePlan {...props} />); fireEvent.click(screen.getByText('Generate LLM alternative'));
  await screen.findByText(/daily copilot limit reached/); expect(mocks.askCopilot).toHaveBeenCalledTimes(1);
});
it('stops recovering when the user cancels', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    mocks.askCopilot.mockRejectedValue(err('NetworkError when attempting to fetch resource.'));
    render(<LLMAlternativePlan {...props} />); fireEvent.click(screen.getByText('Generate LLM alternative'));
    await screen.findByText(/Connection dropped/);
    fireEvent.click(screen.getByText('Cancel generation')); await tick(); await tick();
    expect(mocks.askCopilot).toHaveBeenCalledTimes(1); expect(mocks.cancelCopilot).toHaveBeenCalledWith('request-0001');
  } finally { vi.useRealTimers(); }
});
it('cancels repair attempts and ignores a late result after leaving the card', async () => {
  let resolve!: (value: ReturnType<typeof answer>) => void;
  mocks.askCopilot.mockReturnValue(new Promise(r => { resolve = r; }));
  const v = render(<LLMAlternativePlan {...props} />); fireEvent.click(screen.getByText('Generate LLM alternative'));
  const signal = mocks.askCopilot.mock.calls[0][1]; v.unmount();
  expect(signal.aborted).toBe(true); expect(mocks.cancelCopilot).toHaveBeenCalledWith('request-0001');
  await act(async () => { resolve(answer()); }); expect(screen.queryByText('One reading favors waiting.')).toBeNull();
});
it('ignores a late result after explicit cancellation', async () => {
  let resolve!: (value: ReturnType<typeof answer>) => void;
  mocks.askCopilot.mockReturnValue(new Promise(r => { resolve = r; }));
  render(<LLMAlternativePlan {...props} />); fireEvent.click(screen.getByText('Generate LLM alternative'));
  fireEvent.click(screen.getByText('Cancel generation')); expect(mocks.cancelCopilot).toHaveBeenCalledWith('request-0001');
  await act(async () => { resolve(answer()); }); expect(screen.queryByText('One reading favors waiting.')).toBeNull();
  expect(screen.getByRole('status').textContent).toContain('may still be billed');
});

it('shows entry, stop and profit targets for the exact setup chosen by the LLM', async () => {
  const onSelectionChange = vi.fn();
  const out = answer(); out.message.selected_setup_id = 'breakout'; out.message.plan_setups = [setupFixture(), setupFixture('breakout')];
  mocks.askCopilot.mockResolvedValue(out); render(<LLMAlternativePlan {...props} currency="USD" onSelectionChange={onSelectionChange} />);
  fireEvent.click(screen.getByText('Generate LLM alternative')); await screen.findByLabelText('Breakout entry');
  expect(screen.queryByLabelText('Pullback entry')).toBeNull();
  expect(screen.getByText('Entry reference')).toBeTruthy(); expect(screen.getByText('Stop loss to assess')).toBeTruthy();
  expect(screen.getByText(/USD\s+105\.00.*2\.00R/)).toBeTruthy(); expect(screen.getByText('200')).toBeTruthy();
  expect(onSelectionChange).toHaveBeenLastCalledWith([out.message.plan_setups[1]]);
});
it.each(['grounded', 'rejected'])('publishes no chart setup for a %s wait response', async status => {
  const onSelectionChange = vi.fn(); mocks.askCopilot.mockResolvedValue(answer(status));
  render(<LLMAlternativePlan {...props} onSelectionChange={onSelectionChange} />);
  fireEvent.click(screen.getByText('Generate LLM alternative'));
  await screen.findByText(status === 'rejected' ? 'Stored-data fallback' : 'One reading favors waiting.');
  expect(onSelectionChange).toHaveBeenLastCalledWith([]);
});
it('rejects an unknown or blocked selected setup instead of displaying invented model levels', async () => {
  const out = answer(); out.message.selected_setup_id = 'breakout'; out.message.plan_setups = [{ ...setupFixture('breakout'), state: 'blocked' }];
  mocks.askCopilot.mockResolvedValue(out); render(<LLMAlternativePlan {...props} currency="USD" />);
  fireEvent.click(screen.getByText('Generate LLM alternative')); await screen.findByRole('alert');
  expect(screen.queryByLabelText('Breakout entry')).toBeNull();
});
