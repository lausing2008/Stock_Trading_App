// @vitest-environment jsdom
/**
 * Panel lifecycle, driven through a real DOM (independent review R3 and the Stop findings).
 * The copilot API is mocked with DEFERRED promises so each test controls exactly when a
 * response arrives relative to the user's clicks.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act, cleanup } from '@testing-library/react';

const api = vi.hoisted(() => ({
  askCopilot: vi.fn(), cancelCopilot: vi.fn(), deleteCopilotConversation: vi.fn(),
  getCopilotConversation: vi.fn(), getCopilotEvidence: vi.fn(), listCopilotConversations: vi.fn(),
}));
vi.mock('@/lib/copilot', () => ({ ...api, newRequestId: () => `rid-${Math.random()}` }));
vi.mock('@/lib/ai', () => ({ isAiConfigured: () => true, getAiProviderLabel: () => 'Claude' }));

import CopilotPanel from './CopilotPanel';

function deferred<T>() {
  let resolve!: (v: T) => void, reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
const flush = () => act(async () => { await new Promise(r => setTimeout(r, 0)); });
const answer = (id: number, content: string) => ({
  id, role: 'assistant', content, request_id: `r${id}`, status: 'grounded', evidence_refs: [],
  limitations: [], price_as_of: null,
});
const OLD = { conversation: { id: 7 }, retention_days: 180,
  messages: [{ id: 1, role: 'user', content: 'old question', request_id: 'a' }, answer(2, 'old answer')] };

beforeEach(() => {
  Object.values(api).forEach(f => f.mockReset());
  api.listCopilotConversations.mockResolvedValue({ conversations: [{ id: 7 }], retention_days: 180 });
  api.getCopilotConversation.mockResolvedValue(OLD);
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});
afterEach(cleanup);

async function openPanel() {
  fireEvent.click(screen.getByText(/Ask the copilot about/));
  await flush();
}

describe('copilot panel lifecycle', () => {
  it('New starts a fresh conversation instead of reloading the one just left (R3)', async () => {
    render(<CopilotPanel symbol="AAPL" />);
    await openPanel();
    expect(screen.getByText('old answer')).toBeTruthy();

    fireEvent.click(screen.getByText('New'));
    await flush();
    expect(screen.queryByText('old answer')).toBeNull();
    expect(api.getCopilotConversation).toHaveBeenCalledTimes(1);   // NOT reloaded after New
    // ...and not on the next close-and-reopen either, which is where 'fresh' mode matters.
    fireEvent.click(screen.getByText(/Ask the copilot about/));
    await openPanel();
    expect(screen.queryByText('old answer')).toBeNull();
    expect(api.getCopilotConversation).toHaveBeenCalledTimes(1);

    const d = deferred<unknown>();
    api.askCopilot.mockReturnValue(d.promise);
    fireEvent.change(screen.getByPlaceholderText(/Ask about AAPL/), { target: { value: 'fresh question' } });
    fireEvent.click(screen.getByText('Ask'));
    expect(api.askCopilot.mock.calls[0][0].conversationId).toBeNull();
    d.resolve({ conversation_id: 8, message: answer(9, 'fresh answer'), replayed: false });
    await flush();
    expect(screen.getByText('fresh answer')).toBeTruthy();
    expect(screen.queryByText('old answer')).toBeNull();
  });

  it('a late answer for the previous symbol never lands in the new one', async () => {
    const { rerender } = render(<CopilotPanel symbol="AAPL" />);
    await openPanel();
    const d = deferred<unknown>();
    api.askCopilot.mockReturnValue(d.promise);
    fireEvent.change(screen.getByPlaceholderText(/Ask about AAPL/), { target: { value: 'q' } });
    fireEvent.click(screen.getByText('Ask'));

    api.listCopilotConversations.mockResolvedValue({ conversations: [], retention_days: 180 });
    rerender(<CopilotPanel symbol="MSFT" />);
    await flush();
    d.resolve({ conversation_id: 7, message: answer(10, 'AAPL late answer'), replayed: false });
    await flush();
    expect(screen.queryByText('AAPL late answer')).toBeNull();
    expect(screen.getByText(/Ask the copilot about MSFT/)).toBeTruthy();
  });

  it.each([
    ['confirmed', () => api.cancelCopilot.mockResolvedValue({ cancelled: true }), /Cancellation confirmed/],
    ['nothing to cancel', () => api.cancelCopilot.mockResolvedValue({ cancelled: false }), /nothing left to cancel/],
    ['cancel request failed', () => api.cancelCopilot.mockRejectedValue(new Error('offline')), /could not be confirmed/],
  ])('Stop reports what actually happened: %s', async (_name, setup, expected) => {
    render(<CopilotPanel symbol="AAPL" />);
    await openPanel();
    setup();
    api.askCopilot.mockImplementation((_a: unknown, signal: AbortSignal) => new Promise((_r, rej) =>
      signal.addEventListener('abort', () => rej(new Error('Cancelled')))));
    fireEvent.change(screen.getByPlaceholderText(/Ask about AAPL/), { target: { value: 'q' } });
    fireEvent.click(screen.getByText('Ask'));
    await flush();
    fireEvent.click(screen.getByText('Stop'));
    await flush();
    expect(screen.getByTestId('stop-note').textContent).toMatch(expected);
    expect(screen.queryByText('Retry')).toBeNull();   // no same-id retry after a stop
  });

  it('reopening refreshes the conversation, so a stopped request\'s saved answer appears', async () => {
    render(<CopilotPanel symbol="AAPL" />);
    await openPanel();
    fireEvent.click(screen.getByText(/Ask the copilot about/));   // close
    api.getCopilotConversation.mockResolvedValue({ ...OLD,
      messages: [...OLD.messages, { id: 3, role: 'user', content: 'stopped q', request_id: 's' },
                 answer(4, 'answer saved after stop')] });
    await openPanel();
    expect(screen.getByText('answer saved after stop')).toBeTruthy();
  });
});
