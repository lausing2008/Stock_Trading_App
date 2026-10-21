# Options, dark pool and stock-detail dominance review

Review date: October 21, 2026. Reviewed checkout: `b6a55374`. Scope: reusable analysis for **any stock detail page**, as clarified by the user; options trading improvements and an industry feature roadmap.

## Decision

Build one **Options activity and flow** summary at the top of each stock's Options tab. Answer separately: which right dominates volume, which dominates open interest, which dominates observed premium, and what bid/ask execution evidence suggests about direction. Show the source session, selected expirations and coverage beside the answer. A call-heavy chain is not by itself a bullish trading signal.

Fix the null-premium route failure and expose existing evidence fields before adding signals. Reuse the application's strategy, quote eligibility, archive and ledger work. The biggest opportunity is to connect those components into a trustworthy research workflow, rather than add another independent score.

This is a review and implementation specification. Application behavior, deployments, trading rules and notifications were not changed. No current stock-specific market verdict is asserted: this environment has no running Stock Trading App containers, no authenticated market response was sampled, and the requested scope is every stock. All numerical examples below are synthetic.

## What is already implemented

| Capability | Current implementation | Reuse / limitation |
|---|---|---|
| Chain activity | `api/routes.py:get_options_flow` | Aggregates the first four sorted archive expirations; not a real-time trade tape |
| Historical chain, Greeks, coverage | `services/uw_option_chain.py` and chain capture | Settled-session data; pagination evidence and research-only metadata already exist |
| Flow events and directional alerts | `scheduler.py:check_options_flow_alerts`, `unusual_whales.py` | Ask/bid-side premium, sweeps, expiry and freshness controls; different population from the chain summary |
| Shared snapshots | `services/options_flow_snapshot.py` | Now reuses the route and stores composition/evidence; the former separate yfinance aggregation has been replaced |
| Stock Options workspace | `frontend/src/pages/stock/[symbol].tsx` | IV/HV, historical option performance, gain/loss scenarios, strategy plans, chain, pressure and institutional context |
| Common strategy plans | `OptionsGamePlanCard.tsx`, `commonOptionPlans.ts` | Four common strategies with budgets, holdings and conditional research preferences; existing wider strategy matrix remains useful |
| Eligibility and outcome accounting | `option_strategy_ledger.py`, `flow_outcome_eligibility.py` | Frozen single-leg evidence, fees/slippage, unresolved outcomes and eligible cohorts; not a complete multi-leg execution engine |
| Income research and paper engine | `options_income_engine.py`, income backtest | Reuse collateral, lifecycle and settlement work; verify each execution assumption before extending |
| Dark pool context | UW adapter, stored `DarkPoolPrint`, scheduled alerts, `MarketPressurePanel.tsx` | Print storage, execution-time NBBO inference, size/relative thresholds, recency and cooldown already exist |

The [October 8 audit](2026-10-08-options-squeezes-dark-pool-review.md) is historical evidence, not a list of still-open defects. Current source includes premium missingness, shared snapshot aggregation, eligibility filtering, research-only strategy gates and chain coverage work. The [October 18 common-playbook documentation](../features/2026-10-18-common-options-playbooks.md) also describes shipped functionality that should be retained.

## Findings, prioritized

### F1 — High: null premium sorting can remove the entire flow summary

**Reproduced offline against the actual route body.** `get_options_flow` creates unusual rows with `premium=None` for the settled UW adapter, then calls `unusual.sort(key=lambda x: x["premium"], reverse=True)`. Two such rows require a comparison between nulls, which raises `TypeError`. The outer exception handler returns `available:false, reason:fetch_error`. The stock page only renders the card when `available` is true.

The probe's 3,000-call / 1,000-put chain fails with two unusual rows; the same aggregate with one unusual row succeeds. This is a deterministic integration failure, not evidence that the provider is unavailable. Existing helper tests pass without covering this combination.

**Recommended change:** choose an explicit contract ranking basis, such as volume or separately named estimated turnover; keep unknown tape premium null. Use a deterministic tie-break with expiry, right and strike. Add a route test through the real UW adapter shape with multiple null-premium rows. Also render a visible error/stale-data state instead of making the section disappear.

