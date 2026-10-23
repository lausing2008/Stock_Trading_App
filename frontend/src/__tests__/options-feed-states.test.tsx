// @vitest-environment jsdom
// Audit R01/R02 (2026-10-22): feed failures are visible states, cached data never hides a failed
// refresh, and ages / expiry eligibility advance in an open tab.
import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SWRConfig } from 'swr';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { OptionsFlow } from '@/lib/api';

const api = vi.hoisted(() => ({ getGammaExposure: vi.fn(), getOptionsExpirations: vi.fn(), getDarkPoolPrints: vi.fn() }));
vi.mock('@/lib/api', () => ({ api }));
vi.mock('@/components/OptionsTabCharts', () => ({ GexOiByStrikeChart: () => null, MaxPainByExpiryChart: () => null, OiTermStructureChart: () => null }));
import MarketPressurePanel from '@/components/MarketPressurePanel';
import OptionsFlowCard from '@/components/OptionsFlowCard';

const fresh = (ui: React.ReactElement) => render(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false }}>{ui}</SWRConfig>);
beforeEach(() => Object.values(api).forEach(f => f.mockReset()));
afterEach(() => { cleanup(); vi.useRealTimers(); });

const PRINT = { price: 10, size: 1000, premium: 10000, venue: 'L', executed_at: '2026-10-22T14:00:00Z',
  executed_at_utc: '2026-10-22T14:00:00+00:00', time_status: 'ok', age_minutes: 1, side_estimate: null };
const dp = (over: Record<string, unknown>) => ({ symbol: 'SYNTH', available: false, source: 'none', prints: [], ...over });

