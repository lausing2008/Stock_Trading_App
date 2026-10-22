// @vitest-environment jsdom
import React from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { OptionsFlow } from '@/lib/api';
import OptionsFlowCard from './OptionsFlowCard';

afterEach(cleanup);
const row = (side: 'call' | 'put', extra = {}) => ({ expiry: '2026-10-24', side, strike: 100, volume: 900, oi: 500, vol_oi: 1.8, iv: 40, itm: false,
  premium: null, traded_premium: null, estimated_turnover: 48_000, premium_basis: 'estimated_turnover' as const, is_whale: null, ...extra });
const flow = (over: Partial<OptionsFlow> = {}): OptionsFlow => ({ symbol: 'MU', available: true, call_volume: 1296, put_volume: 1000, cp_ratio: 1.3,
  sentiment: 'bullish', chain_as_of: '2026-10-20T00:00:00', expiries_used: ['2026-10-24', '2026-10-31'], expiries_attempted: ['2026-10-24', '2026-10-31', '2026-11-07'],
  coverage_status: 'partial', unusual: [row('call'), row('put'), row('put', { traded_premium: 600_000, premium: 600_000 })], whale_count: null, ...over });

it('shows loading, then a failed request as unknown with a working retry', () => {
  const onRetry = vi.fn();
  const { rerender } = render(<OptionsFlowCard flow={undefined} error={undefined} onRetry={onRetry} />);
  expect(screen.getByRole('status').textContent).toContain('loading');
  rerender(<OptionsFlowCard flow={undefined} error={new Error('500 Internal Server Error')} onRetry={onRetry} />);
  expect(screen.getByRole('status').textContent).toMatch(/HTTP 500.*unknown, not empty/);
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(onRetry).toHaveBeenCalledTimes(1);
  rerender(<OptionsFlowCard flow={{ symbol: 'MU', available: false, reason: 'chain_read_failed' }} error={undefined} onRetry={onRetry} />);
  expect(screen.getByRole('status').textContent).toMatch(/data failure, not an absence of activity/);
});

it('labels composition from exact volumes, never direction, with visible cut points, coverage and premium facts', () => {
  render(<OptionsFlowCard flow={flow()} error={undefined} onRetry={() => {}} />);
  const badge = screen.getByLabelText('Volume composition');
  expect(badge.textContent).toContain('Mixed volume');                     // 1296/1000 = 1.296 < 1.3, though shown as 1.30
  expect(badge.textContent).toContain('56.4% calls / 43.6% puts');
  const text = document.body.textContent ?? '';
  expect(text).not.toMatch(/bullish|bearish|📉/i);
  expect(text).toContain('Call-heavy when C/P ≥ 1.3 (≈56.5% calls), put-heavy when C/P ≤ 0.8 (≈55.6% puts)');
  expect(text).toContain('Settled session 2026-10-20 · 2 of 3 expirations (2026-10-24, 2026-10-31) · coverage partial');
  expect(screen.getAllByText('unavailable').length).toBeGreaterThanOrEqual(2);  // paid premium cells
  expect(screen.getAllByText('≈$48K').length).toBe(3);                        // turnover in its own column
  expect(text).toContain('Est. turnover (vol × mid × 100)');
  expect(text).toContain('Paid premium needs trade-level data');
  expect(text).toContain('≥$600K paid premium (1 of 2 contracts known)');    // partial sum never shown as complete
});

it('qualifies a split resting on little put volume', () => {
  render(<OptionsFlowCard flow={flow({ call_volume: 30, put_volume: 10, unusual: [] })} error={undefined} onRetry={() => {}} />);
  expect(screen.getByLabelText('Volume composition').textContent).toMatch(/Call-heavy volume.*low put volume/);
});
