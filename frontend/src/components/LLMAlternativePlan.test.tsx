// @vitest-environment jsdom
import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ askCopilot: vi.fn(), cancelCopilot: vi.fn(), newRequestId: vi.fn(), configured: true }));
vi.mock('@/lib/copilot', () => mocks);
vi.mock('@/lib/ai', () => ({ isAiConfigured: () => mocks.configured, getAiProviderLabel: () => 'Claude' }));
vi.mock('./CopilotPanel', () => ({ CopilotAnswer: ({ m }: { m: { content: string } }) => <div>{m.content}</div> }));
import LLMAlternativePlan from './LLMAlternativePlan';
import type { PlanProposal, TradeSetup } from '@/lib/copilot';
import { setupFixture } from './__fixtures__/tradeSetup';
const fingerprint = 'abc'.repeat(8);
const props = { symbol: 'AAPL', fingerprint, inputs: { kind: 'holding' as const, entry: 100, shares: 10, stop: 98, risk: 400 }, unavailable: false };
const waitProposal = { id: 'llm', title: 'LLM-proposed plan', state: 'wait', kind: 'wait', source: 'llm_proposal', reasons: ['The model proposed waiting: no entry now.'], derivation: [], playbook: [] } as unknown as PlanProposal;
const proposalFixture = (state: PlanProposal['state'] = 'conditional', reasons: string[] = []): PlanProposal => ({
  ...setupFixture('breakout'), id: 'llm', key: 'proposal.llm', title: 'LLM-proposed plan', state, kind: 'breakout', source: 'llm_proposal', reasons,
  derivation: [{ level: 'entry', anchor: 'level.high20', anchor_price: 101, atr_multiple: 0, atr: 2, price: 101 },
    { level: 'stop', anchor: 'level.high20', anchor_price: 101, atr_multiple: -1, atr: 2, price: 99 }] });
const answer = (status = 'grounded') => ({ message: { content: status === 'rejected' ? 'Stored-data fallback' : 'One reading favors waiting.', model: 'actual-model-name', status,
  selected_setup_id: null as string | null, plan_setups: [] as TradeSetup[], plan_proposal: (status === 'grounded' ? waitProposal : null) as PlanProposal | null, packet_fingerprint: fingerprint } });
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

it('shows the model-proposed entry, stop and targets with how each price was derived', async () => {
  const onSelectionChange = vi.fn(); const onProposal = vi.fn();
  const out = answer(); out.message.plan_proposal = proposalFixture();
  mocks.askCopilot.mockResolvedValue(out); render(<LLMAlternativePlan {...props} currency="USD" onSelectionChange={onSelectionChange} onProposal={onProposal} />);
  fireEvent.click(screen.getByText('Generate LLM alternative')); await screen.findByLabelText('LLM-proposed plan');
  expect(screen.getByText('Entry reference')).toBeTruthy(); expect(screen.getByText('Stop loss to assess')).toBeTruthy();
  expect(screen.getByText(/USD\s+105\.00.*2\.00R/)).toBeTruthy(); expect(screen.getByText('200')).toBeTruthy();
  const derivation = screen.getByRole('list', { name: 'Proposal derivation' });
  expect(derivation.textContent).toContain('Entry: 20-bar high (101 USD) = 101 USD');
  expect(derivation.textContent).toContain('Stop: 20-bar high (101 USD) − 1 × ATR (2 USD) = 99 USD');
  expect(onSelectionChange).toHaveBeenLastCalledWith([out.message.plan_proposal]);
  expect(onProposal).toHaveBeenLastCalledWith({ proposal: out.message.plan_proposal, message: out.message });
});
it('shows a proposal that failed the code checks with its reasons and never plots it', async () => {
  const onSelectionChange = vi.fn();
  const out = answer(); out.message.plan_proposal = proposalFixture('rejected', ['Target 1 is 1.50R; the minimum is 2R.']);
  mocks.askCopilot.mockResolvedValue(out); render(<LLMAlternativePlan {...props} currency="USD" onSelectionChange={onSelectionChange} />);
  fireEvent.click(screen.getByText('Generate LLM alternative'));
  expect((await screen.findByText(/failed the code checks/)).closest('[role="alert"]')?.textContent).toContain('Target 1 is 1.50R; the minimum is 2R.');
  expect(screen.queryByLabelText('LLM-proposed plan')).toBeNull();
  expect(onSelectionChange).toHaveBeenLastCalledWith([]);
});
it('refuses a grounded answer that carries no proposal', async () => {
  const out = answer(); out.message.plan_proposal = null;
  mocks.askCopilot.mockResolvedValue(out); render(<LLMAlternativePlan {...props} />);
  fireEvent.click(screen.getByText('Generate LLM alternative'));
  expect((await screen.findByRole('alert')).textContent).toMatch(/did not return a plan proposal/);
});
it.each(['grounded', 'rejected'])('publishes no chart setup for a %s wait response', async status => {
  const onSelectionChange = vi.fn(); mocks.askCopilot.mockResolvedValue(answer(status));
  render(<LLMAlternativePlan {...props} onSelectionChange={onSelectionChange} />);
  fireEvent.click(screen.getByText('Generate LLM alternative'));
  await screen.findByText(status === 'rejected' ? 'Stored-data fallback' : 'One reading favors waiting.');
  expect(onSelectionChange).toHaveBeenLastCalledWith([]);
});
it('shows the model reasoning and playbook openly beside its proposal', async () => {
  const out = answer(); out.message.plan_proposal = proposalFixture();
  mocks.askCopilot.mockResolvedValue(out); render(<LLMAlternativePlan {...props} currency="USD" />);
  fireEvent.click(screen.getByText('Generate LLM alternative')); await screen.findByLabelText('LLM-proposed plan');
  const reasoning = screen.getByRole('region', { name: 'Model reasoning and playbook' });
  expect(reasoning.closest('details')).toBeNull();
  expect(reasoning.textContent).toContain('One reading favors waiting.');
  expect(screen.getByText(/code computes every price and checks/)).toBeTruthy();
});
it('labels a platform-written explanation when the model prose failed but its proposal passed', async () => {
  const out = answer(); out.message.plan_proposal = proposalFixture();
  Object.assign(out.message, { narration: 'code', narration_rejection_reasons: ['the answer writes a quantity in words (half)'] });
  mocks.askCopilot.mockResolvedValue(out); render(<LLMAlternativePlan {...props} currency="USD" />);
  fireEvent.click(screen.getByText('Generate LLM alternative')); await screen.findByLabelText('LLM-proposed plan');
  expect(screen.getByText('Explanation (written by the platform)')).toBeTruthy();
  expect(screen.getByText(/failed the evidence check \(the answer writes a quantity in words \(half\)\)/)).toBeTruthy();
});
it('shows an exit-rule-met proposal as an alert and never plots it', async () => {
  const onSelectionChange = vi.fn();
  const out = answer(); out.message.plan_proposal = { ...waitProposal, state: 'exit_triggered', kind: 'hold',
    reasons: ['The latest daily close ($100.00) is below your stated stop ($101.00): the exit rule is met. A model proposal does not override it.'] } as PlanProposal;
  mocks.askCopilot.mockResolvedValue(out); render(<LLMAlternativePlan {...props} onSelectionChange={onSelectionChange} />);
  fireEvent.click(screen.getByText('Generate LLM alternative'));
  expect((await screen.findByText(/Exit rule met/)).closest('[role="alert"]')?.textContent).toContain('below your stated stop');
  expect(screen.queryByLabelText('LLM-proposed plan')).toBeNull();
  expect(onSelectionChange).toHaveBeenLastCalledWith([]);
});
