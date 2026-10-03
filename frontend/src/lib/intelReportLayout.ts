/** Reading order and grouping for a report. Pure, so it is testable without a DOM renderer.
 *
 *  THE PROBLEM THIS SOLVES. A flat, alphabetical field list made the screen a data inspector:
 *  the conclusions were hard to find, critical missing inputs sat among ordinary ones, and —
 *  worst — June's earnings results, October's closing price and today's BUY signal appeared in
 *  one undifferentiated sequence, so today's signal read as a prediction made before the
 *  results. */

export type TimeFrame = 'at_event' | 'current' | 'historical' | 'identity' | 'timeless';
export type Section = 'summary' | 'limitations' | 'event' | 'metrics' | 'interpretation'
                    | 'scenarios' | 'sources';

export type LayoutField = {
  state: string;
  reason?: string | null;
  timeframe?: TimeFrame;
  section?: Section;
  label?: string | null;
  statement?: string;
};

/** Reading order: what it shows, what is missing, then the detail. */
export const SECTION_ORDER: Section[] = [
  'summary', 'limitations', 'event', 'metrics', 'interpretation', 'scenarios', 'sources',
];

export const SECTION_TITLE: Record<Section, string> = {
  summary: 'Summary',
  limitations: 'Analysis limitations',
  event: 'What this report is about',
  metrics: 'Key figures',
  interpretation: 'Interpretation',
  scenarios: 'Conditional scenarios',
  sources: 'Sources and methodology',
};

/** Within a section, separate WHEN the evidence belongs to. */
export const TIMEFRAME_ORDER: TimeFrame[] = ['identity', 'at_event', 'historical', 'current', 'timeless'];

export const TIMEFRAME_TITLE: Record<TimeFrame, string> = {
  identity: '',
  // ABOUT the event, which is not the same as known before it: a consensus revised afterwards
  // is about this event and still carries its own availability warning.
  at_event: 'About this earnings event',
  historical: 'Previous periods',
  current: 'Current market context — as of today, not at the event',
  timeless: 'Method and limits',
};

/** A field's display label: the generator's own, or a readable fallback. */
export function fieldLabel(key: string, f?: LayoutField): string {
  if (f?.label) return f.label;
  const fallback: Record<string, string> = {
    price_as_of: 'Share price', ts: 'Timestamp', pct: 'Percent',
    return_1_bars: 'Return over 1 daily bar', return_5_bars: 'Return over 5 daily bars',
    return_20_bars: 'Return over 20 daily bars', return_63_bars: 'Return over 63 daily bars',
  };
  if (fallback[key]) return fallback[key];
  return key.replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase());
}

const STAGE_WORDS: Record<string, string> = {
  // "First flash" on a months-old event is actively misleading.
  FIRST_FLASH: 'Initial figures — no cross-source reconciliation performed',
  RECONCILED_RESULTS: 'Reconciled against a second source',
  CALL_UPDATE: 'Updated after the earnings call',
  SESSION_REVIEW: 'Reviewed after the following session',
};

/** Technical values a screen should never print raw.
 *
 *  READS BOTH SHAPES ON PURPOSE. This compared against a bare string while the generator had
 *  moved to `{stage, note}`, so the humaniser silently never fired and FIRST_FLASH kept
 *  rendering raw — a mismatch invisible to both sides' own tests, since each was self-consistent.
 */
export function humaniseValue(key: string, v: unknown): string | null {
  if (key !== 'stage') return null;
  const code = typeof v === 'string'
    ? v
    : (v && typeof v === 'object' ? (v as { stage?: unknown }).stage : undefined);
  if (typeof code !== 'string') return null;
  const words = STAGE_WORDS[code];
  if (!words) return null;
  const note = v && typeof v === 'object' ? (v as { note?: unknown }).note : undefined;
  return typeof note === 'string' ? `${words}. ${note}` : words;
}

export type Group = { section: Section; timeframe: TimeFrame; keys: string[] };

/** Group fields into sections, then by timeframe within each — resolved before unsourced. */
export function groupFields(entries: [string, LayoutField][]): Group[] {
  const groups = new Map<string, Group>();
  for (const [key, f] of entries) {
    const section = (f.section ?? 'metrics') as Section;
    const timeframe = (f.timeframe ?? 'current') as TimeFrame;
    const id = `${section}|${timeframe}`;
    if (!groups.has(id)) groups.set(id, { section, timeframe, keys: [] });
    groups.get(id)!.keys.push(key);
  }
  const byKey = new Map(entries);
  return [...groups.values()]
    .sort((a, b) => {
      const sa = SECTION_ORDER.indexOf(a.section), sb = SECTION_ORDER.indexOf(b.section);
      if (sa !== sb) return sa - sb;
      return TIMEFRAME_ORDER.indexOf(a.timeframe) - TIMEFRAME_ORDER.indexOf(b.timeframe);
    })
    .map(g => ({
      ...g,
      keys: g.keys.sort((ak, bk) => {
        const av = byKey.get(ak)!, bv = byKey.get(bk)!;
        if ((av.state === 'OK') !== (bv.state === 'OK')) return av.state === 'OK' ? -1 : 1;
        return fieldLabel(ak, av).localeCompare(fieldLabel(bk, bv));
      }),
    }));
}

/** The fields that block a conclusion, surfaced near the top rather than buried. */
const CRITICAL = [
  'event_coverage', 'consensus_eps', 'consensus_revenue', 'eps_expectation',
  'guidance_change', 'prior_guidance', 'official_release', 'fiscal_period',
  'source_confirmed_fiscal_period', 'pre_report_link',
];

export function criticalLimitations(entries: [string, LayoutField][]): [string, LayoutField][] {
  return entries.filter(([k, f]) => f.state !== 'OK' && CRITICAL.includes(k));
}

/** The banner that must sit ABOVE all results when the event itself is in doubt. */
export function coverageBanner(
  f: LayoutField | undefined, eventDate?: string | null,
): { tone: 'warn' | 'error'; text: string } | null {
  if (!f) return null;
  const value = (f as unknown as { value?: Record<string, unknown> }).value;
  const cs = value?.coverage_state;
  if (cs === 'confirmed_missing_event') {
    return { tone: 'error',
             text: `Showing ${eventDate ?? 'an older'} results. A newer release is confirmed `
                 + `missing from this platform's data.` };
  }
  if (cs === 'suspected_gap') {
    return { tone: 'warn',
             text: `Showing ${eventDate ?? 'an older'} results. Coverage of the latest release `
                 + `is uncertain — verify the issuer's own calendar.` };
  }
  return null;
}
