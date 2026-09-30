import { describe, it, expect } from 'vitest';
import {
  premiumFor, intrinsic, moneynessOf, capitalFor, maxLossFor, breakevenFor,
  buildGainLossRows, nearestStrikes, highestRoi, daysToExpiry,
} from './optionsGainLoss';
import type { ChainQuote } from './optionsGainLoss';

function q(strike: number, bid: number, ask: number, extra: Partial<ChainQuote> = {}): ChainQuote {
  return { strike, bid, ask, last_price: 0, oi: 100, volume: 10, iv: 0.3, ...extra };
}

describe('premiumFor', () => {
  it('uses the midpoint of a two-sided quote', () => {
    const p = premiumFor(q(100, 4.9, 5.1));
    expect(p.premium).toBe(5);
    expect(p.source).toBe('mid');
  });

  it('reports the spread as a percentage of the midpoint', () => {
    // 4.0/6.0 -> mid 5.0, spread 2.0, i.e. 40% of the mid. A row like this is not tradeable at
    // the price it shows, and the table has to be able to say so.
    expect(premiumFor(q(100, 4, 6)).spreadPct).toBe(40);
  });

  it('falls back to the last trade when the quote is one-sided', () => {
    // A midpoint of a missing side would be half the real price — a number with no market behind
    // it, presented with the same confidence as a real one.
    const p = premiumFor(q(100, 0, 5, { last_price: 4.8 }));
    expect(p.premium).toBe(4.8);
    expect(p.source).toBe('last');
    expect(p.spreadPct).toBeNull();
  });

  it('falls back when the bid is missing entirely', () => {
    const p = premiumFor(q(100, 0, 0, { last_price: 2.2 }));
    expect(p.source).toBe('last');
    expect(p.premium).toBe(2.2);
  });
});

describe('intrinsic', () => {
  it('is the in-the-money amount for a call', () => {
    expect(intrinsic('call', 100, 120)).toBe(20);
  });

  it('is never negative for an out-of-the-money call', () => {
    expect(intrinsic('call', 120, 100)).toBe(0);
  });

  it('is the in-the-money amount for a put', () => {
    expect(intrinsic('put', 120, 100)).toBe(20);
  });

  it('is never negative for an out-of-the-money put', () => {
    expect(intrinsic('put', 100, 120)).toBe(0);
  });
});

describe('moneynessOf', () => {
  it('labels a call below spot as in the money', () => {
    expect(moneynessOf('call', 90, 100)).toBe('ITM');
  });

  it('labels a put above spot as in the money', () => {
    expect(moneynessOf('put', 110, 100)).toBe('ITM');
  });

  it('labels a strike at spot as at the money for both rights', () => {
    expect(moneynessOf('call', 100, 100)).toBe('ATM');
    expect(moneynessOf('put', 100, 100)).toBe('ATM');
  });

  it('labels the far side as out of the money', () => {
    expect(moneynessOf('call', 120, 100)).toBe('OTM');
    expect(moneynessOf('put', 80, 100)).toBe('OTM');
  });
});

// ── the ROI denominator, which is the thing four positions can most easily get wrong ────────

describe('capitalFor', () => {
  it('uses the premium paid for a long position', () => {
    expect(capitalFor('buy', 'call', 100, 5, 100, 1)).toEqual({ basis: 500, label: 'premium paid' });
    expect(capitalFor('buy', 'put', 100, 5, 100, 1)).toEqual({ basis: 500, label: 'premium paid' });
  });

  it('uses the cash a short put must post, not the premium it collects', () => {
    // THE POINT. Against premium, a short put that expires worthless returns +100% — so every
    // row would read +100% and selling would look like free money. The real commitment is the
    // strike in cash.
    expect(capitalFor('sell', 'put', 100, 5, 100, 1)).toEqual({ basis: 10000, label: 'cash secured' });
  });

  it('uses the shares a short call must hold', () => {
    expect(capitalFor('sell', 'call', 105, 2, 100, 1)).toEqual({ basis: 10000, label: 'shares held' });
  });

  it('scales with contract count', () => {
    expect(capitalFor('sell', 'put', 100, 5, 100, 3).basis).toBe(30000);
  });
});

describe('maxLossFor', () => {
  it('bounds a long position at the premium paid', () => {
    expect(maxLossFor('buy', 'call', 100, 5, 1)).toBe(500);
    expect(maxLossFor('buy', 'put', 100, 5, 1)).toBe(500);
  });

  it('bounds a short put at the strike less the premium collected', () => {
    // The stock goes to zero: assigned at 100, having collected 5.
    expect(maxLossFor('sell', 'put', 100, 5, 1)).toBe(9500);
  });

  it('returns null for a short call, because there is no worst case', () => {
    // There is no highest price a stock can reach. Any NUMBER in this cell would understate it,
    // so the type has to admit "no such value" rather than pick one.
    expect(maxLossFor('sell', 'call', 105, 2, 1)).toBeNull();
  });
});

