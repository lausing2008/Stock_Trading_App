## Feature Reference: Volume Profile (Tier 250) — How to Read It

**Built 2026-07-16.** User asked for a TradingView-style footprint chart on the stock detail
page. True footprint charts (buy/sell volume split per price level) need tick/quote data no
current data source (yfinance, Alpha Vantage, the current Polygon aggregates-only
integration) provides without a paid Polygon upgrade — deferred as a separate, larger project.
What's built instead is a **volume profile**: POC/VAH/VAL/HVN using the standard
price-bucketing approximation (each bar's volume spread across its high-low range, bucketed
by price), forked from TradingView's own official `lightweight-charts` plugin-examples
volume-profile primitive.

**How to read it** (this exact explanation is also in the UI as hover tooltips on the
POC/VAH/VAL/HVN readout row and the Session/Range dropdown options — added after a user asked
"how do I read this?" with no in-app explanation available):

- **The blue horizontal bars are NOT tied to any single candle.** Each bar represents a
  **price level**, and its length is the total volume summed across every bar in the profiled
  range whose high-low span touched that price level — a sideways aggregation across time,
  projected onto the price (y) axis. If 20 different candles all had prices passing through
  $650-$660, all of their volume adds together into the one bucket at that price level. This
  is exactly why the profile is drawn to the left of the price axis rather than aligned under
  any particular candle: it collapses the time dimension entirely and only answers "how much
  total volume traded at each price," not "when."
- **POC (Point of Control, orange)** — the single price level with the most volume traded.
  Usually the most important line on the profile; acts like a magnet/support-resistance level
  since it's the price the market most agreed was "fair" for that period.
- **VAH / VAL (Value Area High/Low, blue)** — together bracket the price range containing
  70% of total volume (the standard value-area percentage, matching TradingView's own
  default). Price outside this band sat in comparatively under-traded, "thin" territory.
- **HVN (High Volume Nodes)** — specific price levels with locally peaking volume (real
  interior peaks in the bucket histogram, not just the single POC). These tend to act as
  support/resistance on revisit, same reasoning as POC but at a finer granularity.
- **Low Volume Nodes (LVN)** are computed (`VolumeProfileResult.lvn`) but not currently shown
  in the readout row — they mark price zones the market moved through quickly, which tend to
  get moved through fast again on a revisit (the opposite behavior of HVN/POC).

**Three modes** (Volume Profile dropdown in the chart toolbar):
- **Session VP** — profiles only the current trading session's bars. Useful for intraday
  support/resistance.
- **Range VP** — profiles the entire currently-visible chart window (whatever date range is
  currently selected/zoomed).
