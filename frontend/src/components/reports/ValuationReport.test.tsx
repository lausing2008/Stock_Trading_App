// @vitest-environment jsdom
import React from 'react';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ValuationReport from './ValuationReport';

const state = vi.hoisted(() => ({ latest: undefined as any, error: undefined as any,
  generate: vi.fn(), history: { reports: [] }, mutate: vi.fn(), performance: undefined as any }));
vi.mock('swr', () => ({ default: (key: any) => ({
  data: key === 'valuation-latest' ? state.latest : key === 'valuation-history' ? state.history
    : Array.isArray(key) && key[0] === 'valuation-performance' ? state.performance : undefined,
  error: key === 'valuation-latest' ? state.error : undefined, mutate: state.mutate,
}) }));
vi.mock('@/lib/api', () => ({ api: { valuationGenerate: (...args: unknown[]) => state.generate(...args) } }));
vi.mock('@/components/WatchlistPickerButton', () => ({ default: () => <button>Add to watchlist</button> }));
vi.mock('next/link', () => ({ default: ({ children, ...props }: any) => <a {...props}>{children}</a> }));

function fixture() {
  const m = (value: number | null) => ({ value, state: value == null ? 'UNAVAILABLE' : 'OK', reason: value == null ? 'Not supplied' : null });
  return { report: { id: 1, version: 1, generated_at: new Date().toISOString(), status: 'partial',
    valuation: { sampled: 1, discovered: 50, sample_limit: 60, price_session: '2026-10-27',
      universe: 'Yahoo US covered sample', queries_failed: [], policy_version: 'v1', notes: ['Not all US listings'],
      changes: [], comparison_note: 'Previous snapshot', counts: { undervalued: 1, insufficient_evidence: 0 },
      rows: [{ symbol: 'NEW', name: 'New Company', sector: 'Technology', classification: 'undervalued',
        tracked: false, qualified: false, risk_flags: [], blockers: ['Missing durability assessment'],
        metrics: { price: m(80), fair_value: m(100), discount_pct: m(20), upside_pct: m(25),
          forward_pe: m(10), peg: m(null), rsi: m(55), trailing_pe: m(12) },
        thesis: ['Cash generation requires review'], research_checks: ['Check latest filings'],
        valuation: { state: 'OK', uncertainty: 'High', limitations: [] },
        quality: { gates: [], blocking: [] }, peer_pe: { count: 0, median: null, note: 'Insufficient peers' },
        analyst: { mean: 110, low: 90, high: 130, analyst_count: 5, note: 'Publication dates and target horizon not supplied' },
        analyst_actions: [], uw: null, errors: [], fetched_at: new Date().toISOString(),
        source_url: 'https://finance.yahoo.com/quote/NEW/', price_session: '2026-10-27',
        catalyst_note: 'No future catalysts verified' }],
    } } };
}
beforeEach(() => { state.latest = fixture(); state.error = undefined; state.performance = undefined; sessionStorage.clear(); vi.clearAllMocks(); });
afterEach(cleanup);

