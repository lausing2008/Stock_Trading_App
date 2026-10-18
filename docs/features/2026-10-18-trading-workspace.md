# Chart and Game Plan workspace

Implemented locally on 2026-10-18. This record does not assert production deployment.

The stock page now opens with a full-width price chart and an attached Game Plan.
The plan shows one scenario, entry, stop, two targets, shares, next step and an
earnings caution. Detailed steps, sizing, evidence, alert drafts and daily-close
monitoring expand on demand. Rules and the optional LLM perspective remain separate.

Only the selected actionable scenario supplies chart overlays. A grounded model
selection uses its validated server setup; waiting, rejected, blocked or stale
scenarios supply none. Changing scenarios replaces the levels rather than combining
them. Clicking a plan price selects the daily chart, highlights that price and
includes it in the visible price scale. Intraday charts explain the daily-close
basis and offer a return to the daily chart. Position-sizing and max-pain overlays
are available through an explicit checkbox. Existing indicator tools remain available;
the initial chart shows candles and volume with fewer overlays.

Signals, Technicals and Company tabs organize supporting widgets. Their contents
remain mounted so tab changes retain inputs and responses. Inactive panels do not
trigger expansion-driven data loads. Arrow keys, Home and End navigate the tabs.
Focus mode isolates the chart and plan; Escape or the exit button restores the page.
Focus mode and tab choice are temporary and do not rewrite saved layouts.

Chart and Game Plan are pinned open and full width. Existing saved hidden, width
and order preferences for them are retained in storage. Supporting widgets retain
arrange, width, hide/restore, reset, export/import and optional account sync controls.
Chart commands, Trade Board saving, daily-close monitoring and alert confirmations
are preserved. No backend trading rules or delivery switches changed.

## Validation

- Full frontend suite: 642 tests passed across 47 files.
- TypeScript: passed (`npm run typecheck`).
- Optimized production build: passed (`npm run build`).
- Mocked desktop and 390px phone browser acceptance: full-width joined chart/plan,
  scenario switching, price focus, tabs, retained inputs, Focus/Escape, no horizontal
  overflow and no runtime errors. No paid model requests or external writes.
- Regression tests cover selected levels, blocked/stale exclusion, LLM selection
  and wait responses, retained widget state, keyboard tabs and saved-layout preservation.

Run `scripts/smoke_trading_workspace.py` with the frontend dev server on port 3018
and `NEXT_PUBLIC_API_URL=/api`. It requires Python Playwright and Chromium and
intercepts all API calls with fixtures. Screenshots show sample data, not live trades.

![Desktop workspace](assets/trading-workspace/trading-workspace-desktop.png)

[Phone preview](assets/trading-workspace/trading-workspace-mobile.png) ·
[Focus preview](assets/trading-workspace/trading-workspace-focus.png)

## Remaining work

Production deployment and authenticated live acceptance remain separate release steps.
Free-form pixel resizing and masonry are not part of this change; supporting widgets
retain the existing responsive grid and width controls.
