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
  evidence_labels: {
    verified: 'Verified — corporate-action coverage is guaranteed exhaustive for this window',
    provisional: 'Provisional — based on returned corporate actions; completeness unverified',
    unverified: 'Unverified — no adjustment basis could be established for this window' },
  provisional_remedy: 'A source that documents completeness for the span, or corroboration from a second independent source, would make this verified. Until then the figure is usable and labelled, and is never pooled with verified results.',
  coverage: { replay: { origin: 'replay', invalidated_captures: 3, note: '',
                        pools: { verified: { total: 0, by_reason: {} },
                                 provisional: { total: 1, by_reason: {} },
                                 ineligible: { total: 1, by_reason: {} } } },
              prospective: { origin: 'prospective', invalidated_captures: 0, note: '',
                             pools: {} } },
  state_counts: { UNRESOLVED_ADJUSTMENT_UNVERIFIED: 1, UNRESOLVED_INSUFFICIENT_SESSIONS: 1,
                  INVALID_CAPTURE: 1, RESOLVED: 1 },
  observations: [
    { observation_id: 1, origin: 'replay', observed_at: '2026-05-30T00:00:00', horizon: '1-5d',
      horizon_sessions: 5, direction: 'BULLISH', reference_price: 970.85,
      reference_price_as_of: '2026-05-29T00:00:00',
      invalidated_reason: 'captured at a non-trading-day cutoff produced by the session-walk defect',
      publishable: false, is_current_policy: true, outcome: null, superseded: [] },
    { observation_id: 7, origin: 'replay', observed_at: '2026-06-01T00:00:00', horizon: '1-5d',
      horizon_sessions: 5, direction: 'BULLISH', reference_price: 970.85,
      reference_price_as_of: '2026-05-29T00:00:00', invalidated_reason: null, publishable: true, is_current_policy: true,
      confirmation_rule: 'a completed close above 1108.72',
      invalidation_rule: 'a completed close below 902.60',
      triggers: { direction: 'BULLISH', confirms: 'a completed close above 1108.72',
                  invalidates: 'a completed close below 902.60', establishes: null,
                  basis: 'a bullish reading is confirmed by a break UP through resistance' },
      outcome: { id: 10, state: 'UNRESOLVED_ADJUSTMENT_UNVERIFIED', sessions_elapsed: 5,
                 descriptive_return: null, excess_return: null, return_basis: null,
                 reason: 'the adjustment basis could not be established — stock: no corporate-action history covers this window' },
      superseded: [{ id: 4, resolver: 'unrecorded-pre-fingerprint', superseded_by: 10,
                     state: 'RESOLVED', descriptive_return: -0.0224, excess_return: 0.0044 }] },
    { observation_id: 8, origin: 'replay', observed_at: '2026-06-01T00:00:00', horizon: '1-4w',
      horizon_sessions: 20, direction: 'BULLISH', reference_price: 970.85,
      reference_price_as_of: '2026-05-29T00:00:00', invalidated_reason: null, publishable: true, is_current_policy: true,
      outcome: { id: 11, state: 'RESOLVED', sessions_elapsed: 20, descriptive_return: 0.18876,
                 excess_return: 0.19906, return_basis: 'split_adjusted_price',
                 evidence_status: 'provisional',
                 evidence_label: 'Provisional — based on returned corporate actions; completeness unverified',
                 performance_eligibility: 'provisional',
                 reason: 'descriptive from the reference close' },
      superseded: [] },
    { observation_id: 4, origin: 'prospective', observed_at: '2026-10-08T00:00:00',
      horizon: '1-5d', horizon_sessions: 5, direction: 'UNKNOWN', reference_price: 1088,
      reference_price_as_of: '2026-10-07T00:00:00', invalidated_reason: null, publishable: true, is_current_policy: true,
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
                 horizon_sessions: 20,
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
    // Each distinct state is still DISTINGUISHED in the compact summary — pending (time will
    // fix it) and invalidated (nothing will) must never collapse into one another.
    expect(html).toContain('pending');
    expect(html).toContain('3 invalidated');
    expect(html).toContain('Invalid capture — excluded from publication');

    // The research product renders even though two of four outcomes have no return.
    expect(html).toContain('revenue grew 48.9% in the latest stored year');
    expect(html).toContain('2 measured bucket(s) agree');
    // The buckets are the WORKING: present as a fold, not rendered into the conclusion.
    expect(html).toContain('Evidence buckets');
    expect(html).not.toContain('range break observed');

    // Support quality must never be presented as predictive confidence.
    expect(html).toContain('establishes no skill by itself');


    // The LEDGER is folded by default; what shows is the compact pool summary.
    expect(html).toContain('Outcome ledger');
    expect(html).not.toContain('18.88%');
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


describe('calculation, evidence and eligibility are three separate answers', () => {
  it('never lets a calculable figure imply a verified one', () => {
    swr.byKey = { 'stock-outcomes': OUTCOMES, 'stock-intel': INTEL };
    const html = renderToStaticMarkup(<StockIntelligencePanel symbol="MU" />);
    // All three columns exist, and the figure that WAS calculable is still labelled provisional.
    expect(html).toContain('provisional');
    expect(html).toContain(
      'Provisional — based on returned corporate actions; completeness unverified');
  });

  it('states the remedy as stronger evidence, not as acceptance', () => {
    swr.byKey = { 'stock-outcomes': OUTCOMES, 'stock-intel': INTEL };
    const html = renderToStaticMarkup(<StockIntelligencePanel symbol="MU" />);
    expect(html).toContain('documents completeness');
    expect(html).toContain('second independent source');
    // The argument from inconvenience must appear nowhere on the page.
    expect(html).not.toContain('would remain unresolvable');
    expect(html).not.toContain('otherwise');
  });

  it('shows a compact pool summary rather than the whole ledger', () => {
    swr.byKey = { 'stock-outcomes': OUTCOMES, 'stock-intel': INTEL };
    const html = renderToStaticMarkup(<StockIntelligencePanel symbol="MU" />);
    expect(html).toContain('0 verified');
    expect(html).toContain('1 provisional');
    expect(html).toContain('3 invalidated');
    expect(html).toContain('replay');
    // Prospective is counted SEPARATELY and never added to replay.
    expect(html).toContain('prospective');
    expect(html).toContain('Outcome ledger');
  });
});


describe('triggers are oriented by the reading', () => {
  const bearish = {
    ...OUTCOMES,
    observations: [{ ...OUTCOMES.observations[1],
      direction: 'BEARISH',
      confirmation_rule: 'a completed close below 376.88',
      invalidation_rule: 'a completed close above 406.56',
      triggers: { direction: 'BEARISH', confirms: 'a completed close below 376.88',
                  invalidates: 'a completed close above 406.56', establishes: null,
                  basis: 'a bearish reading is confirmed by a break DOWN through support' } }],
  };

  it('confirms a bearish reading with a break DOWN, not up', () => {
    /* THE REPORTED DEFECT: GLD was BEARISH and the panel said a close ABOVE 406.56 would
       confirm it. The boundaries are symmetric; the claim decides which one confirms. */
    swr.byKey = { 'stock-outcomes': bearish, 'stock-intel': INTEL };
    const html = renderToStaticMarkup(<StockIntelligencePanel symbol="GLD" />);
    const confirms = html.indexOf('Confirms: ');
    const invalidates = html.indexOf('Invalidates: ');
    expect(html.slice(confirms, confirms + 60)).toContain('below 376.88');
    expect(html.slice(invalidates, invalidates + 60)).toContain('above 406.56');
  });

  it('attaches no thesis to a non-directional reading', () => {
    const neutral = { ...OUTCOMES, observations: [{ ...OUTCOMES.observations[1],
      direction: 'NEUTRAL', confirmation_rule: null, invalidation_rule: null,
      triggers: { direction: 'NEUTRAL', confirms: null, invalidates: null,
                  establishes: ['a completed close above 120', 'a completed close below 100'],
                  basis: 'no directional reading is being made' } }] };
    swr.byKey = { 'stock-outcomes': neutral, 'stock-intel': INTEL };
    const html = renderToStaticMarkup(<StockIntelligencePanel symbol="MU" />);
    expect(html).toContain('Neither boundary confirms or invalidates anything');
    expect(html).toContain('a completed close above 120');
    expect(html).not.toContain('Confirms: ');
  });
});

describe('the conclusion comes from the current capture generation', () => {
  /* THE DEFECT THAT SHIPPED. After the trigger orientation was corrected, the corrected
     observations existed in the response (GLD #19/20/21 under policy cd28654a) — but the
     renderer used `rows.find(...)`, which returns the FIRST row, and the API orders by
     observed_at then horizon, so it kept rendering a SUPERSEDED capture's reversed rules.
     The data was right; the selection was wrong, and my verification queried the newest row
     by id rather than the one the page would pick. */
  const mixed = {
    ...OUTCOMES,
    capture_policy_fingerprint: 'cd28654a',
    observations: [
      { ...OUTCOMES.observations[1], observation_id: 16, direction: 'BEARISH',
        policy_fingerprint: '6b4bfa35', is_current_policy: false,
        confirmation_rule: 'a completed close above 406.56',   // the OLD, reversed rule
        invalidation_rule: 'a completed close below 376.88',
        triggers: { direction: 'BEARISH', confirms: 'a completed close above 406.56',
                    invalidates: 'a completed close below 376.88', establishes: null,
                    basis: 'superseded' } },
      { ...OUTCOMES.observations[1], observation_id: 19, direction: 'BEARISH',
        policy_fingerprint: 'cd28654a', is_current_policy: true,
        confirmation_rule: 'a completed close below 376.88',   // the CORRECTED rule
        invalidation_rule: 'a completed close above 406.56',
        triggers: { direction: 'BEARISH', confirms: 'a completed close below 376.88',
                    invalidates: 'a completed close above 406.56', establishes: null,
                    basis: 'a bearish reading is confirmed by a break DOWN through support' } },
    ],
  };

  it('renders the current generation even when a superseded row comes first', () => {
    swr.byKey = { 'stock-outcomes': mixed, 'stock-intel': INTEL };
    const html = renderToStaticMarkup(<StockIntelligencePanel symbol="GLD" />);
    const confirms = html.indexOf('Confirms: ');
    expect(confirms).toBeGreaterThan(-1);
    expect(html.slice(confirms, confirms + 60)).toContain('below 376.88');
    expect(html.slice(confirms, confirms + 60)).not.toContain('above 406.56');
  });

  it('labels the superseded capture rather than hiding it', () => {
    swr.byKey = {
      'stock-outcomes': { ...mixed, captures: [
        { origin: 'prospective', captured_on: '2026-10-08', policy_fingerprint: '6b4bfa35',
          is_current_policy: false, horizons: ['1-5d', '1-4w', '1-3m'],
          observation_ids: [16, 17, 18] },
        { origin: 'prospective', captured_on: '2026-10-08', policy_fingerprint: 'cd28654a',
          is_current_policy: true, horizons: ['1-5d', '1-4w', '1-3m'],
          observation_ids: [19, 20, 21] },
      ] },
      'stock-intel': INTEL };
    const html = renderToStaticMarkup(<StockIntelligencePanel symbol="GLD" />);
    // "6 pending" is TWO captures of THREE horizons, and the page now says so.
    expect(html).toContain('superseded');
    expect(html).toContain('current');
    expect(html).toContain('3 horizons');
    expect(html).toContain('captured under an earlier rule set');
  });

  it('shows nothing rather than guessing when no generation is named', () => {
    swr.byKey = {
      'stock-outcomes': { ...mixed,
        observations: mixed.observations.map(o => ({ ...o, is_current_policy: undefined })) },
      'stock-intel': INTEL };
    const html = renderToStaticMarkup(<StockIntelligencePanel symbol="GLD" />);
    expect(html).toContain('did not say which capture generation is in force');
    expect(html).not.toContain('Confirms: ');
  });

  it('rounds displayed trigger prices to a tradeable precision', () => {
    swr.byKey = { 'stock-outcomes': mixed, 'stock-intel': INTEL };
    const html = renderToStaticMarkup(<StockIntelligencePanel symbol="GLD" />);
    expect(html).not.toContain('406.55999755859375');
    expect(html).toContain('406.56');
  });
});


describe('the triggers belong to the horizon the direction came from', () => {
  it('prefers the current row matching the summary horizon', () => {
    /* The summary reads 20 sessions. Taking whichever current row came last put a 1-3m
       boundary under a 1-4w direction, and two horizons can legitimately disagree. */
    const multi = {
      ...OUTCOMES,
      observations: [
        { ...OUTCOMES.observations[1], observation_id: 40, horizon: '1-4w',
          horizon_sessions: 20, is_current_policy: true, direction: 'BEARISH',
          confirmation_rule: 'a completed close below 376.88',
          invalidation_rule: 'a completed close above 406.56',
          triggers: { direction: 'BEARISH', confirms: 'a completed close below 376.88',
                      invalidates: 'a completed close above 406.56', establishes: null,
                      basis: 'bearish' } },
        { ...OUTCOMES.observations[1], observation_id: 41, horizon: '1-3m',
          horizon_sessions: 63, is_current_policy: true, direction: 'BEARISH',
          confirmation_rule: 'a completed close below 300.00',
          invalidation_rule: 'a completed close above 500.00',
          triggers: { direction: 'BEARISH', confirms: 'a completed close below 300.00',
                      invalidates: 'a completed close above 500.00', establishes: null,
                      basis: 'bearish' } },
      ],
    };
    swr.byKey = { 'stock-outcomes': multi, 'stock-intel': INTEL };
    const html = renderToStaticMarkup(<StockIntelligencePanel symbol="GLD" />);
    expect(html).toContain('376.88');
    expect(html).not.toContain('300.00');
    expect(html).not.toContain('come from the');  // no mismatch notice when aligned
  });
});

describe('two panels reading different sessions say so', () => {
  it('flags a session mismatch prominently rather than leaving it to be inferred', () => {
    /* GLD's setup card read the market through Oct 7 and said "Down — range break observed";
       the research panel read it through Oct 8 and said "Downward setup — awaiting break".
       Both are right for their own date, and side by side they read as a contradiction. */
    swr.byKey = { 'stock-outcomes': OUTCOMES,
                  'stock-intel': { ...INTEL, latest_session: '2026-10-08' } };
    const html = renderToStaticMarkup(
      <StockIntelligencePanel symbol="GLD" setupSession="2026-10-07" />);
    expect(html).toContain('DIFFERENT SESSIONS');
    expect(html).toContain('2026-10-07');
    expect(html).toContain('2026-10-08');
    expect(html).toContain('evidence through 2026-10-08');
  });

  it('says nothing when both panels read the same session', () => {
    swr.byKey = { 'stock-outcomes': OUTCOMES,
                  'stock-intel': { ...INTEL, latest_session: '2026-10-07' } };
    const html = renderToStaticMarkup(
      <StockIntelligencePanel symbol="GLD" setupSession="2026-10-07" />);
    expect(html).not.toContain('DIFFERENT SESSIONS');
    expect(html).toContain('evidence through 2026-10-07');
  });
});

describe('pending counts do not mix capture generations', () => {
  const twoGenerations = {
    ...OUTCOMES,
    coverage: { prospective: { origin: 'prospective', invalidated_captures: 0, note: '',
                               pools: {},
                               historical_pools: {} } },
    observations: [
      ...[40, 41, 42].map(id => ({ ...OUTCOMES.observations[3], observation_id: id,
        origin: 'prospective', is_current_policy: true, invalidated_reason: null,
        outcome: { id, state: 'UNRESOLVED_INSUFFICIENT_SESSIONS' } })),
      ...[50, 51, 52, 53, 54, 55].map(id => ({ ...OUTCOMES.observations[3],
        observation_id: id, origin: 'prospective', is_current_policy: false,
        invalidated_reason: null,
        outcome: { id, state: 'UNRESOLVED_INSUFFICIENT_SESSIONS' } })),
    ],
  };

  it('reports current pending apart from historical', () => {
    /* "9 pending" was three current captures plus six under superseded rules. The historical
       ones are preserved and will still resolve — they just describe a different population. */
    swr.byKey = { 'stock-outcomes': twoGenerations, 'stock-intel': INTEL };
    const html = renderToStaticMarkup(<StockIntelligencePanel symbol="GLD" />);
    expect(html).toContain('3 current pending');
    expect(html).toContain('6 historical');
    expect(html).not.toContain('9 pending');
  });
});

describe('selection uses the complete observation identity', () => {
  it('picks the latest cutoff when several captures share one policy', () => {
    /* Policy-and-horizon alone names a SET once two daily captures share a policy, which is the
       ordinary case. Cutoff is what disambiguates. */
    const twoDays = {
      ...OUTCOMES,
      observations: [
        { ...OUTCOMES.observations[1], observation_id: 60, observed_at: '2026-10-08T00:00:00',
          horizon: '1-4w', horizon_sessions: 20, is_current_policy: true, direction: 'BEARISH',
          confirmation_rule: 'a completed close below 100.00',
          invalidation_rule: 'a completed close above 200.00',
          triggers: { direction: 'BEARISH', confirms: 'a completed close below 100.00',
                      invalidates: 'a completed close above 200.00', establishes: null,
                      basis: 'bearish' } },
        { ...OUTCOMES.observations[1], observation_id: 61, observed_at: '2026-10-09T00:00:00',
          horizon: '1-4w', horizon_sessions: 20, is_current_policy: true, direction: 'BEARISH',
          confirmation_rule: 'a completed close below 111.00',
          invalidation_rule: 'a completed close above 222.00',
          triggers: { direction: 'BEARISH', confirms: 'a completed close below 111.00',
                      invalidates: 'a completed close above 222.00', establishes: null,
                      basis: 'bearish' } },
      ],
    };
    swr.byKey = { 'stock-outcomes': twoDays, 'stock-intel': INTEL };
    const html = renderToStaticMarkup(<StockIntelligencePanel symbol="GLD" />);
    expect(html).toContain('111.00');
    expect(html).not.toContain('100.00');
  });
});