- **Fixed Range VP** (added 2026-07-16, after a user asked how to use POC as an entry point
  anchored to a specific swing high/low) — click a start point on the chart, then an end
  point, and the profile computes for exactly that bar range. `lightweight-charts` has no
  native drag-select gesture, so this uses the standard two-sequential-clicks pattern instead
  (same approach TradingView's own drawing tools and most community plugins use), reading
  `param.logical` (a bar index, not a pixel coordinate) from `chart.subscribeClick()` so the
  selection is always bar-aligned. Implemented as a separate, lightweight `useEffect` from
  the main chart-rebuild effect — subscribing on the existing chart instance via `chartRef`
  rather than recreating the whole chart on the first of the two picking clicks, which would
  otherwise flash/reset zoom on every click. A `chartInstanceVersion` counter guards the edge
  case where the user starts picking a range, then also toggles an unrelated overlay before
  finishing — without it, the click effect could stay subscribed to a since-replaced chart.

  **How to redo or clear a selection** (a user asked this directly after first using the
  feature — worth documenting since it's not obvious from the UI alone):
  - **To pick a new range**: once a selection exists, a **"Re-pick range"** button appears in
    the toolbar next to the Volume Profile dropdown (only visible when
    `fixedRangePickState === 'idle' && fixedRangeSelection` is set). Clicking it re-arms
    `picking-start` without touching `volumeProfileMode` — the old selection/profile stays
    visible on the chart until the two new clicks land and replace it.
  - **To turn it off entirely**: open the Volume Profile dropdown and uncheck "Fixed Range
    VP" — this resets `volumeProfileMode` to `'off'` and clears both `fixedRangePickState`
    and `fixedRangeSelection`. Unchecking-then-rechecking also works (same reset path) but
    isn't necessary just to redo a range — "Re-pick range" is the one-click way to do that.

**What to check if this looks wrong**: `src/lib/volumeProfile.ts`'s `computeVolumeProfile()`
is the only place this math lives — 10 tests in `volumeProfile.test.ts` cover POC placement,
VAH/VAL bracketing at exactly 70% volume, HVN detection, and edge cases (degenerate/zero-
volume bars). If a specific stock's profile looks implausible, the first thing to check is
whether `numBuckets` (currently hardcoded to 24 in `PriceChart.tsx`) is too coarse for that
stock's price range — a stock with a very wide 52-week range bucketed into only 24 buckets
will show chunkier, less precise bars than a narrower-range stock.

**How to trade it — breakouts and direction** (a user asked this directly on 2026-07-16,
separately from "how do I read this" — worth keeping distinct since reading the levels and
trading them are different questions):

- **Breakout above VAH** — price has left the "accepted"/fair-value range into thin,
  low-volume territory above it. Thin territory means less resistance overhead, so price can
  move fast — read as bullish continuation, especially if price holds above VAH on a retest
  (old resistance flipping to new support is the confirming signal, not the initial break
  itself).
- **Breakdown below VAL** — the mirror case, bearish. Price rejected the value area from
  below and is now in thin air below it — commonly used as an exit/reduce-position trigger
  (this is exactly what `T252-VALUE-AREA-BREAKDOWN-ALERT`, still `todo` in the tracker, would
  automate as a real alert instead of a manual chart read).
- **Failed breakout (rejection back into the value area)** — if price pokes above VAH or
  below VAL and then closes back inside, that's often a false breakout / reversal signal —
  the market "tested" outside fair value and the market rejected it. Treat a poke-and-reject
  as the opposite signal from a genuine breakout, not a weaker version of the same one.
- **POC as a magnet** — price far from POC often gets pulled back toward it. A stock trading
  well above POC can be extended/due for a pullback to POC before continuing, rather than an
  immediate reversal signal on its own.
- **HVN vs LVN as a roadmap** — HVNs (thick bars) act like speed bumps: price tends to slow
  down, consolidate, or reverse there. LVNs (thin bars/gaps) are zones the market moved
  through fast the first time — expect a quick move back through them too if price revisits
  (much less "friction" than an HVN revisit).
- **Practical entry read**: a higher-quality long setup is often price pulling back toward
  POC or an HVN from above (acting as support), holding there, with volume drying up on the
  pullback itself (thin selling pressure) — generally a better-quality entry than chasing a
  breakout with no pullback at all.
- **Which mode fits which read**: Session VP for intraday direction (where today's volume
  actually concentrated); Range VP for the current visible swing's context; Fixed Range VP
  for judging whether a SPECIFIC prior rally/decline had "real" volume support underneath it
  (HVN-heavy = well-supported move; LVN-heavy = thin/fragile move, more likely to fully
  retrace).
- **Standing caveat**: this is still the bucketing approximation described above, not a true
  buy/sell-split tick footprint — it tells you WHERE volume concentrated, not whether that
  volume was aggressive buying or selling at each level. Directional reads above lean on
  price behavior AROUND the profile (holds vs. rejects a level), not on the profile's volume
  alone distinguishing buyers from sellers.

---


## Feature Reference: Chart Toolbar Redesign + Intraday Indicators (Tier 250 follow-up)

**Built 2026-07-16**, same day as Volume Profile above, after live user feedback found the
toolbar had become overcrowded (~15 flat SMA/EMA/BB/VWAP/Sig/RSI/MACD buttons + the new VP
buttons, all on one wrapping row).

**Toolbar**: redesigned into `frontend/src/components/ToolbarDropdown.tsx` — a reusable
checkbox-list dropdown (open/close/outside-click pattern matches `_app.tsx`'s existing
`NavGroup` nav dropdown). Three groups now: **Indicators** (SMA/EMA/BB/Sig), **Panels**
(RSI/MACD), **Volume Profile** (Session/Range). Vol/VWAP stay as quick single-click toggles
since they're the most frequently used.

**Page width**: `.container-xl` in `globals.css` widened 1200px → 1700px — the whole app
(every page, not just stock detail) was capped well below typical monitor widths.

**Chart height**: main candlestick chart 420px → 600px, ahead of a future drawing-tools
(trendline) feature the user flagged wanting next.

**Intraday indicators fix**: SMA/EMA/BB/RSI/MACD previously disappeared entirely on
intraday timeframes (5m/15m/1h/4h) because the technical-analysis service only computes
indicator series for daily bars — the intraday API response has no `indicators` field at
all. Fixed with new `frontend/src/lib/indicators.ts`, computing these client-side from the
already-fetched intraday bars (same local-computation approach already used in
`PriceChart.tsx` for VWAP/EMA200), hand-translating `shared/common/indicators.py`'s exact
pandas formulas.

**A real bug caught before shipping**: the first version of `indicators.ts` wrongly assumed
pandas' `ewm(adjust=False, min_periods=window)` seeds its recursion with an SMA of the first
`window` values. Cross-checked directly against a real `pandas.Series(...).ewm(...)` call
(not just re-derived from the JS implementation's own output) and found `adjust=False`
actually seeds at the FIRST value unconditionally (`y[0] = x[0]`) and recurses from there —
`min_periods` only masks early output as null, it does not change the seed. This would have
silently produced wrong EMA/RSI/MACD values on every intraday chart (e.g. `EMA[2]` of
`[10,20,30,40,50]` with window=3: the shipped-and-caught-wrong answer was 20, the
pandas-verified correct answer is 22.5). Rewrote all 11 `indicators.test.ts` assertions to
check exact values captured from real pandas runs rather than internally-consistent-but-
unverified expectations.

**Design invariant**: any future hand-translated formula (pandas, numpy, or otherwise) that
"looks right" and produces plausible-looking numbers should still be cross-checked against a
real run of the reference implementation on a fixed, hand-picked input — a test suite that
only re-derives its expected values from the same (possibly wrong) implementation under test
will never catch this class of bug, no matter how many tests it has.

**Test infrastructure**: this is also the first time Vitest was added to this repo (zero
JS/TS test tooling existed before 2026-07-16) — pinned to v1.6.1 rather than the latest v4.x
after discovering v4 requires a Node `styleText` export the local dev environment's Node
18.19.1 doesn't have (production's Docker build uses `node:20-alpine`, where v4 would have
worked, but v1.x was kept for local-dev compatibility). Run via `npm test` in `frontend/`.

---


## Feature Reference: Fair Value Gap (FVG) — What It Is and How to Use It

**Built 2026-07-16.** User asked for Fair Value Gap zones specifically to help set entry,
target, and stop — the same underlying goal as Volume Profile (real structural price levels
instead of eyeballing a chart), but a different pattern with a sharper, more mechanical
entry/stop read than POC/VAH/VAL.

**What it is**: a standard ICT / smart-money-concepts 3-candle pattern. Look at any 3
consecutive candles — call them bar 1, bar 2, bar 3:
- **Bullish FVG**: bar 1's high is BELOW bar 3's low. Bar 2 (the middle candle) moved up so
  decisively that bars 1 and 3 never overlap its range at all — there's a real price zone,
  bounded by bar 1's high (bottom) and bar 3's low (top), that NO candle actually traded
  through. That's the "gap" / "imbalance."
- **Bearish FVG**: the mirror — bar 1's low is ABOVE bar 3's high, leaving an untraded zone
  between bar 3's high (bottom) and bar 1's low (top).
- **Important**: the gap boundary is bar 1 and bar 3's edges, NOT bar 2's own high/low. Bar 2
  is the candle whose move CREATED the gap, but its own range is not the gap itself.

**Why it matters**: an untraded price zone is considered "unfair" — the market moved through
it too fast for real two-sided trading to happen there. Price frequently comes back to
"rebalance" (retrace into) that zone before continuing in the original direction — this makes
the gap a plausible pullback entry zone, not just a curiosity.

**How to read it on the chart**: toggle "Fair Value Gaps" in the chart toolbar's Indicators
dropdown (on by default). Each gap is drawn as a pair of horizontal lines (top edge + bottom
edge of the zone) — solid/dashed and bold green (▲) for an unfilled bullish gap, bold red (▼)
for an unfilled bearish gap. Once a later candle has traded all the way through a gap (fully
closing it, not just dipping partway in), the pair dims to a thin dotted line and its label
disappears — the zone already "did its job" as support/resistance on that revisit and is no
longer an open, actionable target for a NEW entry.

**How to use it for entry/stop/target** — a new "Fair Value Gap Trade Plan" card on the stock
detail page (below Position Sizer) does this automatically for the single most relevant gap
right now:
- **Which gap it picks**: only a bullish gap that sits BELOW the current price (room to
  retrace down into it — a long setup) or a bearish gap ABOVE current price (room to retrace
  up into it — a short setup). A bullish gap already above price, or a bearish gap already
  below it, has nothing left to retrace into from here and is skipped. Among the remaining
  candidates, the NEAREST one to the current price is used — the one most likely to actually
  get touched next.
- **LONG vs. SHORT is not fixed — it's derived from whichever gap wins the pick above** (a
  user asked this directly, since the card only ever seemed to show one direction for a given
  stock at a given moment). If the nearest actionable gap is bullish, the card shows a LONG
  plan; if it's bearish, SHORT. It can flip for the same stock at a different time simply
  because price moved and a different gap became the nearest actionable one. It is NOT tied to
  the SHORT/SWING/LONG/GROWTH signal-horizon tabs elsewhere on the page — FVG is a daily-bar
  chart structure, not a per-horizon signal, so switching horizon tabs does not change which
  gap this card picks.
- **Entry** = the gap's midpoint (not its exact edge — edges are rarely touched with pixel
  precision; the midpoint is the standard, more realistic fill assumption).
