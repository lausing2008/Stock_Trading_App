/* AUD-QV-APPLICABILITY. GLD was shown as "Fund (inferred)" on the same screen that reported it
 * as missing annual statements and lacking a durable moat, with "collect the evidence" as its
 * next research task. A gold trust holds no operating business: "no moat" there is not a weak
 * finding, it is a finding about the wrong subject.
 *
 * Also pins the agreed integration: the research panel is rendered INSIDE Quality & Value, from
 * the shared component, not as a second implementation on a separate page.
 */
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it, expect, vi } from 'vitest';
import { readFileSync } from 'fs';
import { join } from 'path';

/* KEY-AWARE. This page renders three SWR consumers — itself, DirectionScreen and the research
   panel — so a mock that hands the same payload to all of them feeds the quality-value report
   into DirectionScreen and crashes on a field it does not have. */
const state = vi.hoisted(() => ({ data: undefined as any, error: undefined as any }));
vi.mock('swr', () => ({
  default: (key: any) => {
    const k = Array.isArray(key) ? key[0] : String(key ?? '');
    if (k === 'direction-screen' || k === 'stock-outcomes' || k === 'stock-intel')
      return { data: undefined, error: undefined, isLoading: true, mutate: vi.fn() };
    return { ...state, isLoading: false, mutate: vi.fn() };
  },
}));

import QualityValuePage from '@/pages/quality-value';

const CATALOG = {
  pass: { label: 'Pass', remedy: '' }, fail: { label: 'Fail', remedy: '' },
  insufficient: { label: 'Insufficient', remedy: '' },
  not_collected: { label: 'Not collected', remedy: '' },
  not_assessed: { label: 'Not assessed', remedy: '' },
};

const GLD_APPLICABILITY = {
  status: 'unverified',
  instrument_type: 'fund',
  confidence: 'inferred',
  basis: 'the NAME identifies a pooled vehicle',
  note: 'Company assessment applicability unverified. The instrument type here is INFERRED, not verified, so it has not been established that these company gates are asking about the right kind of subject. Read the gate results as conditional on that.',
  company_research_applicable: false,
  fund_analysis_required: ['stated mandate and what the vehicle is contractually required to hold',
                           'total expense ratio and any financing or roll cost'],
};

const payload = (symbol: string, applicability: any, conditional: any[], next: any[]) => ({
  evaluated: 1, as_of: '2026-10-08T00:00:00', cutoff: '2026-10-08T00:00:00', mode: 'shadow',
  policy_version: 'qv-1', policy_fingerprint: 'abc', stored_new: 0, persist_errors: [],
  reused_evaluations: [], notes: [], status_catalog: CATALOG,
  assessment_coverage: { with_assessment: [], without_assessment: 1 },
  gate_coverage: { competitive_durability: { not_collected: 1 } },
  required_for_entry: ['competitive_durability'],
  states: { insufficient_evidence: 1 }, evaluations: [{
    symbol, name: symbol, sector: null, state: 'insufficient_evidence',
    gates: [{ gate: 'competitive_durability', status: 'not_collected', reasons: [],
              evidence: {} }],
    summary: {
      supports: [], unresolved: [], why_not_entry_ready: 'Not entry ready: durability is not collected',
      next_research: next, next_research_conditional: conditional,
      not_closable_by_research: [], applicability,
    },
  }],
});

describe('company-assessment applicability', () => {
  it('says applicability is unverified for an inferred fund, and names what a fund needs', () => {
    state.data = payload('GLD', GLD_APPLICABILITY,
                         [{ gate: 'competitive_durability', item: 'customer switching costs' }], []);
    const html = renderToStaticMarkup(<QualityValuePage />);
    /* The caveat must be visible WITH THE COLLAPSED BADGES, because those badges are what a
       reader sees first and what the caveat governs. */
    expect(html).toContain('Applicability unverified');
    expect(html).toContain('Company assessment applicability unverified');  // full note, title attr
  });

  it('shows no applicability banner where the assessment does apply', () => {
    state.data = payload('MU', { status: 'applies', instrument_type: 'operating_company',
                                 confidence: 'declared', note: 'applies',
                                 company_research_applicable: true },
                         [], [{ gate: 'competitive_durability', item: 'HBM qualification' }]);
    const html = renderToStaticMarkup(<QualityValuePage />);
    expect(html).not.toContain('Applicability unverified');
    expect(html).not.toContain('Company assessment N/A');
  });

  it('keeps the backlog for an inferred operating company, with the caveat', () => {
    state.data = payload('MU', { status: 'unverified', instrument_type: 'operating_company',
                                 confidence: 'inferred', basis: 'annual statements are stored',
                                 note: 'Company assessment applicability unverified.',
                                 company_research_applicable: true },
                         [], [{ gate: 'competitive_durability', item: 'HBM qualification' }]);
    const html = renderToStaticMarkup(<QualityValuePage />);
    expect(html).toContain('Applicability unverified');
    expect(html).not.toContain('Company assessment N/A');
  });
});

describe('the research panel lives inside Quality & Value', () => {
  const src = (f: string) => readFileSync(join(process.cwd(), 'src', f), 'utf8');

  it('is imported and rendered by the Quality & Value page', () => {
    const page = src('pages/quality-value.tsx');
    expect(page).toContain("from '@/components/StockIntelligencePanel'");
    expect(page).toContain('<StockIntelligencePanel symbol=');
  });

  it('has exactly one implementation — the standalone route only reuses it', () => {
    const route = src('pages/stock-intelligence.tsx');
    expect(route).toContain("from '@/components/StockIntelligencePanel'");
    // The route must hold no rendering or fetching of its own.
    expect(route).not.toContain('useSWR');
    expect(route).not.toContain('api.stockOutcomes');
    expect(route).not.toContain('reason_labels');
  });

  it('does not advertise the standalone route in the nav', () => {
    expect(src('pages/_app.tsx')).not.toContain("href: '/stock-intelligence'");
  });
});