### F2 — High: the page presents composition as bullish/bearish direction

**Source-confirmed.** The backend returns `activity_composition`, `directional_intent="not_established"`, `chain_as_of`, source, coverage and evidence-group fields. The stock page instead displays the legacy `sentiment` badge; its tooltip explicitly directs users to that badge for direction. `OptionsFlow` in `frontend/src/lib/api.ts` omits the evidence fields, and its contract premium/whale types exclude null even though the API emits it.

**Recommended change:** display “Call-heavy volume”, “Put-heavy volume” or “Mixed volume”, alongside percentages. Reserve directional wording for a separately labeled aggressor estimate. Add accurate nullable types and render missingness. Display settled-session date and coverage on this card itself, even if another section already discloses archive prices.

### F3 — Medium: the ratio and liquidity rules produce contradictory readings

**Reproduced offline.** With 1,000 calls and zero puts, the route returns `cp_ratio=10`, `activity_composition=call_heavy`, but legacy `sentiment=neutral` because every non-neutral branch requires at least 100 puts. Conversely, 10 calls / 100 puts produces `bearish`. The volume bar, badge and pressure score can therefore tell different stories. Classification also uses the rounded ratio: 1,296 / 1,000 becomes 1.30 and crosses the nominal 1.3 cutoff.

**Recommended change:** classify using unrounded shares; publish sample adequacy as a separate field rather than neutral sentiment. With zero puts, show “100% calls in observed volume; C/P unavailable (zero puts)” instead of a capped 10:1 presented as the actual ratio. Thresholds need versioning and validation; none of the current constants establishes predictive skill.

### F4 — High for interpretation: expiration selection and coverage are narrower than the card suggests

**Source-confirmed.** Flow uses `sorted(_uw_expiries(...))[:4]`. The archive's expiration list is not filtered against the current session here. Historical data can therefore include expirations that have since expired. Each expiry adapter supplies an `as_of`, but the route later obtains a symbol-level latest date and does not reconcile every slice's session. `_ChainPair` does not propagate the archive's detailed completeness evidence. Route coverage is only `partial` on failed expiry reads, otherwise `unverified`.

**Recommended change:** distinguish historical-session analysis from current-contract research. For current research, select eligible expirations using exchange calendar and actual last trading instant; for historical review, retain the original universe and label its date. Bind all aggregates to one source session, propagate row/page completeness and expose included/excluded expirations. “Four requested expiries returned” is not proof of a complete option universe.

### F5 — Medium: unusual activity and pressure are easy to overread

**Source-confirmed.** Unusual selection requires OI >50 and volume >30% of OI, keeps only three contracts per side per expiry, then displays ten overall. It excludes zero/low-OI contracts and cannot establish that contracts were opened. The put-only callout introduces a visual asymmetry: there is no matching elevated-call callout. The pressure backend already exposes measured components and a variable maximum, but the frontend types/card omit that denominator and comparability information.

**Recommended change:** show symmetric call/put anomalies, the selection rules, full eligible counts and displayed counts. Rank normalized activity against each contract/underlying's own baseline where available. Show pressure as a point total with measured components, not conviction or confidence. Do not compare scores across different component sets. Add a separate new-listing/zero-OI category rather than silently excluding it.

### F6 — Medium: a last price multiplied by daily volume is still not tape premium

**Source-confirmed, conditional path.** `unusual_premium_fields` correctly leaves UW premium null, but if another adapter supplies a positive last price, it labels `volume × last_price × 100` as `traded_premium` and can mark it a whale. Daily volume aggregated at one last price does not establish actual premium paid or one large transaction.

**Recommended change:** reserve traded premium for event-level prices/sizes or a documented provider total with an explicit aggregation window. Label any last-price or midpoint multiplication estimated turnover. Validate multiplier/deliverable rather than assuming every contract represents 100 shares. Whale detection should use actual events or reliably linked order groups, not contract-day turnover.

### F7 — Medium: dark pool inference needs stronger provenance and time validation

**Source-confirmed, plus an offline reproduction of the future-time case.** The stock-detail dark pool endpoint returns execution times, but its table shows only size, price, premium and venue. It drops the adapter's NBBO fields. Stored print identity is a composite of symbol, execution time, price and size rather than a provider event identity/correction chain. The freshness helper only checks `parsed >= cutoff`; a timestamp one day in the future passes. Naive timestamps are assumed UTC.

