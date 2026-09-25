/**
 * U01: formatting a CALENDAR DATE without letting the viewer's timezone move it.
 *
 * THE BUG THIS EXISTS TO STOP. `new Date("2026-09-25")` parses a date-only ISO string as
 * MIDNIGHT UTC — that is what the spec requires — and `toLocaleDateString()` then converts that
 * instant into the viewer's zone. In America/Los_Angeles that instant is 17:00 on the 24th, so
 * an option expiring on September 25 rendered as "Sep 24, 26". Browser-confirmed at 1,280 and
 * 390 CSS pixels.
 *
 * An expiry, a settlement session, an earnings date and a disclosure date are CALENDAR dates:
 * they name a day, not a moment. Converting them between zones changes what they mean. A
 * timestamp (`fixed_at`, `added_at`, `taken_at`) is the opposite case — a real instant, where
 * showing it in the viewer's zone is correct — so those must keep using `new Date(...)`
 * directly and are deliberately NOT routed through here.
 *
 * Implemented by reading the year/month/day out of the string and building a LOCAL date from
 * the components, so the rendered day is the day that was supplied, in every zone. The
 * alternative — `timeZone: 'UTC'` — also works, but silently reverts to a shifted day the
 * moment a caller omits the option, and this way the guarantee lives in one function.
 */

const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})/;

export type CalendarDateStyle = 'short' | 'medium';

const STYLES: Record<CalendarDateStyle, Intl.DateTimeFormatOptions> = {
  // "Sep 25, 26" — the options-income card's existing shape.
  short: { month: 'short', day: 'numeric', year: '2-digit' },
  // "Sep 25, 2026"
  medium: { month: 'short', day: 'numeric', year: 'numeric' },
};

/**
 * Format a date-only value (`YYYY-MM-DD`, or an ISO timestamp whose DATE part is what matters)
 * without any timezone shift. Returns the em-dash placeholder for null/undefined/empty, and
 * echoes the input unchanged when it cannot be parsed — never a silent "Invalid Date", which
 * `new Date()` produces without throwing.
 */
export function formatCalendarDate(
  value: string | null | undefined,
  style: CalendarDateStyle = 'short',
): string {
  if (!value) return '—';
  const m = DATE_ONLY.exec(value);
  if (!m) return value;
  const [, y, mo, d] = m;
  const local = new Date(Number(y), Number(mo) - 1, Number(d));
  if (Number.isNaN(local.getTime())) return value;
  return local.toLocaleDateString(undefined, STYLES[style]);
}
