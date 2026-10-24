import type { OptionStrategyLeg } from './api';
import { calendarDay } from './optionCalendarEvidence';
export type OptionSearchPreferences = { min_dte: number | null; max_dte: number | null; min_abs_delta: number | null; max_abs_delta: number | null };
export const EMPTY_OPTION_PREFERENCES: OptionSearchPreferences = { min_dte: null, max_dte: null, min_abs_delta: null, max_abs_delta: null };
export function parseOptionPreferences(text: Record<keyof OptionSearchPreferences, string>) {
  const values = { ...EMPTY_OPTION_PREFERENCES };
  for (const key of Object.keys(values) as (keyof OptionSearchPreferences)[]) {
    const raw = text[key].trim();
    if (!raw) continue;
    const value = Number(raw), dte = key.endsWith('dte');
    if (!Number.isFinite(value) || value < (dte ? 1 : 0) || value > (dte ? 1095 : 1) || (dte && !Number.isInteger(value))) {
      return { values, error: dte ? 'Days-to-expiry bounds must be whole calendar days from 1 to 1095.' : 'Absolute delta bounds must be numbers from 0 to 1.' };
    }
    values[key] = value;
  }
  if (values.min_dte != null && values.max_dte != null && values.min_dte > values.max_dte) return { values, error: 'Minimum days to expiry cannot exceed maximum.' };
  if (values.min_abs_delta != null && values.max_abs_delta != null && values.min_abs_delta > values.max_abs_delta) return { values, error: 'Minimum absolute delta cannot exceed maximum.' };
  return { values, error: null };
}
export function optionPreferenceBlocker(leg: OptionStrategyLeg | undefined, values: OptionSearchPreferences, today: string) {
  if (Object.values(values).every(v => v === null)) return null;
  if (!leg) return 'Search preferences: no contract is available.';
  if (values.min_dte != null || values.max_dte != null) {
    const end = calendarDay(leg.expiry), start = calendarDay(today);
    if (end === null || start === null) return 'Search preferences: expiry is unreadable.';
    const dte = (end - start) / 86_400_000;
    if ((values.min_dte != null && dte < values.min_dte) || (values.max_dte != null && dte > values.max_dte)) return 'Search preferences: contract is outside the declared days-to-expiry band.';
  }
  if (values.min_abs_delta != null || values.max_abs_delta != null) {
    const delta = leg.delta;
    if (typeof delta !== 'number' || !Number.isFinite(delta) || Math.abs(delta) > 1) return 'Search preferences: delta is unavailable or invalid.';
    if ((leg.right === 'call' && delta < 0) || (leg.right === 'put' && delta > 0)) return 'Search preferences: delta sign does not match the contract right.';
    if ((values.min_abs_delta != null && Math.abs(delta) < values.min_abs_delta) || (values.max_abs_delta != null && Math.abs(delta) > values.max_abs_delta)) return 'Search preferences: contract is outside the declared absolute-delta range.';
  }
  return null;
}
