## Recurring Issue: BUG-TRENDLINE-STALEBARINDEX — Trendline Drawings Broke Across Timeframe Switches (Fixed 2026-07-21)

**Symptom:** a user-drawn trendline on the chart (T230-CHARTING-DRAWING-TOOLS) would visibly
snap to the wrong dates/prices after switching the chart's timeframe or changing the visible
date range — no error, just a silently mispositioned line.

**Root cause:** `TrendlineDrawing` (`frontend/src/lib/chartDrawings.ts`) stored only a raw
`startIdx`/`endIdx` — a bar's **position** in whatever `activePrices` array was active at draw
time. `PriceChart.tsx`'s render path (`activePrices[d.startIdx]`) re-indexed that same number
into whatever `activePrices` array is **currently** active. A timeframe switch (daily → 1h) or
a changed visible date range produces a completely different array (different length,
different bars) — the same numeric index now points to an unrelated bar, or, if it exceeds the
new array's length, silently falls back to `activePrices[0]` (today's bar 0) via the existing
`?? activePrices[0]?.ts` guard, with no error or warning surfaced anywhere.

**Fix applied:** added optional `startTs`/`endTs` fields to `TrendlineDrawing` (kept optional
so drawings already saved in a user's `localStorage` from before this fix — which only have
`startIdx`/`endIdx` — still round-trip and render via the old index-based path until
re-drawn), and a new pure `nearestBarIndexByTimestamp(bars, targetTs)` helper in the same file
that finds the bar closest in time to a target timestamp in whatever array is currently
active. The draw-time click handler now captures the real bar timestamp alongside the raw
index; the render path re-anchors by time via `nearestBarIndexByTimestamp()` whenever
`startTs`/`endTs` are present, falling back to the raw index only for pre-fix drawings that
lack them:
```typescript
const startBarIdx = d.startTs != null
  ? nearestBarIndexByTimestamp(activePrices, d.startTs) ?? d.startIdx
  : d.startIdx;
```

**Tests**: 9 new cases added to `frontend/src/lib/chartDrawings.test.ts` (now 20 total) —
`nearestBarIndexByTimestamp()`'s own behavior (empty array, exact match, closest-non-exact
match, before/after-everything snapping to first/last bar, single-bar array), that a
trendline with `startTs`/`endTs` round-trips those fields, that one WITHOUT them (pre-fix
data) still round-trips, and the core scenario the fix actually targets: a daily 10-bar
array's timestamp correctly resolves against a completely different, shorter intraday 3-bar
array covering the same real-world day — which the old index-based approach would have
silently mishandled (reusing index 7 into a 3-bar array is out of range, falling back to bar
0, the wrong day entirely).

**Adversarial verification**: inverted the closest-match comparison operator (`<` to `>`) and
confirmed exactly 3 of the new tests failed for the right reason (closest-match, snap-to-first,
snap-to-last) before reverting.

Full 79-test frontend vitest suite (up from 70) and typecheck green.

**What to check if this looks wrong**: `nearestBarIndexByTimestamp()` in `chartDrawings.ts` is
the only place this matching logic lives — if a trendline still looks mispositioned after a
timeframe switch, first confirm the drawing actually has `startTs`/`endTs` set
(`localStorage.getItem('chart_drawings:SYMBOL')` in devtools) — a drawing made BEFORE this fix
shipped has neither field and will still use the old (occasionally-wrong) index-based path
until the user deletes and re-draws it.

---

