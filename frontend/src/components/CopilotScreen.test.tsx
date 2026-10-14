import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it, expect, vi } from 'vitest';
vi.mock('next/link', () => ({ default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a> }));
import { ScreenResultView } from './CopilotScreen';
import type { CopilotScreenResult } from '@/lib/copilot';

const base: CopilotScreenResult = {
  question: 'BUY in tech above 70', status: 'ok',
  scope: { kind: 'watchlist', description: 'your watchlists (12 symbols)', requested: 12 },
  interpreted: [{ filter: 'min_score', value: 70, label: 'K-Score at least 70' },
    { filter: 'signal', value: 'BUY', label: 'latest SWING signal BUY' }],
  dropped: [{ filter: 'min_momentum', value: '60', reason: 'this threshold does not appear in your question' }],
  unsupported: ['P/E below 15'], sort_by: 'score', limit: 5,
  coverage: { universe: 10, matched: 7, shown: 5, unranked: ['NEWCO', 'OLDCO'], window_days: 60 },
  results: [{ symbol: 'AAA', sector: 'Technology', score: 81, signal: 'BUY', confidence: 55 }],
  basis: 'Scores are engine rankings, not return forecasts.',
};

describe('copilot screen result', () => {
  it('shows scope, coverage, what ran, what did not, and why', () => {
    const html = renderToStaticMarkup(<ScreenResultView r={base} />);
    for (const t of ['Searched your watchlists (12 symbols)', '10 with a ranking in the last 60 days',
      '7 matched (showing 5)', 'Not screened (no recent ranking): NEWCO, OLDCO',
      'K-Score at least 70', 'latest SWING signal BUY',
      'min_momentum 60 — this threshold does not appear in your question',
      'The screen cannot filter on: ', 'P/E below 15', 'href="/stock/AAA"', 'not return forecasts'])
      expect(html).toContain(t);
  });

  it('when nothing could be applied, shows the reason and no result table', () => {
    const html = renderToStaticMarkup(<ScreenResultView r={{ ...base, status: 'nothing_applied',
      message: 'None of the request could be applied as a filter, so no screen was run.',
      interpreted: [], results: [], coverage: { universe: null, matched: null, shown: 0, unranked: [] } }} />);
    expect(html).toContain('no screen was run');
    expect(html).not.toContain('<table');
    expect(html).not.toContain('matched');
  });
});
