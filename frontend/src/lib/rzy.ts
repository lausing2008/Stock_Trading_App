export type RzyAnchor = { index: number; price: number; pivot_time: string; confirmed_at: string; kind: string };
export type RzyStructure = { id: string; direction: 'long'|'short'; state: string; anchors: RzyAnchor[]; extreme: { time: string; price: number; index?: number }; D: number; target: number; runner: number; stop?: number; planned_entry?: number; sequence_number: number; sequence_confidence: string };
export type RzyEvent = { event_id: string; event_type: string; structure_id?: string | null; direction?: string; position_action: string; signal_time: string; bar_time?: string; actual_paper_fill?: number; rejection_reason?: string; reason?: string };
export type RzyResponse = { strategy_version: string; config_hash?: string; status: string; price_grid?: { time: string; open: number; high: number; low: number; close: number; middle_band?: number | null; upper_band?: number | null; lower_band?: number | null }[]; reason?: string; evidence?: { data_as_of?: string; delay?: string; price_basis?: string; gaps?: unknown[]; paper_blockers?: string[] }; structures: RzyStructure[]; events: RzyEvent[]; paper_positions: { structure_id: string; state: string; reason?: string; fill?: number; net?: number }[] };
export type RzyPreferences = { pattern_email: boolean; morning_digest: boolean; timeframes: string[]; schedule?: string; scope?: string };

/** Stored naive timestamps are UTC, never the browser's local timezone. */
export function rzyTime(value: string) {
  const iso = /^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00Z` : /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`;
  return Date.parse(iso);
}

/** Overlay geometry is supplied by the frozen backend structures, never recomputed pivots. */
export function rzyLinePoints(structure: RzyStructure, timestamps: string[], until?: string | null) {
  const [a,,c] = structure.anchors;
  if (!a || !c || c.index <= a.index) return [];
  const at = timestamps.findIndex(t => rzyTime(t) === rzyTime(a.pivot_time));
  // Refuse a different/missing bar grid instead of aligning a trendline by wall clock.
  const end = timestamps.findIndex(t => rzyTime(t) === rzyTime(c.pivot_time));
  if (at < 0 || end < 0 || end-at !== c.index-a.index) return [];
  // An ended structure stops at the bar that ended it instead of extending to the right edge.
  const stop = until ? rzyTime(until) : Infinity;
  return timestamps.slice(at).filter(t => rzyTime(t) <= stop).map((time, offset) => ({ time, value: a.price+(c.price-a.price)*offset/(c.index-a.index) }));
}

// ENTRY_CANCELLED ends a triggered setup whose paper fill failed (the normal path while execution
// data are unverified); its trigger stays on the chart as research history (RZA-08).
const RZY_TERMINAL = new Set(['INVALIDATED','EXPIRED','COMPLETED','REJECTED','PAPER_EXIT','ENTRY_CANCELLED']);

/** What to draw: every live (armed/triggered) structure, plus the most recent structures that
 *  were armed and have since ended, each clipped at its ending bar. Never-armed rejections and
 *  ended structures whose end is unknown are left off rather than drawn open-ended. */
export function rzyStructuresToDraw(structures: RzyStructure[], events: RzyEvent[], maxEnded = 2) {
  const armed = new Set(events.filter(e => e.event_type === 'ARMED').map(e => e.structure_id));
  const endOf = (id: string) => events.filter(e => e.structure_id === id && RZY_TERMINAL.has(e.event_type))
    .map(e => e.bar_time ?? e.signal_time).sort((x, y) => rzyTime(x) - rzyTime(y)).at(-1) ?? null;
  const live = structures.filter(s => s.state === 'ARMED' || s.state === 'TRIGGERED');
  const ended = structures.filter(s => !live.includes(s) && armed.has(s.id))
    .map(s => ({ structure: s, live: false, until: endOf(s.id) }))
    .filter(x => x.until != null).slice(-maxEnded);
  return [...ended, ...live.map(s => ({ structure: s, live: true, until: null as string | null }))];
}

/** Short chart labels; the full event name is in the events table and emails. */
export const RZY_EVENT_LABEL: Record<string, string> = {
  TRIGGERED: 'RZY entry', PAPER_FILL: 'fill', PAPER_EXIT: 'exit', INVALIDATED: '✕',
  EXPIRED: 'exp', REJECTED: 'rej', REVERSAL_WATCH: 'rev?', ENTRY_CANCELLED: 'no fill',
};

/** Pivot label with the moment the strategy knew it: chart axes are UTC for intraday bars. */
export function rzyPivotLabel(letter: string, confirmedAt: string, intraday: boolean) {
  const t = new Date(rzyTime(confirmedAt)).toISOString();
  return `${letter} ✓${intraday ? t.slice(11, 16) : t.slice(5, 10)}`;
}

export function rzyPriceGridMatches(grid: RzyResponse['price_grid'], candles: { ts: string; open: number; high: number; low: number; close: number }[]) {
  if (!grid?.length || !candles.length) return false;
  const reference = new Map(grid.map(b => [rzyTime(b.time), b]));
  const overlap = candles.filter(b => reference.has(rzyTime(b.ts)));
  return overlap.length > 0 && overlap.every(b => {
    const other = reference.get(rzyTime(b.ts))!;
    // 1e-4 relative: stored bars are re-fetched with ~1e-6 storage noise after checkpointing,
    // while a different price basis (adjusted vs raw) differs by whole percents.
    return (['open','high','low','close'] as const).every(k => Math.abs(b[k]-other[k]) <= Math.max(1e-7, Math.abs(other[k])*1e-4));
  });
}
