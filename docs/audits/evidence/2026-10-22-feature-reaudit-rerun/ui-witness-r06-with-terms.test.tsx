// @vitest-environment jsdom
// Companion to the original R06 witness. That fixture row carries no contract terms, so after R05
// it is refused for being UNIDENTIFIED before R06's held-fill logic is reached: it returns [] for
// a different reason. This variant gives the same row standard terms, so it observes R06 itself.
// Copy temporarily into frontend/src/__tests__/ and run like the original witnesses.
import { expect, it } from 'vitest';
import { buildGainLossRows } from '@/lib/optionsGainLoss';

const STD = { status: 'standard_root_100_assumed', premium_multiplier: 100, automatic_payoff_supported: true };

it('R06: a held fill on a standard contract survives a missing quote, with no current mark', () => {
  const rows = buildGainLossRows([{ strike: 100, bid: 0, ask: 0, last_price: 0, oi: 0, volume: 0, iv: 0, contract_terms: STD }],
    'buy', 'call', 110, 120, 1, { strike: 100, premium: 5 });
  expect(rows).toHaveLength(1);
  expect(rows[0].premium).toBe(5);
  expect(rows[0].netPL).toBe(1500);
  expect(rows[0].quotedPremium).toBeNull();
});

it('R05: the original witness row, with no contract terms, is refused as unidentified', () => {
  const rows = buildGainLossRows([{ strike: 100, bid: 0, ask: 0, last_price: 0, oi: 0, volume: 0, iv: 0 }],
    'buy', 'call', 110, 120, 1, { strike: 100, premium: 5 });
  expect(rows).toEqual([]);
});
