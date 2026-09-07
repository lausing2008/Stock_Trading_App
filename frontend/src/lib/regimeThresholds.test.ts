import { describe, it, expect } from 'vitest';
import { bearThresholdFor, BULL_BASE, BEAR_BASE, DYN_BOUNDS, type Horizon } from './regimeThresholds';

/**
 * AUD-HORIZONCOMPARE-BEARREGIME.
 *
 * These tests exist because bearThresholdFor() reimplements signal-engine's
 * _get_dynamic_buy_threshold() on the frontend. The risk is not that the arithmetic is hard —
 * it is that the backend's baselines or bounds change and this copy drifts silently, putting a
 * wrong threshold in front of the user with no error anywhere. So the live-measured production
 * values are pinned explicitly below rather than being recomputed from the same constants the
 * implementation uses (which would make the test vacuous).
 */

describe('bearThresholdFor', () => {
  // ── The four real production cases, measured live 2026-09-07 ────────────────────────────
  //
  // Live effective bull thresholds from GET /tune_status on that date, and the bear values
  // signals.py would actually compute from them. These are hand-verified from the formula in
  // _get_dynamic_buy_threshold(), NOT generated from BULL_BASE/BEAR_BASE — a drift in those
  // constants must fail here.
  const LIVE_CASES: Array<{ h: Horizon; bull: number; expectedBear: number; note: string }> = [
    { h: 'SHORT',  bull: 0.55, expectedBear: 0.60, note: 'calibrated 0.55; delta -0.08' },
    { h: 'SWING',  bull: 0.65, expectedBear: 0.69, note: 'watchdog 0.65 beats calibrated 0.56; delta -0.07' },
    { h: 'LONG',   bull: 0.55, expectedBear: 0.65, note: 'calibrated 0.55; delta -0.05' },
    { h: 'GROWTH', bull: 0.68, expectedBear: 0.76, note: 'watchdog 0.68 beats calibrated 0.65; delta +0.08' },
  ];

  it.each(LIVE_CASES)('matches production for $h ($note)', ({ h, bull, expectedBear }) => {
    expect(bearThresholdFor(h, bull)).toBeCloseTo(expectedBear, 10);
  });

  it('understated GROWTH bear by 8pp before this fix — the regression that motivated it', () => {
    // The page used to show the static 68% default. The real gate was 76% — i.e. it displayed
    // the threshold as LOOSER than reality, the more dangerous direction of the two.
    const live = bearThresholdFor('GROWTH', 0.68);
    expect(live).toBeCloseTo(0.76, 10);
    expect(live!).toBeGreaterThan(BEAR_BASE.GROWTH);
  });

  // ── Delta semantics ─────────────────────────────────────────────────────────────────────

  it('returns the untouched bear default when the live bull equals its baseline', () => {
    // delta == 0 -> no shift. This is also the case the divergence highlighting must NOT
    // flag as "overridden".
    for (const h of Object.keys(BULL_BASE) as Horizon[]) {
      expect(bearThresholdFor(h, BULL_BASE[h])).toBeCloseTo(BEAR_BASE[h], 10);
    }
  });

  it('shifts bear by exactly the same delta applied to bull, not proportionally', () => {
    // A proportional/scaled implementation would pass the delta==0 test above but fail here.
    const bull = BULL_BASE.SWING - 0.04;
    expect(bearThresholdFor('SWING', bull)).toBeCloseTo(BEAR_BASE.SWING - 0.04, 10);
  });

  it('preserves bear >= bull tiering for a downward tune (the point of T232-CAL2)', () => {
    // The whole reason the backend uses a delta instead of a flat override is so bear stays
    // tighter than bull. Guard that property rather than just the arithmetic.
    for (const { h, bull } of LIVE_CASES) {
      const bear = bearThresholdFor(h, bull)!;
      expect(bear).toBeGreaterThanOrEqual(bull);
    }
  });

  // ── Clamping, mirroring _DYNAMIC_BUY_THRESHOLD_BOUNDS ───────────────────────────────────

  it('clamps to the upper bound instead of exceeding it', () => {
    const [, hi] = DYN_BOUNDS;
    // A large upward tune would push SWING's 0.76 bear baseline past 0.85.
    expect(bearThresholdFor('SWING', 0.85)).toBeCloseTo(hi, 10);
  });

  it('clamps to the lower bound instead of falling below it', () => {
    const [lo] = DYN_BOUNDS;
    // A large downward tune would push LONG's bear below 0.55.
    expect(bearThresholdFor('LONG', 0.40)).toBeCloseTo(lo, 10);
  });

  it('never returns a value outside the bounds for any plausible input', () => {
    const [lo, hi] = DYN_BOUNDS;
    for (const h of Object.keys(BULL_BASE) as Horizon[]) {
      for (let bull = 0.2; bull <= 1.0; bull += 0.01) {
        const v = bearThresholdFor(h, bull)!;
        expect(v).toBeGreaterThanOrEqual(lo);
        expect(v).toBeLessThanOrEqual(hi);
      }
    }
  });

  // ── Absent / malformed input ────────────────────────────────────────────────────────────

  it('returns null when the live bull value is missing', () => {
    // /tune_status unreachable or a style absent from the payload — the caller must fall back
    // to the static default rather than render a fabricated number.
    expect(bearThresholdFor('SHORT', null)).toBeNull();
    expect(bearThresholdFor('SHORT', undefined)).toBeNull();
  });

  it('returns null for a non-finite value rather than propagating NaN into the UI', () => {
    expect(bearThresholdFor('SHORT', NaN)).toBeNull();
    expect(bearThresholdFor('SHORT', Infinity)).toBeNull();
  });

  it('preserves a genuine zero rather than treating it as absent', () => {
    // Falsy-zero discipline: 0 is not a valid threshold, but it must reach the clamp and come
    // back as the lower bound, NOT be silently swallowed as "no data" by a truthiness check —
    // the distinction between "absent" (null) and "present but extreme" must survive.
    expect(bearThresholdFor('SHORT', 0)).toBeCloseTo(DYN_BOUNDS[0], 10);
  });
});