NBBO placement is useful evidence of likely aggressor side, but existing source comments calling it “EXACT” or “unambiguously seller-initiated” exceed what the method can establish. Quote synchronization, late reporting, special conditions and complex transactions matter.

**Recommended change:** retain execution, report, receipt and quote timestamps when supplied; show age and timezone in the table; reject future events beyond a documented clock tolerance and quarantine timezone ambiguity. Preserve IDs, corrections/cancels and trade conditions where licensed. Describe bid/ask inference as an estimate, with unknown reasons and coverage. Use “off-exchange prints” unless the feed verifies an ATS/dark venue; a reporting-facility code alone does not establish the executing venue.

### F8 — Medium: relative-size alerts measure a sampled print population

**Source-confirmed.** Dark pool selection already has an absolute floor, a 5× median threshold, a 14-day baseline with at least 20 prints, a 90-minute freshness window and a cooldown. The baseline uses records strictly before each event, which is good. Without sufficient history—or on a baseline query error—it falls back to the absolute floor alone. Stored history comes from bounded, periodically fetched provider windows.

**Recommended change:** expose `baseline_status` with separate insufficient-history and query-failure reasons. Label quantiles as relative to captured prints, not all market activity. Track ingestion gaps and provider limits. Add time-of-day and liquidity conditioning only after capture coverage is measured. Do not infer useful trading edge from alert frequency or absolute stock-move hit rates.

## How call/put dominance works today

The main Options Flow card measures **contract volume across up to four archived expirations**. It does not currently provide one reliable answer for all definitions of dominance.

Let `C` be summed call volume and `P` summed put volume. The current displayed ratio is `round(min(C / max(P,1),10),2)`.

| Existing field | Rule / meaning |
|---|---|
| `activity_composition` | Call-heavy at ratio ≥1.3; put-heavy at ≤0.8; otherwise mixed; no put-volume floor |
| `sentiment` shown on page | With P≥100: strongly bullish ≥2; bullish ≥1.3; bearish ≤0.5; slightly bearish ≤0.8; otherwise neutral |
| Volume bar | Calls as `C/(C+P)`; puts get the remaining width |
| Chain Put/Call OI ratio | `put OI / call OI` for the selected expiry; opposite numerator convention from C/P volume |
| Pressure | Ratio imbalance, volume and available whale/GEX inputs; not a side or probability |

Example: C=3,000 and P=1,000 means **75% call volume / 25% put volume, C/P=3.0**, within the observed universe. It does not establish that calls were bought, positions were opened, or buying a call would be profitable. OI can be put-heavy at the same time; that is a different measurement over a different population.

