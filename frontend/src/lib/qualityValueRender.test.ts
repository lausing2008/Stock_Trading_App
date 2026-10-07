/* AUD-QV-FRONTENDSTATES — the page must not keep its own copy of the backend's states.
 *
 * The defect: quality-value.tsx held a five-entry STATUS_STYLE map with labels in it, and
 * `STATUS_STYLE[s] ?? STATUS_STYLE.unknown`. When the backend grew from five states to nine,
 * all four new ones rendered as "No evidence" — the single label they were introduced to stop
 * saying — and the coverage table, whose columns were a hardcoded ['pass','fail','unknown',
 * 'blocked'], summed to zero for every gate reporting a new state.
 *
 * These assert against the page SOURCE, because the failure was structural: a second copy of a
 * mapping that the server owns.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'fs';
import { join } from 'path';

const PAGE = readFileSync(join(__dirname, '..', 'pages', 'quality-value.tsx'), 'utf8');

describe('the page renders states from the served catalog', () => {
  it('keeps no client-side label map for statuses', () => {
    expect(PAGE).not.toContain('STATUS_STYLE');
    // The tone map is colour only: no `label:` key may appear inside it.
    const tone = PAGE.slice(PAGE.indexOf('const STATUS_TONE'), PAGE.indexOf('const UNKNOWN_TONE'));
    expect(tone).not.toMatch(/\blabel\s*:/);
  });

  /* THESE NOW ASSERT DELEGATION, NOT BEHAVIOUR. The row model and the badge labelling moved
     into qualityValueCoverage.ts, where they are exercised against real payload shapes —
     including the optional unassessed rows that produced "0 ≠ 200". A source test cannot check
     arithmetic; its remaining job is that the page has no SECOND copy of that logic. */
  it('takes badge labels from the shared model, not its own branch', () => {
    expect(PAGE).toContain("const label = badgeLabel(s, catalog);");
    expect(PAGE).not.toMatch(/entry\s*\?\s*entry\.label\s*:/);
  });

  it('prefers a connected assessment’s own remedy over the status default', () => {
    // A completed durability review knows what IT left unassessed; the catalog only knows the
    // status. The gate's own remedy must win where one exists.
    expect(PAGE).toContain('const r = own || badgeRemedy(s, catalog);');
    expect(PAGE).toContain('own={g.remedy}');
  });

  it('never hardcodes the coverage columns', () => {
    expect(PAGE).not.toContain("['pass', 'fail', 'unknown', 'blocked']");
    expect(PAGE).toContain('statusColumns(data)');
  });

  it('builds its rows from the shared model rather than inline', () => {
    expect(PAGE).toContain('coverageRows(data)');
    // No second pass over gate_coverage in the page: that was the duplicate.
    expect(PAGE).not.toContain('Object.entries(data.gate_coverage)');
  });

  it('renders the reconciliation the model computes', () => {
    expect(PAGE).toContain('r.reconciles');
    expect(PAGE).toMatch(/≠ \$\{data\.evaluated\}/);
  });

  it('shows the remedy for a non-passing gate', () => {
    expect(PAGE).toContain('What would close it:');
    expect(PAGE).toContain('<Remedy s={g.status} catalog={catalog} own={g.remedy} />');
  });

  it('does not tell the reader nothing is stored when evaluations are', () => {
    expect(PAGE).not.toContain('Nothing on this page is stored');
    expect(PAGE).toContain('Every verdict IS stored');
  });
});

describe('the status type does not close a set the backend owns', () => {
  it('leaves QvGateStatus open', () => {
    const api = readFileSync(join(__dirname, 'api.ts'), 'utf8');
    expect(api).toContain('export type QvGateStatus = string;');
    expect(api).toContain('status_catalog?:');
  });
});

describe('reading order: the conclusion sits above the audit detail', () => {
  it('renders the summary before the gate detail', () => {
    const iSummary = PAGE.indexOf('{open && e.summary && <Summary s={e.summary} />}');
    const iDetail = PAGE.indexOf("{e.gates.filter(g => g.status === 'pass')");
    expect(iSummary).toBeGreaterThan(-1);
    expect(iSummary).toBeLessThan(iDetail);
  });

  it('leads with compact price setups and keeps the lengthy research behind expansion', () => {
    expect(PAGE).toContain('<DirectionScreen symbols={query} />');
    expect(PAGE).toContain('{open && e.summary && <Summary s={e.summary} />}');
  });

  it('does not render a connected assessment twice', () => {
    // The prose reason lines are suppressed where a structured assessment exists.
    expect(PAGE).toContain('{!g.evidence?.verdict && g.reasons.map(');
  });

  it('labels an assumption as observed or modelled', () => {
    expect(PAGE).toContain("k.kind === 'observed'");
    expect(PAGE).toContain('{k.kind}');
  });

  it('distinguishes an undisclosed gap from an unexamined one', () => {
    expect(PAGE).toContain("o.label === 'not publicly disclosed'");
    expect(PAGE).toContain('Not closable by research:');
  });

  it('keeps the counterevidence attached to every supporting claim', () => {
    const block = PAGE.slice(PAGE.indexOf('What supports it'), PAGE.indexOf('What remains'));
    expect(block).toContain('x.against');
    expect(block).toContain('Against:');
  });
});
