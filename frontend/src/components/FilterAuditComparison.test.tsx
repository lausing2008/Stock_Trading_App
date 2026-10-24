import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import FilterAuditComparison from './FilterAuditComparison';

const base = { n_active: 10, n_inactive: 20, edge_pct: -50, verdict: 'predictive' };
const render = (row: typeof base | (Omit<typeof base, 'edge_pct'> & { edge_pct: number | null })) =>
  renderToStaticMarkup(<table><tbody><tr><FilterAuditComparison row={row} /></tr></tbody></table>);

it.each([{ n_active: 0 }, { n_inactive: 0 }, { n_active: 0, n_inactive: 0 }, { edge_pct: null }])(
  'withholds comparisons for missing evidence, including old API responses: %j', patch => {
    const html = render({ ...base, ...patch });
    expect(html).toContain('Insufficient evidence');
    expect(html).toContain('—');
    expect(html).not.toContain('predictive');
    expect(html).not.toContain('-50%');
  },
);

it.each([[-50, '-50%'], [0, '0%'], [50, '+50%']])('renders a measured edge %s', (edge, label) => {
  const html = render({ ...base, edge_pct: edge });
  expect(html).toContain(label);
  expect(html).toContain('Descriptive cohort comparison');
  expect(html).not.toContain('Insufficient evidence');
});