The alert pipeline separately maps ask-dominant calls and bid-dominant puts to a bullish estimate, with the reverse mapping bearish. It filters neutral/unknown execution splits and has event-time controls. Its selected sweeps/large events are not a complete tape and must not be relabeled whole-market flow. Provider documentation also distinguishes execution side from opening/closing intent and identifies possible multi-leg trades. [UW flow documentation](https://docs.unusualwhales.com/features/2-options-flow/)

## Proposed reusable stock-detail report

Place this above strategy plans, with one shared expiry scope and source-session selector. Preserve specialist details below it. Suggested presentation, using synthetic examples:

```text
OPTIONS ACTIVITY AND FLOW                              Research only
Session: 2026-10-20 close · Source: UW settled chain
Scope: selected expirations · Coverage: unverified

Volume:          CALL-HEAVY     Calls 75% / Puts 25% · 4,000 contracts
Open interest:   PUT-HEAVY      Calls 40% / Puts 60% · selected scope
Traded premium:  UNAVAILABLE    Chain marks are not paid premium
Aggressor flow:  UNAVAILABLE    No comparable event window loaded

Interpretation: Call activity leads volume; positioning differs.
Direction is not established. Fresh quotes required before entry.
```

Do not populate the OI or aggressor rows by joining unrelated windows just to fill the card. Build each row from coherent data and explicitly identify scope differences. Different indicators may disagree without either being wrong.

### Calculation contract

| Dimension | Calculation | Required treatment |
|---|---|---|
| Volume | `call_share=C/(C+P)`; put share is complementary | Unknown if total zero or a side is missing; observed share may be shown on partial coverage with a provisional label |
| OI | Sum calls and puts for the same session/scope | Show OI session separately; do not treat intraday volume as an update to OI |
| Actual premium | Sum event price × contracts × verified multiplier by right, or documented provider premium totals | Unknown where not captured; keep estimated turnover a separate metric |
| Aggressor estimate | `B=ask-call premium + bid-put premium`; `S=bid-call premium + ask-put premium`; imbalance `(B-S)/(B+S)` | Count each event once; preserve midpoint/unclassified premium and classification coverage |
| Delta flow, later | Sum aggressor sign × signed option delta × contracts × multiplier | Delta-equivalent shares; multiplying by contemporaneous spot gives dollar exposure. Not inferred dealer inventory |
| Activity anomaly | Compare same-scope activity with point-in-time historical distributions | Show baseline dates, sample count and capture gaps; no fabricated percentile |

Use event-level classification where possible. Avoid adding overlapping provider alert aggregates as though they were distinct transactions. Quarantine or separately report suspected multi-leg/stock-option activity until reliably grouped. Opening intent remains unknown without sufficient evidence; next-day OI changes provide context, not a per-trade reconstruction. [OIC open-interest explanation](https://prd-web.optionseducation.org/news/open-interest-why-it-matters), [Cboe complex-order mechanics](https://www.cboe.com/markets/us/options/trading/complex-orders)

For a first descriptive policy, consider call-heavy at ≥60% call share, put-heavy at ≥60% put share and mixed in between. This symmetric policy is a **proposal**, not a measured optimum, and differs from existing ratio thresholds. Compute from full precision. Keep activity status separate from data adequacy; partial coverage must never receive a definitive market-wide verdict. Evaluate liquidity floors by instrument and intended horizon before choosing production defaults.

An additive response contract could contain:

```json
{
  "schema_version": "options_dominance_v1",
  "symbol": "<requested symbol>",
  "source_session": "<ISO exchange session>",
  "scope": {"expiries": [], "dte_bucket": "selected"},
  "coverage": {"status": "unknown", "reasons": []},
  "volume": {"call": null, "put": null, "call_share": null, "label": "unknown"},
  "open_interest": {"as_of": null, "call": null, "put": null, "label": "unknown"},
  "traded_premium": {"call": null, "put": null, "label": "unknown"},
  "aggressor": {"window": null, "imbalance": null, "classified_share": null},
  "directional_intent": "not_established",
  "actionability": "research_only"
}
```

These are proposed fields, not an implemented endpoint. Include quality reasons and policy/version identifiers in the final schema. Maintain compatibility for existing consumers while migrating legacy sentiment labels. Zero, missing, not entitled, stale, partial and failed are distinct states.

### Acceptance criteria

1. Each stock renders loading, unavailable/error, stale, partial or usable results visibly; a failure cannot silently erase the section.
2. Two or more null premiums render without a route failure. Unknowns remain unknown throughout JSON, TypeScript and UI.
3. Both-zero volume is unmeasured; all-call and all-put observations are symmetric; ties and exact thresholds are deterministic. Presentation rounding cannot change classification.
4. Expiry, source-session and exchange-calendar boundaries are tested, including expired/same-day contracts, holidays and timezone changes. Historical and current research scopes remain distinct.
5. Premium uses unique eligible events, with corrections/cancels where available. Multi-leg and unclassified events disclose their contribution and do not manufacture direction.
6. A call-selling-dominant fixture can be call-heavy and directionally bearish; buying puts can be put-heavy and bearish; selling puts can be put-heavy and bullish.
7. The selected expiry filter, charts and report agree on scope or explicitly show the difference. Fast symbol changes cannot show another stock's results.
8. Mobile and keyboard users can inspect provenance and quality without hovering. No quantity or executable entry claim appears without the existing eligibility gates.

## Industry feature roadmap

Effort bands are relative: S = one focused change; M = several coordinated components; L = a new data/lifecycle capability. They are not delivery commitments or vendor price estimates.

| Priority | Feature / user value | Reuse and dependencies | Success criterion | Effort |
|---|---|---|---|---|
| P0 | Reliable dominance summary | Fix F1; shared aggregation and typed evidence from F2–F5 | Every symbol gives an explainable result or an explicit unavailable state | M |
| P0 | Feed and quote-quality dashboard | Existing capture coverage; timestamps, counts, age, parser failures, quota metrics | Detect a missing/stale/partial feed before a user mistakes it for no activity | M |
| P0 | Dark pool timing and provenance | Existing print table/adapter; F7–F8 | Future/undated evidence cannot become a fresh directional alert | S–M |
| P1 | Intraday flow timeline | Existing trade/alert adapters; unique events, conditions, appropriate entitlements | Filter 5m/30m/session, DTE and moneyness; expose classifiable premium and gaps | M–L |
| P1 | Dark pool activity map | Stored prints; price/time clustering, baseline coverage | Show repeated price-zone activity, not invented support/resistance certainty | M |
| P1 | Position-aware strategy comparison | Common plans + strategy matrix + ledger gates | Compare researched alternatives using actual holdings, cash and risk constraints | M |
| P1 | Real option outcome attribution | Existing single-leg ledger; fixed contract identity and point-in-time quotes | Reproducible net P&L and unresolved reasons, distinct from stock-return studies | L |
| P2 | IV surface and event-volatility workbench | Existing IV/HV/Greeks; synchronized expiry/strike observations | Show skew/term structure with quality checks; compare earnings-implied move to matched historical moves | M–L |
| P2 | Portfolio risk cockpit | Holdings, reservations, verified contract specs | Net Greeks, concentration, spot/IV/time shocks, assignment and collateral exposure | L |
| P2 | Multi-leg lifecycle simulator | Existing structures and ledger extended to fixed legs | Correct entry/exit, fees, partial fills, exercise, assignment and adjusted deliverables | L |
| P2 | Alert research and calibration console | Eligibility/cohort and delivery accounting already exist | Distinguish discovered, eligible, sent, opened, resolved and profitable observations | M–L |

### High-value extensions to those features

**Trading decisions.** Compare buying a call with a bull-call spread and stock exposure under the same price/time/IV scenarios; compare protective puts and collars against portfolio drawdown reduction. Add earnings, ex-dividend, liquidity, last-trading-time and corporate-action coverage. Present expected value only when a documented probability model exists; an expiry payoff chart and a target-price ROI ranking do not supply that model. Add scenario-dependent mark-to-market P&L before expiration, rather than extrapolating expiry intrinsic values.

**Volatility.** Add 25-delta call-minus-put risk reversal with the sign convention shown, fixed-delta term structure, calendar spreads and matched-horizon implied-versus-realized volatility research. Reject stale/mismatched snapshots and label interpolation. Add surface consistency checks before strategy ranking. Current IV/HV and chain components provide a starting point; a reliable surface requires better synchronized observations.

**Dark pool intelligence.** Cluster repeated prints by price and event time; provide dollar volume, unique events, classified/unknown shares and normalized size percentiles. Compare subsequent stock returns against matched liquidity, sector, time-of-day and volatility controls. Test whether combining flow and off-exchange activity adds value beyond either alone. Avoid treating correlated chain/GEX/pressure measures as independent confirmations.

**Dealer exposure.** Show GEX model, units, source date, expiration universe and assumed dealer signs. Offer sensitivity under alternative assumptions. Gross option volume does not determine dealer net hedging: offsetting customer activity may leave little net exposure. [Cboe positioning research](https://www.cboe.com/insights/posts/volatility-insights-evaluating-the-market-impact-of-spx-0-dte-options/)

**Lifecycle and portfolio.** Extend the existing single-leg ledger before claiming strategy-level profitability. American-style options introduce early exercise/assignment; cash settlement, exercise style and deliverable can differ by instrument. Build ex-dividend and expiration handling into short-option plans. [OIC exercise mechanics](https://prd-web.optionseducation.org/optionsoverview/exercising-options), [OIC equity versus index options](https://prd-web.optionseducation.org/advancedconcepts/equity-vs-index-options)

**Research validation.** Use exchange sessions and point-in-time inputs; split by time with an embargo for overlapping holding periods, group uncertainty by symbol/session and retain an untouched evaluation period. Include all eligible candidates, not just delivered winners. Report costs, drawdown, expectancy, tail loss, capacity and unresolved coverage. Measure incremental value over simple baselines. Underlying directional accuracy, absolute moves and profitable option trades need separate report cards.

## Data and architecture choices

Use the connected capability already represented in code before buying another feed. Public UW documentation shows richer contract/trade data possibilities, but endpoint entitlements, history depth, timeliness, completeness, rate limits, retention and redistribution rights must be verified against this account before implementation. No assumption about a new subscription price or account permission is made here. [UW developer documentation](https://unusualwhales.com/developers)

Use FINRA ATS/non-ATS aggregates as delayed structural context and reconciliation, not as a substitute for an intraday event feed. Weekly publication has instrument-dependent delays; off-exchange includes more than ATS dark pools. [FINRA API documentation](https://developer.finra.org/docs), [FINRA execution-venue explanation](https://syndication.finra.org/content/where-do-stocks-trade)

Keep raw observations and normalized events with provenance, then aggregate once into snapshots consumed by routes, UI, alerts and research. Reuse current persistence and caches. A small versioned aggregation service is sufficient initially; streaming infrastructure should follow measured latency/volume requirements. Store source session, event/report/receipt times, coverage, conditions and model/policy versions. Cache on symbol, scope, source session and aggregation version so incompatible readings cannot share a result.

Recommended delivery sequence: **correctness and evidence → reusable stock summary → intraday event context → fixed-contract outcomes → portfolio and multi-leg expansion**. Treat signal thresholds as hypotheses until the outcome system can test them.

## Evidence and validation

- [Offline probe](evidence/2026-10-21-options-dark-pool-review/probe.py) executes the current route/helper bodies with controlled adapters; [results](evidence/2026-10-21-options-dark-pool-review/probe-results.json) reproduce null-sort failure, composition/legacy-label disagreement, rounded-threshold behavior and future dark pool acceptance. These are synthetic cases, not live market measurements.
- Backend: **147 tests passed**, covering pressure, snapshots, evidence, flow-tab routes, dark pool routes/thresholds/side/freshness, ledger and option-alert freshness. Three existing deprecation warnings.
- Frontend: **53 tests passed** across common option plans, flow decision review, direction cards and Options-tab chart shaping. Existing Vite CJS deprecation notice.
- [Source manifest](evidence/2026-10-21-options-dark-pool-review/source-manifest.json) records hashes for reviewed entry points. Passing focused tests does not negate the uncovered probe failures.
- No authenticated browser session, production database query, live feed/entitlement audit, performance study, order execution or notification delivery was performed. No new profitability claim is supported by this review.

Reproduce the probe from the repository root:

```bash
python3 docs/audits/evidence/2026-10-21-options-dark-pool-review/probe.py
```

## Status update — F1 fixed and deployed (2026-10-21, commits `6f483f5d`, `0b348acf`)

F1 was a **live production outage**, not only an offline reproduction: there were 9,571 `options_flow.error` warnings ("'<' not supported between instances of 'NoneType' and 'NoneType'") on 2026-10-21, and MU, NVDA, AAPL, TSM and AMD all returned `fetch_error`, so their Options Flow card was hidden. Fixes:

- Unusual contracts are ranked by contract volume with a deterministic tie-break (vol/OI, expiry, side, strike) through `rank_unusual`; unknown premium stays null.
- Every unavailable reason now shows a visible status on the stock page instead of removing the section.
- Failed reads of every expiry return `chain_read_failed`, never `no_volume`.
- A route-level test runs the real route body with several null premiums and fails on the old sort.

Verified after deploy: all five symbols are available again (22–24 unusual contracts each), with zero `options_flow.error` in the first 15 minutes.

Restored as a side effect: end-of-day flow snapshots, `opt_cp_ratio` ML coverage, and signal-engine `options_sentiment` reasons for those symbols (scores unchanged, since the directional adjustment is fixed at 0).

Still open from this review: F2–F8, the dominance summary, a visible state when the request itself fails (network/5xx, where SWR data stays undefined), and premium display ("—" on most rows).
