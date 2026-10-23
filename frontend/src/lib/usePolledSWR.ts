// Audit R02 follow-up (2026-10-23): polling that is safe to leave running.
//
//  * Hidden tabs do not poll — SWR's `refreshWhenHidden` stays false (its default), stated here
//    so nobody turns it on per call.
//  * Requests are deduplicated by key: every component asking for the same key inside SWR's
//    deduping window shares one request.
//  * Rate limits back off. A plain refreshInterval keeps polling at full speed after a 429 (or a
//    provider `rate_limited` answer delivered with HTTP 200), and SWR's default error retry adds
//    MORE requests on top. Here each consecutive rate limit doubles the interval (capped), a 429
//    is never fast-retried, and the first ordinary answer restores the base cadence.
//
// The interval is a NUMBER held in React state, never an inline function. SWR re-arms its timer
// whenever `refreshInterval` changes identity (swr 2.2.5, core polling effect deps), so:
//  * an inline function, new on every render, restarted the countdown on EVERY re-render — a
//    component re-rendering more often than its interval (the Market Pressure panel ticks every
//    30 s) never polled at all;
//  * a state number changes only when the backoff step changes, and that change re-arms the
//    timer at once — so a rate limit on the very FIRST answer backs off immediately instead of
//    waiting out the timer armed at mount.
import { useEffect, useRef, useState } from 'react';
import useSWR, { type SWRConfiguration } from 'swr';

export const MAX_BACKOFF_STEPS = 4;   // up to 16x the base interval

/** True for an HTTP 429 from `request()` (its errors read "429 Too Many Requests"). */
export function isRateLimitError(e: unknown): boolean {
  return e instanceof Error && /^429\b/.test(e.message);
}

/** True for a successful response that reports a provider rate limit. */
export function isRateLimitedData(d: unknown): boolean {
  const o = d as { status?: string; reason?: string } | null | undefined;
  return !!o && (o.status === 'rate_limited' || o.reason === 'rate_limited');
}

export function backoffInterval(baseMs: number, consecutiveLimits: number): number {
  return baseMs * 2 ** Math.min(consecutiveLimits, MAX_BACKOFF_STEPS);
}

const canRun = () => (typeof document === 'undefined' || document.visibilityState !== 'hidden')
  && (typeof navigator === 'undefined' || navigator.onLine !== false);

/** One pending error retry with a DEADLINE, owned by one hook instance (review 2026-10-23).
 *
 * The first version scheduled an unconditional setTimeout per retry, so an error received while
 * visible kept firing after the tab was hidden (6 requests in 3 minutes instead of 1). Here:
 *  * at most ONE timer exists; scheduling a new retry replaces the old one;
 *  * a retry that comes due while hidden or offline is HELD, not run;
 *  * on becoming visible and online, an overdue retry runs once immediately, and one not yet due
 *    is re-armed for its REMAINING time, so a rate-limit deadline is never shortened;
 *  * success, a key change or unmount clears it.
 * It cannot simply be dropped while hidden: SWR skips normal polling while an error is cached
 * and focus revalidation is off, so this retry is the only path back to fresh data. */
class RetryScheduler {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private deadline = 0;
  private run: (() => void) | null = null;

  schedule(delayMs: number, run: () => void) {
    this.clear();
    this.deadline = Date.now() + delayMs;
    this.run = run;
    this.arm(delayMs);
  }

  private arm(delayMs: number) {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => { this.timer = null; this.fire(); }, Math.max(0, delayMs));
  }

  private fire() {
    if (!this.run || !canRun()) return;          // held: resumed by resume()
    const run = this.run;
    this.run = null;
    run();
  }

  /** Visibility/online regained: run an overdue retry now, or re-arm for the time remaining. */
  resume() {
    if (!this.run || !canRun()) return;
    const remaining = this.deadline - Date.now();
    if (remaining <= 0) { if (this.timer) { clearTimeout(this.timer); this.timer = null; } this.fire(); }
    else if (!this.timer) this.arm(remaining);
  }

  clear() {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.run = null;
  }

}

export function usePolledSWR<T>(key: string | null, fetcher: () => Promise<T>, baseMs: number,
  extra: SWRConfiguration<T> = {}) {
  // `limits` (ref) is read synchronously by onErrorRetry; `step` (state) drives the interval.
  const limits = useRef(0);
  const [step, setStep] = useState(0);
  const record = (n: number) => { limits.current = n; setStep(Math.min(n, MAX_BACKOFF_STEPS)); };
  const retry = useRef<RetryScheduler | null>(null);
  if (!retry.current) retry.current = new RetryScheduler();

  useEffect(() => {
    const scheduler = retry.current!;
    const onWake = () => scheduler.resume();
    document.addEventListener('visibilitychange', onWake);
    window.addEventListener('online', onWake);
    return () => {
      document.removeEventListener('visibilitychange', onWake);
      window.removeEventListener('online', onWake);
      scheduler.clear();                          // key change or unmount: no orphan retry
    };
  }, [key]);

  return useSWR<T>(key, fetcher, {
    revalidateOnFocus: false,
    refreshWhenHidden: false,
    // The retry scheduler is the single recovery path on 'online'; SWR's own reconnect
    // revalidation would be a second, duplicate request for the same event.
    revalidateOnReconnect: false,
    refreshInterval: backoffInterval(baseMs, step),
    onSuccess: d => { retry.current!.clear(); record(isRateLimitedData(d) ? limits.current + 1 : 0); },
    onError: e => { if (isRateLimitError(e)) record(limits.current + 1); },
    // SWR's polling SKIPS the fetch while the cache holds an error, so recovery after a failure
    // comes only from this retry. A 429 waits the full backed-off interval (never SWR's 5 s
    // default); any other error retries with doubling delays, never longer than the base cadence.
    onErrorRetry: (err, _key, config, revalidate, { retryCount }) => {
      const delay = isRateLimitError(err)
        ? backoffInterval(baseMs, limits.current)
        : Math.min((config.errorRetryInterval ?? 5000) * 2 ** Math.max(0, retryCount - 1), baseMs);   // SWR counts retries from 1
      retry.current!.schedule(delay, () => void revalidate({ retryCount }));
    },
    ...extra,
  });
}
