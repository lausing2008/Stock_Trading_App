/** Presentation decisions for an intelligence report, kept out of the component so they can be
 *  tested without a DOM renderer.
 *
 *  Both rules here exist because of a reported defect: sector rankings rendered as
 *  "[object Object]" (a list of objects nested inside a dict, stringified), and alphabetical
 *  field order produced return windows in the sequence 1, 20, 5, 63 while interleaving identity,
 *  conclusions and raw inputs. */

export type RenderKind = 'empty' | 'scalar' | 'object' | 'list' | 'table';

/** How a value should be drawn. A list of uniform objects is a TABLE, not a string. */
export function renderKind(v: unknown): RenderKind {
  if (v === null || v === undefined) return 'empty';
  if (Array.isArray(v)) {
    if (v.length === 0) return 'empty';
    return v.every(i => i !== null && typeof i === 'object' && !Array.isArray(i))
      ? 'table' : 'list';
  }
  if (typeof v === 'object') return 'object';
  return 'scalar';
}

/** Columns for a table of uniform-ish objects: every key any row carries, first-seen order. */
export function tableColumns(rows: Record<string, unknown>[]): string[] {
  return Array.from(new Set(rows.flatMap(r => Object.keys(r))));
}

const SECTION_RANK: [RegExp, number][] = [
  [/^(issuer|benchmark|event_identity|event_coverage|fiscal_period|stage|release)/, 0],
  [/^(price_as_of|pre_event_reference_price)/, 1],
  [/^(observed_daily_structure|trend_structure)/, 2],
  [/^(eps_|revenue_|accounting_basis|consensus_)/, 3],
  [/^(return_|run_up_|historical_reactions)/, 4],
  [/^(breadth|sector_|signal_engine_assessment|next_catalyst)/, 5],
  [/^(scenarios|outlook_|thesis_|three_verdicts|management_)/, 6],
  [/^execution_status/, 8],
];

export function sectionRank(key: string): number {
  return SECTION_RANK.find(([re]) => re.test(key))?.[1] ?? 7;
}

/** The numeric window a field describes, for ordering 1, 5, 20, 63 rather than 1, 20, 5, 63. */
export function windowOf(key: string): number | null {
  const m = key.match(/_(\d+)_(?:bars|sessions)$/) ?? key.match(/^run_up_(\d+)_/);
  return m ? Number(m[1]) : null;
}

export function orderFields(
  entries: [string, { state: string }][],
): [string, { state: string }][] {
  return [...entries].sort(([ak, av], [bk, bv]) => {
    // Resolved before unsourced: a reader should not walk past five UNAVAILABLE rows.
    if ((av.state === 'OK') !== (bv.state === 'OK')) return av.state === 'OK' ? -1 : 1;
    const ra = sectionRank(ak), rb = sectionRank(bk);
    if (ra !== rb) return ra - rb;
    const wa = windowOf(ak), wb = windowOf(bk);
    if (wa !== null && wb !== null) return wa - wb;
    return ak.localeCompare(bk);
  });
}

/** A percent field carries "%"; everything else keeps its own unit word. */
export function formatScalar(v: unknown, units?: string | null): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  if (typeof v === 'number') {
    const n = Number.isInteger(v) ? v.toLocaleString() : v.toFixed(2);
    return units === 'pct' ? `${n}%` : units ? `${n} ${units}` : n;
  }
  return String(v);
}
