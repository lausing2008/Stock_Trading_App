// @vitest-environment jsdom
// Audit R06 (2026-10-22): a held fill on a strike with no current quote stays visible, with its
// at-expiry figures from the fill and its current market value shown as unavailable.
import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import GainLossTable from '@/components/GainLossTable';
import type { OptionsChainRow } from '@/lib/api';

afterEach(cleanup);
// GainLossTable relies on Next's automatic JSX runtime; this Vitest config compiles classic JSX.
(globalThis as unknown as { React: typeof React }).React = React;

const row = (strike: number, bid: number, ask: number) =>
  ({ strike, bid, ask, last_price: 0, volume: 10, oi: 100, iv: 30, itm: false,
    contract_terms: { status: 'standard_root_100_assumed', premium_multiplier: 100, deliverable_shares: 100, automatic_payoff_supported: true } }) as OptionsChainRow;

function renderTable(calls: OptionsChainRow[], fillStrike: number, fillPremium: number) {
  const noop = () => {};
  return render(
    <GainLossTable symbol="SYNTH" spot={110} onSpotChange={noop} expiries={['2026-11-20']}
      expiry="2026-11-20" onExpiryChange={noop} calls={calls} puts={[]} loading={false}
      action="buy" right="call" onPositionChange={noop} targetPrice={120} onTargetChange={noop}
      contracts={1} onContractsChange={noop} fillStrike={fillStrike} onFillStrikeChange={noop}
      fillPremium={fillPremium} onFillPremiumChange={noop} />,
  );
}

describe('GainLossTable — held position without a quote', () => {
  it('shows the held row and says the current value is unavailable', () => {
    renderTable([row(100, 0, 0), row(105, 4.9, 5.1)], 100, 5);
    expect(screen.getByText('YOUR FILL')).toBeTruthy();
    expect(screen.getByText('(no quote)')).toBeTruthy();
    const box = screen.getByRole('status', { name: 'Held position without a quote' });
    expect(box.textContent).toContain('current market value unavailable');
    expect(document.body.textContent).not.toContain('now marked');
  });

  it('keeps the existing mark when the held strike is quoted', () => {
    renderTable([row(100, 5.9, 6.1)], 100, 5);
    expect(screen.queryByRole('status', { name: 'Held position without a quote' })).toBeNull();
    expect(document.body.textContent).toContain('now marked');
    expect(document.body.textContent).toContain('(mkt $6.00)');
  });
});
