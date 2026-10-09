import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it, expect } from 'vitest';
import { FlowDecisionReview } from '@/components/FlowDecisionReview';
import type { OptionsFlowAlertRow } from '@/lib/api';

describe('flow decision review', () => {
  it('keeps a bearish alert separate from a bullish income recommendation and states missing risk', () => {
    const row = { symbol: 'TEST', direction: 'bearish', option_chain: 'TEST-P100', fired_date: '2026-01-01', calibration_eligibility: 'same_day_expiry', eligibility_reason: 'Expired before next-session entry', volume_oi_ratio: 2 } as OptionsFlowAlertRow;
    const html = renderToStaticMarkup(<FlowDecisionReview row={row} onClose={() => {}} />);
    expect(html).toContain('bear put spread');
    expect(html).not.toContain('bull put spread');
    expect(html).toContain('Research only');
    expect(html).toContain('Maximum loss / quantity');
    expect(html).toContain('Expired before next-session entry');
    expect(html).toContain('option net P&amp;L is unmeasured');
  });
});
