# SCOPE: Options Tab on the Stock Detail Page

**Date:** 2026-09-06
**Ask:** move the stock detail page's *existing* options content into a dedicated Options tab, add
more graphs, and add options-strategy suggestions. Platform-wide options pages (`/options-flow`
etc.) stay where they are — this is scoped to the stock detail page only.

---

## 0. The good news up front

Two findings make this substantially cheaper than it first looks:

1. **The tab system already exists.** `frontend/src/pages/stock/[symbol].tsx:465` —
   `useState<'Overview' | 'Research' | 'Goals'>('Overview')`, tab bar at `:1210-1231`, array is an
   inline literal at `:1211`. Adding `'Options'` is: widen the union, add one array entry, add a
   `{pageTab === 'Options' && ...}` branch, and move existing blocks into it. **No restructure.**
2. **Most "new graphs" need no new backend work.** Five chartable datasets are already typed,
   already served, and currently rendered as text lists or not at all (§3).

---

## 1. What moves — the existing options content

All of it currently lives inside the **Overview** tab of a 4,680-line file:

| Content | Lines | Form | Move? |
|---|---|---|---|
| **Options Game Plan** (protective put + covered call) | 2887-2909 | `components/OptionsGamePlanCard.tsx` (own fetch, Advanced-gated at `:2892`) | ✅ Move |
| **Options Flow** (call/put volume bar, C/P ratio, sentiment, pressure score, whale count, unusual-activity table) | 4402-4524 | **~120 lines inline JSX** | ✅ Move |
| **Market Pressure** (GEX walls, gamma flip/magnet, max pain by expiry, top OI strikes, NOPE, per-expiry OI) | mounted `:4528` | `components/MarketPressurePanel.tsx` | ✅ Move |
| **Options Chain** (expiry pills, max pain, OI-by-strike chart, strike matrix) | 4530-4646 | Mostly inline + `components/OptionsChainChart.tsx` | ✅ Move |
| Max-pain line on the price chart | `:1683`, source `:664-669` | Prop into `PriceChart` | ❌ **Stays** — it belongs on the price chart |

### ⚠ The one real trap

`:3365-3373` — **the Squeeze score consumes `optionsFlow`**. If the flow fetch moves into an
Options-tab-only component, the Squeeze score on Overview silently loses its input. Options:
- keep the `useSWR` flow fetch at page level (`:629`) and pass data down, **or**
- have the Squeeze block fetch independently.

Keeping the fetch at page level is simpler and avoids a duplicate network call. **This is the
single highest-risk part of the move** — a silent data-dependency break, exactly the class of bug
that doesn't show up in a typecheck.

Secondary: the Options Chain is deliberately **lazy-fetched** only when expanded (`:637`,
`chainOpen`) because it's a heavy call. Preserve that — if the tab eagerly fetches everything on
mount, every visit pays for the full chain. Suggest keeping it collapsed-by-default inside the tab.

---

## 2. Tab plumbing — one gap worth closing

The existing tabs use **plain `useState` with no query-param sync**, so tabs aren't deep-linkable
or shareable, and a refresh resets to Overview. Adding `?tab=options` is new work but small, and
`learn.tsx:626-629` already has the exact `router.isReady` + `router.replace(..., {shallow: true})`
pattern to copy. Worth doing while touching this code — otherwise "send me the options view for
NVDA" isn't expressible as a URL.

---

## 3. New graphs — available data, no backend work needed

Ranked by value-per-effort. All follow the **hand-rolled SVG** convention, not lightweight-charts
(see §5).

| # | Chart | Data source | Notes |
|---|---|---|---|
| 1 | **OI distribution across strikes (cross-expiry)** | `GammaExposure.oi_per_strike[]` (`api.ts:1224`) | Currently only a **top-N text list** (`MarketPressurePanel.tsx:102-105`). Unlike `/options-chain` this is cross-expiry, so it complements rather than duplicates the existing chart. |
| 2 | **Max pain vs. expiry** | `GammaExposure.max_pain[]` | Already an **array over expiries**; the page uses only `[0]` (`:669`). Free line chart showing where max pain migrates. |
| 3 | **GEX walls as price lines** | `call_wall`/`put_wall`/`gamma_flip`/`gamma_magnet` | Text-only today. `PriceChart` already accepts `maxPainLevel`, so the prop pattern extends directly. |
| 4 | **OI term structure by expiry** | `OptionsExpirationRow[]` (`api.ts:1416-1427`) | Has call_oi/put_oi/volumes/put_call_oi_ratio/`concentration_pct`. Text rollup today. |
| 5 | **IV rank + expected move + real Greeks** | `getOptionsGamePlanBatch` (`api.ts:589`) | **Biggest unused win.** Already persisted on `OptionsGamePlanSnapshot` with full Greeks (delta/gamma/theta/vega/vanna/charm) — but **the stock page never calls this route**; only `screener.tsx:370` does. Caveats: Advanced-gated, snapshot-only, EOD-fresh (not live). |

**Not free:** live IV rank / full-chain Greeks per symbol. `unusual_whales.get_iv_rank()` and
`get_greeks()` exist but **no per-symbol HTTP route exposes them** — that needs a new endpoint,
plus UW budget consideration (§6).

---

## 4. Strategy suggestions — what exists vs. what's new

