// AUD-UW-RETRY-ADMINTOTAL (2026-11-05): a recovered retry is not a failure.
//
// The bar adapter counts a UW 429 that it then retried as `rate_limited_retried`, under the
// same Redis prefix as real failures, and the server's `adapter_failures_24h_total` sums every
// reason. On Nov 2 that showed ~145 "errors" against 3 failed fetches. The split is done here
// so the badge counts only fetches that actually fell through to the next provider.

export const RECOVERED_REASONS: ReadonlySet<string> = new Set(['rate_limited_retried']);

export interface FailureRow { reason: string; count: number }

export function splitAdapterFailures(rows: FailureRow[] | null | undefined): {
  failed: FailureRow[];
  failedTotal: number;
  recoveredTotal: number;
} {
  const failed: FailureRow[] = [];
  let failedTotal = 0;
  let recoveredTotal = 0;
  for (const row of rows ?? []) {
    if (RECOVERED_REASONS.has(row.reason)) {
      recoveredTotal += row.count;
    } else {
      failed.push(row);
      failedTotal += row.count;
    }
  }
  return { failed, failedTotal, recoveredTotal };
}
