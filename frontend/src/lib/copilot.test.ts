// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
vi.mock('./settings', () => ({ loadSettings: () => ({ aiProvider: 'claude', claudeModel: 'configured-model', claudeApiKey: 'user-key' }) }));
import { askCopilot } from './copilot';
afterEach(() => { vi.unstubAllGlobals(); });
it('sends typed plan context through the existing metered chat endpoint', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ message: {} }) }); vi.stubGlobal('fetch', fetch);
  const planContext = { kind: 'holding' as const, fingerprint: 'a'.repeat(24), entry: 100, shares: 10, stop: 98 };
  await askCopilot({ symbol: 'AAPL', question: 'Compare alternatives', requestId: 'compare-0001', planContext });
  expect(fetch.mock.calls[0][0]).toBe('/api/copilot/chat');
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toMatchObject({ plan_context: planContext, provider: 'claude', model: 'configured-model', request_id: 'compare-0001' });
});
it('leaves ordinary chat requests free of comparison overrides', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ message: {} }) }); vi.stubGlobal('fetch', fetch);
  await askCopilot({ symbol: 'AAPL', question: 'Explain its current call', requestId: 'ordinary-0001' });
  expect(JSON.parse(fetch.mock.calls[0][1].body)).not.toHaveProperty('plan_context');
});