- **Stop** = just past the gap's FAR edge (the bottom for a long, the top for a short) — the
  reasoning: if price fully closes the entire gap and keeps going past it, the "unfair, will
  get rebalanced" thesis has failed and the setup is invalidated, not just pulled back further
  than expected.
- **Target** = a configurable reward:risk floor (1.5:1 by default) measured off the gap's own
  real size, not an arbitrary fixed dollar/percent distance — the target scales naturally with
  how big the actual imbalance is.
- **This is shown as its own separate card, not merged into Position Sizer's numbers** —
  Position Sizer's own entry/stop/target (ATR-based stop, nearest support, analyst target
  price) stays exactly as it was; the FVG plan is an independent, comparable alternative a user
  can weigh against it, not a silent override of one system by the other.
- **No candidate gap** = the card simply doesn't render (no error, no placeholder) — this
  happens whenever there's no unfilled gap positioned to be retraced into from the current
  price, which is a normal, common state, not a bug.

**Architecture**: `services/technical-analysis/src/indicators/trendlines.py`'s
`detect_fair_value_gaps()` (same module and `@dataclass` convention as the existing `Level`/
`Trendline` detectors) scans the last 200 bars, filters out near-zero noise-level gaps, and
tracks `filled`/`filled_idx` by checking every later bar for a FULL cover of `[bottom, top]`
(a bar that only partially dips into the zone does not count as filled). Folded into the
existing `GET /ta/{symbol}/levels` endpoint as a new `fair_value_gaps` field, alongside
`support_resistance`/`trendlines`/`fibonacci` — not a new route, since FVG is conceptually
just another kind of level. `frontend/src/components/PriceChart.tsx` renders it via the exact
same `createPriceLine`-per-level pattern already used for S/R and `gamePlanLevels` — no new
chart primitive was introduced. `frontend/src/lib/fvgTradePlan.ts`'s `nearestActionableFvg()`
is a small, pure, independently-testable function (9 Python detection tests + 10 TypeScript
trade-plan tests, both adversarially verified) — the entry/stop/target math has no server
round-trip of its own; it runs entirely off the same `levels.fair_value_gaps` array already
being fetched for the chart.

