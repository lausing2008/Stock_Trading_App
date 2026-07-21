import { storage } from './storage';

export type HorizontalLineDrawing = {
  id: string;
  type: 'horizontal';
  price: number;
};

export type TrendlineDrawing = {
  id: string;
  type: 'trendline';
  // BUG-TRENDLINE-STALEBARINDEX: startIdx/endIdx are a bar's raw POSITION in whatever
  // activePrices array was active at draw time — re-indexing that same number into a
  // DIFFERENT array (a different timeframe, or a changed visible date range) silently lands
  // on an unrelated bar, or falls back to bar 0 if the index is now out of range. startTs/
  // endTs (the actual bar timestamp at draw time) let the renderer look up the correct bar
  // by TIME in whatever array is currently active, instead of blindly trusting a stale
  // position. Optional (not required) so drawings already saved in a user's localStorage
  // from before this fix — which only have startIdx/endIdx — still round-trip and render,
  // just via the old (occasionally-wrong) index-based path until re-drawn.
  startIdx: number;
  startPrice: number;
  endIdx: number;
  endPrice: number;
  startTs?: string;
  endTs?: string;
};

/** A bar with at least a timestamp — matches the shape PriceChart.tsx's activePrices carries. */
export type TimestampedBar = { ts: string };

/**
 * Finds the index of the bar in `bars` whose timestamp is closest to `targetTs`. Returns
 * `null` for an empty array. Used to re-anchor a trendline's startTs/endTs to the correct bar
 * in whatever `activePrices` array is currently active, instead of trusting a stale raw index
 * captured against a different array (see BUG-TRENDLINE-STALEBARINDEX above).
 */
export function nearestBarIndexByTimestamp(bars: TimestampedBar[], targetTs: string): number | null {
  if (bars.length === 0) return null;
  const target = new Date(targetTs).getTime();
  let bestIdx = 0;
  let bestDiff = Infinity;
  for (let i = 0; i < bars.length; i++) {
    const diff = Math.abs(new Date(bars[i].ts).getTime() - target);
    if (diff < bestDiff) {
      bestDiff = diff;
      bestIdx = i;
    }
  }
  return bestIdx;
}

export type ChartDrawing = HorizontalLineDrawing | TrendlineDrawing;

function storageKey(symbol: string): string {
  return `chart_drawings:${symbol.toUpperCase()}`;
}

export function loadDrawings(symbol: string): ChartDrawing[] {
  const raw = storage.getItem(storageKey(symbol));
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function saveDrawings(symbol: string, drawings: ChartDrawing[]): void {
  storage.setItem(storageKey(symbol), JSON.stringify(drawings));
}

export function addDrawing(symbol: string, drawing: ChartDrawing): ChartDrawing[] {
  const next = [...loadDrawings(symbol), drawing];
  saveDrawings(symbol, next);
  return next;
}

export function removeDrawing(symbol: string, id: string): ChartDrawing[] {
  const next = loadDrawings(symbol).filter(d => d.id !== id);
  saveDrawings(symbol, next);
  return next;
}

export function clearDrawings(symbol: string): void {
  saveDrawings(symbol, []);
}

let _idCounter = 0;
export function nextDrawingId(): string {
  _idCounter += 1;
  return `drawing_${_idCounter}_${Math.floor(performance.now())}`;
}
