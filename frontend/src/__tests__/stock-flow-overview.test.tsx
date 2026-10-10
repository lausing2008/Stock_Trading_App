import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import { StockFlowOverview } from '@/components/StockFlowOverview';

it('keeps conflicting activity visible and does not infer trade readiness', () => {
  const html = renderToStaticMarkup(<StockFlowOverview rows={[{
    symbol: 'MU', alert_count: 12, contracts: 7, dates: 3, bullish: 10, bearish: 2,
    latest_at: '2026-10-09T19:00:00',
  }]} onSelect={() => {}} />);
  expect(html).toContain('Mixed activity');
  expect(html).toContain('Trade readiness: not assessed');
  expect(html).toContain('12 alerts');
  expect(html).toContain('/quality-value?symbols=MU');
  expect(html).not.toContain('80%');
});
