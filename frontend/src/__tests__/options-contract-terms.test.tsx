// @vitest-environment jsdom
// Audit R05 (2026-10-22): the chain-driven calculator prices only contracts with supported terms.
import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import GainLossTable from '@/components/GainLossTable';
import { buildGainLossRows, contractSupported, refusedContracts } from '@/lib/optionsGainLoss';
import type { ChainQuote } from '@/lib/optionsGainLoss';
import type { OptionsChainRow } from '@/lib/api';

afterEach(cleanup);
(globalThis as unknown as { React: typeof React }).React = React;

const STD = { status: 'standard_root_100_assumed', premium_multiplier: 100, deliverable_shares: 100, automatic_payoff_supported: true } as const;
const ADJ = { status: 'nonstandard_root_deliverable_unknown', premium_multiplier: null, deliverable_shares: null, automatic_payoff_supported: false } as const;
const q = (strike: number, terms: ChainQuote['contract_terms'] | undefined): ChainQuote =>
  ({ strike, bid: 4.9, ask: 5.1, last_price: 0, oi: 100, volume: 10, iv: 30, contract_terms: terms });

describe('buildGainLossRows', () => {
  it('refuses an adjusted contract with a usable quote', () => {
    expect(buildGainLossRows([q(100, ADJ)], 'buy', 'call', 100, 120)).toEqual([]);
  });

  it('refuses a row with no contract terms', () => {
    expect(buildGainLossRows([q(100, undefined), q(105, null)], 'buy', 'call', 100, 120)).toEqual([]);
  });

  it('refuses supported-looking terms with a different multiplier', () => {
    expect(contractSupported(q(100, { automatic_payoff_supported: true, premium_multiplier: 10 }))).toBe(false);
  });

  it('a held fill on an adjusted contract is not priced either', () => {
    expect(buildGainLossRows([q(100, ADJ)], 'buy', 'call', 100, 120, 1, { strike: 100, premium: 5 })).toEqual([]);
  });

  it('standard contracts keep their existing results', () => {
    const [row] = buildGainLossRows([q(100, STD)], 'buy', 'call', 100, 120);
    expect(row.capitalBasis).toBe(500);
    expect(row.netPL).toBe(1500);
  });

  it('counts refusals by reason', () => {
    expect(refusedContracts([q(95, STD), q(100, ADJ), q(105, undefined)]))
      .toEqual({ nonstandard_root_deliverable_unknown: 1, no_contract_terms: 1 });
  });
});

describe('GainLossTable', () => {
  const row = (strike: number, terms: unknown) =>
    ({ strike, bid: 4.9, ask: 5.1, last_price: 0, volume: 10, oi: 100, iv: 30, itm: null, contract_terms: terms }) as OptionsChainRow;
  const noop = () => {};

  it('says how many contracts were not priced and why', () => {
    render(<GainLossTable symbol="MU" spot={100} onSpotChange={noop} expiries={['2026-11-20']} expiry="2026-11-20"
      onExpiryChange={noop} calls={[row(95, STD), row(100, ADJ), row(105, undefined)]} puts={[]} loading={false}
      action="buy" right="call" onPositionChange={noop} targetPrice={120} onTargetChange={noop}
      contracts={1} onContractsChange={noop} fillStrike={0} onFillStrikeChange={noop} fillPremium={0} onFillPremiumChange={noop} />);
    const note = screen.getByRole('status', { name: 'Contracts not priced' }).textContent ?? '';
    expect(note).toContain('2 contracts not priced');
    expect(note).toContain('1 adjusted (deliverable may not be 100 shares)');
    expect(note).toContain('1 without identifiable contract terms');
    expect(document.querySelectorAll('tbody tr').length).toBe(1);   // only the standard strike
  });
});

describe('R05/R06 — held positions on unsupported identity, tested separately from R06', () => {
  const noop = () => {};
  const row = (strike: number, terms: unknown, bid = 0, ask = 0) =>
    ({ strike, bid, ask, last_price: 0, volume: 0, oi: 0, iv: null, itm: null, contract_terms: terms }) as OptionsChainRow;
  const table = (calls: OptionsChainRow[]) => render(<GainLossTable symbol="MU" spot={110} onSpotChange={noop} expiries={['2026-11-20']}
    expiry="2026-11-20" onExpiryChange={noop} calls={calls} puts={[]} loading={false} action="buy" right="call"
    onPositionChange={noop} targetPrice={120} onTargetChange={noop} contracts={1} onContractsChange={noop}
    fillStrike={100} onFillStrikeChange={noop} fillPremium={5} onFillPremiumChange={noop} />);

  it('an adjusted contract the user holds is kept on record with the reason, never priced', () => {
    table([row(100, ADJ)]);
    const rec = screen.getByRole('status', { name: 'Held position not priced' }).textContent ?? '';
    expect(rec).toContain('1 × $100 call @ $5.00 — kept on record, payoff not computed: adjusted contract');
    expect(document.querySelectorAll('tbody tr').length).toBe(0);
    expect(screen.queryByText('YOUR FILL')).toBeNull();
  });

  it('an unidentifiable contract the user holds is kept on record with its own reason', () => {
    table([row(100, undefined)]);
    expect(screen.getByRole('status', { name: 'Held position not priced' }).textContent).toContain('cannot be identified');
  });

  it('a standard contract the user holds, with no quote, is priced from the fill (R06) — no blocked record', () => {
    table([row(100, STD)]);
    expect(screen.queryByRole('status', { name: 'Held position not priced' })).toBeNull();
    expect(screen.getByText('YOUR FILL')).toBeTruthy();
    expect(screen.getByText('(no quote)')).toBeTruthy();
  });
});
