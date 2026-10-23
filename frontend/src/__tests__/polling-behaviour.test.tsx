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

let online = true;
beforeEach(() => {
  vi.useFakeTimers();
  visibility = 'visible';
  online = true;
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility });
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => visibility === 'hidden' });
  Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => online });
});
const setVisible = (v: boolean) => act(() => { visibility = v ? 'visible' : 'hidden'; document.dispatchEvent(new Event('visibilitychange')); });
const setOnline = (v: boolean) => act(() => { online = v; window.dispatchEvent(new Event(v ? 'online' : 'offline')); });
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

// ── review 2026-10-23: error retries respect visibility and connectivity ─────────────────────

describe('error retries while hidden or offline', () => {
  const E503 = () => new Error('503 Service Unavailable');
  const E429 = () => new Error('429 Too Many Requests');

  it('the review witness: a 503 retry does not run in a hidden tab', async () => {
    const f = vi.fn().mockRejectedValue(E503());
    mount(<Probe k="h503" fetcher={f} />);
    await advance(0);
    await setVisible(false);
    await advance(BASE * 3);
    expect(f).toHaveBeenCalledTimes(1);                       // was 6
  });

  it('a 429 retry does not run in a hidden tab either', async () => {
    const f = vi.fn().mockRejectedValue(E429());
    mount(<Probe k="h429" fetcher={f} />);
    await advance(0);
    await setVisible(false);
    await advance(BASE * 10);
    expect(f).toHaveBeenCalledTimes(1);
  });

  it('503: hidden before the deadline, then shown after it — one immediate retry, then recovery', async () => {
    const f = vi.fn().mockRejectedValueOnce(E503()).mockResolvedValue({ status: 'ok' });
    mount(<Probe k="h503r" fetcher={f} />);
    await advance(0);
    await advance(2000);
    await setVisible(false);                                  // retry due at 5 s
    await advance(BASE * 3);
    expect(f).toHaveBeenCalledTimes(1);
    await setVisible(true);
    await advance(0);
    expect(f).toHaveBeenCalledTimes(2);                       // overdue retry runs once, now
    await advance(BASE);
    expect(f).toHaveBeenCalledTimes(3);                       // normal polling resumed
  });

  it('429: shown again before its deadline — the deadline is kept, not shortened', async () => {
    const f = vi.fn().mockRejectedValueOnce(E429()).mockResolvedValue({ status: 'ok' });
    mount(<Probe k="h429r" fetcher={f} />);
    await advance(0);                                         // limits=1: retry due at 2B
    await advance(BASE / 2);
    await setVisible(false);
    await advance(BASE);                                      // t = 1.5B, hidden
    await setVisible(true);
    await advance(0);
    expect(f).toHaveBeenCalledTimes(1);                       // not early
    await advance(BASE / 2 - 1);
    expect(f).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(f).toHaveBeenCalledTimes(2);                       // exactly at the 2B deadline
  });

  it('429: shown again after its deadline — retried once, not twice', async () => {
    const f = vi.fn().mockRejectedValueOnce(E429()).mockResolvedValue({ status: 'ok' });
    mount(<Probe k="h429late" fetcher={f} />);
    await advance(0);
    await setVisible(false);
    await advance(BASE * 5);
    await setVisible(true);
    await setVisible(false);
    await setVisible(true);                                   // repeated wake events
    await advance(0);
    expect(f).toHaveBeenCalledTimes(2);
  });

  it('repeated failures back off without duplicate timers, and pause while hidden', async () => {
    const f = vi.fn().mockRejectedValue(E503());
    mount(<Probe k="rep" fetcher={f} />);
    await advance(0);                                         // #1
    await advance(5000);                                      // #2 at 5 s
    await advance(10000);                                     // #3 at 15 s
    expect(f).toHaveBeenCalledTimes(3);
    await setVisible(false);
    await advance(BASE * 3);
    expect(f).toHaveBeenCalledTimes(3);                       // nothing while hidden
    await setVisible(true);
    await advance(0);
    expect(f).toHaveBeenCalledTimes(4);                       // one overdue retry
    await advance(BASE);
    expect(f).toHaveBeenCalledTimes(5);                       // the next step (40 s) only: no burst, no duplicate
  });

  it('offline holds the retry; coming back online resumes it once', async () => {
    const f = vi.fn().mockRejectedValueOnce(E503()).mockResolvedValue({ status: 'ok' });
    mount(<Probe k="off" fetcher={f} />);
    await advance(0);
    await setOnline(false);
    await advance(BASE * 2);
    expect(f).toHaveBeenCalledTimes(1);
    await setOnline(true);
    await advance(0);
    expect(f).toHaveBeenCalledTimes(2);
  });

  it('unmount leaves no retry timer behind', async () => {
    // Asserted on the timer queue itself: after unmount SWR's bound revalidate is a no-op, so an
    // orphaned timer would not show up as a request — only as a leaked timer.
    // SWR/React leave one internal timer after any unmount, retry or not, so the check is the
    // DELTA against a control mount that never failed (measured: control +1, failing with
    // cleanup +1, failing without cleanup +2).
    const leftAfterUnmount = async (fetcher: () => Promise<unknown>, k: string) => {
      const before = vi.getTimerCount();
      const v = mount(<Probe k={k} fetcher={fetcher} />);
      await advance(0);
      const during = vi.getTimerCount() - before;
      v.unmount();
      return { during, left: vi.getTimerCount() - before };
    };
    const control = await leftAfterUnmount(vi.fn().mockResolvedValue({ status: 'ok' }), 'ctl');
    const f = vi.fn().mockRejectedValue(E503());
    const failing = await leftAfterUnmount(f, 'gone');
    expect(failing.during).toBeGreaterThan(control.during);   // a retry was pending
    expect(failing.left).toBe(control.left);                  // and it went with the component
    await advance(BASE * 3);
    expect(f).toHaveBeenCalledTimes(1);
  });

  it('a key change clears the old key\'s pending retry', async () => {
    const f = vi.fn().mockRejectedValue(E503());
    const g = vi.fn().mockResolvedValue({ status: 'ok' });
    function Switch({ k }: { k: string }) { usePolledSWR(k, k === 'old' ? f : g, BASE); return null; }
    const ui = (k: string) => <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}><Switch k={k} /></SWRConfig>;
    const view = render(ui('old'));
    await advance(0);
    view.rerender(ui('new'));
    await advance(BASE / 2);
    expect(f).toHaveBeenCalledTimes(1);                       // old key never retried
    expect(g).toHaveBeenCalledTimes(1);
  });
});