**What to check if this looks wrong**: `detect_fair_value_gaps()` in `trendlines.py` is the
only place the detection math lives; `nearestActionableFvg()` in `fvgTradePlan.ts` is the only
place the trade-plan math lives. If a gap looks like it should be marked filled but isn't (or
vice versa), check whether a later bar's range genuinely covers the FULL `[bottom, top]` span
— a bar that pokes partway into the zone and reverses does NOT count as a fill by design.

**Game Plan vs. FVG Trade Plan vs. T252 Risk/Reward lines — three DIFFERENT systems, not
duplicates** (a user asked directly whether Game Plan and FVG are "the same or similar," after
finding the chart cluttered with multiple sets of entry/stop/target lines at once):
- **Game Plan** — on-demand, LLM-generated (Claude writes a specific plan with catalysts/risk
  narrative in prose). `null` until a user explicitly clicks to request one.
- **T252 Risk/Reward lines** (`riskRewardLevels` prop) — always-computed, ATR/nearest-support/
  analyst-target-derived, the same numbers already shown as text in Position Sizer, just drawn
  on the chart. No LLM call.
- **Fair Value Gap Trade Plan** — always-computed, purely mechanical (3-candle imbalance
  pattern), completely independent math from the other two, shown as its own separate card.

**On-chart collision handling**: Game Plan and the T252 Risk/Reward lines are mutually
exclusive on the chart itself — `riskRewardLevels` only renders `when !gamePlanLevels`, so
opening a Game Plan hides the ATR-based lines rather than stacking both. The FVG Trade Plan
card is NOT gated by either of these — it always shows independently whenever an actionable
gap exists, since it lives in its own card below Position Sizer, not on the chart's price-line
layer. This means a user can still see, at the same time: FVG's chart lines (toggle-controlled,
see above) + either Game Plan's OR the T252 lines (never both) + the separate FVG Trade Plan
card's own numbers — three distinct sources of "where's my entry" that are deliberately not
merged into one, so a user can compare independent reads rather than have one silently pick a
winner.

**Chart decluttering (2026-07-16)**: a user reported the chart as too cluttered to read once
S/R levels + 52-week High/Low + FVG lines + the new Risk/Reward lines + SMA/EMA curves were
all stacking up with no way to turn any group off. Support/Resistance and 52-Week High/Low
were changed from always-on to togglable (off by default) in the Indicators dropdown, matching
the pattern already used for Fair Value Gaps — a user now opts into extra context instead of
seeing everything at once unasked.

**Follow-up same day — FVG itself was still the real culprit.** After the S/R/52W fix, the
user reported the chart looked identical and still cluttered. The dense stack of thin
horizontal lines across the whole chart turned out to be FVG, not S/R — `detect_fair_value_gaps()`
can return up to 20 gaps (its own `max_gaps` default), rendered as 2 `createPriceLine()` calls
each = up to 40 lines, and FVG's own toggle had shipped defaulting to **on**, unlike every
other opt-in overlay added in the same decluttering pass. Two fixes: (1) `showFVG` now
defaults to `false`, matching S/R/52W's just-added off-by-default convention instead of being
the one exception; (2) even when a user does turn FVG on, `PriceChart.tsx` now caps rendering
to the 6 most relevant gaps — all unfilled ones (up to 6, since those are the only ones
actionable for a NEW entry) plus the most recent filled ones if there's room left in the cap,
never all 20 at once. The backend's own `fair_value_gaps` array is unchanged (still returns up
to 20 — useful for the "FVG Trade Plan" card's `nearestActionableFvg()`, which only ever picks
one gap anyway and isn't affected by this cap); this is purely a chart-rendering-density fix.

---


## Feature Reference: T252-AUTO-SWING-PIVOTS — Chart Swing Pivot Markers + Click-Snap (Built 2026-07-19)

**Gap this closes**: `services/technical-analysis/src/indicators/trendlines.py` already had
`_find_pivots(series, order=5)` — real, tested local-max/local-min detection, used internally
to anchor server-side trendlines and support/resistance levels — but it was never exposed as a
standalone list of pivot points, and nothing client-side ever called it. Fixed Range VP (built
2026-07-16) requires two manual clicks to pick a swing high and swing low, and eyeballing the
exact extremum bar is imprecise.