describe('Valuation report user flow', () => {
  it('separates pending performance from missing returns and keeps catalog coverage honest', () => {
    state.latest.report.valuation.catalog = { state: 'OK', eligible_count: 5600, selected_count: 40,
      source_dates: { nasdaq: '2026-10-29' }, rotation_persisted: true };
    state.performance = { evaluation: { report_id: 1, published_at: '2026-10-30T05:00:00Z',
      evaluated_at: '2026-10-30T05:10:00Z', benchmark: 'SPY', notes: ['Membership remains frozen'],
      horizons: [{ sessions: 5, entry_session: '2026-10-30', end_session: '2026-11-06',
        benchmark_return_pct: null, groups: {
          qualified: { state: 'NO_COHORT', expected: 0, observed: 0, return_pct: null, excess_percentage_points: null },
          undervalued_research: { state: 'PENDING', expected: 1, observed: 0, return_pct: null, excess_percentage_points: null } } }] } };
    render(<ValuationReport market="US" />);
    expect(screen.getByText('Awaiting sessions')).toBeTruthy();
    expect(screen.getByText('None in snapshot')).toBeTruthy();
    expect(screen.getByText(/does not mean that every company has current ratings/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Export outcomes CSV' })).toBeTruthy();
    expect(screen.getByText('0/1')).toBeTruthy();
  });
  it('filters reviewed leads and exposes source-linked research without implying qualification', () => {
    state.latest.report.valuation.rows[0].candidate_review = {
      version: 1, reviewed_at: '2026-10-29', author: 'Issuer review', status: 'current', decision: 'Research only',
      summary: 'Owner costs reduce the apparent discount.', thesis: ['Test recurring cash generation'],
      findings: [{ claim: 'Filed cash flow', counterevidence: 'Compensation is an owner cost', source_ids: ['annual'] }],
      sources: [{ id: 'annual', title: 'Issuer 10-K', url: 'https://www.sec.gov/Archives/filing.htm',
        published_at: '2026-01-15', period_end: '2025-11-28' }],
      invalidation: ['Retention weakens'], monitor: ['Next organic growth report'], unresolved: ['AI durability unresolved'],
      qualification_blockers: ['AI durability unresolved'],
      filing_verification: { state: 'OK', checked_at: '2026-10-30T12:00:00Z', since: '2026-01-15',
        scope: 'Filing inventory only', filings: [{ accession: '123', form: '10-Q', filed: '2026-09-22', url: 'https://www.sec.gov/Archives/quarter.htm' }] },
      financial_verification: { state: 'OK', checked_at: '2026-10-30T12:00:00Z', latest_period_end: '2025-11-28' },
      reconciliation_flags: ['Provider fiscal period differs'],
      sensitivity: { method: 'Owner-cash equity sensitivity', period_end: '2025-11-28', cashflow: 7910e6,
        diluted_shares: 427e6, limitations: ['Uncalibrated assumptions'],
        scenarios: { bear: { fair_value: 180 }, base: { fair_value: 260 }, bull: { fair_value: 340 } } },
    };
    render(<ValuationReport market="US" />);
    fireEvent.click(screen.getByRole('button', { name: 'Reviewed candidates' }));
    fireEvent.click(screen.getByRole('button', { name: '+ NEW' }));
    expect(screen.getByRole('region', { name: 'Issuer qualification review' })).toBeTruthy();
    expect(screen.getByText('Owner costs reduce the apparent discount.')).toBeTruthy();
    expect(screen.getByText('Retention weakens')).toBeTruthy();
    expect(screen.getByText(/\$180 \/ \$260 \/ \$340/)).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Issuer 10-K' }).getAttribute('href')).toContain('sec.gov');
    expect(screen.getByText('Provider fiscal period differs')).toBeTruthy();
    expect(screen.getByRole('link', { name: '10-Q · 2026-09-22' })).toBeTruthy();
    expect(screen.getByText(/Finding a filing does not establish/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Qualified opportunities' }));
    expect(screen.getByText(/No companies meet all opportunity requirements/)).toBeTruthy();
  });
  it('discovers a non-watchlist stock, expands evidence and explains an empty qualified screen', () => {
    render(<ValuationReport market="US" />);
    expect(screen.getByText('New Company')).toBeTruthy();
    expect(screen.getByText(/Discovered beyond tracked stocks/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '+ NEW' }));
    expect(screen.getByText('Missing durability assessment')).toBeTruthy();
    expect(screen.getByText(/Publication dates and target horizon/)).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Yahoo source ↗' }).getAttribute('href')).toContain('/NEW/');
    fireEvent.click(screen.getByRole('button', { name: 'Qualified opportunities' }));
    expect(screen.getByText(/No companies meet all opportunity requirements/)).toBeTruthy();
  });
  it('filters companies without mutating the stored report', () => {
    render(<ValuationReport market="US" />);
    fireEvent.change(screen.getByLabelText('Search companies'), { target: { value: 'absent' } });
    expect(screen.getByText(/No companies match these filters/)).toBeTruthy();
    expect(state.latest.report.valuation.rows).toHaveLength(1);
  });
  it('shows failures as failures and provides an explicit first-generation action', () => {
    state.latest = undefined; state.error = new Error('offline');
    const { unmount } = render(<ValuationReport market="US" />);
    expect(screen.getByRole('alert').textContent).toContain('offline');
    expect(screen.queryByText(/No companies match/)).toBeNull();
    unmount(); state.error = undefined; state.latest = { report: null };
    render(<ValuationReport market="US" />);
    expect(screen.getByRole('button', { name: 'Generate first report' })).toBeTruthy();
    expect(screen.getByText('Discover beyond your watchlist')).toBeTruthy();
  });
  it('makes US-only coverage visible under the HK selector', () => {
    render(<ValuationReport market="HK" />);
    expect(screen.getByText(/Select US to view the report/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Refresh report/ })).toBeNull();
  });

  it('labels a dated provider RSI separately from its unknown adjustment basis', () => {
    state.latest.report.valuation.rows[0].metrics.rsi = { value: 49.75, state: 'OK', reason: null,
      source: 'Unusual Whales daily screener', as_of: '2026-10-27', basis: 'Adjustment basis unverified' };
    state.latest.report.valuation.rows[0].metrics.eps_yoy_growth_pct = { value: 10.7, state: 'OK', reason: null,
      basis: 'Historical quarterly growth; not forward PEG' };
    render(<ValuationReport market="US" />);
    fireEvent.click(screen.getByRole('button', { name: '+ NEW' }));
    expect(screen.getByText(/Unusual Whales daily screener · 2026-10-27/)).toBeTruthy();
    expect(screen.getByText('Historical quarterly growth; not forward PEG')).toBeTruthy();
    expect(screen.getAllByTitle(/Adjustment basis unverified/).length).toBeGreaterThan(0);
  });
});
