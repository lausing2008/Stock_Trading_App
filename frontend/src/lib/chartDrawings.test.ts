import { describe, it, expect, beforeEach, vi } from 'vitest';

// storage.ts's real getItem/setItem no-op when `window` is undefined (this test environment
// is 'node', not 'jsdom' — matching every other pure-logic test file in this repo). Mock the
// module with an in-memory Map standing in for localStorage, keyed exactly like storage.ts
// itself would key it, so chartDrawings.ts's own namespacing logic is still exercised for real.
const mockStore = new Map<string, string>();
vi.mock('./storage', () => ({
  storage: {
    getItem: (key: string) => mockStore.get(key) ?? null,
    setItem: (key: string, value: string) => { mockStore.set(key, value); },
    removeItem: (key: string) => { mockStore.delete(key); },
  },
}));

import { loadDrawings, saveDrawings, addDrawing, removeDrawing, clearDrawings, nextDrawingId } from './chartDrawings';
import type { HorizontalLineDrawing, TrendlineDrawing } from './chartDrawings';

beforeEach(() => {
  mockStore.clear();
});

describe('chartDrawings', () => {
  it('returns an empty array when nothing has been saved yet', () => {
    expect(loadDrawings('AAPL')).toEqual([]);
  });

  it('round-trips a horizontal line drawing', () => {
    const drawing: HorizontalLineDrawing = { id: 'd1', type: 'horizontal', price: 150.5 };
    saveDrawings('AAPL', [drawing]);
    expect(loadDrawings('AAPL')).toEqual([drawing]);
  });

  it('round-trips a trendline drawing', () => {
    const drawing: TrendlineDrawing = {
      id: 'd2', type: 'trendline', startIdx: 10, startPrice: 100, endIdx: 20, endPrice: 110,
    };
    saveDrawings('AAPL', [drawing]);
    expect(loadDrawings('AAPL')).toEqual([drawing]);
  });

  it('keeps drawings separate per symbol', () => {
    saveDrawings('AAPL', [{ id: 'a', type: 'horizontal', price: 100 }]);
    saveDrawings('MSFT', [{ id: 'b', type: 'horizontal', price: 200 }]);
    expect(loadDrawings('AAPL')).toEqual([{ id: 'a', type: 'horizontal', price: 100 }]);
    expect(loadDrawings('MSFT')).toEqual([{ id: 'b', type: 'horizontal', price: 200 }]);
  });

  it('is case-insensitive on symbol (uppercases for the storage key)', () => {
    saveDrawings('aapl', [{ id: 'a', type: 'horizontal', price: 100 }]);
    expect(loadDrawings('AAPL')).toEqual([{ id: 'a', type: 'horizontal', price: 100 }]);
  });

  it('addDrawing appends without clobbering existing drawings', () => {
    addDrawing('AAPL', { id: 'a', type: 'horizontal', price: 100 });
    const result = addDrawing('AAPL', { id: 'b', type: 'horizontal', price: 200 });
    expect(result).toHaveLength(2);
    expect(loadDrawings('AAPL')).toHaveLength(2);
  });

  it('removeDrawing removes only the matching id', () => {
    addDrawing('AAPL', { id: 'a', type: 'horizontal', price: 100 });
    addDrawing('AAPL', { id: 'b', type: 'horizontal', price: 200 });
    const result = removeDrawing('AAPL', 'a');
    expect(result).toEqual([{ id: 'b', type: 'horizontal', price: 200 }]);
  });

  it('clearDrawings empties the list for that symbol only', () => {
    addDrawing('AAPL', { id: 'a', type: 'horizontal', price: 100 });
    addDrawing('MSFT', { id: 'b', type: 'horizontal', price: 200 });
    clearDrawings('AAPL');
    expect(loadDrawings('AAPL')).toEqual([]);
    expect(loadDrawings('MSFT')).toEqual([{ id: 'b', type: 'horizontal', price: 200 }]);
  });

  it('gracefully returns an empty array for corrupted JSON', () => {
    mockStore.set('chart_drawings:AAPL', '{not valid json');
    expect(loadDrawings('AAPL')).toEqual([]);
  });

  it('gracefully returns an empty array for a non-array JSON value', () => {
    mockStore.set('chart_drawings:AAPL', '{"not":"an array"}');
    expect(loadDrawings('AAPL')).toEqual([]);
  });

  it('nextDrawingId produces unique, non-empty ids', () => {
    const a = nextDrawingId();
    const b = nextDrawingId();
    expect(a).not.toEqual(b);
    expect(a.length).toBeGreaterThan(0);
  });
});
