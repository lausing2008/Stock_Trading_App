/**
 * AUD-HORIZONCOMPARE-BEARREGIME — bear-regime BUY threshold derivation.
 *
 * Lives in lib/ rather than inside horizon-compare.tsx because this repo has no
 * component-level React test harness (same reasoning as optionsTabCharts.ts / T270): pure
 * logic here is the only way it gets real test coverage. That matters more than usual for this
 * function, because it REIMPLEMENTS BACKEND LOGIC on the frontend and would otherwise drift
 * silently — see the sync note on DYN_BOUNDS below.
 *
 * ── What this reproduces, and why it is needed ──────────────────────────────────────────────
 *
 * signal-engine's _get_dynamic_buy_threshold() (signals.py) does NOT apply a live Redis BUY
 * threshold as a flat regime-agnostic number. It applies it as a DELTA FROM THE BULL BASELINE
 * to whichever regime is active, then clips to _DYNAMIC_BUY_THRESHOLD_BOUNDS:
 *
 *     delta       = redis_value - profile.buy_threshold["bull"]
 *     effective   = clip(profile.buy_threshold[regime] + delta, 0.55, 0.85)
 *
 * That is deliberate (T232-CAL2): the calibrated value is fit mostly on bull-market samples, so
 * overriding all regimes with one flat number would destroy the regime tiering that keeps bear
 * tighter than bull. The consequence is that ANY tuning of the BUY threshold silently moves the
 * bear-regime threshold too.
 *
 * GET /tune_status exposes only `buy_threshold_bull`, so the horizon-compare page previously
 * showed the bear row as a static hardcoded default and its legend told users the system "only
 * auto-tunes the bull-regime BUY threshold." Both were wrong. Measured live 2026-09-07, every
 * horizon's bear row was misreported:
 *
 *     SHORT   68% shown -> 60% actual   (-8pp)
 *     SWING   76% shown -> 69% actual   (-7pp)
 *     LONG    70% shown -> 65% actual   (-5pp)
 *     GROWTH  68% shown -> 76% actual   (+8pp)
 *
 * GROWTH is the one that matters most: the page understated the real bear threshold by 8pp,
 * i.e. displayed the gate as LOOSER than it actually is.
 *
 * The bear value is recoverable from the bull value alone precisely because the delta is
 * defined against a KNOWN CONSTANT (the bull baseline), not against live state.
 */

export type Horizon = 'SHORT' | 'SWING' | 'LONG' | 'GROWTH';

/**
 * Hardcoded baselines from signals.py _STYLE_PROFILES[style]["buy_threshold"].
 *
 * KEEP IN SYNC with signals.py. These are the DEFAULT (untuned) values — they are inputs to the
 * delta computation, never the displayed answer. If a profile's bull/bear default changes in
 * signals.py without changing these, this function returns a wrong bear threshold. The tests
 * pin the exact live-measured cases so such a drift fails loudly rather than silently.
 */
export const BULL_BASE: Record<Horizon, number> = { SHORT: 0.63, SWING: 0.72, LONG: 0.60, GROWTH: 0.60 };
export const BEAR_BASE: Record<Horizon, number> = { SHORT: 0.68, SWING: 0.76, LONG: 0.70, GROWTH: 0.68 };

/** Mirrors signals.py _DYNAMIC_BUY_THRESHOLD_BOUNDS exactly. KEEP IN SYNC. */
export const DYN_BOUNDS: readonly [number, number] = [0.55, 0.85];

/**
 * Derive the live bear-regime BUY threshold from the live bull-regime one.
 *
 * @param h             horizon/style
 * @param effectiveBull the `effective.buy_threshold_bull` value from GET /tune_status
 * @returns the effective bear threshold as a 0-1 fraction, or null when the bull value is absent
 */
export function bearThresholdFor(h: Horizon, effectiveBull: number | null | undefined): number | null {
  if (effectiveBull == null || !Number.isFinite(effectiveBull)) return null;
  const delta = effectiveBull - BULL_BASE[h];
  const [lo, hi] = DYN_BOUNDS;
  return Math.min(hi, Math.max(lo, BEAR_BASE[h] + delta));
}
