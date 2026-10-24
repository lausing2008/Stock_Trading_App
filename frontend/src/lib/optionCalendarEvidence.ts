/** Calendar retrieval policy matches the existing 24-hour fundamentals cache TTL.
 * Retrieval freshness does not confirm the provider's event date or full calendar coverage. */
export const OPTION_CALENDAR_MAX_AGE_HOURS = 24;
export type OptionCalendarContext = {
  symbol: string; retrievedAt?: string | null;
  earningsDate?: string | null; exDividendDate?: string | null;
};
export function calendarDay(value: unknown): number | null {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) && new Date(parsed).toISOString().slice(0, 10) === value ? parsed : null;
}
export function reviewOptionCalendar(input: OptionCalendarContext | undefined, symbol: string, now: number) {
  const raw = input?.retrievedAt;
  const dated = typeof raw === 'string' && /T.*(?:Z|[+-]\d{2}:\d{2})$/i.test(raw) && calendarDay(raw.slice(0, 10)) !== null;
  const retrieved = dated ? Date.parse(raw!) : NaN;
  const ageHours = (now - retrieved) / 3_600_000;
  const status = !input ? 'missing' : typeof input.symbol !== 'string' || !symbol || input.symbol.toUpperCase() !== symbol.toUpperCase() ? 'mismatched'
    : !raw ? 'undated' : !dated ? 'timezone_or_date_unverified' : !Number.isFinite(retrieved) || !Number.isFinite(now) ? 'invalid'
    : ageHours < 0 ? 'future' : ageHours > OPTION_CALENDAR_MAX_AGE_HOURS ? 'stale' : 'current';
  const labels = {
    missing: 'Calendar unavailable', mismatched: 'Calendar belongs to another symbol', undated: 'Calendar retrieval time unavailable',
    timezone_or_date_unverified: 'Calendar retrieval timezone or date unverified', invalid: 'Calendar retrieval time unreadable',
    future: 'Calendar retrieval time is in the future', stale: 'Calendar retrieved over 24 hours ago', current: 'Calendar retrieved within 24 hours',
  };
  return { status, label: labels[status], current: status === 'current', retrievedAt: raw ?? null,
    earningsDate: input?.earningsDate ?? null, exDividendDate: input?.exDividendDate ?? null };
}
export function optionEventWindow(calendar: ReturnType<typeof reviewOptionCalendar>, event: string | null,
  expiry: string | null | undefined, today: string, holdingDays?: number | null) {
  if (!calendar.current) return { code: 'unknown', label: 'Unverified — calendar retrieval is not current and matched' };
  if (!event) return { code: 'missing', label: 'Date not reported; event coverage unknown' };
  const date = calendarDay(event), start = calendarDay(today), end = calendarDay(expiry);
  if (date === null) return { code: 'invalid', label: 'Reported date unreadable; event coverage unknown' };
  if (start === null || end === null || end <= start) return { code: 'unknown', label: 'Contract window unavailable' };
  if (date < start) return { code: 'past', label: 'Past reported date; next event unknown' };
  if (date > end) return { code: 'after_expiry', label: 'Reported date after expiry; coverage unverified' };
  if (holdingDays != null && Number.isInteger(holdingDays) && holdingDays >= 1 && holdingDays <= 365) {
    if (date <= start + holdingDays * 86_400_000) return { code: 'in_hold', label: 'Reported within planned holding period and by expiry' };
    return { code: 'before_expiry', label: 'Reported after planned hold, but by expiry' };
  }
  return { code: 'before_expiry', label: 'Reported by expiry; declare holding period for comparison' };
}
