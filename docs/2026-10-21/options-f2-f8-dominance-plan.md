# Plan: options review F2–F8 and the stock-page dominance summary

Source: [options and dark pool review](../audits/2026-10-21-options-dark-pool-and-dominance-review.md). F1 is closed (production-verified, `6f483f5d`, `0b348acf`). This is a plan, not built. Each phase ships as a separately reviewed change. No trading rules, alert thresholds or notification behaviour change unless a phase says so explicitly.

## Priorities requested

Request-failure visibility, accurate call-heavy / put-heavy labels, source dates and coverage, unavailable premium kept explicit, and estimated turnover under its own label.

## Phase A — make the existing card honest (P0, small, frontend-led)

| # | Change | Where | Acceptance |
|---|---|---|---|
| A1 | **Request-failure visibility.** Distinguish loading, request failed (network / HTTP status, with retry), unavailable (the route's reason) and usable for the Options Flow card, and apply the same states to the dark-pool and market-pressure panels. Today a failed request leaves SWR `data` undefined and the section disappears. | `pages/stock/[symbol].tsx` (SWR `error`), `MarketPressurePanel.tsx`, `lib/optionsFlowStatus.ts` | A 500, a 401 and a network drop each render a visible state; a fast symbol change never shows the previous stock's result. |
| A2 | **Composition labels (F2, F3).** Replace the bullish/bearish badge with **Call-heavy volume / Put-heavy volume / Mixed volume** plus the call and put shares. Compute from unrounded shares; zero puts reads "100% calls in observed volume; C/P undefined (no puts)" instead of a capped 10:1. Keep the API's legacy `sentiment` field for signal-engine compatibility, unused by the page, and add `call_share`, `put_share` and `sample_adequacy` additively. | route `get_options_flow` (additive fields), card UI, `OptionsFlow` type | All-call and all-put cases are symmetric; exact thresholds are deterministic; display rounding cannot change the label; the label never says bullish or bearish. |
| A3 | **Source date and coverage on the card.** Show "Settled session <date> · <n> of <m> expirations · coverage <status>", plus the included expirations. | card UI (fields already returned: `chain_as_of`, `expiries_used`, `expiries_attempted`, `coverage_status`) | Visible without hovering, on mobile and keyboard; "unverified" coverage is labelled as such. |
| A4 | **Premium explicit, turnover separate (F6, UI part).** Make `premium` and whale fields nullable in TypeScript. The premium column reads "unavailable" (with why) instead of "—"; add an **"Est. turnover (volume × mid × 100)"** column from the existing `estimated_turnover` field, labelled as an estimate. Hide the whale badge when the count is unknown, never showing 0. | `lib/api.ts` types, card table | No null rendered as 0; turnover never sits in a column titled premium. |

**Decision needed for A2 thresholds.** Option 1 (recommended for A): keep today's cut points, expressed as shares — call-heavy ≥ 56.5% calls (ratio 1.3), put-heavy ≥ 55.6% puts (ratio 0.8). The label meaning doesn't move. Option 2: the review's symmetric 60/60 proposal, which changes what existing users see. Either is descriptive only; neither has measured predictive value.

## Phase B — correctness behind the card (P1, backend-led)

| # | Change | Acceptance |
|---|---|---|
| B1 | **Expiration scope (F4).** For current research, exclude expirations already past their last trading instant at the source session (exchange calendar); bind all slices to one source session (reconcile each slice's `as_of`); report included and excluded expirations with reasons; carry the archive's completeness evidence instead of a bare "unverified". Historical review keeps its original universe, labelled with its date. | Tests for expired and same-day contracts, holidays, DST, and mixed-session slices (flagged, never silently pooled). |
| B2 | **Estimated turnover in the backend (F6).** `traded_premium` comes only from event-level price × size or a documented provider total with its window; any last-price or midpoint × daily volume is `estimated_turnover`. Whale detection uses actual events only. Validate the multiplier instead of assuming 100. | The non-UW last-price path no longer emits `traded_premium`; a contract-day aggregate can never set `is_whale`. |
| B3 | **Unusual activity disclosure (F5).** Symmetric call and put callouts; state the selection rule (OI > 50, volume > 30% OI, top 3 per side per expiry) with eligible versus displayed counts; a separate zero/low-OI "new listing" category instead of silent exclusion. Pressure shows points with measured components and its maximum, never as conviction. | No put-only callout asymmetry; pressure scores with different component sets are not compared. |
| B4 | **Dark-pool time validation (F7).** Reject prints in the future beyond a documented clock tolerance (proposal: 2 min); quarantine zone-less timestamps as ambiguous instead of assuming UTC; add execution age and time zone to the stock-page table and show the NBBO side as an estimate; word it as "off-exchange prints" unless a venue is verified; soften "EXACT/unambiguously" comments. | The future-timestamp case reproduced by the review is rejected; the table shows age. |
| B5 | **Baseline status (F8).** Expose `baseline_status` = ok / insufficient_history / query_failed on alerts and the panel; label percentiles as relative to captured prints; track ingestion gaps. | A query failure is never shown as "absolute floor only"; tests for each status. |

## Phase C — the reusable dominance summary (P0 per the review, built after A and B1)

One **Options activity and flow** block at the top of every stock's Options tab, from an additive `options_dominance_v1` response.

| Row | Built from | Unavailable when |
|---|---|---|
| Volume | Same session and expirations as the card: call and put share, contracts, label (A2) | Both sides zero → "unmeasured", not mixed |
| Open interest | Same session and scope, summed by right; its own as-of date | OI missing for the scope |
| Traded premium | Event-level premium only (B2) | Not captured → "unavailable — chain marks are not premium" (the usual state today) |
| Aggressor estimate | Flow events in a stated window: (ask-calls + bid-puts − bid-calls − ask-puts) / total, with classified share | No comparable event window → unavailable. Sampled alert events are labelled as a sample, not the tape |

Rules: rows are never filled by joining unrelated windows; disagreement between rows is shown, not reconciled; `directional_intent` stays `not_established`; `actionability` stays `research_only`; zero, missing, not entitled, stale, partial and failed are distinct states. Acceptance criteria 1–8 from the review apply, including the call-selling (call-heavy yet bearish) and put-selling (put-heavy yet bullish) fixtures.

## Phase D — roadmap (later, separately scoped)

Feed and quote-quality dashboard, intraday flow timeline, dark-pool price-zone clustering, position-aware strategy comparison, single-leg outcome attribution, IV surface, portfolio Greeks and the multi-leg simulator, as prioritised in the review. Each needs its own data-coverage check before building.

## Order, size and review

| Order | Phase | Size | Notes |
|---|---|---|---|
| 1 | A1 + A3 | S | Frontend only; can ship first |
| 2 | A2 + A4 | S–M | Additive route fields plus UI; needs the threshold decision |
| 3 | B4 + B5 | S–M | Dark pool; independent of options |
| 4 | B1, B2, B3 | M | Backend correctness; B1 before C |
| 5 | C | M | New endpoint and block; reuses A–B |

Every phase gets behavioural tests at the route and UI boundary (fixtures, never source-text checks), a sabotage check, an independent review before deploy, and a production verification on real symbols.

## Status (2026-10-22)

| Phase | State | Commits |
|---|---|---|
| A (A1–A4) | Deployed (frontend), production-verified on MU | `f18a1b2c`, `93af3549`, `ed320575` |
| A2 thresholds | Option 1 chosen: today's cut points on exact ratios; 60/60 deferred as a separate evaluated change | — |
| B1 + backend A2 | Committed, **not deployed** (restarts market-data; held until the Oct 22 capture finishes) | `ec1a90e4` |
| B2 + B3 | Committed, not deployed | `21813638` |
| B4 + B5 | Committed, not deployed | `51ad8845` |
| C | Not started | — |

Each B slice had an independent review (no blockers; findings fixed), sabotage checks, and the full market-data and frontend suites green. B ships as one market-data deploy (routes.py, options_scope.py, uw_option_chain.py, options_game_plan_snapshot.py, options_flow_snapshot.py, dark_pool_evidence.py, scheduler.py, email_service.py, unusual_whales.py) plus the frontend.

Behaviour changes to know at deploy:
- `OptionsFlowSnapshot.sentiment` is labelled from exact volumes from the deploy onward; boundary rows (e.g. 1296/1000, puts = 0) differ from older rows, and `evidence.composition_rule` marks the new ones.
- Expiries past their last trading instant stay as labelled historical evidence; the game plan, its snapshot and both calculators offer only current expiries (`reason: no_current_expiries` when none).
- Dark-pool prints without a timezone are now quarantined (previously assumed UTC); UW sends `Z` on every print, so none are expected.
- After deploy, check for duplicate prints: `SELECT symbol, price, size, count(*) FROM dark_pool_prints GROUP BY 1,2,3 HAVING count(*) > 1` restricted to post-deploy rows.

Found, not fixed (pre-existing): the premarket "Options flow" note in `scheduler.py` compares the stored snapshot sentiment against bullish/bearish labels, but the snapshot stores call_heavy/put_heavy/mixed, so the note can never fire.
