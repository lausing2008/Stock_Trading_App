// @vitest-environment jsdom
// Audit R02 follow-up (2026-10-23): polling stops in hidden tabs, shares requests, and backs off
// on rate limits. Driven through the real hook with fake timers.
import React from 'react';
import { act, cleanup, render } from '@testing-library/react';
import { SWRConfig } from 'swr';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { backoffInterval, isRateLimitError, usePolledSWR } from '@/lib/usePolledSWR';

const BASE = 60_000;
let visibility: DocumentVisibilityState = 'visible';

beforeEach(() => {
  vi.useFakeTimers();
  visibility = 'visible';
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility });
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => visibility === 'hidden' });
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

function Probe({ k, fetcher }: { k: string; fetcher: () => Promise<unknown> }) {
  usePolledSWR(k, fetcher, BASE);
  return null;
}
const mount = (ui: React.ReactElement, dedupingInterval = 0) =>
  render(<SWRConfig value={{ provider: () => new Map(), dedupingInterval }}>{ui}</SWRConfig>);
const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

describe('visibility', () => {
  it('polls at the base interval while visible', async () => {
    const f = vi.fn().mockResolvedValue({ status: 'ok' });
    mount(<Probe k="a" fetcher={f} />);
    await advance(0);
    expect(f).toHaveBeenCalledTimes(1);
    await advance(BASE);
    expect(f).toHaveBeenCalledTimes(2);
    await advance(BASE);
    expect(f).toHaveBeenCalledTimes(3);
  });

  it('a hidden tab does not poll, however long it stays hidden', async () => {
    const f = vi.fn().mockResolvedValue({ status: 'ok' });
    mount(<Probe k="b" fetcher={f} />);
    await advance(0);
    visibility = 'hidden';
    await advance(BASE * 10);
    expect(f).toHaveBeenCalledTimes(1);
  });
});

describe('deduplication', () => {
  it('components asking for the same key share one request', async () => {
    const f = vi.fn().mockResolvedValue({ status: 'ok' });
    mount(<><Probe k="same" fetcher={f} /><Probe k="same" fetcher={f} /><Probe k="same" fetcher={f} /></>, 2000);
    await advance(0);
    expect(f).toHaveBeenCalledTimes(1);
  });
});

describe('rate-limit backoff', () => {
  it('a provider rate_limited answer doubles the interval each time, then recovers', async () => {
    const f = vi.fn()
      .mockResolvedValueOnce({ status: 'rate_limited' })     // t=0
      .mockResolvedValueOnce({ status: 'rate_limited' })     // t=B: the timer armed at mount (known lag)
      .mockResolvedValueOnce({ status: 'rate_limited' })     // t=B+4B
      .mockResolvedValue({ status: 'ok' });                  // t=B+4B+8B, then every B
    mount(<Probe k="c" fetcher={f} />);
    await advance(0);
    await advance(BASE);
    expect(f).toHaveBeenCalledTimes(2);
    await advance(BASE * 4 - 1);
    expect(f).toHaveBeenCalledTimes(2);                      // backed off: nothing at 2B or 3B
    await advance(1);
    expect(f).toHaveBeenCalledTimes(3);                      // 4x after the second consecutive limit
    await advance(BASE * 8);
    expect(f).toHaveBeenCalledTimes(4);                      // 8x after the third
    await advance(BASE);
    expect(f).toHaveBeenCalledTimes(5);                      // recovered: base cadence
  });

  it('an HTTP 429 is never fast-retried, backs off, and still recovers', async () => {
    const f = vi.fn().mockRejectedValueOnce(new Error('429 Too Many Requests')).mockResolvedValue({ status: 'ok' });
    mount(<Probe k="d" fetcher={f} />);
    await advance(0);
    await advance(BASE * 2 - 1);
    expect(f).toHaveBeenCalledTimes(1);                      // no 5 s error retry, no base-interval poll
    await advance(1);
    expect(f).toHaveBeenCalledTimes(2);                      // the backed-off retry
    await advance(BASE);
    expect(f).toHaveBeenCalledTimes(3);                      // polling resumed at the base cadence
  });

  it('a non-rate-limit error retries sooner, never slower than the base cadence', async () => {
    const f = vi.fn().mockRejectedValueOnce(new Error('503 Service Unavailable')).mockResolvedValue({ status: 'ok' });
    mount(<Probe k="e" fetcher={f} />);
    await advance(0);
    await advance(5000);
    expect(f).toHaveBeenCalledTimes(2);
  });

  it('the interval is capped', () => {
    expect(backoffInterval(BASE, 0)).toBe(BASE);
    expect(backoffInterval(BASE, 3)).toBe(BASE * 8);
    expect(backoffInterval(BASE, 50)).toBe(BASE * 16);
    expect(isRateLimitError(new Error('429 Too Many Requests'))).toBe(true);
    expect(isRateLimitError(new Error('503 Service Unavailable'))).toBe(false);
  });
});
