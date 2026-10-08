/* Lives in src/__tests__/, NOT beside the page.
 *
 * Next.js treats every `.tsx` under `src/pages/` as a ROUTE, so a co-located test file is
 * built as a page — `next build` tried to collect page data for `/stock-intelligence.test`,
 * vitest's `expect` ran outside a vitest worker, and the FRONTEND DEPLOY FAILED. The unit
 * suite passed throughout, because vitest does not care where the file sits.
 */
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it, expect, vi } from 'vitest';

const swr = vi.hoisted(() => ({ byKey: {} as Record<string, any>, error: undefined as any }));
vi.mock('swr', () => ({
  default: (key: any) => {
    const k = Array.isArray(key) ? key[0] : key;
    const data = swr.byKey[k];
    return { data, error: swr.error, isLoading: !data && !swr.error, mutate: vi.fn() };
  },
}));

import StockIntelligencePanel from '@/components/StockIntelligencePanel';

/* SERVED LABELS, exactly as the backend's UNRESOLVED_LABEL sends them. The page must not keep
   its own copy — that is how four backend states once all rendered as the single thing they
   were added to stop saying. */
const LABELS = {
  RESOLVED: 'Resolved',
  UNRESOLVED_INSUFFICIENT_SESSIONS: 'Pending — the horizon has not elapsed',
  UNRESOLVED_ADJUSTMENT_UNVERIFIED: 'Corporate-action adjustment evidence unavailable',
  INVALID_CAPTURE: 'Invalid capture — excluded from publication',
  NOT_RESOLVED: 'Not yet scored',
};

const OUTCOMES = {
  symbol: 'MU', resolver_fingerprint: 'abc123', reason_labels: LABELS,
  note: 'Superseded outcomes are retained as audit records and are excluded from every performance reading.',
  state_counts: { UNRESOLVED_ADJUSTMENT_UNVERIFIED: 1, UNRESOLVED_INSUFFICIENT_SESSIONS: 1,
                  INVALID_CAPTURE: 1, RESOLVED: 1 },
  observations: [
    { observation_id: 1, origin: 'replay', observed_at: '2026-05-30T00:00:00', horizon: '1-5d',
      horizon_sessions: 5, direction: 'BULLISH', reference_price: 970.85,
      reference_price_as_of: '2026-05-29T00:00:00',
      invalidated_reason: 'captured at a non-trading-day cutoff produced by the session-walk defect',
      publishable: false, outcome: null, superseded: [] },
    { observation_id: 7, origin: 'replay', observed_at: '2026-06-01T00:00:00', horizon: '1-5d',
      horizon_sessions: 5, direction: 'BULLISH', reference_price: 970.85,
      reference_price_as_of: '2026-05-29T00:00:00', invalidated_reason: null, publishable: true,
      confirmation_rule: 'a completed close above 1108.72',
      invalidation_rule: 'a completed close below 902.60',
      outcome: { id: 10, state: 'UNRESOLVED_ADJUSTMENT_UNVERIFIED', sessions_elapsed: 5,
                 descriptive_return: null, excess_return: null, return_basis: null,
                 reason: 'the adjustment basis could not be established — stock: no corporate-action history covers this window' },
      superseded: [{ id: 4, resolver: 'unrecorded-pre-fingerprint', superseded_by: 10,
                     state: 'RESOLVED', descriptive_return: -0.0224, excess_return: 0.0044 }] },
    { observation_id: 8, origin: 'replay', observed_at: '2026-06-01T00:00:00', horizon: '1-4w',
      horizon_sessions: 20, direction: 'BULLISH', reference_price: 970.85,
      reference_price_as_of: '2026-05-29T00:00:00', invalidated_reason: null, publishable: true,
      outcome: { id: 11, state: 'RESOLVED', sessions_elapsed: 20, descriptive_return: 0.18876,
                 excess_return: 0.19906, return_basis: 'split_adjusted_price',
                 reason: 'descriptive from the reference close' },
      superseded: [] },
    { observation_id: 4, origin: 'prospective', observed_at: '2026-10-08T00:00:00',
      horizon: '1-5d', horizon_sessions: 5, direction: 'UNKNOWN', reference_price: 1088,
      reference_price_as_of: '2026-10-07T00:00:00', invalidated_reason: null, publishable: true,
      outcome: { id: 12, state: 'UNRESOLVED_INSUFFICIENT_SESSIONS', sessions_elapsed: 0,
                 descriptive_return: null, excess_return: null, return_basis: null,
                 reason: '0 of 5 trading sessions have completed' },
      superseded: [] },
  ],
};

const INTEL = {
  subject: 'stock:MU', as_of: '2026-10-08T00:00:00', origin: 'prospective',
  latest_session: '2026-10-07',
  buckets: [
    { bucket: 'technical', direction: 'BULLISH', status: 'measured',
      evidence: [{ claim: 'Up — range break observed', source: 'daily price series',
                   source_ref: '20-session range to 2026-10-07', as_of: '2026-10-07' }],
      limitations: [{ claim: 'Unadjusted prices', source: 'platform' }] },
    { bucket: 'revisions', direction: 'UNKNOWN', status: 'not_collected',
      evidence: [{ claim: 'no forward EPS or revenue consensus history exists', source: 'platform' }] },
    { bucket: 'options', direction: 'UNKNOWN', status: 'not_implemented',
      evidence: [{ claim: 'options data is stored but not composed into a bucket reading',
                   source: 'platform' }] },
  ],
  observations: [
    { id: 4, horizon: '1-5d', horizon_sessions: 5, direction: 'UNKNOWN', created: false },
    { id: 5, horizon: '1-4w', horizon_sessions: 20, direction: 'UNKNOWN', created: false,
      summary: { subject: 'stock:MU', direction: 'BULLISH',
                 direction_basis: '2 measured bucket(s) agree', horizon: '1-4w',
                 support_quality: 'MEDIUM', predictive_confidence: null,
                 predictive_confidence_note: 'not calibrated — a stored observation makes future measurement possible and establishes no skill by itself',
                 three_factors: [{ claim: 'revenue grew 48.9% in the latest stored year',
                                   source: 'stored annual statements' }],
                 counterevidence: [], open_questions: ['normalised earnings are unresolved'] } },
  ],
};