// ── long call: the canonical primer table ───────────────────────────────────────────────────

describe('buildGainLossRows — long call, the worked example', () => {
  const quotes = [q(90, 14.9, 15.1), q(100, 4.9, 5.1), q(110, 1.9, 2.1), q(120, 0.45, 0.55)];
  const rows = buildGainLossRows(quotes, 'buy', 'call', 100, 120);
  const byStrike = Object.fromEntries(rows.map(r => [r.strike, r]));

  it('produces one row per quoted strike, ascending', () => {
    expect(rows.map(r => r.strike)).toEqual([90, 100, 110, 120]);
  });

  it('matches the hand-computed cost, value, profit and ROI', () => {
    expect(byStrike[90].capitalBasis).toBe(1500);
    expect(byStrike[90].valueAtTarget).toBe(3000);
    expect(byStrike[90].netPL).toBe(1500);
    expect(byStrike[90].roiPct).toBe(100);

    expect(byStrike[100].capitalBasis).toBe(500);
    expect(byStrike[100].netPL).toBe(1500);
    expect(byStrike[100].roiPct).toBe(300);

    expect(byStrike[110].capitalBasis).toBe(200);
    expect(byStrike[110].netPL).toBe(800);
    expect(byStrike[110].roiPct).toBe(400);

    expect(byStrike[120].capitalBasis).toBe(50);
    expect(byStrike[120].valueAtTarget).toBe(0);
    expect(byStrike[120].netPL).toBe(-50);
    expect(byStrike[120].roiPct).toBe(-100);
  });

  it('records the premium as a debit', () => {
    expect(byStrike[100].cashFlow).toBe(-500);
  });

  it('bounds a long option loss at -100%', () => {
    for (const r of rows) expect(r.roiPct).toBeGreaterThanOrEqual(-100);
  });

  it('scales with contract count without changing ROI', () => {
    const ten = buildGainLossRows(quotes, 'buy', 'call', 100, 120, 10);
    const many = ten.find(r => r.strike === 100)!;
    expect(many.capitalBasis).toBe(5000);
    expect(many.netPL).toBe(15000);
    expect(many.roiPct).toBe(300);
  });
});

// ── the other three positions ───────────────────────────────────────────────────────────────

describe('buildGainLossRows — long put', () => {
  it('profits as the target falls', () => {
    const rows = buildGainLossRows([q(100, 4.9, 5.1)], 'buy', 'put', 100, 80);
    expect(rows[0].valueAtTarget).toBe(2000);
    expect(rows[0].netPL).toBe(1500);
    expect(rows[0].roiPct).toBe(300);
  });

  it('loses its whole premium when the target is above the strike', () => {
    const rows = buildGainLossRows([q(100, 4.9, 5.1)], 'buy', 'put', 100, 120);
    expect(rows[0].netPL).toBe(-500);
    expect(rows[0].roiPct).toBe(-100);
  });
});

describe('buildGainLossRows — short put', () => {
  it('keeps the whole premium when it expires worthless', () => {
    const rows = buildGainLossRows([q(95, 2.9, 3.1)], 'sell', 'put', 100, 120);
    expect(rows[0].valueAtTarget).toBe(0);
    expect(rows[0].netPL).toBe(300);
    expect(rows[0].cashFlow).toBe(300);      // a credit, not a debit
  });

  it('measures that gain against the cash posted, NOT the premium', () => {
    // Against premium this would read +100%. Against the $9,500 actually tied up it is ~3.2%,
    // which is the number that lets a reader compare it to anything else they could do with
    // $9,500. This is the single most important assertion in the file.
    const rows = buildGainLossRows([q(95, 2.9, 3.1)], 'sell', 'put', 100, 120);
    expect(rows[0].capitalBasis).toBe(9500);
    expect(rows[0].capitalLabel).toBe('cash secured');
    expect(rows[0].roiPct).toBe(3.2);
    expect(rows[0].roiPct).not.toBe(100);
  });

  it('loses when assigned below the strike', () => {
    // Target 80, strike 95, premium 3: intrinsic 15 owed, 3 collected -> -1200.
    const rows = buildGainLossRows([q(95, 2.9, 3.1)], 'sell', 'put', 100, 80);
    expect(rows[0].netPL).toBe(-1200);
  });

  it('carries a bounded max loss', () => {
    const rows = buildGainLossRows([q(95, 2.9, 3.1)], 'sell', 'put', 100, 120);
    expect(rows[0].maxLoss).toBe(9200);
  });
});