### Exists and is genuinely reusable

`compute_options_game_plan()` (`services/market-data/src/api/routes.py:4385`) is **pure** (no
DB/HTTP, so directly testable) and returns two real legs — `protective_put` and `covered_call` —
each with expiry, DTE, strike, mid price, cost/credit as % of position, per-contract cost,
effective floor/cap price, IV, and OI. Helpers `_nearest_strike()` and
`_nearest_expiry_in_dte_window()` are the reusable primitives. The EOD wrapper
(`options_game_plan_snapshot.py:193-256`) adds `expected_move_pct`, `iv_rank_1y`, and real Greeks.

Note its docstring (`:4405-4415`) **explicitly refuses** to suggest strategies the user didn't ask
for. Extending it means deliberately revisiting that stance — worth doing knowingly, not silently.

### Does not exist anywhere

Verified by grep: **no spread-construction code exists in the codebase.** Vertical/credit/debit
spreads, iron condors, straddles/strangles, calendars, poor-man's covered call, and the wheel all
return hits only in tests, guide prose, and email templates — never real logic.

### Recommended approach: a recommender, not a pricing engine

The cheap, honest win is an **IV-regime-conditioned recommender layered over existing primitives**,
rather than new chain math:

```
IV rank >= 70  →  favor SELLING premium   (covered call, cash-secured put, credit spread)
IV rank <= 30  →  favor BUYING premium    (long call/put, debit spread, LEAPS — link the playbook)
IV rank 30-70  →  neutral / directional-only, or skip
```

Cross with the existing signal direction and expected move to pick among them. Each suggestion
reuses `_nearest_strike`/`_nearest_expiry_in_dte_window` for real strikes/expiries and shows real
mid prices — so **every number displayed is a real quote, not a model output.**

Effort: **M.** A vertical spread is genuinely easy (two `_nearest_strike` calls + net debit/credit
arithmetic). Iron condors and calendars are more, and I'd defer them.

### Honesty requirement

Per the platform's own audited findings — and consistent with what the two new Learning pages
already state — suggestions must **not** imply measured edge. This is a *structural* recommender
("given this IV regime, these structures fit"), not a predictive one. It must not consume AI BUY
signals or confidence for strategy selection, both documented as unreliable for that purpose. Link
`/option-trading-guide` and `/qqq-leaps-playbook` for the mechanics rather than re-explaining.

---

## 5. Charting convention — a hard-won precedent to follow

`OptionsChainChart.tsx:1-18` documents this explicitly, and the tracker (T270) records the full
reasoning: **`lightweight-charts` is fundamentally a time-series library** — every series' x-axis
expects a real time value, with no first-class categorical axis. Forcing strikes through it means
either faking timestamps (fragile, confusing on hover/zoom) or writing a custom rendering
primitive. The established alternative is **hand-rolled SVG** following the page's own volume
histogram.

Also mirror the **logic/render split**: pure math goes in `frontend/src/lib/` (see
`lib/optionsChainChart.ts` with `aggregateOiByStrike`, `maxOiAcrossStrikes`, `labelStepFor`,
`hasNoRealOi`), because this repo has **no component-level React test infrastructure** — extracting
pure functions is the only way any of it gets real test coverage.

T270 also left a lesson worth repeating: a sabotage cycle that *passes* isn't proof of
correctness — it can mean a coverage gap. Its `labelStepFor` floor sabotage went uncaught because
no test exercised `strikeCount=0`.

---

## 6. Risks / open decisions

| Risk | Mitigation |
|---|---|
| **Squeeze score silently loses `optionsFlow`** | Keep the flow fetch at page level; verify the Squeeze score still renders after the move (highest-risk item) |
| Eager-fetching the whole tab on mount | Preserve the chain's lazy `chainOpen` behavior; consider per-section lazy loading |
| Overview becomes too sparse | Decide whether a small options *summary* strip (C/P ratio + IV rank + pressure score) stays on Overview linking into the tab |
| Advanced-tier gating | Game Plan is Advanced-gated today. Decide: gate the whole tab, or only the gated panels within it? (I'd keep per-panel gating — a Basic user should still see flow/chain) |
| UW budget for *live* IV rank/Greeks | Per-symbol live calls on a page view are unbounded; snapshot data is EOD but free. Prefer snapshot; if live is wanted, bound it like `_bounded_options_flow_symbols()` does |

---

## 7. Suggested phasing

| Phase | Content | Effort |
|---|---|---|
| **A** | Create the Options tab; move all 4 existing blocks; fix the `optionsFlow` dependency; add `?tab=` deep-linking | **S-M** |
| **B** | Charts 1-4 from §3 (pure frontend, no backend) + `lib/` helpers with tests | **M** |
| **C** | Wire `getOptionsGamePlanBatch` for IV rank / expected move / Greeks (chart 5) | **S** |
| **D** | IV-regime strategy recommender + vertical spreads, reusing existing primitives | **M** |
| **E** | *(defer)* Iron condor / calendar / PMCC; live per-symbol IV rank & Greeks endpoints | — |

**A is worth doing on its own** — it's mostly a move, immediately reduces a 4,680-line file's
Overview bloat, and unblocks everything else. B is the best value-per-effort after that, since it's
frontend-only against data you already serve.
