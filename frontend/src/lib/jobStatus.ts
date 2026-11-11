// AUD-SCAN-SILENTZERO (2026-11-11): scheduler job statuses as the health page shows them. A scan
// that ran but measured nothing records "degraded: <why>"; it used to read "ok".
export type JobStatusKind = 'ok' | 'error' | 'degraded' | 'skipped' | 'unknown';

export function jobStatusKind(status: string | null | undefined): JobStatusKind {
  const s = status ?? '';
  if (s === 'ok') return 'ok';
  if (s === 'error') return 'error';
  if (s.startsWith('degraded')) return 'degraded';
  if (s.startsWith('skipped:') || s.startsWith('Need ')) return 'skipped';
  return 'unknown';
}

/** The reason after "degraded: ", or null. */
export function degradedReason(status: string | null | undefined): string | null {
  const s = status ?? '';
  return s.startsWith('degraded') ? s.replace(/^degraded:?\s*/, '') || null : null;
}
