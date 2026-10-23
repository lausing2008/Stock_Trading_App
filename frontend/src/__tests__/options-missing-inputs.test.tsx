// @vitest-environment jsdom
// Audit R03/R04 (2026-10-22): unknown moneyness, volume, OI and IV render as unknown in BOTH the
// Options Flow card and the full chain table — never OTM, 0 or 0%.
import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import OptionsFlowCard from '@/components/OptionsFlowCard';
import OptionsChainTable from '@/components/OptionsChainTable';
import type { OptionsChainRow, OptionsFlow } from '@/lib/api';

afterEach(cleanup);

const contract = (extra = {}) => ({ expiry: '2099-01-16', side: 'call' as const, strike: 100, volume: 900, oi: 500, vol_oi: 1.8,
  iv: 40 as number | null, itm: null as boolean | null, moneyness: null as 'ITM' | 'ATM' | 'OTM' | null,
  premium: null, traded_premium: null, estimated_turnover: null, premium_basis: 'unavailable' as const, is_whale: null, ...extra });
const flow = (over: Partial<OptionsFlow> = {}): OptionsFlow => ({ symbol: 'SYNTH', available: true, call_volume: 1500, put_volume: 1200,
  chain_as_of: '2026-10-20', expiries_used: ['2099-01-16'], expiries_attempted: ['2099-01-16'], coverage_status: 'complete',
  composition_scope: 'complete', unusual: [contract()], ...over });

describe('Options Flow card', () => {
  const cells = () => within(screen.getAllByRole('row')[1]).getAllByRole('cell').map(c => c.textContent);

  it('shows unknown moneyness and IV as unknown, and says why', () => {
    render(<OptionsFlowCard flow={flow({ unusual: [contract({ iv: null })] })} error={undefined} onRetry={() => {}} />);
    const row = cells();
    expect(row[6]).toBe('—');            // IV
    expect(row[7]).toBe('unknown');      // moneyness, not OTM
    expect(document.body.textContent).not.toMatch(/\bOTM\b/);
    expect(screen.getByLabelText('Moneyness reference').textContent).toContain("Today's price is not used");
  });

  it('a close on a different price basis (a split) is named as the reason, not used', () => {
    render(<OptionsFlowCard flow={flow({ moneyness_reference: null,
      moneyness_unknown_reason: 'stored close 60.00 vs chain-implied 120.00 (ratio 0.500): different price bases, e.g. a split adjustment',
      unusual: [contract({ moneyness: null, itm: null })] })} error={undefined} onRetry={() => {}} />);
    expect(cells()[7]).toBe('unknown');
    expect(screen.getByLabelText('Moneyness reference').textContent).toMatch(/Moneyness unknown: stored close 60.00 vs chain-implied 120.00.*split/);
  });

  it('shows measured moneyness and names the session close it used', () => {
    render(<OptionsFlowCard flow={flow({ moneyness_reference: { price: 120, session: '2026-10-20', source: 'daily_close' },
      unusual: [contract({ moneyness: 'ITM', itm: true })] })} error={undefined} onRetry={() => {}} />);
    expect(cells()[7]).toBe('ITM (est.)');                     // R03: plausible, never verified
    const line = screen.getByLabelText('Moneyness reference').textContent ?? '';
    expect(line).toContain('Moneyness is estimated against the 2026-10-20 close of $120.00');
    expect(line).toContain('a plausibility check, not proof that both use the same price basis');
  });

  it('an unmeasured put side is never shown as all calls', () => {
    render(<OptionsFlowCard flow={flow({ put_volume: null, cp_ratio: null, activity_composition: 'unmeasured',
      volume_coverage: { call: { known: 2, total: 2 }, put: { known: 0, total: 2 } }, composition_scope: 'observed_subtotal' })}
      error={undefined} onRetry={() => {}} />);
    const badge = screen.getByLabelText('Volume composition').textContent ?? '';
    expect(badge).toContain('Volume not measured');
    expect(badge).not.toContain('100');
    expect(document.body.textContent).toContain('Puts not measured');
    expect(screen.getByLabelText('Volume coverage').textContent).toContain('2 of 2 put contracts have no measured volume');
  });

  it('says when composition is an observed subtotal', () => {
    render(<OptionsFlowCard flow={flow({ composition_scope: 'observed_subtotal',
      volume_coverage: { call: { known: 3, total: 4 }, put: { known: 2, total: 2 } } })} error={undefined} onRetry={() => {}} />);
    expect(screen.getByLabelText('Volume coverage').textContent).toBe('Observed subtotal: 1 of 4 call contracts have no measured volume.');
  });

  it('a complete chain shows no coverage warning', () => {
    render(<OptionsFlowCard flow={flow()} error={undefined} onRetry={() => {}} />);
    expect(screen.queryByLabelText('Volume coverage')).toBeNull();
  });
});

describe('full chain table', () => {
  const r = (strike: number, extra: Partial<OptionsChainRow> = {}): OptionsChainRow =>
    ({ strike, bid: 1, ask: 1.2, last_price: 0, volume: 10, oi: 100, iv: 35, itm: null, moneyness: null, ...extra });
  const rowFor = (strike: number) => document.querySelector(`tr[data-strike="${strike}"]`)!;
  const tds = (strike: number) => Array.from(rowFor(strike).querySelectorAll('td')).map(td => td.textContent);

  it('renders unknown volume, OI and IV as — and a genuine zero as 0', () => {
    render(<OptionsChainTable calls={[r(100, { volume: null, oi: null, iv: null }), r(105, { volume: 0, oi: 0, iv: 0 })]}
      puts={[]} fieldCoverage={{ calls: { volume: { known: 1, total: 2 }, oi: { known: 1, total: 2 }, iv: { known: 1, total: 2 } },
        puts: { volume: { known: 0, total: 0 }, oi: { known: 0, total: 0 }, iv: { known: 0, total: 0 } } }} />);
    expect(tds(100).slice(3, 6)).toEqual(['—', '—', '—']);
    expect(tds(105).slice(3, 6)).toEqual(['0', '0', '0%']);
    expect(screen.getByLabelText('Chain field coverage').textContent)
      .toContain('IV unknown for 1 of 2 contracts');
  });

  it('tints nothing as in the money when moneyness is unknown, and says why', () => {
    render(<OptionsChainTable calls={[r(90), r(110)]} puts={[r(90), r(110)]} />);
    expect(document.querySelectorAll('td[data-itm]').length).toBe(0);
    expect(screen.getByLabelText('Chain moneyness reference').textContent).toContain('Moneyness unknown');
  });

  it('tints ITM strikes on each side against the session close', () => {
    render(<OptionsChainTable
      moneynessReference={{ price: 100, session: '2026-10-20', source: 'daily_close' }}
      calls={[r(90, { moneyness: 'ITM', itm: true }), r(100, { moneyness: 'ATM', itm: false }), r(110, { moneyness: 'OTM', itm: false })]}
      puts={[r(90, { moneyness: 'OTM', itm: false }), r(100, { moneyness: 'ATM', itm: false }), r(110, { moneyness: 'ITM', itm: true })]} />);
    const itm = (strike: number) => Array.from(rowFor(strike).querySelectorAll('td[data-itm]')).length;
    expect([itm(90), itm(100), itm(110)]).toEqual([1, 0, 1]);    // ATM tints neither side
    expect(screen.getByLabelText('Chain moneyness reference').textContent).toContain('2026-10-20 close of $100.00');
  });
});
