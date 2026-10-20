export type RzyAnchor = { index: number; price: number; pivot_time: string; confirmed_at: string; kind: string };
export type RzyStructure = { id: string; direction: 'long'|'short'; state: string; anchors: RzyAnchor[]; extreme: { time: string; price: number }; D: number; target: number; runner: number; stop?: number; planned_entry?: number; sequence_number: number; sequence_confidence: string };
export type RzyEvent = { event_id: string; event_type: string; direction?: string; position_action: string; signal_time: string; bar_time?: string; actual_paper_fill?: number; rejection_reason?: string; reason?: string };
export type RzyResponse = { strategy_version: string; config_hash?: string; status: string; price_grid?: { time: string; open: number; high: number; low: number; close: number; middle_band?: number | null; upper_band?: number | null; lower_band?: number | null }[]; reason?: string; evidence?: { data_as_of?: string; delay?: string; price_basis?: string; gaps?: unknown[]; paper_blockers?: string[] }; structures: RzyStructure[]; events: RzyEvent[]; paper_positions: { structure_id: string; state: string; reason?: string; fill?: number; net?: number }[] };
export type RzyPreferences = { pattern_email: boolean; morning_digest: boolean; timeframes: string[]; schedule?: string; scope?: string };

/** Stored naive timestamps are UTC, never the browser's local timezone. */
export function rzyTime(value: string) {
  const iso = /^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00Z` : /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`;
  return Date.parse(iso);
}

/** Overlay geometry is supplied by the frozen backend structures, never recomputed pivots. */
export function rzyLinePoints(structure: RzyStructure, timestamps: string[]) {
  const [a,,c] = structure.anchors;
  if (!a || !c || c.index <= a.index) return [];
  const at = timestamps.findIndex(t => rzyTime(t) === rzyTime(a.pivot_time));
  // Refuse a different/missing bar grid instead of aligning a trendline by wall clock.
  const end = timestamps.findIndex(t => rzyTime(t) === rzyTime(c.pivot_time));
  if (at < 0 || end < 0 || end-at !== c.index-a.index) return [];
  return timestamps.slice(at).map((time, offset) => ({ time, value: a.price+(c.price-a.price)*offset/(c.index-a.index) }));
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
