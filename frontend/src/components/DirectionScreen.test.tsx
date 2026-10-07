import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it, expect, vi } from 'vitest';
import DirectionScreen from './DirectionScreen';

const state = vi.hoisted(() => ({ data: undefined as any, error: undefined as any }));
vi.mock('swr', () => ({ default: () => ({ ...state, isLoading: false, mutate: vi.fn() }) }));

describe('direction screen rendered output', () => {
  it('renders filters, factors, trigger and approach without claiming a probability', () => {
    state.data = { scanned: 250, matching: 1, calendar_available: true, sectors: ['Tech'],
      session_dates: { US: '2026-10-05' }, policy: 'test', note: 'ranking note',
      rows: [{ symbol: 'ABC', name: 'Example', market: 'US', currency: 'USD', setup: {
        direction: 'breakout_watch', label: 'Upward setup — awaiting break', close: 104,
        support: 95, resistance: 105, factors: ['Volume 1.50×'], strategy: 'Wait for close above resistance',
        session: '2026-10-05', limitations: ['No earnings check'] } }] };
    const html = renderToStaticMarkup(<DirectionScreen symbols="" />);
    for (const text of ['US + HK', 'Top 20', 'Breakdown watch', 'Sector', 'ABC',
      'Volume 1.50', '105.00', 'Wait for close above resistance', 'Evidence &amp; limits',
      'not a probability']) expect(html).toContain(text);
    expect(html).not.toContain('high chance');
  });
  it('makes fetch failure visible instead of reporting no candidates', () => {
    state.data = undefined; state.error = new Error('offline');
    const html = renderToStaticMarkup(<DirectionScreen symbols="MU" />);
    expect(html).toContain('Could not load price setups');
    expect(html).not.toContain('No listings match');
    state.error = undefined;
  });
});
