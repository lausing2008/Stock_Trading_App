import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it, expect } from 'vitest';
import { CopilotAnswer } from './CopilotPanel';
import type { CopilotMessage } from '@/lib/copilot';

const base: CopilotMessage = {
  role: 'assistant', request_id: 'r1', content: 'The latest close was $130.00.',
  status: 'grounded', data_as_of: '2026-10-09T21:00:00',
  limitations: ['Stock report: unavailable — no stock intelligence report has been generated'],
  evidence_refs: [{
    key: 'price.close', label: 'Latest daily close', display: '$130.00', state: 'STALE',
    note: 'latest daily bar is 9 calendar days old',
    evidence: [{ id: 'price:1:2026-10-09T21:00', source: 'prices:1', observed_period: '2026-10-09' }],
  }, {
    key: 'headline.1', label: 'Recent headlines', display: '“Test” (pr_newswire, 2026-10-08)',
    state: 'OK', url: 'https://example.com/a',
    evidence: [{ id: 'news:1', source: 'https://example.com/a', published_at: '2026-10-08T12:00:00' }],
  }],
};

describe('copilot answer', () => {
  it('shows the data time, limitations and every citation with its state and source', () => {
    const html = renderToStaticMarkup(<CopilotAnswer m={base} defaultOpen />);
    for (const t of ['Figures from stored data', 'Data as of 2026-10-09 21:00 UTC', 'Limitations',
      'no stock intelligence report', 'Evidence (2)', 'STALE', 'latest daily bar is 9 calendar days old',
      'price:1:2026-10-09T21:00', 'https://example.com/a', 'published 2026-10-08 12:00 UTC'])
      expect(html).toContain(t);
  });

  it('labels a rejected draft as a failed evidence check and says why', () => {
    const html = renderToStaticMarkup(<CopilotAnswer m={{ ...base, status: 'rejected',
      rejection_reasons: ['the answer writes figures directly ($129.50)'] }} />);
    expect(html).toContain('Evidence check failed');
    expect(html).toContain('writes figures directly');
    expect(html).not.toContain('Figures from stored data');
  });

  it('says the data time is unknown rather than inventing one', () => {
    const html = renderToStaticMarkup(<CopilotAnswer m={{ ...base, data_as_of: null }} />);
    expect(html).toContain('Data as of unknown');
  });
});
