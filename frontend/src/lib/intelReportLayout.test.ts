import { describe, it, expect } from 'vitest';
import {
  groupFields, criticalLimitations, coverageBanner, fieldLabel, humaniseValue,
  SECTION_ORDER, type LayoutField,
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
  it('puts limitations above the detail', () => {
    const groups = groupFields(POST_EARNINGS);
    const lim = groups.findIndex(g => g.section === 'limitations');
    const met = groups.findIndex(g => g.section === 'metrics');
    expect(lim).toBeGreaterThanOrEqual(0);
    expect(lim).toBeLessThan(met);
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
