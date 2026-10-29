import { describe, it, expect } from 'vitest';
import { selectValuationRows, valuationCsv, type ValuationRow, type ValuationReport } from './valuationReport';

const row = (symbol: string, value: number | null, classification = 'undervalued') => ({
  symbol, name: symbol, sector: 'Technology', classification, qualified: false, risk_flags: [],
  metrics: { discount_pct: { value, state: value == null ? 'UNAVAILABLE' : 'OK' } },
}) as unknown as ValuationRow;
const options = { filter: 'all' as const, search: '', sector: '', sort: 'discount_pct', ascending: false };

describe('valuation research ordering and export', () => {
  it('sorts unavailable values last in both directions and keeps stable symbol ties', () => {
    const rows = [row('C', null), row('B', 20), row('A', 20), row('D', 30)];
    expect(selectValuationRows(rows, options).map(r => r.symbol)).toEqual(['D', 'A', 'B', 'C']);
    expect(selectValuationRows(rows, { ...options, ascending: true }).map(r => r.symbol)).toEqual(['A', 'B', 'D', 'C']);
    expect(rows[0].symbol).toBe('C');
  });
  it('filters analyst flags independently of valuation classification', () => {
    const flagged = { ...row('RISK', 25), risk_flags: [{ kind: 'analyst', text: 'downgrade' }] };
    expect(selectValuationRows([flagged, row('EXPENSIVE', -30, 'overvalued')], { ...options, filter: 'analyst_risk' }).map(r => r.symbol)).toEqual(['RISK']);
    expect(selectValuationRows([flagged], { ...options, filter: 'qualified' })).toEqual([]);
  });
  it('exports frozen values with source dates and protects spreadsheet text cells', () => {
    const company = { ...row('TEST', 20), name: '=HYPERLINK("bad")',
      source_url: 'https://finance.yahoo.com/quote/TEST/', price_session: '2026-10-27',
      fetched_at: '2026-10-28T12:00:00Z', analyst: { mean: 100, median: 100, low: 80, high: 120,
        analyst_count: 10, consensus: 'buy', state: 'UNKNOWN', note: 'Undated aggregate', upside_pct: null },
      metrics: { ...row('TEST', 20).metrics, rsi: { value: 49.75, state: 'OK', reason: null,
        source: 'Unusual Whales daily screener', as_of: '2026-10-27', basis: 'Adjustment basis unverified' } },
      blockers: ['Missing evidence'] } as ValuationRow;
    const report = { id: 4, version: 2, generated_at: '2026-10-28T12:00:00Z',
      valuation: { rows: [company], policy_version: 'v1' } } as ValuationReport;
    const csv = valuationCsv(report);
    expect(csv).toContain('discount_pct_state');
    expect(csv).toContain('2026-10-27');
    expect(csv).toContain('https://finance.yahoo.com/quote/TEST/');
    expect(csv).toContain('"\'=HYPERLINK(""bad"")"');
    expect(csv).toContain('"20","OK"');
    expect(csv).toContain('Undated aggregate');
    expect(csv).toContain('rsi_source');
    expect(csv).toContain('Unusual Whales daily screener');
    expect(csv).toContain('Adjustment basis unverified');
  });
});
