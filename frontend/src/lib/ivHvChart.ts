// T411-IVHV: pure geometry and formatting for the IV vs HV chart, extracted so it is
// independently unit-testable without a DOM harness — this repo has no component-level test
// setup, and every existing frontend test covers pure logic lifted into lib/ (see
// optionsChainChart.ts, volumeProfile.ts). The component is then only markup.
//
// The one thing this file exists to get right is GAPS. Both series have holes in them for
// legitimate reasons: HV emits nothing until a full 20-session window exists, Unusual Whales
// can be missing a day, and the two sources do not agree on which days are trading days. A
// naive polyline drawn straight through a hole invents a measurement that was never taken and
// draws it as confidently as a real one — so a gap must BREAK the line, not bridge it.

export interface IvHvPoint {
  date: string;
  iv: number | null;
  hv: number | null;
  iv_rank_1y?: number | null;
  close?: number | null;
}

export interface IvHvResponse {
  symbol: string;
  available: boolean;
  reason?: string;
  hv_window_sessions?: number;
  points?: IvHvPoint[];
  latest?: IvHvPoint | null;
  iv_minus_hv?: number | null;
  iv_points?: number;
  hv_points?: number;
}

/** Contiguous runs of real readings. Each run becomes its own polyline, so a missing day
 * leaves a visible break instead of a straight line through data that does not exist. */
export function segments(
  points: IvHvPoint[],
  key: 'iv' | 'hv',
): { i: number; value: number }[][] {
  const out: { i: number; value: number }[][] = [];
  let run: { i: number; value: number }[] = [];
  points.forEach((p, i) => {
    const v = p[key];
    if (v == null || !Number.isFinite(v)) {
      if (run.length) out.push(run);
      run = [];
      return;
    }
    run.push({ i, value: v });
  });
  if (run.length) out.push(run);
  return out;
}

/** Vertical bounds across BOTH series, padded by 8%.
 *
 * Deliberately a shared scale: the whole read of this chart is where one line sits relative to
 * the other, and separately-scaled axes would let IV look higher than HV in a window where it
 * was lower throughout. Returns a usable band even for a single flat reading, so the line
 * renders mid-chart rather than collapsing onto an edge. */
export function volBounds(points: IvHvPoint[]): { min: number; max: number } {
  const vals: number[] = [];
  for (const p of points) {
    if (p.iv != null && Number.isFinite(p.iv)) vals.push(p.iv);
    if (p.hv != null && Number.isFinite(p.hv)) vals.push(p.hv);
  }
  if (!vals.length) return { min: 0, max: 1 };
  let min = Math.min(...vals);
  let max = Math.max(...vals);
  if (max === min) {
    const pad = Math.max(Math.abs(max) * 0.1, 0.01);
    return { min: min - pad, max: max + pad };
  }
  const pad = (max - min) * 0.08;
  min -= pad;
  max += pad;
  // Volatility cannot be negative; clamping the floor stops the padding from implying it can.
  return { min: Math.max(0, min), max };
}

/** A vol fraction as a percentage string. 0.2473 -> "24.7%". */
export function fmtVol(v: number | null | undefined, digits = 1): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return `${(v * 100).toFixed(digits)}%`;
}

/** The IV-minus-HV spread, signed, in percentage POINTS — not a percentage of anything.
 * 0.247 - 0.180 renders "+6.7 pts", which is the honest unit for a difference of two rates. */
export function fmtSpread(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  const pts = v * 100;
  return `${pts >= 0 ? '+' : ''}${pts.toFixed(1)} pts`;
}

/** How to describe the current gap in words.
 *
 * Returns a DESCRIPTION, never a recommendation. The thresholds are a presentation choice about
 * when a gap is large enough to be worth a reader's attention, not a validated edge, and
 * nothing downstream trades on them. */
export function spreadRead(spread: number | null | undefined): {
  label: string;
  tone: 'rich' | 'cheap' | 'fair' | 'unknown';
} {
  if (spread == null || !Number.isFinite(spread)) {
    return { label: 'Not enough overlapping data', tone: 'unknown' };
  }
  const pts = spread * 100;
  if (pts >= 5) return { label: 'Options priced above realized movement', tone: 'rich' };
  if (pts <= -5) return { label: 'Options priced below realized movement', tone: 'cheap' };
  return { label: 'Implied and realized broadly in line', tone: 'fair' };
}

/** Evenly spaced date labels that always include the first and last point. `max` is a cap on
 * total labels, so a narrow chart on a phone does not overlap them into illegibility. */
export function dateTicks(points: IvHvPoint[], max = 6): number[] {
  const n = points.length;
  if (n === 0) return [];
  if (n <= max) return points.map((_, i) => i);
  const step = (n - 1) / (max - 1);
  const idx = new Set<number>();
  for (let k = 0; k < max; k++) idx.add(Math.round(k * step));
  return Array.from(idx).sort((a, b) => a - b);
}

/** "2026-09-28" -> "Sep 28". Built from the string rather than `new Date()` so it cannot shift
 * by a day in a timezone west of UTC — the dates here are trading days, not instants. */
export function shortDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const mi = parseInt(m[2], 10) - 1;
  if (mi < 0 || mi > 11) return iso;
  return `${months[mi]} ${parseInt(m[3], 10)}`;
}
