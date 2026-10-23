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
      .mockResolvedValueOnce({ status: 'rate_limited' })     // t=2B
      .mockResolvedValueOnce({ status: 'rate_limited' })     // t=2B+4B
      .mockResolvedValue({ status: 'ok' });                  // t=6B+8B, then every B
    mount(<Probe k="c" fetcher={f} />);
    await advance(0);
    await advance(BASE * 2 - 1);
    expect(f).toHaveBeenCalledTimes(1);                      // FIRST answer limited: no poll at B (lag fixed)
    await advance(1);
    expect(f).toHaveBeenCalledTimes(2);                      // 2x
    await advance(BASE * 4);
    expect(f).toHaveBeenCalledTimes(3);                      // 4x
    await advance(BASE * 8);
    expect(f).toHaveBeenCalledTimes(4);                      // 8x
    await advance(BASE);
    expect(f).toHaveBeenCalledTimes(5);                      // recovered: base cadence
  });

  it('a 429 on the very first response backs off immediately (no base-interval poll)', async () => {
    const f = vi.fn().mockRejectedValueOnce(new Error('429 Too Many Requests')).mockResolvedValue({ status: 'ok' });
    mount(<Probe k="first429" fetcher={f} />);
    await advance(0);
    expect(f).toHaveBeenCalledTimes(1);
    await advance(BASE);
    expect(f).toHaveBeenCalledTimes(1);                      // nothing at the base interval
    await advance(BASE);
    expect(f).toHaveBeenCalledTimes(2);                      // the 2x backed-off retry
  });

  it('a component that re-renders more often than its interval still polls on time', async () => {
    // Re-renders are forced from the test: a component's own state ticks are not flushed under
    // fake timers, so an in-component interval would not actually re-render (checked).
    const f = vi.fn().mockResolvedValue({ status: 'ok' });
    function Rendered({ n }: { n: number }) { usePolledSWR('tick', f, BASE); return <i>{n}</i>; }
    const ui = (n: number) => <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}><Rendered n={n} /></SWRConfig>;
    const view = render(ui(0));
    await advance(0);
    for (let i = 1; i <= 8; i++) {                           // a re-render every quarter interval, for 2 intervals
      await advance(BASE / 4);
      view.rerender(ui(i));
    }
    expect(f).toHaveBeenCalledTimes(3);                      // an inline-function interval gave 1: every render reset it
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

it('review: retries pause when tab becomes hidden after an error', async () => {
  const f = vi.fn().mockRejectedValue(new Error('503 Service Unavailable'));
  mount(<Probe k="review-hidden-retry" fetcher={f} />);
  await advance(0);
  visibility = 'hidden';
  await advance(BASE * 3);
  expect(f).toHaveBeenCalledTimes(1);
});
