// T270-STOCKDETAIL-CALLPUT-CHART: pure data-aggregation logic for OptionsChainChart.tsx,
// extracted so it's independently unit-testable without needing a component/DOM test
// harness (this repo has none for page-level React components — every existing frontend
// test covers pure logic extracted into lib/, matching that established convention).
import type { OptionsChainRow } from './api';

export interface StrikeOiPoint {
  strike: number;
  callOi: number;
  putOi: number;
}

/** One point per distinct strike across BOTH sides, sorted ascending, OI summed per side
 * (a strike could in principle appear more than once in either array — summing rather than
 * assuming uniqueness keeps this correct either way). */
export function aggregateOiByStrike(calls: OptionsChainRow[], puts: OptionsChainRow[]): StrikeOiPoint[] {
  const strikes = Array.from(new Set([...calls.map(c => c.strike), ...puts.map(p => p.strike)])).sort((a, b) => a - b);
  const callOiByStrike = new Map<number, number>();
  for (const c of calls) callOiByStrike.set(c.strike, (callOiByStrike.get(c.strike) ?? 0) + c.oi);
  const putOiByStrike = new Map<number, number>();
  for (const p of puts) putOiByStrike.set(p.strike, (putOiByStrike.get(p.strike) ?? 0) + p.oi);
  return strikes.map(strike => ({
    strike,
    callOi: callOiByStrike.get(strike) ?? 0,
    putOi: putOiByStrike.get(strike) ?? 0,
  }));
}

/** The largest single OI value across either side, at any strike — used to scale bar
 * heights symmetrically above/below the zero line. Returns 1 (never 0) so callers can
 * safely divide by it without a NaN/Infinity guard of their own. */
export function maxOiAcrossStrikes(points: StrikeOiPoint[]): number {
  let max = 0;
  for (const p of points) max = Math.max(max, p.callOi, p.putOi);
  return max || 1;
}

/** Human-readable OI figure: 1_500_000 -> "1.5M", 42_000 -> "42K", 900 -> "900". */
export function fmtOi(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${Math.round(v / 1_000)}K`;
  return String(v);
}

/** Which strike indices should carry an x-axis label, so labels don't overlap when there
 * are many strikes — shows at most maxLabels evenly-spaced labels (always including the
 * first strike). */
export function labelStepFor(strikeCount: number, maxLabels = 14): number {
  return Math.max(1, Math.ceil(strikeCount / maxLabels));
}
