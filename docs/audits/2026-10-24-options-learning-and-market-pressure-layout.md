# Options/Dark Pool Learning tutorial and Market Pressure layout repair

Date: October 24, 2026, America/Los_Angeles. User requested a Learning page covering all newly shipped features and supplied a screenshot of the narrow, overflowing dark-pool table. Validation, native-browser evidence and deployment are recorded below.

## Tutorial and navigation

New route: `/options-dark-pool-tutorial`, linked as **Options & Dark Pool Tutorial** in Learning. It uses the same login plus Advanced/admin page guard as the existing Learning guides. Game Plan links to the tutorial, and the prints card links directly to its off-exchange section. The stock example deep-links to `/stock/TSM?tab=options` using the existing tab query support.

The page explains call/put volume composition and separate OI dominance, partial source/expiry coverage, paid premium versus estimated turnover, unknown measurements and estimated moneyness, all four strategies' declarations and risk/capital scopes, horizon/holding/expiry fit, shared delta/DTE controls, bounded search and rejection reasons, comparison-table sorting/selection, quote/calendar provenance and optional earnings filter, GEX/OI/max-pain/NOPE context, off-exchange time/side/notional/venue and sampled-baseline limits, held fills/unsupported settlements, and loading/error/retry behavior. It keeps goal fit, issuer confirmation, execution evidence and validated return forecasts distinct.

Twelve anchored topics and troubleshooting make the long lesson navigable. A fictional covered-call exercise reuses the actual comparison component: original strike 110, alternatives 115/105, $100 stock reference, fixed November 2 scenario date and December 18 expiry. The example numbers are internally consistent and explicitly not TSM quotes or recommended trades. Sorting and selecting change only local lesson state and make no feed requests. Normal app-shell requests and existing stock-page polling are unchanged.

Market-structure explanations cite [FINRA: where stocks trade](https://syndication.finra.org/content/where-do-stocks-trade), [Investor.gov: ATSs/dark pools](https://www.investor.gov/introduction-investing/investing-basics/glossary/alternative-trading-systems-atss), and [OIC: assignment](https://www.optionseducation.org/referencelibrary/faq/options-assignment). These support the limited venue/assignment context, not optimal settings, probability estimates or a claim of trading edge.

## Screenshot defect and fix

The Market Pressure grid used `repeat(auto-fit, minmax(220px, 1fr))` for all children. The dense six-column prints table therefore occupied a single ~226 px card at the screenshot's 1449 px width, with its content outside the card. A 1024 px fixture also made the whole document 1074 px wide. Long timestamps and baseline text wrapped into a tall narrow column. Default grid stretching made other cards unnecessarily tall.

The fix targets this component:

- Chart cards wrap around a 360 px target using a parent-width-safe minimum, with `min-width:0` and start alignment.
- Prints and expiration-concentration tables span the full grid row.
- Focusable, named scroll regions contain tables at narrow widths; print cells remain on one line and horizontal scrolling stays inside the card.
- Add an Off-exchange prints heading, clearer dense-table header/status contrast, a scroll hint and a direct help link.
- Label the equity print amount **Notional ($)** instead of Premium. The existing provider field/price-times-size fallback and all values are unchanged; this distinguishes it from an option premium.

The grid's other positioning/chart calculations, feed states, clock, polling, retries, API routes and signal/trading settings are unchanged. This is a frontend-only release.

## Native-browser reproduction and limits

The fixture compiles the actual MarketPressurePanel, its real SVG charts/status helpers and the actual stylesheet, using synthetic feed responses and a fixed clock. Chrome renders this offline HTML at 1449, 1024 and 390 px. It does not forge a production login or use live market data. The tutorial content is similarly rendered offline; default page guards are tested separately in DOM tests and the navigation/page-guard parity suite.

| Viewport | Before prints-card width | Before document width | After prints-card width | After document width |
| --- | --- | --- | --- | --- |
| 1449 | 226.17 | 1449 | 1417 | 1449 |
| 1024 | 239 | 1074 | 992 | 1024 |
| 390 | 358 | 390 | 358 | 390 |

At 390 px, the prints table is 720 px wide inside a 324 px scroll viewport; the card/page do not overflow. Keyboard scrolling is exercised in the final witness. The expiration table is also independently scrollable. Tutorial checks validate all twelve native topic anchors, sample-table expansion and no document overflow at the same widths. DOM tests exercise real practice sorting/selection plus unauthenticated/basic rejection and permitted access.

These are native layout and local interaction checks, not authenticated production browser acceptance or a fresh validation of provider data. A before/after witness is saved separately from the original user screenshot; synthetic layout data is labeled as such.

## Validation and rollout

Focused checks: 106 passed (tutorial, Market Pressure, navigation/guard parity), full typecheck clean. Full frontend suite: **945 passed across 77 files**, full typecheck and whitespace clean. Application commit `c93d1695df3be4dd83d9b33837a514baa0b99e85` was pushed to the authorized public `prod` branch. Production rollout verification is recorded below. Backend source is unchanged; preceding validation remains 5,849 passed / 26 skipped, not a new backend test run. Initial tutorial test selectors were tightened to the practice section and functional anchors; temporary offline renderer setup was corrected for module/JSX interop before successful browser runs.

### Deployment preparation

The first pinned deployment stopped before building or recreating any service because the custom disk guard required 25 GiB (rounded `df -BG` output). Dangling-image cleanup reclaimed 1.602 GB but left the host at 24G. Under the same host-wide deployment lock, pruning only unused build cache reclaimed another 676.3 MB and restored 25G. Tagged rollback images, volumes and running containers were preserved. The same exact application revision was then retried, built fresh from EC2 production settings with the classic Docker builder, and retained the prior frontend image for rollback.

### Final production verification

Application **`c93d1695df3be4dd83d9b33837a514baa0b99e85`** is deployed with frontend build ID **`hGq1HqqUrzvkapUP75fDb`**. The fresh EC2 build loaded `.env.production` and generated **77/77 pages**, including the new tutorial. Local build artifacts were not shipped.

- All **15 containers healthy**, with unchanged start times for **all 14 non-frontend containers**.
- All **12 backend source/shared digests match** the preceding unchanged source baseline.
- Public HTTP 200 for the tutorial, TSM, Improvements, home, Signal Tuning and Signal Accuracy.
- Served page build IDs match the running/prepared frontend; compiled tutorial/navigation, table/help, tracker markers and responsive CSS are present.
- No dark-pool ledger capture/persist errors in the checked 15-minute log window.
- Prior image retained as `stockai-frontend:rollback-20261024T153734Z`; final disk output 22G available. The guard is a pre-build margin, not a claim that post-build free space remains at least 25G.

The Improvements page includes `OPTIONS-DARK-POOL-TUTORIAL` and `MARKET-PRESSURE-TABLE-RESPONSIVE` as done, with this report linked. The complete session index is updated through the existing `.claude/CLAUDE.md` pointer. Final evidence/docs are committed separately and synchronized without rebuilding or restarting services. Production checks verify delivery and health; authenticated interaction remains covered by local tests/offline browser witnesses rather than a claimed live logged-in browser run.

## Open boundaries

This lesson and layout repair do not add live quote observations, issuer-confirmed event coverage, complete tape coverage, validated option-return forecasts or optimal settings. Dark-pool provider correction identity/gap semantics and the other open items remain in the [complete session index](../2026-10-24/session-index.md).

[Evidence, screenshots and final verification](evidence/2026-10-24-options-learning-layout/README.md).
