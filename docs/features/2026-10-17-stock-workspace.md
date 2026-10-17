# Stock detail workspace — 2026-10-17

Status: implemented locally; not deployed. Tracker: `STOCK-WORKSPACE`, tier 451.

## Design

The former independent chart/sidebar stacks made the bottom of the page follow the taller sidebar, leaving a large empty main column. One responsive grid now holds the stock's trading widgets. The default order prioritizes the chart, Game Plan, AI signals, confluence and signal subscriptions. Supporting research starts collapsed. Company financials and analyst research are also collapsed below the workspace. Stock-page signal strength explanations, factor breakdowns and conviction checklists expand on demand while headline values, gate result and warning chips remain visible. The header's metrics and refresh controls now wrap on phones.

Desktop uses three columns, tablet two and phones one. The chart and larger research groups span two columns when space permits. The grid follows the saved order rather than visually packing cards into a different reading order. Expanding a tall card can leave space beside it in that row; this is not a free-form masonry canvas.

## Compact Game Plan

The rules-based view shows one selected scenario at a time: entry reference or original cost, stop, two profit objectives with reward/risk, share count and three guidance steps (trigger, invalidation, earnings). Scenario selection also determines the exact setup saved to the Trade Board. Blocked/stale states and reasons stay visible. Selecting the LLM perspective exposes its explicit generation control; merely switching views never generates or bills a model call.

Sizing detail, nominal capital and P/L, supporting levels, rules, historical records, evidence and model explanations remain available through collapsed disclosures. Alert drafts have their own disclosure and retain explicit confirmation. Price timestamps, currency, refusals and missing-input warnings remain visible. No calculations, model admission, alert delivery switches or trading rules changed.

Switching perspective hides the other view without discarding its inputs or generated response. An already-started model request continues unless explicitly cancelled or the plan unmounts/changes; the existing cancellation disclosure remains beside its controls. Collapsing/reordering a widget also preserves its mounted state. Layout actions do not save plans, generate model answers or create alerts.

## Arrange widgets

Choose **Arrange widgets** to expose handles and earlier/later buttons. Desktop handles support native HTML drag-and-drop onto another widget's outlined title bar; chart canvases and form bodies are not drop targets. The buttons provide keyboard and phone operation without relying on touch dragging. **Reset layout** restores default order and expansion states.

Order and expansion state are stored in localStorage under the current account's workspace key, shared across stock symbols in that browser. Corrupt/unavailable storage falls back to a usable in-memory layout. Unknown saved widget IDs are ignored; newly introduced widgets append in default order. Widgets without data do not leave empty frames.

The movable units are 17 named workspace groups, including combined K-Score/sentiment and technical-level/pattern groups. Individual subcards inside those groups, the page header, full financial research, news, dividends and institutional holdings are not independently draggable in this slice.

## Verification

The full frontend suite passed **620 tests**. Focused coverage exercises saved expansion, order restoration, reset, internal drag/drop gating, keyboard movement, retained component state, account isolation, corrupt/unavailable storage, compact disclosures, scenario-to-Board consistency and model-view selection. After finalizing title-bar drop targets, the 15 affected workspace/plan/signal tests passed again. TypeScript passed.

Browser checks used the actual stock route with intercepted sample API responses at 1440-pixel desktop and 390-pixel phone widths. Checks passed for both plan perspectives, native dragging onto title bars, move buttons, persisted order after reload, reset and full-page mobile width without horizontal overflow. Dropping onto chart canvases was unreliable, so title bars are the explicit outlined drop zones; this protects chart interaction. The seeded browser session exposed an existing authentication hydration mismatch in unchanged `_app.tsx`; its development overlay was dismissed for interaction checks and hidden in screenshots. No other runtime errors were observed. Live sign-in and paid-model quality were not evaluated in this UI slice.

Previews use sample data, not current market recommendations: [desktop](assets/stock-workspace/desktop.png), [phone](assets/stock-workspace/mobile.png).

## Deferred

- Cross-device/account-server layout sync; this slice is browser-local.
- Free-form resizing and pixel-positioned masonry; meaningful default grouping and stable reading order take precedence here.
- Splitting every research subcard and the full financial section into independently movable widgets.
- Production deployment, using an off-host frontend build after review. Do not compile this frontend on the production host; see the 2026-10-16 incident record.
