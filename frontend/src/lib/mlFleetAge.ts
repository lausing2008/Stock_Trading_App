/**
 * AUD-MLAGE — model-fleet freshness summary for the ML Model Accuracy panel.
 *
 * In lib/ rather than inline because this repo has no component-level React test harness (same
 * T270 precedent as optionsTabCharts.ts / regimeThresholds.ts), and because the null-handling
 * here is genuinely easy to get wrong in a way that misreports the fleet.
 *
 * ── The distinction this file exists to protect ─────────────────────────────────────────────
 *
 * `age_days` has THREE meaningful states, and collapsing any two of them produces a wrong
 * answer on the live fleet:
 *
 *   - a number  -> real, known age
 *   - null      -> UNKNOWN age. The bundle predates trained_at (added Tier 21, 2026-06-15), so
 *                  it is necessarily OLDER than that date. Measured live 2026-09-07: 90 of 581
 *                  artifacts (~15%) are in this state.
 *   - 0         -> genuinely trained TODAY.
 *
 * Treating null as 0 would report the fleet's very oldest models as the freshest — exactly
 * inverted. Treating 0 as null (a truthiness check on a numeric, the falsy-zero class this
 * codebase has fixed repeatedly) would drop every model trained today from the counts, and
 * "trained today" is the single most common state right after a retrain.
 *
 * So `unknown` is counted and surfaced as its OWN category, never folded into stale or fresh.
 */

import type { MlModelMetric } from './api';

/** Age beyond which predict_latest() itself logs a staleness warning (trainer.py). */
export const STALE_AGE_DAYS = 30;

export type FleetAgeSummary = {
  /** Models with a known age <= STALE_AGE_DAYS. */
  fresh: number;
  /** Models with a known age > STALE_AGE_DAYS. */
  stale: number;
  /** Models with NO trained_at at all — age unknowable, but necessarily old. */
  unknown: number;
  /** Models currently suppressed at inference (substituted with a neutral 0.5). */
  suppressed: number;
  /** Oldest known age in days, or null when no model has a known age. */
  oldestDays: number | null;
  /** Median known age in days, or null when no model has a known age. */
  medianDays: number | null;
  /** Count of models whose age is known at all (fresh + stale). */
  dated: number;
  total: number;
};

export function summarizeFleetAge(models: MlModelMetric[]): FleetAgeSummary {
  let fresh = 0, stale = 0, unknown = 0, suppressed = 0;
  const ages: number[] = [];

  for (const m of models) {
    if (m.oos_suppressed) suppressed++;
    const a = m.age_days;
    // Explicit null/undefined check — NOT `if (!a)`, which would misclassify a genuine 0.
    if (a == null || !Number.isFinite(a)) {
      unknown++;
      continue;
    }
    ages.push(a);
    if (a > STALE_AGE_DAYS) stale++;
    else fresh++;
  }

  ages.sort((x, y) => x - y);
  return {
    fresh,
    stale,
    unknown,
    suppressed,
    oldestDays: ages.length ? ages[ages.length - 1] : null,
    medianDays: ages.length ? ages[Math.floor(ages.length / 2)] : null,
    dated: ages.length,
    total: models.length,
  };
}

/** Short human label for one model's age cell. */
export function formatAge(ageDays: number | null | undefined): string {
  if (ageDays == null || !Number.isFinite(ageDays)) return 'unknown';
  if (ageDays === 0) return 'today';
  if (ageDays === 1) return '1d';
  return `${ageDays}d`;
}

/**
 * Color for an age cell. Unknown is deliberately given the SAME warning treatment as stale —
 * an unknown-age model is necessarily older than 2026-06-15, so presenting it neutrally would
 * understate a real problem.
 */
export function ageColor(ageDays: number | null | undefined): string {
  if (ageDays == null || !Number.isFinite(ageDays)) return '#f87171';
  if (ageDays > STALE_AGE_DAYS) return '#f87171';
  if (ageDays > 7) return '#fbbf24';
  return '#4ade80';
}
