import { describe, it, expect } from 'vitest';
import { computeRiskReward } from './riskReward';

describe('computeRiskReward', () => {
  // ── Long setup (stop below entry) ──────────────────────────────────────────

  it('computes a positive R:R for a valid long setup', () => {
    const r = computeRiskReward({ entry: 100, stop: 95, target: 110 });
    expect(r.isLong).toBe(true);
    expect(r.riskPerShare).toBe(5);
    expect(r.rewardPerShare).toBe(10);
    expect(r.rr).toBe(2);
    expect(r.targetDirectionValid).toBe(true);
  });

  it('suppresses R:R when target is below entry on a long setup (AUD-POSITIONSIZER-INVERTEDRR/AUD-CHART-INVERTEDRR regression)', () => {
    // stop < entry implies a long, but target is ALSO below entry — e.g. a bearish analyst
    // target on an overvalued name. Math.abs() on both legs would have produced a misleading
    // positive-looking ratio before this fix.
    const r = computeRiskReward({ entry: 100, stop: 95, target: 90 });
    expect(r.isLong).toBe(true);
    expect(r.targetDirectionValid).toBe(false);
    expect(r.rr).toBeNull();
    // risk/reward-per-share are still computed (real distances) even though the ratio itself
    // is suppressed — only the combined R:R claim is withheld, not the raw numbers.
    expect(r.riskPerShare).toBe(5);
    expect(r.rewardPerShare).toBe(10);
  });

  // ── Short setup (stop above entry) ──────────────────────────────────────────

  it('computes a positive R:R for a valid short setup', () => {
    const r = computeRiskReward({ entry: 100, stop: 105, target: 90 });
    expect(r.isLong).toBe(false);
    expect(r.riskPerShare).toBe(5);
    expect(r.rewardPerShare).toBe(10);
    expect(r.rr).toBe(2);
    expect(r.targetDirectionValid).toBe(true);
  });

  it('suppresses R:R when target is above entry on a short setup', () => {
    const r = computeRiskReward({ entry: 100, stop: 105, target: 110 });
    expect(r.isLong).toBe(false);
    expect(r.targetDirectionValid).toBe(false);
    expect(r.rr).toBeNull();
  });

  // ── Missing/invalid inputs ──────────────────────────────────────────────────

  it('returns nulls when entry is missing', () => {
    const r = computeRiskReward({ entry: null, stop: 95, target: 110 });
    expect(r.riskPerShare).toBeNull();
    expect(r.rewardPerShare).toBeNull();
    expect(r.rr).toBeNull();
  });

  it('returns nulls when stop is missing', () => {
    const r = computeRiskReward({ entry: 100, stop: null, target: 110 });
    expect(r.riskPerShare).toBeNull();
    expect(r.rr).toBeNull();
  });

  it('returns nulls when target is missing', () => {
    const r = computeRiskReward({ entry: 100, stop: 95, target: null });
    expect(r.rewardPerShare).toBeNull();
    expect(r.rr).toBeNull();
  });

  it('treats a zero or negative entry/stop/target as missing', () => {
    const r = computeRiskReward({ entry: 0, stop: 95, target: 110 });
    expect(r.riskPerShare).toBeNull();
    expect(r.rr).toBeNull();
  });

  it('defaults isLong/targetDirectionValid to true when stop or entry is unavailable', () => {
    // Matches both original call sites' own "assume long, no data to say otherwise" default.
    const r = computeRiskReward({ entry: null, stop: null, target: null });
    expect(r.isLong).toBe(true);
    expect(r.targetDirectionValid).toBe(true);
  });

  it('does not divide by zero when risk is exactly zero', () => {
    const r = computeRiskReward({ entry: 100, stop: 100, target: 110 });
    expect(r.riskPerShare).toBe(0);
    expect(r.rr).toBeNull();
  });
});
