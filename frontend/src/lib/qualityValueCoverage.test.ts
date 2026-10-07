import { describe, it, expect } from 'vitest';
import { coverageRows, statusColumns, badgeLabel, badgeRemedy,
         type CoverageInput, type StatusCatalog } from './qualityValueCoverage';

const CATALOG: StatusCatalog = {
  pass:            { label: 'Pass', remedy: '', is_work_remaining: false, is_pass: true },
  fail:            { label: 'Fail', remedy: 'Nothing — the condition is not met', is_work_remaining: false, is_pass: false },
  not_implemented: { label: 'Not implemented', remedy: 'Requires sourced evidence and an implemented assessment', is_work_remaining: true, is_pass: false },
  stale:           { label: 'Stale', remedy: 'Refresh the stored data', is_work_remaining: true, is_pass: false },
  insufficient:    { label: 'Insufficient', remedy: 'More periods or a different measure are needed', is_work_remaining: true, is_pass: false },
  not_assessed:    { label: 'Not assessed', remedy: 'Evaluate this gate — it is optional and was not run', is_work_remaining: false, is_pass: false },
};

/* Production's real shape on 2026-10-07: five required gates with mixed statuses, and two
   OPTIONAL gates that were never evaluated. The optional rows are what produced "0 ≠ 200". */
const MIXED: CoverageInput = {
  evaluated: 200,
  status_catalog: CATALOG,
  required_for_entry: ['business_quality', 'competitive_durability', 'valuation',
                       'entry_condition', 'value_trap_risk'],
  gate_claims: { business_quality: { label: 'Fundamental checks' },
                 entry_condition: { label: 'Price-stabilization rule' } },
  gate_coverage: {
    business_quality:       { pass: 151, stale: 2, insufficient: 47 },
    competitive_durability: { not_implemented: 200 },
    valuation:              { not_implemented: 200 },
    entry_condition:        { pass: 86, fail: 105, insufficient: 9 },
    value_trap_risk:        { not_implemented: 200 },
    catalysts:              { not_assessed: 200 },
    portfolio_fit:          { not_assessed: 200 },
  },
};

describe('coverage rows with mixed statuses and optional unassessed gates', () => {
  it('reconciles every row to the evaluated population', () => {
    const bad = coverageRows(MIXED).filter(r => !r.reconciles)
      .map(r => `${r.gate}: ${r.total} of ${MIXED.evaluated}`);
    expect(bad).toEqual([]);
  });

  it('reconciles the OPTIONAL rows too — those companies did not disappear', () => {
    const rows = coverageRows(MIXED);
    for (const gate of ['catalysts', 'portfolio_fit']) {
      const row = rows.find(r => r.gate === gate)!;
      expect(row.total).toBe(200);
      expect(row.reconciles).toBe(true);
      expect(row.required).toBe(false);
      expect(row.counts.not_assessed).toBe(200);
    }
  });

  it('REGRESSION: an optional gate left empty fails reconciliation loudly', () => {
    // The exact defect: no not_assessed count, so the row summed to zero against 200.
    const broken = { ...MIXED, gate_coverage: { ...MIXED.gate_coverage, catalysts: {} } };
    const row = coverageRows(broken).find(r => r.gate === 'catalysts')!;
    expect(row.total).toBe(0);
    expect(row.reconciles).toBe(false);
  });

  it('counts AND renders a status the catalog does not know', () => {
    /* A sabotage that summed only the rendered columns passed this as first written, because
       every non-zero status becomes a column — so the two formulations are equivalent today
       and that test proved nothing. What IS worth pinning is the invariant that makes them
       equivalent: a status present in the data must appear in the columns, so it cannot be
       counted into the total while being invisible in the row. The total summing over all keys
       stays as defence if that invariant ever changes. */
    const withGhost = { ...MIXED,
      gate_coverage: { ...MIXED.gate_coverage,
        business_quality: { pass: 151, stale: 2, insufficient: 46, some_future_state: 1 } } };
    const cols = statusColumns(withGhost);
    const row = coverageRows(withGhost).find(r => r.gate === 'business_quality')!;
    expect(cols).toContain('some_future_state');
    expect(row.counts.some_future_state).toBe(1);
    expect(row.total).toBe(200);
    expect(Object.keys(row.counts).every(k => cols.includes(k))).toBe(true);
  });

  it('keeps a state the catalog does not know as a visible column', () => {
    const future = { ...MIXED, gate_coverage: { ...MIXED.gate_coverage,
      valuation: { not_implemented: 199, some_future_state: 1 } } };
    expect(statusColumns(future)).toContain('some_future_state');
  });

  it('orders columns by the catalog, not by discovery order', () => {
    const cols = statusColumns(MIXED);
    expect(cols.indexOf('pass')).toBeLessThan(cols.indexOf('not_implemented'));
    expect(cols.indexOf('fail')).toBeLessThan(cols.indexOf('stale'));
  });

  it('omits a status no gate reports', () => {
    expect(statusColumns(MIXED)).not.toContain('blocked');
  });

  it('marks required and optional gates correctly', () => {
    const rows = coverageRows(MIXED);
    expect(rows.filter(r => r.required).map(r => r.gate).sort())
      .toEqual([...MIXED.required_for_entry].sort());
  });

  it('uses the served gate label where one exists', () => {
    const rows = coverageRows(MIXED);
    expect(rows.find(r => r.gate === 'business_quality')!.label).toBe('Fundamental checks');
    expect(rows.find(r => r.gate === 'catalysts')!.label).toBe('catalysts');
  });
});

describe('badges', () => {
  it('labels each state distinctly from the catalog', () => {
    expect(badgeLabel('stale', CATALOG)).toBe('Stale');
    expect(badgeLabel('not_implemented', CATALOG)).toBe('Not implemented');
    expect(badgeLabel('not_assessed', CATALOG)).toBe('Not assessed');
  });

  it('shouts about a state it does not know instead of defaulting', () => {
    expect(badgeLabel('some_future_state', CATALOG)).toBe('UNKNOWN STATE: some_future_state');
    expect(badgeLabel('stale', undefined)).toContain('UNKNOWN STATE');
  });

  it('never renders the phrase that was hiding five problems', () => {
    for (const s of Object.keys(CATALOG)) {
      expect(badgeLabel(s, CATALOG).toLowerCase()).not.toContain('no evidence');
    }
  });

  it('gives the remedy for a non-passing state and none for a pass', () => {
    expect(badgeRemedy('stale', CATALOG)).toBe('Refresh the stored data');
    expect(badgeRemedy('not_implemented', CATALOG)).toContain('sourced evidence');
    expect(badgeRemedy('pass', CATALOG)).toBeNull();
  });
});