describe('stock intelligence panel', () => {
  it('ships the research view with mixed states and no returns available', () => {
    swr.byKey = { 'stock-outcomes': OUTCOMES, 'stock-intel': INTEL };
    const html = renderToStaticMarkup(<StockIntelligencePanel symbol="MU" />);

    // THE REASON THE USER ASKED FOR, verbatim and visible.
    expect(html).toContain('Corporate-action adjustment evidence unavailable');
    // Every distinct state renders its OWN label; none collapses into another.
    expect(html).toContain('Pending — the horizon has not elapsed');
    expect(html).toContain('Invalid capture — excluded from publication');
    expect(html).toContain('Resolved');

    // The research product renders even though two of four outcomes have no return.
    expect(html).toContain('revenue grew 48.9% in the latest stored year');
    expect(html).toContain('2 measured bucket(s) agree');
    // The buckets are the WORKING: present as a fold, not rendered into the conclusion.
    expect(html).toContain('Evidence buckets');
    expect(html).not.toContain('range break observed');

    // Support quality must never be presented as predictive confidence.
    expect(html).toContain('establishes no skill by itself');


    // A resolved row shows its basis; an unresolved one shows no fabricated number.
    expect(html).toContain('split_adjusted_price');
    expect(html).toContain('18.88%');
  });

  it('puts conclusions first and folds the working away', () => {
    swr.byKey = { 'stock-outcomes': OUTCOMES, 'stock-intel': INTEL };
    const html = renderToStaticMarkup(<StockIntelligencePanel symbol="MU" />);
    const at = (needle: string) => html.indexOf(needle);
    // 1 direction+horizon -> 2 main factors -> 3 counterevidence -> 4 triggers -> 5 outcome
    expect(at('over 1-4w')).toBeGreaterThan(-1);
    expect(at('over 1-4w')).toBeLessThan(at('Main factors'));
    expect(at('Main factors')).toBeLessThan(at('Strongest counterevidence'));
    expect(at('Strongest counterevidence')).toBeLessThan(at('Triggers'));
    expect(at('Triggers')).toBeLessThan(at('Outcome status'));
    // Triggers are the actionable half and were previously never served at all.
    expect(html).toContain('a completed close above 1108.72');
    expect(html).toContain('a completed close below 902.60');
    // The working is present but BELOW the conclusion, and collapsed.
    expect(at('Outcome status')).toBeLessThan(at('Evidence buckets'));
    expect(html).toContain('Adjustment evidence and return basis');
    expect(html).not.toContain('no forward EPS or revenue consensus history exists');
  });

  it('never renders a superseded figure as a current result', () => {
    swr.byKey = { 'stock-outcomes': OUTCOMES, 'stock-intel': INTEL };
    const html = renderToStaticMarkup(<StockIntelligencePanel symbol="MU" />);
    // The superseded panel is collapsed by default, so the withdrawn -2.24%/0.44% pair must
    // not appear anywhere in the default render.
    expect(html).not.toContain('-2.24%');
    expect(html).not.toContain('0.44%');
    expect(html).toContain('Superseded results (audit records)');
  });

  it('shows a failed request as an error, never as an empty result', () => {
    /* A 500 rendered as an empty table is indistinguishable from "nothing was observed", and
       this codebase has shipped that confusion before (the health panel stuck on Loading…). */
    swr.byKey = {}; swr.error = new Error('offline');
    const html = renderToStaticMarkup(<StockIntelligencePanel symbol="MU" />);
    expect(html).toContain('Could not load intelligence for MU');
    expect(html).toContain('not an empty result');
    expect(html).not.toContain('Loading MU intelligence');
    swr.error = undefined;
  });

  it('shows loading as loading, distinctly from both error and empty', () => {
    swr.byKey = {}; swr.error = undefined;
    const html = renderToStaticMarkup(<StockIntelligencePanel symbol="MU" />);
    expect(html).toContain('Loading MU intelligence');
    expect(html).not.toContain('Could not load intelligence');
  });
});

describe('test files never live under src/pages', () => {
  it('because next build treats every .tsx there as a route', () => {
    /* This is a DEPLOY-BLOCKING mistake the unit suite cannot see on its own: vitest happily
       runs a test wherever it sits, and only `next build` fails — after the backend has
       already gone out. */
    const { readdirSync, statSync } = require('fs') as typeof import('fs');
    const { join } = require('path') as typeof import('path');
    const offenders: string[] = [];
    const walk = (dir: string) => {
      for (const e of readdirSync(dir)) {
        const p = join(dir, e);
        if (statSync(p).isDirectory()) walk(p);
        else if (/\.(test|spec)\.(t|j)sx?$/.test(e)) offenders.push(p);
      }
    };
    walk(join(process.cwd(), 'src', 'pages'));
    expect(offenders).toEqual([]);
  });
});
