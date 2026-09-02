## Feature Reference: Mobile Nav Drawer (T251-MOBILE-RESPONSIVE-DESIGN, Phase 1)

**Built 2026-07-17.** A 2026-07-16 audit found the whole app effectively desktop-only — zero
`isMobile`/`useMediaQuery`/`matchMedia` usage anywhere, and the shared nav bar in `_app.tsx`
(logo + up to 6 dropdown groups + search box + user controls, one non-wrapping flex row) is the
single worst offender: it will visibly clip/overflow on any phone-width screen with no fallback
at all, unlike most page bodies which at least degrade to horizontally-scrollable tables.

**What shipped (Phase 1 only — the nav bar, the one component every page shares):**
- `frontend/src/styles/globals.css` — a `.desktop-nav-row`/`.mobile-nav-toggle` CSS pair, swapped
  by a single `@media (max-width: 767px)` block. Above 768px this is a no-op (`.desktop-nav-row`
  is `display:flex` unconditionally, `.mobile-nav-toggle` is `display:none`) — the desktop layout
  is pixel-identical to before this change.
- `frontend/src/pages/_app.tsx` — a new `mobileMenuOpen` state + hamburger button (☰ / ✕,
  `.mobile-nav-toggle`, only visible below 768px) and a new `MobileNavDrawer` component: a
  click-to-expand accordion (not hover — hover has no touch equivalent) over the same
  `NAV_GROUPS` data the desktop dropdowns use, so there is exactly one source of truth for nav
  structure. The drawer also repeats the search box and user controls (settings/logout) at the
  bottom, since those live in the same now-hidden desktop row. The drawer auto-closes on route
  change (a `useEffect` keyed on `router.pathname`) and on any item click, so it never lingers
  open behind a freshly-navigated page.
- Verified via a full `npx next build` (all pages compiled clean, not just the changed one) and
  by grepping the actual compiled `.next/static/css`/`.next/static/chunks` output for the new
  class names, the `max-width:767px` rule, and the hamburger's aria-label — confirming the
  change is really present in what would ship, not just correct-looking in source.

**Not yet built (Phase 2, tracked as the remaining scope on the same tracker item):** per-page
responsive breakpoints for the ~57 files using rigid fixed-pixel-width grids (stock detail's
`1fr 320px` sidebar, positions/insider's 8-column tables, strategies.tsx's `240px 1fr`, etc.).
These pages still don't collapse to single-column on a phone — most are at least wrapped in
`overflowX:auto` so they degrade to scrollable tables rather than breaking outright, which is
why the nav bar (no such fallback) was prioritized first.

**What to check if the mobile nav looks wrong:** `_app.tsx`'s `MobileNavDrawer` function and the
`.desktop-nav-row`/`.mobile-nav-toggle` rules in `globals.css` are the only two places this
logic lives — if the hamburger doesn't appear or the desktop row doesn't hide at phone width,
check the compiled CSS actually contains the `max-width:767px` block (a stale cached build could
serve pre-change CSS, same class of bug as the frontend build-cache issues documented above).

---


## Feature Reference: T230-UX-MOBILE-RESPONSIVE (Phase 2 slice) — Stock Detail Page Grid Collapses on Mobile (Built 2026-07-20)

**Scoped down from the original ask** ("refactor the whole ~4000-line page, ~3 days") to the
single highest-value, lowest-risk slice: the page's ONE genuinely rigid layout. Matches the
same Phase 1/Phase 2 split already established for the Mobile Nav Drawer
(`T251-MOBILE-RESPONSIVE-DESIGN`) — fix the one broken thing that actually clips content off
mobile screens now, defer a full ground-up mobile redesign as its own larger, separately-scoped
item.

**The fix**: the page's outer chart+sidebar layout (`frontend/src/pages/stock/[symbol].tsx`)
was a hardcoded `gridTemplateColumns: '1fr 320px'` inline style — inline styles can't respond
to a media query directly, so a new `.stock-detail-main-grid` class was added to
`globals.css` instead, following the exact same `.desktop-nav-row`/`.mobile-nav-toggle`
breakpoint-class pattern already proven for the nav drawer. Above 768px it's pixel-identical to
the prior inline style; below it, the sidebar collapses to a single column below the chart
instead of being cut off entirely.

