// @vitest-environment jsdom
import React from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { OptionsDominance, OptionsFlow } from '@/lib/api';
import OptionsDominanceCard from './OptionsDominanceCard';

afterEach(cleanup);
const side = (value: number) => ({ known: 1, total: 1, value, observed_subtotal: value });
function summary(): OptionsDominance {
  return { method_version: 'options_dominance_v1', symbol: 'ANY', available: true,
    source: 'unusual_whales_settled_chain', source_session: '2026-10-22', computed_at: '2026-10-23T20:00:00Z',
    scope: { universe: 'nearest_four_expirations_of_source_session', requested: ['2026-11-20'], included: ['2026-11-20'], missing: [], coverage_status: 'complete', slices: [] },
    rows: [
      { metric: 'volume', status: 'measured', sides: { call: side(3000), put: side(1000) }, call_share: .75, put_share: .25, composition: 'call_heavy', note: 'Volume does not establish trade direction.' },
      { metric: 'open_interest', status: 'measured', sides: { call: side(100), put: side(900) }, call_share: .1, put_share: .9, composition: 'more_puts', reported_as_of: null, note: 'Provider OI reporting date is not supplied.' },
      { metric: 'traded_premium', status: 'unavailable', note: 'Trade-level events are not captured for this scope.' },
      { metric: 'aggressor_estimate', status: 'unavailable', note: 'No comparable event window.' },
    ], volume_oi_disagree: true, directional_intent: 'not_established', actionability: 'research_only' };
}
const flow = (s = summary()): OptionsFlow => ({ symbol: 'ANY', available: true, dominance: s });

it('shows loading and request failure with a working retry, rather than hiding activity', () => {
  const retry = vi.fn();
  const { rerender } = render(<OptionsDominanceCard onRetry={retry} />);
  expect(screen.getByRole('status').textContent).toContain('Loading');
  rerender(<OptionsDominanceCard error={new Error('503 server error')} onRetry={retry} />);
  expect(screen.getByRole('alert').textContent).toMatch(/unknown|failed/i);
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(retry).toHaveBeenCalledOnce();
  rerender(<OptionsDominanceCard flow={{ symbol: 'ANY', available: false, reason: 'chain_read_failed' }} onRetry={retry} />);
  expect(screen.getByRole('status').textContent).toContain('data failure');
});

it('reports volume and OI separately and exposes unavailable premium and buy/sell evidence', () => {
  render(<OptionsDominanceCard flow={flow()} onRetry={() => {}} />);
  const volume = screen.getByRole('row', { name: /Contract volume/ });
  const oi = screen.getByRole('row', { name: /Open interest/ });
  expect(volume.textContent).toContain('Call-heavy volume');
  expect(volume.textContent).toContain('75.0% calls / 25.0% puts');
  expect(oi.textContent).toContain('Higher put OI');
  expect(oi.textContent).toContain('reporting date is not supplied');
  expect(screen.getByRole('row', { name: /Paid premium/ }).textContent).toContain('Unavailable');
  expect(screen.getByRole('row', { name: /Buy\/sell estimate/ }).textContent).toContain('Unavailable');
  expect(document.body.textContent).toContain('Volume and OI favor different sides');
  expect(document.body.textContent).toContain('Research only. Direction is not established');
});

it.each(['call_heavy', 'put_heavy'] as const)('does not infer a trade direction from %s, including selling scenarios', composition => {
  const s = summary();
  s.rows[0].composition = composition;
  render(<OptionsDominanceCard flow={{ ...flow(s), sentiment: composition === 'call_heavy' ? 'bearish' : 'bullish' }} onRetry={() => {}} />);
  expect(document.body.textContent).not.toMatch(/bullish|bearish/i);
  expect(document.body.textContent).toContain('calls and puts can be bought or sold');
});

it('shows partial subtotals and withholds shares rather than converting missing values to zero', () => {
  const s = summary();
  Object.assign(s.rows[0], { status: 'partial', call_share: null, put_share: null, composition: 'unmeasured',
    sides: { call: { known: 1, total: 3, value: null, observed_subtotal: 100 }, put: side(100) } });
  render(<OptionsDominanceCard flow={flow(s)} onRetry={() => {}} />);
  const row = screen.getByRole('row', { name: /Contract volume/ });
  expect(row.textContent).toContain('≥100 (1/3 contracts measured)');
  expect(row.textContent).toContain('Shares withheld');
  expect(row.textContent).not.toMatch(/\d\.\d%/);
});

it('retains available OI when the legacy flow response has no volume, with zero distinct from missing', () => {
  const s = summary();
  Object.assign(s.rows[0], { status: 'zero', call_share: null, put_share: null, composition: 'unmeasured', sides: { call: side(0), put: side(0) } });
  render(<OptionsDominanceCard flow={{ ...flow(s), available: false, reason: 'no_volume' }} onRetry={() => {}} />);
  const volume = screen.getByRole('row', { name: /Contract volume/ });
  expect(volume.textContent).toContain('Measured total is zero');
  expect(within(volume).getAllByText('0')).toHaveLength(2);
  expect(screen.getByRole('row', { name: /Open interest/ }).textContent).toContain('Higher put OI');
});

it('discloses the included subset and missing reads, and keeps a failed refresh warning beside cached data', () => {
  const s = summary();
  s.scope.requested.push('2026-12-18'); s.scope.missing.push('2026-12-18'); s.scope.coverage_status = 'partial';
  render(<OptionsDominanceCard flow={flow(s)} error={new Error('timeout')} onRetry={() => {}} />);
  expect(screen.getByRole('alert').textContent).toContain('not current');
  expect(document.body.textContent).toContain('1 of 2 selected expirations');
  expect(document.body.textContent).toContain('Missing expiration reads: 2026-12-18');
  expect(document.body.textContent).toContain('Computed');
  expect(document.body.textContent).toContain('not a current trade-candidate list');
});

it('does not synthesize a summary from an old response without the new measurement block', () => {
  render(<OptionsDominanceCard flow={{ symbol: 'ANY', available: true, call_volume: 3000, put_volume: 1000 }} onRetry={() => {}} />);
  expect(screen.getByRole('status').textContent).toContain('summary unavailable');
  expect(screen.queryByRole('table')).toBeNull();
});