describe('buildGainLossRows — short call', () => {
  it('keeps the whole premium when it expires worthless', () => {
    const rows = buildGainLossRows([q(110, 1.9, 2.1)], 'sell', 'call', 100, 100);
    expect(rows[0].netPL).toBe(200);
    expect(rows[0].cashFlow).toBe(200);
  });

  it('measures that gain against the shares held', () => {
    const rows = buildGainLossRows([q(110, 1.9, 2.1)], 'sell', 'call', 100, 100);
    expect(rows[0].capitalBasis).toBe(10000);
    expect(rows[0].capitalLabel).toBe('shares held');
    expect(rows[0].roiPct).toBe(2);
  });

  it('loses as the target rises past the strike', () => {
    // Target 130, strike 110, premium 2: 20 owed, 2 collected -> -1800.
    const rows = buildGainLossRows([q(110, 1.9, 2.1)], 'sell', 'call', 100, 130);
    expect(rows[0].netPL).toBe(-1800);
  });

  it('reports max loss as unbounded, never as a number', () => {
    // A number here would be a lie — there is no highest price a stock can reach.
    const rows = buildGainLossRows([q(110, 1.9, 2.1)], 'sell', 'call', 100, 100);
    expect(rows[0].maxLoss).toBeNull();
  });
});

describe('buildGainLossRows — the two sides are exact mirrors', () => {
  it('a buyer gains exactly what the seller loses, at every strike', () => {
    // The structural check that catches a sign error anywhere in the P&L expression, which is the
    // most likely way four positions go wrong.
    const quotes = [q(90, 14.9, 15.1), q(100, 4.9, 5.1), q(110, 1.9, 2.1)];
    for (const right of ['call', 'put'] as const) {
      for (const target of [70, 100, 130]) {
        const longs = buildGainLossRows(quotes, 'buy', right, 100, target);
        const shorts = buildGainLossRows(quotes, 'sell', right, 100, target);
        expect(longs).toHaveLength(shorts.length);
        longs.forEach((l, i) => {
          expect(l.strike).toBe(shorts[i].strike);
          expect(l.netPL).toBe(-shorts[i].netPL);
        });
      }
    }
  });

  it('a debit for one side is a credit of the same size for the other', () => {
    const l = buildGainLossRows([q(100, 4.9, 5.1)], 'buy', 'call', 100, 120)[0];
    const s = buildGainLossRows([q(100, 4.9, 5.1)], 'sell', 'call', 100, 120)[0];
    expect(l.cashFlow).toBe(-s.cashFlow);
  });
});

describe('buildGainLossRows — data quality', () => {
  it('drops an unquoted strike rather than showing it as free', () => {
    // A strike with no price is not a cheaper trade, it is one nobody is quoting. Included, it
    // would divide by zero and render an infinite ROI at the top of the table.
    const rows = buildGainLossRows(
      [q(100, 0, 0, { last_price: 0 }), q(110, 1.9, 2.1)], 'buy', 'call', 100, 120);
    expect(rows.map(r => r.strike)).toEqual([110]);
  });

  it('flags a strike with no open interest and no volume as illiquid', () => {
    const rows = buildGainLossRows([q(105, 1, 1.2, { oi: 0, volume: 0 })], 'buy', 'call', 100, 120);
    expect(rows[0].illiquid).toBe(true);
  });

  it('does not flag a strike that has open interest', () => {
    const rows = buildGainLossRows([q(105, 1, 1.2, { oi: 50, volume: 0 })], 'buy', 'call', 100, 120);
    expect(rows[0].illiquid).toBe(false);
  });

  it('ignores a nonsensical strike', () => {
    expect(buildGainLossRows([q(0, 1, 2), q(-5, 1, 2)], 'buy', 'call', 100, 120)).toEqual([]);
  });
});