**Audited the rest of the page first** to confirm this really was the ONLY rigid layout that
needed fixing, rather than assuming: every other grid/flex container in the file already uses
`flexWrap: 'wrap'` (16 occurrences), self-wrapping `repeat(auto-fill, minmax(...))` grid
tracks, or constrains only small individual elements (badges/icons at 8-48px) rather than large
rigid columns. This means the sidebar's own internal content (AI Signal card, K-Score panel,
etc.) needed zero changes — it already rendered correctly at any width; only the OUTER grid
cutting the whole sidebar off-screen needed the fix.

**Verification is CSS-only, not browser-verified** — no browser/device-emulator tool was
available in this environment to visually confirm real rendered behavior (touch target sizes,
actual scroll behavior, chart legibility at narrow width). What WAS verified: the compiled
production CSS (`.next/static/css/*.css`) contains both the unconditional base rule
(`.stock-detail-main-grid{grid-template-columns:1fr 320px}`) and the correct media-query
override (`@media(max-width:767px){.stock-detail-main-grid{grid-template-columns:1fr!important}}`)
— proving the intended CSS reaches production, but not that it renders as expected on a real
device. Flagged explicitly in the tracker as not fully closed pending an actual visual check.

**Explicitly not done in this pass**: the chart itself was not made touch-pinch-zoomable
(lightweight-charts' default touch handling is used as-is); the page's remaining internal
density (many small stat grids and tables) was not restructured for a genuinely mobile-
optimized reading experience. This fix stops the sidebar from being cut off — it does not
redesign the page for mobile.

**What to check if this looks wrong**:
```bash
# Confirm the compiled CSS contains both the base rule and the breakpoint override:
docker exec stockai-frontend-1 sh -c "grep -o 'stock-detail-main-grid[^}]*}' /app/.next/static/css/*.css"
docker exec stockai-frontend-1 sh -c "grep -o 'max-width:767px)[^{]*{[^}]*stock-detail[^}]*}' /app/.next/static/css/*.css"
```
If either line is missing, the CSS didn't compile/deploy correctly — re-check
`frontend/src/styles/globals.css` and confirm a real frontend rebuild (not just a `docker cp`
hotfix — CSS is baked into the Next.js build) was actually run.

**CORRECTION 2026-07-23 — the original "audited the rest of the page, this was the ONLY rigid
layout" claim was wrong.** A follow-up survey (triggered by re-checking `T230-UX-MOBILE-
RESPONSIVE` against real code rather than trusting its own `done` status, per this file's own
standing "verify in both directions" discipline) found 4 more genuinely rigid, page-width,
multi-column grids still squeezing full panels side-by-side on a phone with no fallback,
missed by the original audit: the K-Score + Fear&Greed side-by-side row (line ~1434, dynamic
`1fr 1fr`/`1fr` depending on which panels have data), the fundamentals Row 3 (Balance Sheet /
Margins / Returns&Growth, a rigid 3-column `1fr 1fr 1fr`), the fundamentals Row 4 (Per Share&Risk
/ 52-Week Range, `1fr 1fr`), and the analyst-ratings Buy Zone / Sell Zone panel (`1fr 1fr`) —
all in `frontend/src/pages/stock/[symbol].tsx`. These are genuinely different from the
`flexWrap`/`repeat(auto-fill, minmax(...))` grids the original audit correctly found safe:
those self-wrap by design; these 4 use a fixed N-up column count with no wrap and no breakpoint.

**Fix**: 4 new CSS classes in `globals.css` (`.stock-detail-kscore-feargreed-grid`,
`.stock-detail-fundamentals-row3-grid`, `.stock-detail-fundamentals-row4-grid`,
`.stock-detail-buyzone-sellzone-grid`), following the EXACT same pattern as
`.stock-detail-main-grid` above — each class's base rule matches its pre-existing inline
`gridTemplateColumns` exactly (a no-op above 768px), overridden to `1fr !important` under
`@media (max-width: 767px)`. The K-Score/Fear&Greed grid keeps its dynamic inline
`gridTemplateColumns` ternary for the desktop case (only shows 2 columns when BOTH panels have
real data) — the mobile `!important` override still wins under 768px regardless of which
inline value the ternary resolves to, so adding the class alongside the existing inline style
was safe without needing to special-case the dynamic grid separately.

**Verification, more thorough than the original pass**: same CSS-only discipline (no browser/
device-emulator available), but this time verified BOTH the compiled CSS (`.next/static/css/*.css`
— confirmed all 4 base rules present, and confirmed the combined `@media (max-width:767px)`
selector correctly bundles all 4 classes together forcing `1fr!important`) AND the compiled JS
page bundle (`.next/static/chunks/pages/stock/*.js` — grepped for each class name and confirmed
all 4 appear exactly once, proving the `className` attributes actually reached the shipped
page, not just the source file). A full `next build` and the existing 89-test frontend vitest
suite (unaffected — no test imports `[symbol].tsx` directly, the same seam gap already
documented for this page) both ran clean.

**Lesson reinforced**: an earlier "I audited the rest of the page and found nothing else"
claim — even one made carefully, with real grep evidence at the time — is not a permanent
guarantee. A large (4000+ line), actively-growing page can accumulate new rigid grids between
audits, or an earlier audit's grep pattern can simply miss instances outside its search scope.
Re-verify a "this was fully audited" claim against live code before trusting it, the same
discipline this file already applies to stale tracker `defaultStatus` claims — this was found
specifically because the next session re-checked rather than assuming the earlier "done"
status meant the page was actually fully covered.

---


## Feature Reference: Mobile Nav Drawer — Scroll Lock, Banner-Aware Height, Duplicate Search Listener (Fixed 2026-07-21)

**Three related bugs in the mobile nav drawer (T251-MOBILE-RESPONSIVE-DESIGN Phase 1),
`frontend/src/pages/_app.tsx`**, all flagged during the AUD256 deep audit and deferred until
now:

**1. BUG-MOBILEDRAWER-DUPLICATESEARCH — duplicate global keyboard shortcut.**
`GlobalSearch` is rendered TWICE in the header — once in the desktop nav row, once inside the
mobile drawer — because the desktop/mobile split is pure CSS media-query visibility
(`.desktop-nav-row`/`.mobile-nav-toggle` classes), not conditional mounting. Both DOM trees
(and both instances' own `useEffect`s) are always live regardless of viewport width, so both
instances registered the SAME global `keydown` shortcut (`Cmd/Ctrl+K`, `/`) — pressing it
tried to focus/open whichever instance happened to be CSS-hidden just as often as the visible
one. Fixed with a new `registerGlobalShortcut` prop (defaults `true`, matching the desktop
instance's existing behavior), explicitly set `false` on the mobile drawer's own `GlobalSearch`
instance — that instance is only ever visible after the hamburger menu is already open, so
there's no "type a shortcut to reveal it" need there.

**2. BUG-MOBILEDRAWER-BANNERHEIGHT — impersonation banner not accounted for.** The drawer's
`maxHeight` hardcoded `calc(100vh - 52px)` (the header's own height) unconditionally, never
accounting for the 33px impersonation banner stacking on top of the header when an admin is
impersonating a user (`top: impersonating ? '33px' : 0` on the header's own sticky
positioning). On a phone with impersonation active, the drawer's available height was
overstated by 33px. Fixed:
```typescript
maxHeight: `calc(100vh - ${52 + (impersonating ? 33 : 0)}px)`,
```

**3. BUG-MOBILEDRAWER-NOSCROLLLOCK — background page scrolled behind the open drawer.** No
scroll lock existed at all — the drawer is just an absolutely-flowed block in the header, not
a true modal overlay, so any touch/scroll gesture over it scrolled the page underneath too.
Fixed with a new effect keyed on `mobileMenuOpen`:
```typescript
useEffect(() => {
  if (!mobileMenuOpen) return;
  const previousOverflow = document.body.style.overflow;
  document.body.style.overflow = 'hidden';
  return () => { document.body.style.overflow = previousOverflow; };
}, [mobileMenuOpen]);
```
Restores the **previous** value (not just `''`) on close/unmount, so a route change (which
already closes the drawer via a separate, pre-existing effect) or any other unmount path can't
leave scrolling permanently disabled.

**No dedicated test file** — `_app.tsx` is the root Next.js app wrapper, tightly coupled to
routing/session context in a way that would need mocking most of the Next.js app shell to test
in isolation. Matches this repo's own established precedent that `_app.tsx`-level fixes are
verified via typecheck + a full production build rather than unit tests (same seam gap already
documented for other `_app.tsx`/`PriceChart.tsx`-only changes elsewhere in this file).

Full 79-test frontend vitest suite (unaffected — nothing imports `_app.tsx`) and typecheck
green.

**What to check if this looks wrong**:
```bash
docker exec stockai-frontend-1 sh -c "grep -o 'registerGlobalShortcut' /app/.next/static/chunks/pages/_app-*.js"
```
Should find a match confirming the fix compiled in. For the scroll-lock and banner-height
fixes specifically, they're only observable by actually opening the drawer on a real
phone-width viewport (or a browser's device-emulation mode) — there's no automated test
covering the visual/interactive behavior itself.

---


## Feature Reference: T230-UX-MOBILE-RESPONSIVE (Phase 2 continued) — Positions Page + Dashboard Now Collapse on Mobile (Built 2026-07-30)

**Scoped down deliberately** from the tracker's full ~57-file audit to the two single
highest-traffic pages after the already-twice-fixed stock detail page: `positions.tsx` (the
page anyone actively holding a portfolio checks constantly) and `index.tsx` (the literal
dashboard landing page at `/`). Matches the same Phase 1/Phase 2 incremental-scope pattern
already established for the mobile nav drawer and the stock detail page's own 2 prior passes.

**Explicitly did NOT trust the prior "audited and found nothing else" claims** — this exact
claim was proven wrong twice already for `stock/[symbol].tsx` alone (documented in this file's
own 2026-07-20 and 2026-07-23 entries). A fresh, exhaustive grep across all 49 page files and
12 component files for rigid `gridTemplateColumns:` occurrences (excluding self-wrapping
`repeat(auto-fill/auto-fit, minmax(...))` patterns) found dozens more across the app —
`alerts.tsx`, `journal.tsx`, `portfolio.tsx`, `board.tsx`, `strategies.tsx`,
`research/[symbol].tsx`, `decide.tsx`, `regime.tsx`, `insider.tsx`, `congress.tsx`,
`sector-rotation.tsx`, `intelligence.tsx`, `forecast.tsx`, `settings.tsx`, and several
admin-only pages — all deliberately left untouched this pass as a documented, scoped-out
remainder rather than silently claimed as covered.

**`positions.tsx` — 4 fixes**:
- 3 straightforward `1fr 1fr` grids (the Add/Edit trade modal's Shares/Price fields, the
  Sector/Market allocation donut-chart row, the Allocation-donut + Best/Worst-performer row) —
  new `.positions-modal-fields-grid`/`.positions-donut-row-grid`/`.positions-highlights-row-grid`
  classes, following the exact `.stock-detail-*-grid` pattern (base rule matches the pre-existing
  inline value, `@media (max-width: 767px) { grid-template-columns: 1fr !important; }`).
- The positions **table** itself needed a genuinely different fix, not a `1fr` collapse: an
  8-fixed-column row (Symbol/Shares/Avg Cost/Cur Price/Mkt Value/P&L$/P&L%/Actions) forced to
  `1fr` would destroy the table's column alignment entirely (every field stacked vertically per
  row, unreadable as a table). Instead wrapped the header + per-position rows in a new
  `.positions-table-scroll` container (`overflow-x: auto` + a `min-width: 640px` floor on its
  direct child) so the table degrades to horizontally-scrollable on a phone — this app's own
  established dense-table fallback convention (already used elsewhere for wide content), applied
  here for the first time on a page that had it missing entirely (confirmed via grep: zero
  `overflowX`/`overflow-x` occurrences anywhere in the file before this fix).

**`index.tsx` (dashboard) — 2 fixes**: the top-of-page US Markets / HK Markets / Portfolio Pulse
3-column row (`.dashboard-markets-grid`, `1fr 1fr auto`) and the nested Buy/Hold/Wait/Sell legend
inside the Portfolio Pulse panel (`.dashboard-pulse-legend-grid`, `1fr 1fr`) — both collapse
cleanly to a single stacked column, same pattern as every other fix in this pass.

**A real JSX-nesting bug caught before shipping, not shipped**: the positions-table wrap
required inserting 2 new opening `<div>` elements (the scroll container + an inner flex-column,
since the header row and the `.map()`'d position rows previously sat as direct siblings inside
one existing flex-column div) — the first attempt at closing them miscounted by one (added only
2 closing tags where 3 were needed, since the pre-existing structure already owed one closing
tag to the ORIGINAL outer div). `npx tsc --noEmit` caught this immediately and precisely
(`JSX element 'div' has no corresponding closing tag`) — fixed by tracing the actual open/close
balance line by line rather than trusting a quick manual count, and re-verified clean.

**Verification, deliberately more thorough than either prior stock-detail pass** (both of which
were later found to have missed real rigid grids elsewhere on the same page): `npx tsc --noEmit`
clean, full 89-test frontend vitest suite unaffected (no test imports either page directly — the
same seam gap already documented for `PriceChart.tsx`/`_app.tsx`-only changes), and a full
`next build` compiling all 51 routes clean (`/positions` 11.3 kB, `/` unchanged in the build
summary since `index.tsx`'s change added negligible bytes). Confirmed the actual COMPILED output
contains the fix, not just correct-looking source: grepped `.next/static/css/*.css` for all 5
new class names' rules (including the shared `@media (max-width: 767px)` block correctly
bundling `dashboard-markets-grid`/`dashboard-pulse-legend-grid` together) and grepped both pages'
own compiled JS chunks (`positions-*.js`, `index-*.js`) confirming the `className` attributes
actually reached the shipped bundles.

**Still no real device/browser verification performed** — same limitation already noted for the
original stock-detail Phase 2 fix (no browser/device-emulator tool available in this
environment). This remains a CSS-compile-time verification only, not a confirmed real-device
render check — flagged explicitly here rather than silently claimed as fully verified, per this
file's own standing discipline around this exact gap.

**Deliberately NOT touched this pass, documented not silently dropped** (real rigid grids found
by the same exhaustive audit, left for a future scoped session): `alerts.tsx` (4 grids,
including a very tight 9-fixed-column subscription-list row), `journal.tsx` (3 grids, including
a 9-fixed-column trade-journal row), `portfolio.tsx`, `board.tsx`, `strategies.tsx` (one grid
has the exact same `'240px 1fr'` shape as the ORIGINAL stock-detail bug), `research/[symbol].tsx`
(9 identical `1fr 1fr` occurrences, one per report tab — fixable in a single shared CSS rule
since they're all the same shape), `decide.tsx`, `regime.tsx`, `insider.tsx`, `congress.tsx`,
`sector-rotation.tsx`, `intelligence.tsx`, `forecast.tsx`, `settings.tsx`, and several admin-only
pages (`admin-health.tsx`, `signal-accuracy.tsx`, `paper-portfolio.tsx`, `horizon-compare.tsx`,
`watchlist-rotation-explainer.tsx`, `improvements.tsx` itself) — all lower-traffic than the two
fixed here. `screener.tsx` and `watchlist.tsx` were confirmed genuinely clean (all flex-with-wrap
or flex-column stacks, zero rigid grids) — not overlooked, actually checked and found safe.

**What to check if this looks wrong**:
```bash
docker exec stockai-frontend-1 sh -c "grep -o 'positions-table-scroll[^}]*}\|dashboard-markets-grid[^}]*}' /app/.next/static/css/*.css"
docker exec stockai-frontend-1 sh -c "grep -l 'positions-table-scroll' /app/.next/static/chunks/pages/positions-*.js"
```
If either shows nothing, the CSS/JS didn't compile/deploy correctly — confirm a real frontend
rebuild (not a `docker cp` hotfix — this is CSS/JSX baked into the Next.js build) actually ran.

**Lesson reinforced (again)**: this is now the 3rd time in this codebase's history that a
"mobile responsiveness" fix on one page was followed by a LATER, independent re-check finding
more rigid grids the original pass missed (stock detail: twice; now this pass's own exhaustive
audit found dozens more across the rest of the app that this session deliberately scoped out
rather than rushing). Treat "I audited page X and found nothing else" as a claim scoped to the
one page it was made about, never as evidence the REST of the app is clean — only an actual
fresh, exhaustive grep across every page file establishes that, and even then only as of the
moment it was run on an actively-growing codebase.

---


## Feature Reference: T230-UX-MOBILE-RESPONSIVE-3 — alerts.tsx Now Collapses on Mobile (Built 2026-08-01)

**Continues the same incremental page-by-page pass** already run against stock detail (twice),
positions.tsx, and index.tsx — each of those passes' own "found nothing else" claim was
explicitly NOT trusted for the next page; instead, `alerts.tsx` was picked because the prior
pass's own exhaustive grep (2026-07-30) had already named it as the next real, deferred
candidate (a 9-fixed-column subscription row), and a fresh grep against the current file
confirmed exactly 4 rigid grids remained — no more, no fewer than the prior pass's own count.

**The 4 grids**: 3 create-alert form rows (bulk-pattern watchlist/pattern/threshold, price-alert
stock/condition/value, signal-subscription stock/horizon/email) — all simple 2-3 column panels
collapsed to `1fr` under 767px via new `.alerts-bulk-pattern-grid`/`.alerts-price-form-grid`/
`.alerts-signal-form-grid` classes, matching the established `.stock-detail-*-grid`/
`.positions-*-grid` pattern (base rule matches the pre-existing inline style exactly — a no-op
above 768px). The 4th — the subscriptions LIST, a 9-fixed-column row (Symbol/Horizon/Email/
Signal/Confidence/Last Sent/Cooldown/Toggle/Delete) rendered once per subscription — got the
same different treatment the positions table needed before it: forcing 9 columns to `1fr` would
destroy the row's alignment entirely, so it's wrapped in a new `.alerts-table-scroll` class
(`overflow-x: auto` + a 760px `min-width` floor) instead. Deliberately a NEW class, not a reuse
of `.positions-table-scroll` — sharing a class name across two unrelated pages risks a future
change to one page's `min-width` silently affecting the other.

**One thing checked and correctly left alone**: the page's OTHER list (`PriceAlertsTab`'s own
alert rows, a separate section from the SignalAlert subscriptions list above) was confirmed
already mobile-safe via grep — it uses `display: 'flex'`/`flexWrap: 'wrap'`, not a rigid grid —
so no fix was needed there. This is why the fix count matches the prior pass's own "4 grids"
tally exactly rather than needing a 5th.

**Verification**: `tsc --noEmit` clean, full 89-test frontend vitest suite unaffected, full
`next build` (51 routes) clean, and confirmed the actual COMPILED output contains the fix, not
just correct-looking source — grepped `.next/static/css/*.css` for all 4 new class rules
(including the combined `@media (max-width: 767px)` selector correctly bundling all 3 form-grid
classes together) and grepped the compiled `alerts-*.js` chunk for both new class names.

**Still no real device/browser verification performed** — same standing limitation already
noted on every prior page in this series (no browser/device-emulator tool available in this
environment).

**What to check if this looks wrong**:
```bash
docker exec stockai-frontend-1 sh -c "grep -o 'alerts-table-scroll[^}]*}\|alerts-bulk-pattern-grid[^}]*}' /app/.next/static/css/*.css"
docker exec stockai-frontend-1 sh -c "grep -l 'alerts-table-scroll' /app/.next/static/chunks/pages/alerts-*.js"
```
If either shows nothing, the CSS/JS didn't compile/deploy correctly — confirm a real frontend
rebuild (not a `docker cp` hotfix — this is CSS/JSX baked into the Next.js build) actually ran.

**Remaining deferred pages** (from the prior pass's own exhaustive 2026-07-30 audit, still
open, not silently dropped): `journal.tsx` (3 grids, incl. a 9-fixed-column trade row),
`research/[symbol].tsx` (9 identical `1fr 1fr` tab-panel grids), `portfolio.tsx`, `board.tsx`,
`strategies.tsx` (one grid has the exact `240px 1fr` shape as the original stock-detail bug),
`decide.tsx`, `regime.tsx`, `insider.tsx`, `congress.tsx`, `sector-rotation.tsx`,
`intelligence.tsx`, `forecast.tsx`, `settings.tsx`, and several lower-traffic admin-only pages.

---


## Feature Reference: T230-UX-MOBILE-RESPONSIVE-4 — research/[symbol].tsx Now Collapses on Mobile (Built 2026-08-02)

**Continues the same incremental page-by-page pass** (stock detail twice, positions.tsx,
index.tsx, alerts.tsx) — `research/[symbol].tsx` was picked because the prior pass's own
exhaustive grep (2026-07-30) had already named it as a deferred candidate with "9 identical
`1fr 1fr` tab-panel grids, fixable in one shared CSS rule." A fresh grep against the current
file confirmed exactly 9 such grids, plus a 10th (`repeat(auto-fill, minmax(160px, 1fr))`, the
peer-comparison card row) already self-wrapping and correctly left untouched.

**The cheapest instance of this bug class fixed so far** — all 9 grids are BYTE-IDENTICAL
(`display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px'`), so this was one new class
(`.research-panel-grid`) applied 9 times plus one `@media (max-width: 767px)` block, with no
shape variation to account for (unlike `alerts.tsx`'s 4 differently-shaped grids) and no dense-
table scroll-wrapper edge case (unlike `positions.tsx`'s/`alerts.tsx`'s table rows).

**One real name-collision check performed before touching anything**: line 437's grid already
carried a `research-tab-panel` class — before assuming that class already handled layout
responsiveness (which would have made this fix a no-op, or worse, meant reusing it for a
different concern), traced its only rule directly: a `@media print` block (in an inline
`<style>` tag at the bottom of the page component) that forces `display: block !important` for
PDF/print export — completely unrelated to screen-width responsiveness. Added the new
`research-panel-grid` class ALONGSIDE the existing one on that single line, rather than
repurposing `research-tab-panel` itself, so print behavior and screen-width behavior stay two
genuinely independent concerns rather than an accidental shared name.

**A real "where did the fix actually land" check, not assumed**: this page's component is
deduped by Next.js into a SHARED chunk (`9915-*.js`) rather than its own
`research/[symbol]-*.js` route chunk — the same dedup this file already documented when the
Research tab was added to `stock/[symbol].tsx` (that page imports and renders the same
`ResearchPage` component directly). An initial verification grep against the page's own
per-route chunk found nothing and could have been mistaken for the fix not landing at all;
re-checked with a repo-wide grep across all of `.next/static/chunks/` instead, which found it
correctly bundled into the shared chunk.

**Verification**: `tsc --noEmit` clean, full 89-test frontend vitest suite unaffected, full
`next build` (51 routes) clean, and confirmed the actual COMPILED output contains the fix —
both `.next/static/css/*.css` (the base rule + the `max-width:767px` override) and the shared
JS chunk it was actually bundled into (found via the repo-wide grep above, not the page's own
chunk).

**Still no real device/browser verification performed** — same standing limitation noted on
every prior page in this series (no browser/device-emulator tool available in this
environment).

**What to check if this looks wrong**:
```bash
docker exec stockai-frontend-1 sh -c "grep -o 'research-panel-grid[^}]*}' /app/.next/static/css/*.css"
docker exec stockai-frontend-1 sh -c "grep -rl 'research-panel-grid' /app/.next/static/chunks/"
```
If the CSS check passes but the JS check finds nothing in the page's own `research/[symbol]-
*.js` chunk, don't conclude the fix is missing — grep the WHOLE `.next/static/chunks/`
directory first, since this page's component is deduped into a shared chunk, not its own.

**Remaining deferred pages** (from the T230-UX-MOBILE-RESPONSIVE-3 pass's own exhaustive
2026-07-30 audit, still open, not silently dropped): `journal.tsx` (3 grids, incl. a
9-fixed-column trade row), `portfolio.tsx`, `board.tsx`, `strategies.tsx` (one grid has the
exact `240px 1fr` shape as the original stock-detail bug), `decide.tsx`, `regime.tsx`,
`insider.tsx`, `congress.tsx`, `sector-rotation.tsx`, `intelligence.tsx`, `forecast.tsx`,
`settings.tsx`, and several lower-traffic admin-only pages.

---