describe('R01 — Market Pressure dark-pool states', () => {
  const STATES: [Record<string, unknown>, RegExp][] = [
    [{ status: 'timeout', reason: 'timeout' }, /did not answer in time/],
    [{ status: 'rate_limited', reason: 'rate_limited', http_status: 429 }, /rate limit was reached/],
    [{ status: 'upstream_error', reason: 'upstream_error', http_status: 503 }, /returned an error \(HTTP 503\)/],
    [{ status: 'unauthorized', reason: 'unauthorized' }, /rejected the subscription key or entitlement/],
    [{ status: 'malformed', reason: 'malformed', malformed_rows: 3 }, /could not be read \(3 unparseable prints\)/],
    [{ status: 'empty', reason: 'no_data', fetched_at: '2026-10-22T14:05:00+00:00' }, /returned none for this symbol \(answered Oct 22, 10:05 ET\)/],
    [{ status: 'disabled', reason: 'unusual_whales_disabled' }, /provider not configured/],
  ];

  it.each(STATES)('%o is shown, not hidden', async (payload, text) => {
    api.getGammaExposure.mockResolvedValue({ available: false, source: 'none' });
    api.getOptionsExpirations.mockResolvedValue({ available: false });
    api.getDarkPoolPrints.mockResolvedValue(dp(payload));
    fresh(<MarketPressurePanel symbol="SYNTH" />);
    const line = await screen.findByLabelText('Dark pool feed status');
    expect(line.textContent).toMatch(text);
    const failure = !['empty', 'disabled'].includes(payload.status as string);
    expect(line.textContent!.includes('feed failure, not an absence of prints')).toBe(failure);
    expect(Boolean(screen.queryByRole('button', { name: 'Retry dark pool' }))).toBe(failure);
  });

  it('the seven states render seven different texts', async () => {
    const texts = new Set<string>();
    for (const [payload] of STATES) {
      api.getGammaExposure.mockResolvedValue({ available: false, source: 'none' });
      api.getOptionsExpirations.mockResolvedValue({ available: false });
      api.getDarkPoolPrints.mockResolvedValue(dp(payload));
      fresh(<MarketPressurePanel symbol="SYNTH" />);
      texts.add((await screen.findByLabelText('Dark pool feed status')).textContent!);
      cleanup();
    }
    expect(texts.size).toBe(STATES.length);
  });

  it('a last good sample is shown only beside the failure, labelled as not current', async () => {
    api.getGammaExposure.mockResolvedValue({ available: false, source: 'none' });
    api.getOptionsExpirations.mockResolvedValue({ available: false });
    api.getDarkPoolPrints.mockResolvedValue(dp({ available: true, source: 'unusual_whales', status: 'rate_limited',
      stale: true, stale_fetched_at: '2026-10-22T13:00:00+00:00', fetched_at: null, prints: [PRINT] }));
    fresh(<MarketPressurePanel symbol="SYNTH" />);
    expect((await screen.findByLabelText('Dark pool feed status')).textContent)
      .toMatch(/rate limit.*last good sample, retrieved Oct 22, 09:00 ET — not current/);
    expect(screen.getByLabelText('Dark pool retrieved').textContent).toContain('Last good sample retrieved Oct 22, 09:00 ET');
    expect(screen.getByText('$10.00')).toBeTruthy();
  });

  it('a dark-pool failure leaves other panels usable, and Retry targets only it', async () => {
    api.getGammaExposure.mockResolvedValue({ available: true, source: 'unusual_whales', call_wall: 110, put_wall: 90, gamma_flip: 100, as_of_date: '2026-10-21' });
    api.getOptionsExpirations.mockResolvedValue({ available: false });
    api.getDarkPoolPrints.mockResolvedValue(dp({ status: 'upstream_error', http_status: 503 }));
    fresh(<MarketPressurePanel symbol="SYNTH" />);
    expect(await screen.findByText('Call wall')).toBeTruthy();
    await screen.findByLabelText('Dark pool feed status');
    const gexCalls = api.getGammaExposure.mock.calls.length, dpCalls = api.getDarkPoolPrints.mock.calls.length;
    api.getDarkPoolPrints.mockResolvedValue(dp({ available: true, source: 'unusual_whales', status: 'ok', fetched_at: '2026-10-22T14:06:00+00:00', prints: [PRINT] }));
    fireEvent.click(screen.getByRole('button', { name: 'Retry dark pool' }));
    await waitFor(() => expect(api.getDarkPoolPrints.mock.calls.length).toBeGreaterThan(dpCalls));
    await waitFor(() => expect(screen.queryByLabelText('Dark pool feed status')).toBeNull());
    expect(api.getGammaExposure.mock.calls.length).toBe(gexCalls);
  });

  it('no data from the previous symbol is shown after navigating', async () => {
    api.getGammaExposure.mockResolvedValue({ available: false, source: 'none' });
    api.getOptionsExpirations.mockResolvedValue({ available: false });
    api.getDarkPoolPrints.mockResolvedValue(dp({ available: true, source: 'unusual_whales', status: 'ok', fetched_at: '2026-10-22T14:06:00+00:00', prints: [PRINT] }));
    const view = fresh(<MarketPressurePanel symbol="AAA" />);
    expect(await screen.findByText('$10.00')).toBeTruthy();
    api.getDarkPoolPrints.mockReturnValue(new Promise(() => {}));   // BBB never answers
    view.rerender(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false }}><MarketPressurePanel symbol="BBB" /></SWRConfig>);
    await waitFor(() => expect(screen.queryByText('$10.00')).toBeNull());
  });
});