describe('breakevenFor', () => {
  it('is strike plus premium for a call', () => {
    expect(breakevenFor('call', 100, 5)).toBe(105);
  });

  it('is strike minus premium for a put', () => {
    expect(breakevenFor('put', 100, 5)).toBe(95);
  });

  it('is IDENTICAL for the buyer and the seller of the same contract', () => {
    // The point of the column. Both sides break even at the same price — it is where the
    // contract's intrinsic value equals the premium that changed hands. Making it differ by side
    // would be a real error, and an easy one to introduce by "signing" it like P&L.
    const l = buildGainLossRows([q(100, 4.9, 5.1)], 'buy', 'call', 100, 120)[0];
    const s = buildGainLossRows([q(100, 4.9, 5.1)], 'sell', 'call', 100, 120)[0];
    expect(l.breakeven).toBe(s.breakeven);
    expect(l.breakeven).toBe(105);
  });

  it('is the price at which net P/L is actually zero', () => {
    // Behavioural check against the P&L engine itself rather than against the formula: build the
    // row, then re-price the same strike AT its own breakeven and require the P&L to vanish.
    for (const right of ['call', 'put'] as const) {
      for (const action of ['buy', 'sell'] as const) {
        const row = buildGainLossRows([q(100, 4.9, 5.1)], action, right, 100, 120)[0];
        const atBe = buildGainLossRows([q(100, 4.9, 5.1)], action, right, 100, row.breakeven)[0];
        expect(atBe.netPL).toBeCloseTo(0, 6);
      }
    }
  });

  it('measures the required move from SPOT, not from the strike', () => {
    // Spot deliberately != strike, or the two formulas agree and the test proves nothing — which
    // is exactly what an earlier version of this test did, and a sabotage walked straight
    // through it. Spot 90, strike 100, premium 5 -> breakeven 105, which is +16.7% from 90 and
    // would be a misleading +5% if measured from the strike.
    const row = buildGainLossRows([q(100, 4.9, 5.1)], 'buy', 'call', 90, 120)[0];
    expect(row.breakeven).toBe(105);
    expect(row.breakevenMovePct).toBe(16.7);
  });

  it('reports a NEGATIVE move for a put, which can fall that far and still break even', () => {
    // Spot 110, strike 100, premium 5 -> breakeven 95, i.e. the stock must fall 13.6% from where
    // it is now. Measured from the strike it would read a much gentler -5%.
    const row = buildGainLossRows([q(100, 4.9, 5.1)], 'buy', 'put', 110, 80)[0];
    expect(row.breakeven).toBe(95);
    expect(row.breakevenMovePct).toBe(-13.6);
  });

  it('leaves the move percentage null when there is no spot to measure from', () => {
    const row = buildGainLossRows([q(100, 4.9, 5.1)], 'buy', 'call', 0, 120)[0];
    expect(row.breakevenMovePct).toBeNull();
  });
});

describe('nearestStrikes', () => {
  const rows = buildGainLossRows(
    Array.from({ length: 40 }, (_, i) => q(80 + i, 1, 1.2)), 'buy', 'call', 100, 120,
  );

  it('keeps the requested count', () => {
    expect(nearestStrikes(rows, 100, 12)).toHaveLength(12);
  });

  it('centres on spot rather than truncating one end', () => {
    const kept = nearestStrikes(rows, 100, 12).map(r => r.strike);
    expect(Math.min(...kept)).toBeLessThan(100);
    expect(Math.max(...kept)).toBeGreaterThan(100);
  });

  it('returns ascending strikes, not distance order', () => {
    const kept = nearestStrikes(rows, 100, 12).map(r => r.strike);
    expect(kept).toEqual([...kept].sort((a, b) => a - b));
  });

  it('passes a short list through untouched', () => {
    const few = rows.slice(0, 5);
    expect(nearestStrikes(few, 100, 12)).toEqual(few);
  });
});

describe('highestRoi', () => {
  it('finds the highest-ROI profitable row', () => {
    const rows = buildGainLossRows(
      [q(90, 14.9, 15.1), q(100, 4.9, 5.1), q(110, 1.9, 2.1)], 'buy', 'call', 100, 120);
    expect(highestRoi(rows)!.strike).toBe(110);
  });

  it('returns null when no strike profits at the target', () => {
    // Every row a loss is a real answer about the target price, not a reason to pick the least
    // bad one and present it as a winner.
    const rows = buildGainLossRows([q(120, 0.45, 0.55), q(130, 0.2, 0.3)], 'buy', 'call', 100, 100);
    expect(highestRoi(rows)).toBeNull();
  });

  it('returns null for an empty table', () => {
    expect(highestRoi([])).toBeNull();
  });
});

describe('daysToExpiry', () => {
  it('counts whole calendar days', () => {
    expect(daysToExpiry('2026-10-09', new Date(2026, 8, 29))).toBe(10);
  });

  it('is zero on expiry day', () => {
    expect(daysToExpiry('2026-09-29', new Date(2026, 8, 29))).toBe(0);
  });

  it('is negative for a past expiry', () => {
    expect(daysToExpiry('2026-09-20', new Date(2026, 8, 29))).toBe(-9);
  });

  it('does not shift by a day in a timezone west of UTC', () => {
    // Both sides are reduced to a UTC date built from date PARTS. Comparing the expiry against a
    // local wall-clock instant instead is how a 10-day expiry renders as 9 in New York.
    expect(daysToExpiry('2026-10-09', new Date(2026, 9, 9, 20, 30))).toBe(0);
    expect(daysToExpiry('2026-01-01', new Date(2026, 0, 1, 23, 59))).toBe(0);
    expect(daysToExpiry('2026-03-02', new Date(2026, 2, 1, 21, 0))).toBe(1);
  });

  it('returns null for a malformed expiry', () => {
    expect(daysToExpiry('not-a-date')).toBeNull();
  });
});
