// OPTIONSTAB-B: pure data-shaping logic for the Options tab's three new charts, extracted so
// it's independently unit-testable without a component/DOM harness — this repo has none for
// page-level React components, so every existing frontend test covers pure logic pulled into
// lib/ (see optionsChainChart.ts, volumeProfile.ts, swingPivots.ts, riskReward.ts for the same
// established split).
//
// Why these three specifically: each renders data the app ALREADY fetches and types but
// currently shows only as a text list, or not at all —
//   1. GammaExposure.oi_per_strike[]  — cross-expiry OI distribution (MarketPressurePanel
//      renders only a top-N text list of it today). Distinct from OptionsChainChart, which is
//      single-expiry off /options-chain.
//   2. GammaExposure.max_pain[]       — an ARRAY over expiries; the stock page reads only [0].
//   3. OptionsExpirationRow[]         — OI/volume term structure, currently a text rollup.
//
// Every field on the upstream rows is nullable (see api.ts's OIPerStrikeRow/MaxPainRow), so
// each function below filters explicitly on `!= null` rather than truthiness — a genuine 0 OI
// or a strike of 0 must be distinguishable from "absent", the falsy-zero discipline this
// codebase has had to fix repeatedly.
import type { OIPerStrikeRow, MaxPainRow, OptionsExpirationRow } from './api';

// ── 1. Cross-expiry OI distribution by strike ────────────────────────────────────────────

export interface GexStrikePoint {
  strike: number;
  callOi: number;
  putOi: number;
}

/** One point per distinct strike, ascending, call/put OI summed per strike.
 *
 * Rows with a null strike are dropped (there is no meaningful x-position for them); a null
 * call_oi/put_oi on an otherwise-valid strike counts as 0 for THAT side only, so a strike with
 * real put OI and no reported call OI still plots. */
export function aggregateGexOiByStrike(rows: OIPerStrikeRow[] | undefined | null): GexStrikePoint[] {
  if (!rows || rows.length === 0) return [];
  const byStrike = new Map<number, { callOi: number; putOi: number }>();
  for (const r of rows) {
    if (r.strike == null) continue;
    const cur = byStrike.get(r.strike) ?? { callOi: 0, putOi: 0 };
    cur.callOi += r.call_oi ?? 0;
    cur.putOi += r.put_oi ?? 0;
    byStrike.set(r.strike, cur);
  }
  return Array.from(byStrike.entries())
    .map(([strike, v]) => ({ strike, callOi: v.callOi, putOi: v.putOi }))
    .sort((a, b) => a.strike - b.strike);
}

/** Largest single-side OI at any strike — the shared y-scale for a mirrored chart. Floored at
 * 1 so a division by it can never produce Infinity/NaN on an all-zero dataset. */
export function maxGexOi(points: GexStrikePoint[]): number {
  let max = 0;
  for (const p of points) {
    if (p.callOi > max) max = p.callOi;
    if (p.putOi > max) max = p.putOi;
  }
  return max || 1;
}

/** True when there is nothing worth drawing — no strikes at all, or every strike reporting
 * zero on BOTH sides. The second case is real and was a live bug in the sibling chart
 * (BUG-OPTIONSCHAINCHART-ALLZEROOI): open interest genuinely lags for a freshly-listed
 * near-term expiry, so many strikes resolve with oi=0 across the board, which a bare
 * length check renders as an unexplained empty grid. */
export function hasNoRealGexOi(points: GexStrikePoint[]): boolean {
  return points.length === 0 || points.every(p => p.callOi === 0 && p.putOi === 0);
}

// ── 2. Max pain across expiries ──────────────────────────────────────────────────────────

export interface MaxPainPoint {
  expiry: string;
  maxPain: number;
}

/** Chronological max-pain-by-expiry series. Rows missing either field are dropped — a
 * max-pain point needs both an x (expiry) and a y (strike) to mean anything.
 *
 * Sorted lexicographically, which is correct for the ISO YYYY-MM-DD strings this API returns
 * and avoids constructing Date objects (whose parsing is locale/timezone-sensitive and would
 * be a needless source of off-by-one-day bugs for a pure ordering task). */