**Chose a client-side port over a new backend endpoint**: Fixed Range VP's click handler already
reads bar indices out of `activePrices[]`, the exact array PriceChart.tsx has in memory — a new
backend endpoint would need its own index-alignment logic against whatever bar window the
frontend happens to be showing, a real synchronization risk. This matches the established
convention (`volumeProfile.ts`, `indicators.ts`) of doing chart-only computation locally instead
of adding a network round-trip.

**New `frontend/src/lib/swingPivots.ts`**: `detectSwingPivots(bars, order=5)` ports
`_find_pivots()`'s exact algorithm — detecting on `high`/`low`, NOT `close`. This deliberately
matches `trendlines.py`'s own `T247-TA-CLUSTERPIVOTS-CLOSE-HIGH-MISMATCH` fix (a genuine swing
high/low is the bar's actual extremum, not wherever it happened to close) rather than
`detect_trendlines()`'s close-based pivots, which serve an unrelated purpose (trendline
least-squares fitting) and would give the wrong answer for "where's the real swing high."
`nearestPivot(pivots, targetIdx, maxDistance)` snaps an arbitrary clicked bar index to the
closest real pivot within tolerance.

**Verified against the real Python reference**, not just internally-consistent TS expectations —
per this repo's own standing lesson from the Tier 250 EMA/RSI/MACD port (a hand-translated
formula that "looks right" can still be wrong in a way only a real reference run catches). Ran
the identical zigzag fixture through both the real `_find_pivots(pd.Series(highs), order=3)`
and `detectSwingPivots()`: both produced the identical pivot indices (high at idx 4, low at idx
8), confirming the port is faithful.

**PriceChart.tsx wiring**:
- A new "Swing Pivots" toggle in the Indicators dropdown (off by default, daily-only), rendering
  small dot markers via `candles.setMarkers()`.
- **A real clobbering bug avoided during implementation, not shipped**: `setMarkers()` replaces
  the ENTIRE marker set on each call — the existing signal-transition-arrow code already called
  it once. Adding a second `setMarkers()` call for pivot dots would have silently erased
  whichever ran second. Restructured both marker sources to accumulate into one array and call
  `setMarkers()` exactly once.
- Fixed Range VP's click handler now always snaps the raw clicked bar index to the nearest pivot
  within 3 bars, regardless of whether the pivot-marker overlay itself is toggled on — the
  snap-to-precision benefit shouldn't require turning on the visual dots.

**Tests**: `frontend/src/lib/swingPivots.test.ts`, 10 cases — empty/too-short input, correct
high/low identification on a zigzag fixture (cross-checked against the real Python function as
described above), no false positives on a strictly monotonic run (a monotonic series has no
interior local extremum at all), the `+-order` edge-exclusion matching Python's
`range(order, n-order)`, `ts` pass-through, and `nearestPivot`'s within-tolerance / out-of-range
/ tie-break / empty-list behavior. Full 52-test frontend vitest suite, typecheck, and a full
`next build` all green.

**What to check if this looks wrong**: `detectSwingPivots()` in `swingPivots.ts` is the only
place this logic lives — if a marker looks like it's not a real local extremum, or Fixed Range
VP's clicks aren't landing where expected, re-run the cross-check above (`_find_pivots()` in
`trendlines.py` vs. `detectSwingPivots()` on the same fixture) to confirm the two haven't
drifted apart. Extended 2026-07-19 to also run on intraday timeframes (5m/15m/1h/4h), not just
daily — the client-side computation has no dependency on the backend's daily-only
`/ta/{symbol}/levels` endpoint, so the earlier daily-only restriction wasn't structurally
necessary. A separate bug (pivot markers set to `size: 0`, making them invisible even with the
toggle on) was found and fixed the same day.

---


## Design Reference: Swing Pivots + Fixed Range VP — What Each One Finds, and How to Use Them Together

**What a "swing pivot" is finding.** A swing high is a bar whose high is the highest point
within a window of nearby bars on both sides (`+-order`, default 5) — i.e. a real local top,
not just "a candle that went up." A swing low is the mirror: a real local bottom. These are
the same reference points every discretionary trader means when they say "draw your trendline
from swing low to swing low" or "the market made a lower high" — this feature just finds them
mechanically instead of eyeballing the chart. The small gray dots (▾ toggle: Indicators →
"Swing Pivots") mark every such point currently detected on the chart.

**What Fixed Range VP is finding.** Fixed Range VP answers a completely different question:
"of all the volume that traded between these two exact points I pick, where did most of it
concentrate?" It needs two clicks — a start bar and an end bar — and computes POC/VAH/VAL/HVN
(see the Volume Profile section above for what those mean) using ONLY the bars between those
two points. Unlike Session VP or Range VP (which profile a fixed calendar window), Fixed
Range VP is deliberately structure-anchored: the two points you pick define what "this move"
means, and the profile tells you how the market actually traded during it.

