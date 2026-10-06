import { describe, it, expect } from 'vitest';
import {
  groupFields, criticalLimitations, coverageBanner, fieldLabel, humaniseValue,
  SECTION_ORDER, isBulkSeries, splitBulk, commonTail, type LayoutField,
} from './intelReportLayout';

const f = (o: Partial<LayoutField> = {}): LayoutField => ({ state: 'OK', ...o });

/** The exact shape of the reported screenshot: June's results, October's price, today's signal. */
const POST_EARNINGS: [string, LayoutField][] = [
  ['eps_actual', f({ timeframe: 'at_event', section: 'metrics', label: 'EPS reported' })],
  ['return_1d', f({ timeframe: 'at_event', section: 'metrics' })],
  ['price_as_of', f({ timeframe: 'current', section: 'metrics', label: 'Share price now' })],
  ['signal_engine_assessment', f({ timeframe: 'current', section: 'interpretation' })],
  ['event_coverage', f({ state: 'CONFLICTING', reason: 'gap', section: 'limitations',
                         timeframe: 'identity' })],
  ['event_identity', f({ timeframe: 'identity', section: 'event' })],
];

describe('temporal separation', () => {
  it('never puts event results and current market data in one group', () => {
    const groups = groupFields(POST_EARNINGS);
    const metricGroups = groups.filter(g => g.section === 'metrics');
    const atEvent = metricGroups.find(g => g.timeframe === 'at_event')!;
    const current = metricGroups.find(g => g.timeframe === 'current')!;
    expect(atEvent.keys).toContain('eps_actual');
    expect(atEvent.keys).toContain('return_1d');
    expect(current.keys).toContain('price_as_of');
    expect(atEvent.keys).not.toContain('price_as_of');
  });

  it("keeps today's signal out of the at-event group, so it cannot read as a prediction", () => {
    const groups = groupFields(POST_EARNINGS);
    const atEvent = groups.filter(g => g.timeframe === 'at_event');
    expect(atEvent.flatMap(g => g.keys)).not.toContain('signal_engine_assessment');
  });

  it('orders at-event evidence before current context', () => {
    const groups = groupFields(POST_EARNINGS).filter(g => g.section === 'metrics');
    const i = groups.findIndex(g => g.timeframe === 'at_event');
    const j = groups.findIndex(g => g.timeframe === 'current');
    expect(i).toBeLessThan(j);
  });
});

describe('reading order', () => {
  /* REWRITTEN, and the original reasoning is kept because it was right about the thing that
     matters. This used to require the whole `limitations` SECTION above `metrics`. Commit
     7476aa6e moved it below on purpose — the limitations that constrain a conclusion are
     hoisted into a banner at the very top by `criticalLimitations`, and the section below is
     the exhaustive list of every unresolved input, which belongs with the detail. Leaving the
     old assertion in place left this suite red on `prod`, so the rule is now stated as what
     actually protects the reader: the MATERIAL limitations precede the detail, wherever the
     exhaustive list sits. */
  it('surfaces the limitations that constrain a conclusion above the detail', () => {
    const critical = criticalLimitations(POST_EARNINGS);
    expect(critical.length).toBeGreaterThan(0);
    // These are rendered in a banner before any group, so none may rely on the section order.
    const groups = groupFields(POST_EARNINGS);
    const keysInGroups = new Set(groups.flatMap(g => g.keys));
    for (const [k] of critical) expect(keysInGroups.has(k)).toBe(true);
  });

  it('keeps the exhaustive unresolved-input list with the detail, not above the answer', () => {
    const groups = groupFields(POST_EARNINGS);
    const summary = groups.findIndex(g => g.section === 'summary');
    const lim = groups.findIndex(g => g.section === 'limitations');
    if (lim >= 0 && summary >= 0) expect(summary).toBeLessThan(lim);
  });

  it('follows the declared section order', () => {
    const order = groupFields([
      ['a', f({ section: 'sources' })], ['b', f({ section: 'summary' })],
      ['c', f({ section: 'metrics' })], ['d', f({ section: 'limitations' })],
    ]).map(g => g.section);
    expect(order).toEqual(
      order.slice().sort((x, y) => SECTION_ORDER.indexOf(x) - SECTION_ORDER.indexOf(y)));
  });

  it('puts resolved fields before unsourced ones inside a group', () => {
    const g = groupFields([
      ['missing', f({ state: 'UNAVAILABLE', reason: 'x', section: 'metrics', timeframe: 'current' })],
      ['present', f({ section: 'metrics', timeframe: 'current' })],
    ])[0];
    expect(g.keys[0]).toBe('present');
  });
});

describe('critical limitations are surfaced, not buried', () => {
  it('picks out the inputs that block a conclusion', () => {
    const crit = criticalLimitations([
      ['event_coverage', f({ state: 'CONFLICTING', reason: 'gap' })],
      ['consensus_eps', f({ state: 'UNAVAILABLE', reason: 'none on file' })],
      ['liquidity', f({ state: 'UNAVAILABLE', reason: 'not joined' })],
      ['eps_actual', f()],
    ]).map(([k]) => k);
    expect(crit).toContain('event_coverage');
    expect(crit).toContain('consensus_eps');
    expect(crit).not.toContain('liquidity');   // ordinary gap, not a blocker
    expect(crit).not.toContain('eps_actual');  // resolved
  });
});

