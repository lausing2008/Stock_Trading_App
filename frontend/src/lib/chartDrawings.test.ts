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

import { loadDrawings, saveDrawings, addDrawing, removeDrawing, clearDrawings, nextDrawingId, nearestBarIndexByTimestamp } from './chartDrawings';
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

  it('a trendline saved with startTs/endTs round-trips those fields too', () => {
    const drawing: TrendlineDrawing = {
      id: 'd3', type: 'trendline', startIdx: 5, startPrice: 100, endIdx: 15, endPrice: 110,
      startTs: '2026-01-05T00:00:00Z', endTs: '2026-01-15T00:00:00Z',
    };
    saveDrawings('AAPL', [drawing]);
    expect(loadDrawings('AAPL')).toEqual([drawing]);
  });

  it('a trendline saved WITHOUT startTs/endTs (pre-fix data) still round-trips', () => {
    const drawing: TrendlineDrawing = {
      id: 'd4', type: 'trendline', startIdx: 5, startPrice: 100, endIdx: 15, endPrice: 110,
    };
    saveDrawings('AAPL', [drawing]);
    expect(loadDrawings('AAPL')).toEqual([drawing]);
  });
});

// ── BUG-TRENDLINE-STALEBARINDEX: nearestBarIndexByTimestamp ─────────────────────────────────
// A trendline's startIdx/endIdx are a raw bar POSITION captured against whatever activePrices
// array was active at draw time. Re-indexing that same number into a DIFFERENT array (a
// timeframe switch, or a changed visible date range) silently lands on an unrelated bar. This
// function re-anchors by the actual TIMESTAMP instead, which survives the array changing.

function bar(ts: string) {
  return { ts };
}

describe('nearestBarIndexByTimestamp', () => {
  it('returns null for an empty bars array', () => {
    expect(nearestBarIndexByTimestamp([], '2026-01-05T00:00:00Z')).toBeNull();
  });

  it('finds the exact match when one exists', () => {
    const bars = [bar('2026-01-01T00:00:00Z'), bar('2026-01-02T00:00:00Z'), bar('2026-01-03T00:00:00Z')];
    expect(nearestBarIndexByTimestamp(bars, '2026-01-02T00:00:00Z')).toBe(1);
  });

  it('finds the CLOSEST bar when there is no exact match', () => {
    const bars = [bar('2026-01-01T00:00:00Z'), bar('2026-01-05T00:00:00Z'), bar('2026-01-10T00:00:00Z')];
    // 2026-01-04 is 3 days from Jan 1, 1 day from Jan 5 — Jan 5 (index 1) is closer.
    expect(nearestBarIndexByTimestamp(bars, '2026-01-04T00:00:00Z')).toBe(1);
  });

  it('a target before every bar snaps to the FIRST bar', () => {
    const bars = [bar('2026-01-10T00:00:00Z'), bar('2026-01-11T00:00:00Z')];
    expect(nearestBarIndexByTimestamp(bars, '2020-01-01T00:00:00Z')).toBe(0);
  });

  it('a target after every bar snaps to the LAST bar', () => {
    const bars = [bar('2026-01-01T00:00:00Z'), bar('2026-01-02T00:00:00Z')];
    expect(nearestBarIndexByTimestamp(bars, '2030-01-01T00:00:00Z')).toBe(1);
  });

  it('a single-bar array always returns index 0 regardless of target', () => {
    const bars = [bar('2026-01-01T00:00:00Z')];
    expect(nearestBarIndexByTimestamp(bars, '2099-01-01T00:00:00Z')).toBe(0);
  });

  it('this is the core mechanism the trendline fix relies on: a DIFFERENT (shorter, intraday-shaped) bar array still resolves to the bar closest to the ORIGINAL draw-time timestamp, not a re-indexed unrelated position', () => {
    // Simulates the real bug scenario: a trendline drawn on a daily chart (10 bars spanning
    // 10 days) is later viewed after switching to an intraday timeframe with only 3 bars
    // covering a single day. The OLD index-based approach would blindly reuse index 7 into
    // this 3-bar array (out of range, falling back to bar 0 — today's start). The NEW
    // timestamp-based approach instead finds whichever of the 3 bars is closest in time to
    // the original draw-time timestamp.
    const dailyBars = Array.from({ length: 10 }, (_, i) => bar(`2026-01-${String(i + 1).padStart(2, '0')}T00:00:00Z`));
    const originalTs = dailyBars[7].ts; // '2026-01-08T00:00:00Z'
    const intradayBars = [bar('2026-01-08T09:30:00Z'), bar('2026-01-08T10:30:00Z'), bar('2026-01-08T11:30:00Z')];
    const result = nearestBarIndexByTimestamp(intradayBars, originalTs);
    expect(result).toBe(0); // 09:30 on the same day is the closest of the 3 intraday bars
  });
});
