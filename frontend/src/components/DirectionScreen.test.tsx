import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it, expect, vi } from 'vitest';
import DirectionScreen from './DirectionScreen';
import { readFileSync } from 'fs';
import { join } from 'path';

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

describe('instrument type beside the symbol', () => {
  const row = (symbol: string, instrument: any) => ({
    symbol, name: symbol, market: 'US', currency: 'USD', instrument,
    setup: { direction: 'breakout', label: 'Up — range break observed', close: 10,
      support: 8, resistance: 9, factors: [], strategy: 'Watch for a hold/retest',
      session: '2026-10-05', limitations: [] } });
  const render = (rows: any[]) => {
    state.data = { scanned: rows.length, matching: rows.length, calendar_available: true,
      sectors: [], session_dates: { US: '2026-10-05' }, policy: 'test', note: 'n', rows };
    state.error = undefined;
    return renderToStaticMarkup(<DirectionScreen symbols="" />);
  };

  it('labels a fund as a fund, not as a company', () => {
    const html = render([row('QQQM', { type: 'fund', leveraged: false,
      basis: 'name identifies a pooled vehicle' })]);
    expect(html).toContain('Fund');
    expect(html).toContain('name identifies a pooled vehicle');
  });

  it('flags a daily-reset leveraged fund separately', () => {
    // §30: SOXL must not be read like an ordinary 1x ETF. QLD is the one in the live list.
    const html = render([row('QLD', { type: 'fund', leveraged: true, basis: 'b' })]);
    expect(html).toContain('Fund');
    expect(html).toContain('Leveraged');
    expect(html).toContain('multiple of a daily move');
  });

  it('does not call an ordinary fund leveraged', () => {
    const html = render([row('QQQM', { type: 'fund', leveraged: false, basis: 'b' })]);
    expect(html).not.toContain('Leveraged');
  });

  it('shows an unresolved identity as unverified rather than guessing', () => {
    const html = render([row('HOOD', { type: 'unverified', leveraged: false,
      basis: 'not established either way' })]);
    expect(html).toContain('Type unverified');
    expect(html).toContain('not established either way');
  });

  it('carries the basis for every type, because the type is inferred', () => {
    for (const t of ['operating_company', 'fund', 'unverified']) {
      const html = render([row('X', { type: t, leveraged: false, basis: `why-${t}` })]);
      expect(html).toContain(`why-${t}`);
    }
  });

  it('renders a row that predates the field without breaking', () => {
    const html = render([row('OLD', undefined)]);
    expect(html).toContain('OLD');
    expect(html).not.toContain('Type unverified');
  });
});

describe('the setup guide remembers a collapse', () => {
  it('opens by default when nothing is stored', () => {
    state.data = { scanned: 0, matching: 0, calendar_available: true, sectors: [],
      session_dates: {}, policy: 't', note: 'n', rows: [] };
    const html = renderToStaticMarkup(<DirectionScreen symbols="" />);
    // Server render has no localStorage; the fallback must be OPEN, never hidden.
    expect(html).toContain('What is a setup?');
    expect(html).toMatch(/<details[^>]*\sopen/);
  });

  it('guards every storage access and falls back to open', () => {
    const src = readFileSync(join(__dirname, 'DirectionScreen.tsx'), 'utf8');
    const fn = src.slice(src.indexOf('function readGuideOpen'), src.indexOf('function SetupGuide'));
    expect(fn).toContain('try {');
    expect(fn).toContain('catch { return true; }');
    // The write is guarded too: a throwing setItem must not take the page down.
    expect(src).toContain("try { window.localStorage.setItem(GUIDE_KEY");
  });

  it('reads storage after mount, not during render', () => {
    // Reading during render produces markup that disagrees with the client.
    const src = readFileSync(join(__dirname, 'DirectionScreen.tsx'), 'utf8');
    expect(src).toContain('useEffect(() => { setOpen(readGuideOpen()); }, []);');
    expect(src).toContain('useState(true)');
  });
});

describe('instrument type reads as an inference, not a fact', () => {
  const row = (symbol: string, instrument: any) => ({
    symbol, name: symbol, market: 'US', currency: 'USD', instrument,
    setup: { direction: 'breakout', label: 'Up — range break observed', close: 10,
      support: 8, resistance: 9, factors: [], strategy: 's', session: '2026-10-05',
      limitations: [] } });
  const render = (rows: any[]) => {
    state.data = { scanned: rows.length, matching: rows.length, calendar_available: true,
      sectors: [], session_dates: { US: '2026-10-05' }, policy: 't', note: 'n', rows };
    state.error = undefined;
    return renderToStaticMarkup(<DirectionScreen symbols="" />);
  };

  it('marks an inferred company as inferred rather than asserting it', () => {
    const html = render([row('AMD', { type: 'operating_company', confidence: 'inferred',
      leveraged: false, basis: 'metadata' })]);
    expect(html).toContain('Company (inferred)');
  });

  it('marks an inferred fund as inferred', () => {
    const html = render([row('QQQM', { type: 'fund', confidence: 'inferred',
      leveraged: false, basis: 'name' })]);
    expect(html).toContain('Fund (inferred)');
  });

  it('says in the tooltip that no declared source exists', () => {
    const html = render([row('QQQM', { type: 'fund', confidence: 'inferred',
      leveraged: false, basis: 'name identifies a pooled vehicle' })]);
    expect(html).toContain('no declared instrument type is available');
  });

  it('does not double-label an already-unverified type', () => {
    const html = render([row('HOOD', { type: 'unverified', confidence: 'inferred',
      leveraged: false, basis: 'b' })]);
    expect(html).toContain('Type unverified');
    expect(html).not.toContain('Type unverified (inferred)');
  });

  it('would not call a declared type inferred', () => {
    const html = render([row('QQQM', { type: 'fund', confidence: 'declared',
      leveraged: false, basis: 'declared by the source' })]);
    expect(html).toContain('Fund');
    expect(html).not.toContain('(inferred)');
    expect(html).not.toContain('no declared instrument type is available');
  });
});
