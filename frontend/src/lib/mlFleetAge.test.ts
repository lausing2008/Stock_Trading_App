import { describe, it, expect } from 'vitest';
import { summarizeFleetAge, formatAge, ageColor, STALE_AGE_DAYS } from './mlFleetAge';
import type { MlModelMetric } from './api';

const mk = (over: Partial<MlModelMetric>): MlModelMetric => ({
  symbol: 'X', model: 'xgboost', test_auc: 0.6, cv_auc: 0.6,
  accuracy: 0.5, overfit_gap: 0, buy_threshold: 0.5, ...over,
});

describe('summarizeFleetAge', () => {
  // ── The three-state distinction this module exists to protect ───────────────────────────

  it('counts a genuine 0 as fresh, NOT as unknown', () => {
    // Falsy-zero: `if (!age_days)` would drop every model trained today — the most common
    // state immediately after a retrain, and the one a user is most likely to be checking for.
    const s = summarizeFleetAge([mk({ age_days: 0 })]);
    expect(s.fresh).toBe(1);
    expect(s.unknown).toBe(0);
    expect(s.dated).toBe(1);
  });

  it('counts a null age as unknown, NOT as fresh', () => {
    // Coercing null->0 would report the fleet's OLDEST models (pre-Tier-21 bundles with no
    // trained_at) as the freshest — exactly inverted from the truth.
    const s = summarizeFleetAge([mk({ age_days: null })]);
    expect(s.unknown).toBe(1);
    expect(s.fresh).toBe(0);
    expect(s.stale).toBe(0);
    expect(s.dated).toBe(0);
  });

  it('treats a missing age_days field the same as an explicit null', () => {
    // The field is optional on the type — an older backend that has not been redeployed simply
    // omits it, and that must not be read as "0 days old".
    const s = summarizeFleetAge([mk({})]);
    expect(s.unknown).toBe(1);
    expect(s.fresh).toBe(0);
  });

  it('keeps unknown out of BOTH the fresh and stale buckets', () => {
    // unknown is its own category; folding it either way misstates the fleet.
    const s = summarizeFleetAge([mk({ age_days: null }), mk({ age_days: null })]);
    expect(s.fresh + s.stale).toBe(0);
    expect(s.unknown).toBe(2);
    expect(s.total).toBe(2);
  });

  // ── Stale boundary ──────────────────────────────────────────────────────────────────────

  it('treats exactly STALE_AGE_DAYS as fresh and one day past it as stale', () => {
    const s = summarizeFleetAge([
      mk({ age_days: STALE_AGE_DAYS }),
      mk({ age_days: STALE_AGE_DAYS + 1 }),
    ]);
    expect(s.fresh).toBe(1);
    expect(s.stale).toBe(1);
  });

  // ── Aggregate stats ─────────────────────────────────────────────────────────────────────

  it('computes oldest and median only over models with a KNOWN age', () => {
    // A null-aged model must not silently pull the median toward 0.
    const s = summarizeFleetAge([
      mk({ age_days: 0 }), mk({ age_days: 10 }), mk({ age_days: 82 }), mk({ age_days: null }),
    ]);
    expect(s.oldestDays).toBe(82);
    expect(s.medianDays).toBe(10);
    expect(s.dated).toBe(3);
    expect(s.total).toBe(4);
  });

  it('returns null aggregates rather than 0 when no model has a known age', () => {
    // Reporting "oldest: 0 days" for a fleet of entirely unknown-age models would be a lie.
    const s = summarizeFleetAge([mk({ age_days: null })]);
    expect(s.oldestDays).toBeNull();
    expect(s.medianDays).toBeNull();
  });

  it('handles an empty fleet without throwing', () => {
    const s = summarizeFleetAge([]);
    expect(s).toMatchObject({ fresh: 0, stale: 0, unknown: 0, total: 0, oldestDays: null, medianDays: null });
  });

  it('counts suppressed models independently of age', () => {
    // Suppression and staleness are orthogonal: a model trained today can be suppressed, and a
    // stale model can still be contributing.
    const s = summarizeFleetAge([
      mk({ age_days: 0, oos_suppressed: true }),
      mk({ age_days: 90, oos_suppressed: false }),
    ]);
    expect(s.suppressed).toBe(1);
    expect(s.fresh).toBe(1);
    expect(s.stale).toBe(1);
  });

  it('reproduces the live 2026-09-07 fleet shape', () => {
    // Anchored to real measured production numbers rather than invented ones.
    const models = [
      ...Array.from({ length: 428 }, () => mk({ age_days: 0 })),
      ...Array.from({ length: 63 }, () => mk({ age_days: 45 })),
      ...Array.from({ length: 90 }, () => mk({ age_days: null })),
    ];
    const s = summarizeFleetAge(models);
    expect(s.total).toBe(581);
    expect(s.dated).toBe(491);   // matches the 491 artifacts carrying trained_at
    expect(s.unknown).toBe(90);  // matches the 90 pre-Tier-21 bundles
    expect(s.stale).toBe(63);    // matches the 63 measured >30d
  });
});

describe('formatAge', () => {
  it('renders a genuine 0 as "today", never as "unknown"', () => {
    expect(formatAge(0)).toBe('today');
  });
  it('renders null/undefined as "unknown", never as "0d"', () => {
    expect(formatAge(null)).toBe('unknown');
    expect(formatAge(undefined)).toBe('unknown');
  });
  it('renders ordinary ages', () => {
    expect(formatAge(1)).toBe('1d');
    expect(formatAge(82)).toBe('82d');
  });
});

describe('ageColor', () => {
  it('flags unknown age with the SAME warning color as stale', () => {
    // An unknown-age bundle predates trained_at, so it is necessarily old — showing it
    // neutrally would understate a real problem.
    expect(ageColor(null)).toBe(ageColor(STALE_AGE_DAYS + 1));
  });
  it('greens a genuine 0 rather than treating it as missing', () => {
    expect(ageColor(0)).toBe('#4ade80');
  });
  it('escalates green -> amber -> red as age grows', () => {
    expect(ageColor(3)).toBe('#4ade80');
    expect(ageColor(14)).toBe('#fbbf24');
    expect(ageColor(60)).toBe('#f87171');
  });
});