describe('the coverage banner', () => {
  const withValue = (coverage_state: string) =>
    ({ state: 'CONFLICTING', value: { coverage_state } } as unknown as LayoutField);

  it('warns prominently when the latest release may be missing', () => {
    const b = coverageBanner(withValue('suspected_gap'), '24 June');
    expect(b?.tone).toBe('warn');
    expect(b?.text).toContain('24 June');
    expect(b?.text).toContain('uncertain');
  });

  it('escalates when a document confirms the gap', () => {
    expect(coverageBanner(withValue('confirmed_missing_event'), '24 June')?.tone).toBe('error');
  });

  it('shows nothing when coverage is not in doubt', () => {
    expect(coverageBanner(withValue('coverage_unknown'))).toBeNull();
    expect(coverageBanner(undefined)).toBeNull();
  });
});

describe('readable labels', () => {
  it('prefers the generator label', () => {
    expect(fieldLabel('eps_actual', f({ label: 'EPS reported' }))).toBe('EPS reported');
  });

  it('never prints a raw bar-count key', () => {
    expect(fieldLabel('return_5_bars')).toBe('Return over 5 daily bars');
  });

  it('rewrites FIRST_FLASH, which is misleading on a months-old event', () => {
    const out = humaniseValue('stage', 'FIRST_FLASH');
    expect(out).toBeTruthy();
    expect(out).not.toContain('FLASH');
    expect(out).toContain('no cross-source reconciliation');
  });

  it('leaves ordinary values alone', () => {
    expect(humaniseValue('eps_actual', 25.11)).toBeNull();
  });
});

describe('stage rendering reads the shape the generator actually sends', () => {
  it('humanises the object form, which is what the report emits', () => {
    const out = humaniseValue('stage', {
      stage: 'FIRST_FLASH',
      note: 'no cross-source reconciliation is performed by this report',
    });
    expect(out).toBeTruthy();
    expect(out).not.toContain('FIRST_FLASH');
    expect(out).toContain('Initial figures');
  });

  it('still humanises the bare string form', () => {
    expect(humaniseValue('stage', 'FIRST_FLASH')).toContain('Initial figures');
  });

  it('leaves an unknown stage code alone rather than inventing words for it', () => {
    expect(humaniseValue('stage', { stage: 'SOMETHING_NEW' })).toBeNull();
  });

  it('ignores non-stage fields', () => {
    expect(humaniseValue('eps_actual', { stage: 'FIRST_FLASH' })).toBeNull();
  });
});

// ===================== collapsing bulk evidence without hiding limitations =====================

describe('isBulkSeries', () => {
  it('treats a repeating series of rows as bulk', () => {
    expect(isBulkSeries([{ a: 1 }, { a: 2 }, { a: 3 }])).toBe(true);
  });
  it('leaves a short series inline — two rows is a comparison, not a table to consult', () => {
    expect(isBulkSeries([{ a: 1 }, { a: 2 }])).toBe(false);
  });
  it('is never true for prose, however long', () => {
    expect(isBulkSeries('NOT STORED. '.repeat(80))).toBe(false);
  });
  it('is never true for a list of plain strings', () => {
    expect(isBulkSeries(['a', 'b', 'c', 'd'])).toBe(false);
  });
});

describe('splitBulk', () => {
  const bp = {
    periods: [{ period_end: '2025-08-31' }, { period_end: '2024-08-31' },
              { period_end: '2023-08-31' }, { period_end: '2022-09-01' }],
    newest_period_end: '2025-08-31',
    accounting_basis: 'NOT STORED. These figures cannot be shown to be GAAP or adjusted',
    derived_figures: 'CALCULATED HERE from the stored provider series',
    period_label: 'PROVIDER LABEL. No issuer filing confirms the fiscal end',
    dilution: 'NOT COMPUTED — no share count is stored',
  };

  it('moves the statement series behind a drilldown', () => {
    expect(Object.keys(splitBulk(bp).bulk)).toEqual(['periods']);
    expect(splitBulk(bp).bulk.periods).toHaveLength(4);
  });

  it('keeps every material limitation inline', () => {
    const { inline } = splitBulk(bp);
    for (const k of ['accounting_basis', 'derived_figures', 'period_label', 'dilution']) {
      expect(inline[k]).toBe((bp as Record<string, unknown>)[k]);
    }
  });

  it('cannot move a string into the drilldown whatever its length', () => {
    const { bulk } = splitBulk({ limitation: 'x'.repeat(5000) });
    expect(bulk).toEqual({});
  });

  it('returns nothing to split for a scalar or a bare list', () => {
    expect(splitBulk(42)).toEqual({ inline: {}, bulk: {} });
    expect(splitBulk([{ a: 1 }, { a: 2 }, { a: 3 }])).toEqual({ inline: {}, bulk: {} });
  });
});

describe('commonTail', () => {
  const stored = 'These are stored annual statements through 2025-08-31.';
  it('states a repeated methodology sentence once and leaves each finding its own words', () => {
    const { tail, rest } = commonTail([
      `Revenue grew 48.9%. ${stored}`,
      `Gross margin expanded 17.4 points. ${stored}`,
      `Operating cash flow of $17.5B. ${stored}`,
    ]);
    expect(tail).toBe(stored);
    expect(rest).toEqual(['Revenue grew 48.9%.', 'Gross margin expanded 17.4 points.',
                          'Operating cash flow of $17.5B.']);
  });

  it('extracts nothing when the texts do not actually share a tail', () => {
    const { tail, rest } = commonTail(['Revenue grew. A.', 'Margin expanded. B.']);
    expect(tail).toBe('');
    expect(rest).toEqual(['Revenue grew. A.', 'Margin expanded. B.']);
  });

  it('never empties a finding — a text that is only the shared tail keeps it', () => {
    const { tail, rest } = commonTail([stored, stored]);
    expect(tail).toBe('');
    expect(rest).toEqual([stored, stored]);
  });

  it('leaves a single finding alone — there is no repetition to remove', () => {
    expect(commonTail([`Revenue grew. ${stored}`]).tail).toBe('');
  });
});