**Why they're built to be used together, not separately.** Fixed Range VP's whole value
depends on picking a *meaningful* start/end pair — profiling from a random Tuesday to a random
Friday tells you very little. Profiling from one real swing low to the next real swing high
(or vice versa) tells you exactly how a specific, identifiable move built its volume structure.
Before this feature, picking those two points meant zooming in and clicking as close as
possible to what looked like the swing extreme by eye. Now: turn on Swing Pivots to see the
dots, then use Fixed Range VP as normal — every click is silently snapped to the nearest real
pivot within 3 bars, whether or not the dots themselves are visually toggled on. You don't have
to be pixel-perfect anymore; clicking near a dot is enough.

**A concrete example of what this combination is trying to help you find**: suppose a stock
ran from a swing low at $80 to a swing high at $110, then pulled back to $95. Turn on Swing
Pivots, Fixed Range VP the $80→$110 leg specifically (snap-clicking near each dot), and read
the profile:
- If POC/HVN cluster near $95-98, that's telling you the pullback has landed almost exactly on
  the price level the market spent the most volume agreeing was fair DURING that specific
  rally — a materially stronger signal than "price is near a round number" or "price touched
  the 50-day MA," because it's derived from real, structural volume during the exact move in
  question, not a generic indicator.
- If the pullback has instead landed in a thin, low-volume gap of that same profile (an LVN
  region, or clearly below VAL), that tells you the current price wasn't a place the market
  spent much time agreeing on last time it was here — a weaker-conviction support level, more
  likely to be sliced through than held.
- If POC/HVN sit much higher (say, near $105), that tells you most of the rally's volume
  happened late and high, near the top — often a sign the move was thin/fast on the way up
  (a LVN-heavy rally per the "how to trade it" section above) and more fragile than it looked
  candle-by-candle alone.

**In one sentence**: Swing Pivots finds the real structural anchor points a discretionary
trader would draw lines between; Fixed Range VP tells you how volume actually distributed
across the specific move between two such points — together they replace "eyeball the chart
and guess where support is" with "profile the exact swing you care about, anchored precisely."

---


## Feature Reference: T252-FVG-COMBINATION-BADGES — Pivot-Anchor + Volume-Context Badges on FVG Trade Plan (Built 2026-07-19)

**Direct follow-on from the swing-pivots + Fixed-Range-VP combination above** — after the user
said they liked that pattern and asked for more, this closes the two cheapest, purely-wiring
proposals: cross-referencing the existing Fair Value Gap Trade Plan pick against two OTHER
already-computed features it had never been checked against.

**`nearestActionableFvg()`'s pick is pure price-distance** — the nearest unfilled gap to the
current price, nothing more. Two new pure functions in `frontend/src/lib/fvgTradePlan.ts`
corroborate (or don't) that pick:

- **`nearestPivotToFvg(gap, pivots, tolerancePct=0.015)`** — compares the gap's FAR edge (the
  one the stop sits beyond) against every `detectSwingPivots()`-detected swing pivot's price.
  Returns the closest one within tolerance (a % of price, so it scales sensibly across a $5
  stock and a $500 stock) or `null`. Deliberately compares the FAR edge, not the near edge —
  the far edge is the one whose structural significance actually matters to the trade thesis
  (it's where the stop sits and where the setup would be invalidated), not wherever price
  happens to be retracing from right now.
- **`classifyFvgVolumeContext(gap, profile, tolerancePct=0.005)`** — checks the gap's
  `[bottom, top]` range against a `computeVolumeProfile()` result: `'poc'` if it contains the
  Point of Control, `'hvn'` if it contains a High Volume Node (checked second, since POC is
  itself always also technically a volume peak — POC takes priority), `'thin'` if it overlaps
  the profiled range but hits neither, `'unknown'` if the gap falls entirely outside what was
  profiled (a different range was profiled — NOT the same as "definitely thin").

**UI**: `frontend/src/pages/stock/[symbol].tsx`'s existing "Fair Value Gap Trade Plan" card now
computes `detectSwingPivots()` and `computeVolumeProfile()` from the same `data.prices` already
on the page, and shows up to two extra badges next to the existing LONG/SHORT one: "⚓
Pivot-anchored" and one of "📊 At POC" / "📊 At HVN" / "📊 Thin zone" — each with a hover
tooltip explaining what it means, matching the card's existing badge convention.

**Tests**: 12 new cases in `fvgTradePlan.test.ts` — 10 for `nearestPivotToFvg` (the far-vs-near
edge distinction, tolerance behavior, closest-pivot tie-breaking among several candidates), 5
for `classifyFvgVolumeContext` (all four return states, including the POC-over-HVN priority
ordering). Adversarially verified 3 guards by sabotage, all caught and reverted: swapping the
far/near edge comparison (4 tests caught it — a bearish gap's pivot match landed on the wrong
edge entirely); disabling the `'thin'` fallback classification (1 test caught it); swapping
POC's priority over HVN (1 test caught it, correctly expecting `'poc'` and getting `'hvn'`
instead). **A real test-writing bug of my own was caught and fixed before it could ship**: the
first version of the volume-profile test fixture built its `poc`/`hvn` fields by re-deriving
the max-volume bucket generically regardless of which spike the test intended, which silently
produced a fixture where the "HVN, not POC" test case actually had POC land inside the gap too
— caught immediately by the test failing for the RIGHT reason (asserting `'hvn'`, getting
`'poc'`), fixed by rewriting the fixture to take an explicit, distinct POC price and a separate
list of HVN prices rather than inferring one from the other. Full 63-test frontend vitest
suite, typecheck, and a full `next build` all green.

