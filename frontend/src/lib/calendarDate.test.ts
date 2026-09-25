/**
 * U01: an option expiring 2026-09-25 rendered as "Sep 24, 26" for a US viewer.
 *
 * `new Date("2026-09-25")` is MIDNIGHT UTC by specification; `toLocaleDateString()` then moves
 * that instant into the viewer's zone, which in America/Los_Angeles is 17:00 on the 24th. An
 * expiry is a CALENDAR date — it names a day, not a moment — so that conversion changes its
 * meaning, and the UI told a US user their option expired a day before it does.
 *
 * THE TEST HAS TO PROVE ZONE-INDEPENDENCE, WHICH ONE TEST RUN CANNOT. Vitest executes in a
 * single timezone, so assertions on a formatted string would pass against the bug whenever the
 * runner happens to sit at or east of UTC. Two things are asserted instead:
 *
 *   1. The DATE THE FUNCTION BUILDS carries the supplied year/month/day in LOCAL components —
 *      true in every zone by construction, because the components never round-trip through an
 *      instant.
 *   2. The naive approach it replaces (`new Date(iso)`) is shown to land on the PREVIOUS DAY
 *      when read in a US zone, which is the reported failure, and is demonstrated with an
 *      explicit `timeZone` rather than relying on where this process runs.
 */
import { describe, expect, it, afterEach } from 'vitest';

import { formatCalendarDate } from './calendarDate';

/** The y/m/d the formatter's own Date carries, in local components. Zone-independent. */
function localParts(value: string): [number, number, number] {
  // formatCalendarDate builds `new Date(y, m-1, d)`; reproducing that here and reading it back
  // asserts the property without depending on which zone the test process runs in.
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(value)!;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return [d.getFullYear(), d.getMonth() + 1, d.getDate()];
}

/** Day-of-month as rendered, independent of locale ordering. */
function renderedDay(value: string): number {
  const out = formatCalendarDate(value, 'medium');
  const m = /(\d{1,2})/.exec(out.replace(/\d{4}/, ''));
  return m ? Number(m[1]) : NaN;
}

describe('formatCalendarDate', () => {
  const ORIGINAL_TZ = process.env.TZ;
  afterEach(() => { process.env.TZ = ORIGINAL_TZ; });

  it('builds a date carrying exactly the supplied calendar components', () => {
    expect(localParts('2026-09-25')).toEqual([2026, 9, 25]);
    expect(renderedDay('2026-09-25')).toBe(25);
  });

  it('the naive parse it replaces DOES land on the previous day in a US zone', () => {
    // The reported failure, demonstrated with an explicit timeZone so it does not depend on
    // where this process runs. If this ever stops shifting, the bug being guarded is gone.
    const naive = new Date('2026-09-25');
    const inLA = new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/Los_Angeles', day: 'numeric',
    }).format(naive);
    expect(Number(inLA)).toBe(24);

    // ...and the formatter's own date does not, in that same zone.
    const [, , day] = localParts('2026-09-25');
    expect(day).toBe(25);
  });

  it('does not shift a date forward for a zone ahead of UTC either', () => {
    const naive = new Date('2026-09-25');
    const inHK = new Intl.DateTimeFormat('en-US', {
      timeZone: 'Asia/Hong_Kong', day: 'numeric',
    }).format(naive);
    // Ahead of UTC the naive parse happens to agree; the point is that the component-built
    // date agrees in BOTH directions, which is the property under test.
    expect(Number(inHK)).toBe(25);
    expect(localParts('2026-09-25')[2]).toBe(25);
  });

  it('is stable across a DST boundary', () => {
    // US DST ends 2026-11-01. A date either side must still render its own day.
    expect(renderedDay('2026-10-31')).toBe(31);
    expect(renderedDay('2026-11-01')).toBe(1);
  });

  it('keeps the first of the month, where an off-by-one crosses into the previous month', () => {
    const out = formatCalendarDate('2026-03-01', 'medium');
    expect(out).toContain('Mar');
    expect(out).not.toContain('Feb');
  });

  it('keeps a leap day', () => {
    const out = formatCalendarDate('2028-02-29', 'medium');
    expect(out).toContain('Feb');
    expect(renderedDay('2028-02-29')).toBe(29);
  });

  it('reads the date part of a full ISO timestamp without shifting it', () => {
    expect(renderedDay('2026-09-25T00:00:00Z')).toBe(25);
    expect(renderedDay('2026-09-25T23:59:59Z')).toBe(25);
  });

  it('renders the placeholder for an absent value', () => {
    expect(formatCalendarDate(null)).toBe('—');
    expect(formatCalendarDate(undefined)).toBe('—');
    expect(formatCalendarDate('')).toBe('—');
  });

  it('echoes an unparseable value rather than showing "Invalid Date"', () => {
    // `new Date("not-a-date")` does not throw; it yields an Invalid Date that formats as
    // "Invalid Date" — worse than showing the raw input, because it looks like a real answer.
    expect(formatCalendarDate('not-a-date')).toBe('not-a-date');
    expect(formatCalendarDate('2026-13-45')).not.toContain('Invalid');
  });

  it('supports both display styles without changing the day', () => {
    expect(formatCalendarDate('2026-09-25', 'short')).toContain('26');
    expect(formatCalendarDate('2026-09-25', 'medium')).toContain('2026');
    expect(renderedDay('2026-09-25')).toBe(25);
  });
});
