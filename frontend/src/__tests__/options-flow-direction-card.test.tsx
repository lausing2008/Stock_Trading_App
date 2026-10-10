import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import { DirectionCard } from '@/pages/options-flow-alerts';
import type { OptionsFlowAlertDirectionSummary, OptionsFlowWindowStat } from '@/lib/api';

function row(window: OptionsFlowWindowStat): OptionsFlowAlertDirectionSummary {
  return {
    direction: 'bullish', fired_count: 40, window_10d: window,
    window_1d: null, window_2d: null, window_3d: null, window_5d: null, window_20d: null,
  };
}

it('renders shared cohort status as unmeasured instead of a thin precise rate', () => {
  const html = renderToStaticMarkup(<DirectionCard row={row({
    n: 12, wins: 10, win_rate: null, avg_return_pct: null,
    original_n: 20, excluded_total: 8, distinct_dates: 4,
    status: 'insufficient_eligible_history', required_eligible_outcomes: 30,
    required_distinct_dates: 5,
  })} />);
  expect(html).toContain('Unmeasured');
  expect(html).toContain('12 eligible outcomes; 30 required');
  expect(html).not.toContain('83%');
});

it('shows a rate only when the shared cohort status is measured', () => {
  const html = renderToStaticMarkup(<DirectionCard row={row({
    n: 40, wins: 22, win_rate: .55, avg_return_pct: 1.2,
    original_n: 45, excluded_total: 5, distinct_dates: 8,
    status: 'measured', required_eligible_outcomes: 30, required_distinct_dates: 5,
  })} />);
  expect(html).toContain('55%');
  expect(html).not.toContain('Unmeasured');
});