**What to check if this looks wrong**: both functions live in `fvgTradePlan.ts` — if a badge
looks wrong, check `nearestPivotToFvg()`'s edge selection (`gap.kind === 'bullish' ? gap.bottom
: gap.top`) and `classifyFvgVolumeContext()`'s POC-then-HVN-then-thin ordering directly; both
are pure functions with no network/state dependency, so a wrong badge on a real symbol should
be reproducible by feeding that symbol's actual gap/pivot/profile data into either function
directly in a REPL.

---


## Feature Reference: T252-ANCHORED-VWAP — Click-to-Anchor VWAP Recalculation (Built 2026-07-19)

**Gap this closes**: `PriceChart.tsx` already computed VWAP (`computeVwap()` — cumulative
typical-price×volume / cumulative volume), but only ever anchored to the start of whatever
date-range window was currently selected. There was no way to anchor it to an arbitrary bar a
user picks — an earnings gap, a breakout day, a swing low — the standard "is price still above
VWAP from the day I would have entered" trend-continuation check.

**Implementation**: reuses `computeVwap()` completely unchanged — the only difference from the
existing rolling VWAP is which slice of `activePrices` it's fed
(`activePrices.slice(anchoredVwapIdx)` instead of the full array) and that the resulting line
only draws starting at the anchor bar's own time, not from the first visible bar. New
`showAnchoredVwap`/`anchoredVwapPickState`/`anchoredVwapIdx` state, a new "Anchored VWAP" entry
in the existing Volume Profile toolbar dropdown, and a dedicated click-subscribe `useEffect`
(same separate-effect-from-the-main-chart-rebuild pattern already established for Fixed Range
VP and the drawing tools) — one click sets the anchor directly, unlike Fixed Range VP's
two-click start/end pair. The click snaps to the nearest `detectSwingPivots()` pivot within 3
bars, same reasoning as Fixed Range VP's snap: an anchor planted on a real swing high/low is
far more useful than one landing a few pixels off from what the user actually meant to click.
Rendered as a solid cyan line, visually distinct from the existing dashed violet rolling VWAP,
plus its own legend entry.

**Correctness check performed** (this repo's own established discipline of verifying
hand-translated/derived math against a real computed reference, not just "it compiles"):
manually ran a 4-bar fixture through `computeVwap()` twice — once on the full series, once on
`.slice(2)` — and confirmed the anchored version's first value correctly resets to bar 2's own
typical price (115) and diverges meaningfully from the full-window VWAP at that same point
(107.2 vs. 115), proving the anchor genuinely changes the underlying calculation, not just
which portion of an unchanged line gets drawn.

**A real, PRE-EXISTING bug found and fixed while touching this code, unrelated to Anchored
VWAP itself**: this repo has no live Tailwind pipeline (no `tailwind.config.js`/
`postcss.config.js` — the same root cause already documented for `ToolbarDropdown.tsx`'s
fully-transparent-dropdown bug earlier this session). Fixed Range VP's own click-picking status
pill (`bg-violet-900/40`, `border-violet-500/50`, `text-violet-300`) and the VWAP legend
swatch (`border-violet-400`) both used classes with zero matching rule anywhere in
`globals.css` — silently no-oping in production the whole time, just less noticeably than the
fully-invisible dropdown (a missing border/background tint on a small status pill is easy to
miss; a fully see-through dropdown panel is not). Fixed both to inline styles while implementing
the new Anchored VWAP status pill and legend swatch, using them as the reference for what the
broken ones were supposed to look like.

**No dedicated test file** — `computeVwap()` lives inline in `PriceChart.tsx`, which has no
test file at all (same seam gap as every other `PriceChart.tsx`-only change in this repo, e.g.
the marker-clobbering fix documented in the Swing Pivots entry above). Correctness relies on the
manual verification above plus the fact that `computeVwap()` itself is unchanged — only its
input slice is new. Full 63-test frontend vitest suite (unaffected — none of it imports
`PriceChart.tsx` directly), typecheck, and a full `next build` all green.

**What to check if this looks wrong**: `computeVwap()` is the only place the math lives, and
it's untouched by this feature — if the anchored line looks wrong, first confirm
`anchoredVwapIdx` is the bar index you expect (log it, or check via React DevTools), since the
slicing (`activePrices.slice(anchoredVwapIdx)`) is the only new logic here. If the anchor point
seems to have moved from where you actually clicked, check the swing-pivot snap radius (3 bars)
— clicking near, but not on, a real pivot will snap to that pivot instead of your exact click.

---


## Feature Reference: T252-VALUE-AREA-BREAKDOWN-ALERT — Server-Side POC/VAH/VAL + Alert (Built 2026-07-21)

**The gap this closes**: volume profile (POC/VAH/VAL) computation was 100% client-side —
`frontend/src/lib/volumeProfile.ts`'s `computeVolumeProfile()`, built for the Fixed Range VP
chart feature — with no server-side equivalent, persistence, or scheduled job anywhere. Unlike
T249's earnings/macro alerts (which reused already-computed backend values), this required
porting the actual bucket/value-area-expansion algorithm to Python before any alert could exist.

**New module**: `services/market-data/src/services/volume_area.py` — `compute_value_area()` is
a faithful port of `computeVolumeProfile()`'s bucketing + value-area-expansion math (POC/VAH/VAL
only; HVN/LVN/individual-bucket detail deliberately not ported, since nothing on the backend
consumes them — the chart's own client-side `computeVolumeProfile()` remains the source of
truth for anything actually rendered). This is an **independent port**, not a shared
implementation with `volumeProfile.ts` — if the value-area-expansion logic in one is ever
changed, check whether the other needs the same change too. Cross-checked against
`volumeProfile.test.ts`'s own fixtures (same bar data, same expected POC-bucket range) to
confirm the two ports agree, per this repo's established discipline of verifying a hand-
translated formula against its real reference rather than trusting internal consistency alone.

**New table**: `VolumeAreaLevel` (`shared/db/models.py`) — `(stock_id, as_of)` unique, stores
`poc`/`vah`/`val` per symbol/date. A brand-new table, so `create_all()` handles it automatically
— no manual `ALTER TABLE` needed (unlike adding a column to an existing table, per this file's
own standing `create_all()`-gap invariant).

**Daily compute job**: `compute_value_area_levels_daily()` (`scheduler.py`, 18:00 ET — after US
close, HK's own bars already landed too) computes a rolling 60-day value area for every symbol
any user has a `PriceAlert` on — the same v1 scope-narrowing convention already established for
`check_earnings_reactions()`/`check_volume_anomalies()` (an alert-eligible audience, not the
whole universe). Upserts via `ON CONFLICT DO UPDATE` on `(stock_id, as_of)` — safe to re-run
idempotently (e.g. a retry after a partial failure).

**Alert checker**: `check_value_area_breakdown()` (1-minute interval, matching every other
T249/T257-era fast-reaction checker) reads only the existing `stockai:live_prices` Redis cache
(no yfinance/DB call in the loop — matches `check_volume_anomalies()`'s established rate-limit
discipline) plus the daily-persisted `VolumeAreaLevel`. Fires `send_value_area_breakdown_email()`
with a per-`(user, symbol, kind, as_of)` Redis dedup key so a stock sitting below VAL for hours
doesn't re-alert every cycle — same discipline as every other T249-era alert. Reports the
**measured** close price relative to POC/VAH/VAL — never a "will continue" prediction, matching
this repo's established alert-honesty discipline (T249-P3, T257's volume-anomaly/top3-conviction
alerts).

**Deliberate scope note**: only a close below VAL (breakdown) or above VAH (breakout) fires —
not a symmetric "poke back below VAH after trading above it" reversal signal, since that would
need intraday tracking of "was it above VAH earlier today" state this v1 doesn't carry; the
docs' own "poke-and-reject = false breakout" read (Volume Profile "How to trade it" section)
remains a manual chart read for now, not yet automated.

**Tests**: `services/market-data/tests/test_volume_area.py` (10 cases, direct import — the
function is pure with zero DB dependency, so no source-text extraction or stub workaround was
needed) covers degenerate inputs, POC placement, VAH/VAL bracketing, the TS-fixture cross-check,
and zero-volume-bar handling. `services/market-data/tests/test_value_area_breakdown_alert.py`
(14 cases) covers `send_value_area_breakdown_email()` directly (pure composition) plus
source-text regression checks for the scheduler wiring (`scheduler.py` can't be imported in this
test environment — its import chain pulls in `apscheduler`, matching every other scheduler.py
test file's documented constraint), confirming both jobs exist, are registered with the correct
trigger type/schedule, and that the alert checker reads the live-prices cache (never yfinance)
and has both a lock and a dedup key.

**Adversarial verification**: reverted the value-area-expansion tie-break comparison
(`vol_above >= vol_below` → `vol_above < vol_below`) and confirmed 4 tests failed correctly
before restoring it; reverted the dedup-key prefix to an unrelated string and confirmed the
wiring test caught it before restoring it. Full 368-test market-data suite (up from 344) and
frontend typecheck green.

**What to check if this looks wrong**:
```bash
# Confirm the daily compute job actually ran and wrote real rows:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT st.symbol, val.poc, val.vah, val.val, val.as_of FROM volume_area_levels val JOIN stocks st ON val.stock_id = st.id ORDER BY val.as_of DESC LIMIT 10;"

# Check job status/logs directly:
docker logs stockai-market-data-1 --since 24h | grep 'value_area_levels\|value_area_breakdown'

# Manually trigger the daily compute job (needs the running container, not a standalone script):
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0, '/app'); sys.path.insert(0, '/app/src')
from src.services.scheduler import compute_value_area_levels_daily
compute_value_area_levels_daily()
"
```
If a breakdown/breakout alert never fires despite a real close outside VAL/VAH, first confirm
the daily compute job actually populated a `VolumeAreaLevel` row for that symbol/date — the
alert checker never computes on the fly, only reads what the daily job already persisted.

---

