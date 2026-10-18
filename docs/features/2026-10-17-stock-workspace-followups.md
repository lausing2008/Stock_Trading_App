# Stock workspace follow-ups — 2026-10-17

Status: deployed and verified in production on 2026-10-17. Tracker 452: `STOCK-WORKSPACE-CONTROLS`, complete.

The remaining practical workspace controls are implemented:

- Every main page panel participates in the same arrangement, including financial research, copilot, alerts, dividends, ownership and news. K-Score breakdown, radar, sentiment, support/resistance, Fibonacci and each pattern panel move independently. The stock identity/header stays fixed.
- Arrange mode offers one-column, two-column and full-row widths. The phone layout remains one column. Widths persist alongside order and expansion preferences.
- Widgets can be hidden and restored through **Hidden widgets**. Hiding preserves mounted inputs, plan responses and component state; it does not cancel a model call. The existing explicit cancellation control remains available when restored.
- **Export layout** downloads only versioned arrangement preferences. **Import layout** validates a JSON file before applying it. It accepts original browser layouts, rejects malformed/oversized files and ignores a late import if the account changed or the workspace unmounted. No prices, positions, credentials or username are exported.
- Imports and reset do not create alerts, plans or model answers. Layout storage remains scoped by browser and account; original saved arrangements migrate without dropping their existing choices.
- Storage write failures now explain that changes last for the visit and recommend export instead of claiming persistence.
- Dividends and ownership load only when their widget expands. Empty lazy panels remain reachable before their first fetch. Provider failures display unavailable/retry guidance instead of an endless loading label.
- The authenticated app shell now begins with identical server/client state and reads browser session state in its existing effect. This fixes the hydration mismatch observed during the first redesign's browser review without changing authentication or API authorization rules.

## Validation

The frontend suite passed 626 tests and TypeScript. Added checks exercise legacy layout migration, schema bounds, unsupported versions, hidden state retention, persisted widths, import application, account-switch races and lazy panel reachability. Browser acceptance exercises the actual stock route with intercepted API fixtures: native drag, move buttons, persistence, reset, width selection, hiding/restoring a built plan, export/download/import round trip, provider-failure display and full-page phone width. Hydration/runtime errors are now asserted absent, with no exceptions or ignored errors.

The native drop zone remains the outlined title bar, preserving chart interactions. Grid widths are bounded choices rather than pointer-driven pixel sizing; row gaps can still occur when neighboring cards have different heights. Reading and keyboard order stay aligned with the saved arrangement.

Sample-data previews: [desktop](assets/stock-workspace-controls/desktop.png), [phone](assets/stock-workspace-controls/mobile.png).

## Remaining optional work

Automatic cross-device sync still needs an authenticated account-preferences API with persistence and conflict/version handling. There is no existing general layout-storage endpoint to reuse. Portable export/import provides a manual cross-device path now; automatic sync is explicitly not claimed. Arbitrary pixel resizing/masonry remains a separate layout-engine change rather than a requirement for moving and sizing these widgets.

Future production releases must be built off-host. This change affects frontend only; no database schema, alert delivery, backend trading logic or model provider is changed.

## Production evidence

Release `7cee46a6` was built off-host from an isolated committed checkout as `stockai-frontend:workspace-7cee46a6`. The exact optimized image passed the desktop/mobile fixture browser checks, including import/export and absence of hydration/runtime errors. The transferred archive passed SHA-256 verification; loaded image configuration digest was `sha256:30e3583b19319054be4292b028360e625f5b5b28d447d08c7c8326a8e5777a9e`.

Activation used the authorized SSH key, a verified fast-forward bundle from `daca1ac0`, and the host deployment lock. Only frontend was recreated with `--no-deps --no-build`. The previous image remains `stockai-frontend:rollback-before-workspace-7cee46a6`. No production build or reboot was needed.

Acceptance: **15/15 healthy**; preflight **0/12 backend drift**, with no backend/shared changes in the bundle and all other container start times unchanged. Public stock-page build ID **`1EbPrAsQiDi-eS4ZG8y7l`** matched the verified local image. Served stock and tracker bundles contain the new controls and tracker 452. The unauthenticated Game Plan route returned **401**; the risk-delivery flag stayed absent/off. Database checks measured **0 blocked sessions** and **0 idle transactions over 60 seconds**. Host I/O settled to 0.2% disk utilization and 0–0.1% iowait after image activation.

Deployment generated no model answers, trading actions or alert drafts. GitHub publication remains dependent on a repository login with write access, as recorded in the initial workspace release; production and its documentation were delivered through SSH.
