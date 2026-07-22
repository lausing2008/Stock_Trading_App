/**
 * Shared risk:reward computation — consolidates two independent copies (PositionSizer.tsx's
 * AUD-POSITIONSIZER-INVERTEDRR fix and PriceChart.tsx's AUD-CHART-INVERTEDRR fix) that had the
 * exact same direction-validity bug fixed separately, in parallel, with no shared source
 * (AUD-DUPLOGIC).
 *
 * Both call sites take an externally-supplied entry/stop/target (a Game Plan value, an analyst
 * target, an ATR-based stop) that can legitimately land on the WRONG side of entry for the
 * direction the stop implies (e.g. an analyst target below current price drawn as if it were a
 * bullish take-profit) — naively taking Math.abs() of both legs produces a positive-looking R:R
 * even when the setup doesn't actually make sense. Direction is inferred from stop vs. entry
 * (neither tool has an explicit long/short toggle): a long setup (stop < entry) requires
 * target > entry to have a real reward leg; a short setup (stop > entry) requires target < entry.
 *
 * NOTE: frontend/src/lib/fvgTradePlan.ts's own Math.abs()-based R:R is NOT the same bug class —
 * its target is DERIVED from entry ± risk*minRR based on the gap's own kind, so it's
 * mathematically guaranteed to land on the correct side by construction; there's no external
 * target that could be on the wrong side to guard against. Left as its own independent
 * (correctly non-duplicated) computation.
 */

export type RiskRewardInput = {
  entry: number | null | undefined;
  stop: number | null | undefined;
  target: number | null | undefined;
};

export type RiskRewardResult = {
  /** True if `stop` is below `entry` (long setup) — the direction the position implies. */
  isLong: boolean;
  /** True if `target` sits on the correct side of `entry` for the inferred direction. */
  targetDirectionValid: boolean;
  /** abs(entry - stop), or null if entry/stop aren't usable. */
  riskPerShare: number | null;
  /** abs(target - entry), or null if entry/target aren't usable. */
  rewardPerShare: number | null;
  /** rewardPerShare / riskPerShare, or null if inputs are missing OR target is on the wrong side. */
  rr: number | null;
};

/**
 * Compute risk:reward from an entry/stop/target triple, correctly suppressing a misleading
 * ratio when target sits on the wrong side of entry for the direction the stop implies.
 */
export function computeRiskReward({ entry, stop, target }: RiskRewardInput): RiskRewardResult {
  const hasEntry = entry != null && entry > 0;
  const hasStop = stop != null && stop > 0;
  const hasTarget = target != null && target > 0;

  const isLong = hasStop && hasEntry ? (stop as number) < (entry as number) : true;
  const targetDirectionValid =
    hasTarget && hasEntry
      ? isLong
        ? (target as number) > (entry as number)
        : (target as number) < (entry as number)
      : true;

  const riskPerShare = hasEntry && hasStop ? Math.abs((entry as number) - (stop as number)) : null;
  const rewardPerShare = hasEntry && hasTarget ? Math.abs((target as number) - (entry as number)) : null;

  const rr =
    riskPerShare != null && rewardPerShare != null && riskPerShare > 0 && targetDirectionValid
      ? rewardPerShare / riskPerShare
      : null;

  return { isLong, targetDirectionValid, riskPerShare, rewardPerShare, rr };
}
