import { storage } from './storage';

export type HorizontalLineDrawing = {
  id: string;
  type: 'horizontal';
  price: number;
};

export type TrendlineDrawing = {
  id: string;
  type: 'trendline';
  startIdx: number;
  startPrice: number;
  endIdx: number;
  endPrice: number;
};

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