describe('R02 — freshness', () => {
  const flow = (over: Partial<OptionsFlow> = {}): OptionsFlow => ({ symbol: 'SYNTH', available: true, call_volume: 1500, put_volume: 1200,
    chain_as_of: '2026-10-21', source_session: '2026-10-21', computed_at: '2026-10-22T19:50:00+00:00',
    expiries_used: ['2026-10-22'], expiries_attempted: ['2026-10-22'], coverage_status: 'complete', composition_scope: 'complete',
    expiry_scope: { source_session: '2026-10-21', evaluated_at: '2026-10-22T19:50:00+00:00', historical_expiries: ['2026-10-22'],
      current_expiries: ['2026-10-22'], excluded_from_current: [], rule: '',
      expiries: [{ expiry: '2026-10-22', historical: true, current: true, reason: null, last_trading_instant: '2026-10-22T20:00:00+00:00' }] },
    unusual: [{ expiry: '2026-10-22', side: 'call', strike: 100, volume: 900, oi: 500, vol_oi: 1.8, iv: 40, itm: null, moneyness: null,
      premium: null, traded_premium: null, estimated_turnover: null, premium_basis: 'unavailable', is_whale: null, current_candidate: true }],
    ...over });

  it('a failed refresh keeps the data, warns, names its computed time, and offers Retry', () => {
    const onRetry = vi.fn();
    render(<OptionsFlowCard flow={flow()} error={new Error('503 Service Unavailable')} onRetry={onRetry} />);
    expect(screen.getByLabelText('Volume composition')).toBeTruthy();                  // data retained
    const alert = screen.getByRole('alert', { name: 'Options Flow refresh failed' });
    expect(alert.textContent).toMatch(/HTTP 503.*Showing the data computed Oct 22, 15:50 ET/);
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('advancing the clock ages the data and expires a contract without a new response', () => {
    vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
    vi.setSystemTime(new Date('2026-10-22T19:55:00Z'));
    const data = flow();
    render(<OptionsFlowCard flow={data} error={undefined} onRetry={() => {}} />);
    const text = () => document.body.textContent ?? '';
    expect(text()).toContain('Computed Oct 22, 15:50 ET · 5 min ago');
    expect(text()).not.toContain('expired (historical)');
    // advanceTimersByTime also moves the faked clock: 20:00:30 + 30 s = 20:01:00.
    act(() => { vi.setSystemTime(new Date('2026-10-22T20:00:30Z')); vi.advanceTimersByTime(30_000); });
    expect(text()).toContain('Computed Oct 22, 15:50 ET · 11 min ago');   // same source time, older
    expect(text()).toContain('expired (historical)');                     // past its last trading instant
    expect(text()).toMatch(/1 of 1 expirations \(2026-10-22\) have expired since that session/);
  });

  it('an off-exchange print age is elapsed time, not the frozen response value', async () => {
    vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
    vi.setSystemTime(new Date('2026-10-22T14:01:00Z'));
    api.getGammaExposure.mockResolvedValue({ available: false, source: 'none' });
    api.getOptionsExpirations.mockResolvedValue({ available: false });
    api.getDarkPoolPrints.mockResolvedValue(dp({ available: true, source: 'unusual_whales', status: 'ok', fetched_at: '2026-10-22T14:01:00+00:00', prints: [PRINT] }));
    fresh(<MarketPressurePanel symbol="SYNTH" />);
    expect(await screen.findByText('Oct 22, 10:00 ET · 1 min ago')).toBeTruthy();
    act(() => { vi.setSystemTime(new Date('2026-10-22T16:00:30Z')); vi.advanceTimersByTime(30_000); });
    expect(screen.getByText('Oct 22, 10:00 ET · 2.0 h ago')).toBeTruthy();
  });

  it('NOPE shows its own reading time and age, not a cadence claim', async () => {
    vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
    vi.setSystemTime(new Date('2026-10-22T15:00:00Z'));
    api.getGammaExposure.mockResolvedValue({ available: true, source: 'unusual_whales', call_wall: 110, put_wall: 90, gamma_flip: 100,
      as_of_date: '2026-10-22', nope: { nope: 0.2, nope_fill: null, call_delta: null, put_delta: null, call_vol: null, put_vol: null, stock_vol: null, timestamp: '2026-10-22T14:57:00Z' } });
    api.getOptionsExpirations.mockResolvedValue({ available: false });
    api.getDarkPoolPrints.mockResolvedValue(dp({ status: 'empty' }));
    fresh(<MarketPressurePanel symbol="SYNTH" />);
    const label = await screen.findByLabelText('NOPE reading time');
    expect(label.textContent).toContain('Reading from Oct 22, 10:57 ET · 3 min ago');
    expect(document.body.textContent).not.toContain('updates roughly every minute');
  });
});
