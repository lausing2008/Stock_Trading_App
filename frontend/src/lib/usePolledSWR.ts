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
//    One known lag: SWR arms the first refresh timer at mount, before the first answer arrives,
//    so a rate limit on the very first answer is followed by one poll at the base interval;
//    from the next interval on, the backoff applies.
import { useRef } from 'react';
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

export function usePolledSWR<T>(key: string | null, fetcher: () => Promise<T>, baseMs: number,
  extra: SWRConfiguration<T> = {}) {
  const limits = useRef(0);
  return useSWR<T>(key, fetcher, {
    revalidateOnFocus: false,
    refreshWhenHidden: false,
    refreshInterval: () => backoffInterval(baseMs, limits.current),
    onSuccess: d => { limits.current = isRateLimitedData(d) ? limits.current + 1 : 0; },
    onError: e => { if (isRateLimitError(e)) limits.current += 1; },
    // SWR's polling SKIPS the fetch while the cache holds an error, so recovery after a failure
    // comes only from this retry. A 429 waits the full backed-off interval (never SWR's 5 s
    // default); any other error retries with doubling delays, never longer than the base cadence.
    onErrorRetry: (err, _key, config, revalidate, { retryCount }) => {
      const delay = isRateLimitError(err)
        ? backoffInterval(baseMs, limits.current)
        : Math.min((config.errorRetryInterval ?? 5000) * 2 ** Math.max(0, retryCount - 1), baseMs);   // SWR counts retries from 1
      setTimeout(() => revalidate({ retryCount }), delay);
    },
    ...extra,
  });
}