export function buildMaxPainSeries(rows: MaxPainRow[] | undefined | null): MaxPainPoint[] {
  if (!rows || rows.length === 0) return [];
  return rows
    .filter((r): r is { expiry: string; max_pain: number } => r.expiry != null && r.max_pain != null)
    .map(r => ({ expiry: r.expiry, maxPain: r.max_pain }))
    .sort((a, b) => (a.expiry < b.expiry ? -1 : a.expiry > b.expiry ? 1 : 0));
}

/** [min, max] of the max-pain values, padded by `padPct` so a flat or near-flat series still
 * renders as a visible line rather than collapsing onto the axis. Returns null for an empty
 * series so the caller can render its own empty state. */
export function maxPainRange(points: MaxPainPoint[], padPct = 0.05): { min: number; max: number } | null {
  if (points.length === 0) return null;
  let min = points[0].maxPain;
  let max = points[0].maxPain;
  for (const p of points) {
    if (p.maxPain < min) min = p.maxPain;
    if (p.maxPain > max) max = p.maxPain;
  }
  if (min === max) {
    // A single point, or every expiry sharing one max-pain strike — both real. Pad around the
    // value so it lands mid-plot instead of on the boundary.
    const pad = Math.abs(min) * padPct || 1;
    return { min: min - pad, max: max + pad };
  }
  const pad = (max - min) * padPct;
  return { min: min - pad, max: max + pad };
}

// ── 3. OI term structure by expiry ───────────────────────────────────────────────────────

export interface TermStructurePoint {
  expiry: string;
  callOi: number;
  putOi: number;
  totalOi: number;
  putCallOiRatio: number | null;
  concentrationPct: number;
  level: OptionsExpirationRow['level'];
}

/** Term-structure series, chronological, optionally capped to the nearest `limit` expiries.
 *
 * The cap exists because a liquid underlying can carry 20+ listed expiries stretching years
 * out, and the far ones are both illiquid and visually squash the near-dated ones that
 * actually matter. Capping keeps the NEAREST expiries (the informative end) rather than an
 * arbitrary slice. */
export function buildTermStructure(
  rows: OptionsExpirationRow[] | undefined | null,
  limit = 10,
): TermStructurePoint[] {
  if (!rows || rows.length === 0) return [];
  const sorted = [...rows].sort((a, b) => (a.expiry < b.expiry ? -1 : a.expiry > b.expiry ? 1 : 0));
  const capped = limit > 0 ? sorted.slice(0, limit) : sorted;
  return capped.map(r => ({
    expiry: r.expiry,
    callOi: r.call_oi,
    putOi: r.put_oi,
    totalOi: r.total_oi,
    putCallOiRatio: r.put_call_oi_ratio,
    concentrationPct: r.concentration_pct,
    level: r.level,
  }));
}

/** Largest total OI across the series — the y-scale for a stacked call/put bar chart. Floored
 * at 1 for the same divide-by-zero reason as maxGexOi(). */
export function maxTermStructureOi(points: TermStructurePoint[]): number {
  let max = 0;
  for (const p of points) {
    const total = p.callOi + p.putOi;
    if (total > max) max = total;
  }
  return max || 1;
}

// ── Shared formatting/layout helpers ─────────────────────────────────────────────────────

/** Compact OI/volume label: 12345 -> "12.3K", 1234567 -> "1.2M". Matches
 * optionsChainChart.ts's own fmtOi thresholds so the two charts label consistently. */
export function fmtCompact(n: number): string {
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(Math.round(n));
}

/** Shorten an ISO date to a compact axis label: "2026-09-18" -> "Sep 18".
 * Returns the input unchanged if it isn't the expected shape, rather than throwing or
 * rendering "Invalid Date". */
export function fmtExpiryLabel(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const monthIdx = parseInt(m[2], 10) - 1;
  if (monthIdx < 0 || monthIdx > 11) return iso;
  return `${MONTHS[monthIdx]} ${parseInt(m[3], 10)}`;
}

/** Every Nth x-axis label to draw so they don't overlap, given how many points there are and
 * roughly how many labels fit. Always >= 1 — a step of 0 would make `i % step` NaN-y and
 * silently render no labels at all (the exact gap a T270 sabotage test caught late). */
export function labelStepFor(count: number, maxLabels = 12): number {
  return Math.max(1, Math.ceil(count / maxLabels));
}
