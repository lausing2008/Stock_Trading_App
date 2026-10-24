import type { OptionStrategy } from './api';
import { calendarDay } from './optionCalendarEvidence';
export type CandidateSort = 'server' | 'spread' | 'oi' | 'capital' | 'expiry';
export type CandidateComparisonRow = { sourceIndex: number; strategy: OptionStrategy; dte: number | null };
export function nonnegativeMeasurement(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
}
export function candidateDte(expiry: unknown, today: string): number | null {
  const end = calendarDay(expiry), start = calendarDay(today);
  return end === null || start === null ? null : (end - start) / 86_400_000;
}
export function compareCandidateRows(alternatives: OptionStrategy[], sort: CandidateSort, today: string): CandidateComparisonRow[] {
  const rows = alternatives.map((strategy, sourceIndex) => ({ strategy, sourceIndex, dte: candidateDte(strategy.legs[0]?.expiry, today) }));
  if (sort === 'server') return rows;
  const metric = (row: CandidateComparisonRow) => {
    const leg = row.strategy.legs[0];
    if (sort === 'expiry') return row.dte;
    if (sort === 'capital') {
      const capital = nonnegativeMeasurement(row.strategy.collateral_per_contract);
      return capital !== null && capital > 0 ? capital : null;
    }
    if (sort === 'oi') {
      const oi = nonnegativeMeasurement(leg?.oi);
      return oi !== null && Number.isInteger(oi) ? oi : null;
    }
    return nonnegativeMeasurement(leg?.spread_pct);
  };
  return rows.sort((a, b) => {
    const x = metric(a), y = metric(b);
    if (x === null || y === null) {
      if (x !== y) return x === null ? 1 : -1;
    } else if (x !== y) return sort === 'oi' ? y - x : x - y;
    const left = a.strategy.legs[0], right = b.strategy.legs[0];
    const identity = [left?.expiry ?? '', left?.strike ?? '', left?.option_symbol ?? ''].join('|');
    const other = [right?.expiry ?? '', right?.strike ?? '', right?.option_symbol ?? ''].join('|');
    return identity < other ? -1 : identity > other ? 1 : a.sourceIndex - b.sourceIndex;
  });
}
