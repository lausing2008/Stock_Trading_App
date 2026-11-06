import { describe, expect, it } from 'vitest';
import { splitAdapterFailures } from './uwAdapterFailures';

describe('splitAdapterFailures', () => {
  it('keeps recovered retries out of the failure total (Nov 2 shape)', () => {
    const s = splitAdapterFailures([
      { reason: 'rate_limited_retried', count: 145 },
      { reason: 'rate_limited', count: 3 },
      { reason: 'http_422', count: 2 },
    ]);
    expect(s.failedTotal).toBe(5);
    expect(s.recoveredTotal).toBe(145);
    expect(s.failed.map(r => r.reason)).toEqual(['rate_limited', 'http_422']);
  });

  it('keeps the server order, so the first failed row is the largest real cause', () => {
    const s = splitAdapterFailures([
      { reason: 'rate_limited_retried', count: 90 },
      { reason: 'timeout', count: 7 },
      { reason: 'rate_limited', count: 1 },
    ]);
    expect(s.failed[0].reason).toBe('timeout');
  });

  it('only recovered retries means no errors', () => {
    const s = splitAdapterFailures([{ reason: 'rate_limited_retried', count: 12 }]);
    expect(s.failedTotal).toBe(0);
    expect(s.failed).toEqual([]);
    expect(s.recoveredTotal).toBe(12);
  });

  it('handles a missing list', () => {
    expect(splitAdapterFailures(undefined)).toEqual({ failed: [], failedTotal: 0, recoveredTotal: 0 });
  });
});
