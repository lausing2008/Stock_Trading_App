import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import SignalCard from '@/components/SignalCard';
import type { Signal } from '@/lib/api';

function signal(reasons: Signal['reasons']): Signal {
  return {
    symbol: 'MU',
    signal: 'HOLD',
    horizon: 'SWING',
    confidence: 18,
    bullish_probability: 0.59,
    reasons,
  };
}

describe('signal strength explanation', () => {
  it('explains the formula, provenance, and active reducers without calling strength a probability', () => {
    const html = renderToStaticMarkup(<SignalCard signal={signal({
      fused_pre_compression: 0.68,
      ml_quality_status: 'measured',
      ml_test_auc: 0.541,
      ml_model: 'ensemble',
      ml_weight: 0.16,
      low_oos_accuracy: true,
      breadth_compression: true,
      compression_cap_applied: true,
    })} />);

    expect(html).toContain('Why this strength?');
    expect(html).toContain('|0.590 − 0.500| × 200');
    expect(html).toContain('Before the recorded filter chain: 0.680 directional, 36/100 strength');
    expect(html).toContain('ensemble AUC 0.541, weight 16/100');
    expect(html).toContain('low out-of-sample model quality; weak market breadth');
    expect(html).toContain('Weak Market Breadth');
    expect(html).not.toContain('Breadth Compressed −100%');
  });

  it('does not turn a false breadth flag into a 100 percent penalty', () => {
    const html = renderToStaticMarkup(<SignalCard signal={signal({
      ml_quality_status: 'unavailable',
      breadth_compression: false,
    })} />);

    expect(html).toContain('quality unavailable or invalid; no measured AUC is claimed');
    expect(html).toContain('Active reducers: none recorded');
    expect(html).not.toContain('Weak Market Breadth');
    expect(html).not.toContain('−100%');
  });
});
