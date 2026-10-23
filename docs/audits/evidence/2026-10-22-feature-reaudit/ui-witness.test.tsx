// @vitest-environment jsdom
// Copy temporarily into frontend/src/__tests__/feature-reaudit-witness.test.tsx and run Vitest.
// These are witnesses of CURRENT gaps, not acceptance tests for a fix.
import React from 'react';
import { afterEach, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import OptionsFlowCard from '@/components/OptionsFlowCard';
import { buildGainLossRows } from '@/lib/optionsGainLoss';
import { printTimeText } from '@/lib/optionsFlowStatus';
afterEach(cleanup);

it('cached available flow hides a subsequent failed refresh', () => {
  render(<OptionsFlowCard flow={{ symbol: 'SYNTH', available: true, call_volume: 1300,
    put_volume: 1000, cp_ratio: 1.3, sentiment: 'bullish', unusual: [] }}
    error={new Error('503 Service Unavailable')} onRetry={() => {}} />);
  expect(screen.getByLabelText('Volume composition')).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull();
  expect(document.body.textContent).not.toContain('Request failed');
});

it('an explicit held fill disappears when its archived quote is missing', () => {
  const rows = buildGainLossRows([{ strike: 100, bid: 0, ask: 0, last_price: 0,
    oi: 0, volume: 0, iv: 0 }], 'buy', 'call', 110, 120, 1, { strike: 100, premium: 5 });
  expect(rows).toEqual([]);
});

it('the print age is a frozen response value, not elapsed time since execution', () => {
  const text = printTimeText({ executed_at_utc: '2020-01-02T15:00:00Z', time_status: 'ok', age_minutes: 1 });
  expect(text).toContain('1 min ago');
});
