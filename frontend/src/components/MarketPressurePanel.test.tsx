// @vitest-environment jsdom
import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SWRConfig } from 'swr';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const api = vi.hoisted(() => ({ getGammaExposure: vi.fn(), getOptionsExpirations: vi.fn(), getDarkPoolPrints: vi.fn() }));
vi.mock('@/lib/api', () => ({ api }));
vi.mock('./OptionsTabCharts', () => ({ GexOiByStrikeChart: () => null, MaxPainByExpiryChart: () => null, OiTermStructureChart: () => null }));
import MarketPressurePanel from './MarketPressurePanel';

const fresh = (ui: React.ReactElement) => render(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false }}>{ui}</SWRConfig>);
beforeEach(() => Object.values(api).forEach(f => f.mockReset()));
afterEach(cleanup);

it('shows each failed request as unknown with a retry, instead of rendering nothing', async () => {
  api.getGammaExposure.mockRejectedValue(new Error('500 Internal Server Error'));
  api.getOptionsExpirations.mockResolvedValue({ available: false });
  api.getDarkPoolPrints.mockRejectedValue(new TypeError('Failed to fetch'));
  fresh(<MarketPressurePanel symbol="MU" />);
  const status = await screen.findByRole('status', { name: 'Market Pressure status' });
  await waitFor(() => expect(status.textContent).toContain('dark pool'));
  expect(status.textContent).toMatch(/dealer gamma: Request failed \(HTTP 500\).*unknown, not empty/);
  expect(status.textContent).toMatch(/dark pool: Request failed \(network\)/);
  expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
});

it('renders nothing when every request succeeded with no data, and shows loading while pending', async () => {
  let release: (v: unknown) => void = () => {};
  api.getGammaExposure.mockReturnValue(new Promise(r => { release = r; }));
  api.getOptionsExpirations.mockResolvedValue({ available: false });
  api.getDarkPoolPrints.mockResolvedValue({ available: false });
  const view = fresh(<MarketPressurePanel symbol="MU" />);
  expect((await screen.findByRole('status', { name: 'Market Pressure status' })).textContent).toContain('loading');
  release({ available: false });
  await waitFor(() => expect(view.container.textContent).toBe(''));
});

it('keeps showing loaded data while naming the request that failed', async () => {
  api.getGammaExposure.mockResolvedValue({ available: true, source: 'unusual_whales', call_wall: 110, put_wall: 90, gamma_flip: 100, as_of_date: '2026-10-21' });
  api.getOptionsExpirations.mockResolvedValue({ available: false });
  api.getDarkPoolPrints.mockRejectedValue(new Error('502 Bad Gateway'));
  fresh(<MarketPressurePanel symbol="MU" />);
  expect(await screen.findByText('Call wall')).toBeTruthy();
  await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/dark pool: Request failed \(HTTP 502\)/));
  // Retry re-fetches the failed request.
  api.getDarkPoolPrints.mockResolvedValue({ available: false });
  const before = api.getDarkPoolPrints.mock.calls.length;
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await waitFor(() => expect(api.getDarkPoolPrints.mock.calls.length).toBeGreaterThan(before));
});

it('labels an expiration past its last trade as historical in the rollup', async () => {
  api.getGammaExposure.mockResolvedValue({ available: false, source: 'none' });
  api.getDarkPoolPrints.mockResolvedValue({ available: false });
  const r = (expiry: string, current: boolean) => ({ expiry, call_oi: 10, put_oi: 10, total_oi: 20, call_volume: 1, put_volume: 1,
    put_call_oi_ratio: 1, concentration_pct: 50, level: 'extreme', current });
  api.getOptionsExpirations.mockResolvedValue({ available: true, expirations: [r('2026-10-21', false), r('2026-10-23', true)] });
  fresh(<MarketPressurePanel symbol="MU" />);
  const cell = await screen.findByText(/2026-10-21/);
  expect(cell.textContent).toContain('expired (historical)');
  expect((await screen.findByText(/2026-10-23/)).textContent).not.toContain('expired');
});
