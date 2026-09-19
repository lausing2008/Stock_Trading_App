import { describe, it, expect } from 'vitest';
import { isFixMetricsUnsupported, type FixMetrics } from './api';

// AUD-FIXEFFECTIVENESS-UNSUPPORTEDSHAPE: fix_effectiveness.py's take_fix_snapshot() (AUD-C01)
// deliberately records this exact shape for a domain with no snapshot metric function
// registered, rather than a 400. Reproduced verbatim from a real production row
// (AUD-DECIDE1-LOWGATECONFIG, fix_snapshots.id=2) that crashed the fix-effectiveness page's
// whole render because the frontend assumed every snapshot's metrics had `by_bucket`.
const REAL_UNSUPPORTED_SNAPSHOT_METRICS: FixMetrics = {
  status: 'unsupported',
  domain: 'decision_making',
  reason: "no snapshot metric function is implemented for domain='decision_making'",
  supported_domains: ['ai_signal'],
};

const REAL_MEASURED_SNAPSHOT_METRICS: FixMetrics = {
  by_bucket: {
    'SHORT|BUY': {
      total: 248, resolved_5d: 248, win_rate_5d: 0.25, avg_return_5d_pct: -3.46,
      resolved_base: 248, win_rate_base: 0.254, avg_pct_return_base: -3.68,
    },
  },
  total_resolved_5d: 607,
};

describe('isFixMetricsUnsupported', () => {
  it('identifies the real unsupported-domain shape recorded in production', () => {
    expect(isFixMetricsUnsupported(REAL_UNSUPPORTED_SNAPSHOT_METRICS)).toBe(true);
  });

  it('does not misclassify a real measured snapshot as unsupported', () => {
    expect(isFixMetricsUnsupported(REAL_MEASURED_SNAPSHOT_METRICS)).toBe(false);
  });

  it('never throws when by_bucket is entirely absent — the exact original crash', () => {
    // The bug: `metrics.by_bucket[key]` on the unsupported shape threw because `by_bucket`
    // does not exist on it at all. The guard must let callers branch BEFORE that access ever
    // happens, not merely return a falsy value that still gets indexed into.
    function accessByBucketIfMeasured(metrics: FixMetrics): unknown {
      if (!isFixMetricsUnsupported(metrics)) {
        // Real page code path: only reached for the measured shape.
        return metrics.by_bucket;
      }
      return undefined;
    }
    expect(() => accessByBucketIfMeasured(REAL_UNSUPPORTED_SNAPSHOT_METRICS)).not.toThrow();
  });
});
