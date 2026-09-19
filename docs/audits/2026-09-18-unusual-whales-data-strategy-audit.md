# Unusual Whales Data Strategy Audit

**Audit date:** 2026-09-18 (America/Los_Angeles)  
**Repository reviewed:** local/origin `prod` at `f92584c`  
**Production observed:** EC2 at `2a185378`  
**Scope:** current Unusual Whales integration, production data coverage, signal and alert use, options research and execution, and a practical path from decision support to paper and controlled live trading  
**Method:** source review, existing project documentation, read-only production PostgreSQL/Redis queries, and current official Unusual Whales documentation. No provider calls, trades, settings changes, deployments, or production writes were made.

**Reviewed and corrected later on September 18:** this document's original measurements describe `f92584c` / production `2a185378`; they are not current deployment assertions. The subsequent [report review](2026-09-18-uw-and-broker-report-review.md) checks newer local/production `7fb03da`, corrects overstatements, and records T410's independent-outcome implementation. See also the [email/playbook audit](2026-09-18-alert-email-accuracy-and-uw-playbooks-audit.md). Historical count tables below retain their original observation window unless explicitly dated otherwise.

## Executive assessment

The platform has a substantial UW integration. It is no longer an empty adapter or a few dashboard cards. It stores historical option chains, GEX snapshots, options-flow aggregates, dark-pool prints and alert outcomes, ETF creations/redemptions, 13F ownership, Congress trades, and options game plans. It also exposes live GEX levels, max pain, open interest by strike, NOPE, market tide, flow alerts, raw option trades, a contract screener, short-interest data, earnings moves, and related views.

The strongest next research direction is turning this data into point-in-time, testable decision features. The reviewed evidence does not establish that adding UW inputs to an AI score improves returns. Bullish and bearish flow outcomes differ sharply between date cohorts, but this audit did not perform a matched benchmark or regime-attribution study. Common market movement is a plausible explanation, not a demonstrated cause.

The best order of work is:

1. Fix data provenance and execution boundaries.
2. Use UW first as a market-regime, trade-quality, and risk layer.
3. Preserve the additional UW fields needed to separate likely opening directional flow from multi-leg, closing, and hedging activity.
4. Build one feature family at a time and measure incremental net expectancy against the existing system.
5. Promote validated playbooks into paper trading with real contract marks and realistic fills.
6. Permit live automation only after broker lifecycle, position reconciliation, and risk controls are complete.

UW can improve decisions. It cannot by itself guarantee a high win rate or profit. The goal should be a repeatable increase in **net expectancy**, calibration, and drawdown-adjusted return, rather than a raw win-rate target. A covered-call system can win often and still underperform after one large loss; a trend system can win less often and still have better expectancy.

## What is already built

| Data family | Current implementation | Current decision use | Main limitation |
|---|---|---|---|
| Historical option chains | UW settled-session chain persisted in `option_chain_history`, including NBBO, OI, volume, IV and Greeks | Options pages, game plans, LEAPS backtests, options-income selection and marking | Settled quotes are research marks, not executable quotes |
| Flow alerts | REST polling of UW flow alerts with premium, ask/bid split, sweep, volume/OI and contract identity | Emails, Options Flow page, directional alert outcomes | Normalized parser lacks opening/multi-leg/Greek/IV-change evidence documented for UW's stream; validate REST field availability before migration |
| Options-flow EOD aggregate | Yahoo chain call/put volume, estimated premium and large-contract-volume counts | Premarket brief and two ML features | Source differs from migrated chains; premium/whale fields are proxies, not actual trade-level premium or investor counts |
| GEX | Cached provider-estimated call wall, put wall, gamma flip and magnet; daily `gex_snapshots` | Stock pressure UI, options-pressure score and some squeeze/gamma context | Eight original production dates; stored history has no model/playbook reader found; four levels plus spot do not constitute full signed GEX history |
| Market tide and NOPE | Cached routes and UI | Human context | No decision-time capture found; current stored data alone cannot support a point-in-time incremental-value study |
| Dark pool | Raw prints with provider-supplied NBBO; alert outcomes preserve inferred quote-side classification | Alerts, digest and UI | Evaluation is non-directional; NBBO classification is not verified trader intent; no persistent cluster/level consumer found |
| Short interest | Cached UW short interest, SI float, borrow fee, availability and days-to-cover | Squeeze screen, signal corroboration and alerts | No dedicated durable UW history found; source reporting cadence is not necessarily real time |
| ETF fund flow | Daily creations/redemptions for 21 ETFs | Stored only | Rich three-year history has no signal, regime or sector-rotation consumer |
| Institutional ownership | Per-institution 13F-derived positions and vendor `avg_price` metadata | On-demand capture only | Five symbols and one report quarter; `avg_price` is not verified actual purchase cost; no consumer found in the research score |
| Congress data | UW-first source with fallback | Event-intelligence score and UI | Slow and filing-lagged by nature; useful as context, not timing |
| Earnings/IV | Historical expected versus realized move, IV rank, contract Greeks and game plans | Earnings UI and options plans | Source records a transcript entitlement limitation; current account access was not freshly probed; IV/skew/term-structure use is narrow |

**Ownership correction:** the original report repeated the model comment that `avg_price` was a holder's actual cost basis. Form 13F reports holdings and fair market value, not purchase-cost history. A vendor-derived average requires a documented methodology and must not be presented as the institution's verified entry price. [SEC Form 13F guidance](https://www.sec.gov/rules-regulations/staff-guidance/division-investment-management-frequently-asked-questions/frequently-asked-questions-about-form-13f).

Relevant source paths include:

- `services/market-data/src/services/unusual_whales.py`
- `services/market-data/src/services/uw_option_chain.py`
- `services/market-data/src/services/options_flow_snapshot.py`
- `services/market-data/src/services/gex_snapshot.py`
- `services/market-data/src/services/options_income_engine.py`
- `services/market-data/src/services/scheduler.py`
- `services/ml-prediction/src/training/feature_ablation.py`
- `shared/common/uw_congress.py`
- `shared/db/models.py`

## Production evidence

The following was measured read-only during the original evening-of-September-18 New York snapshot. Small-table counts are exact at that time. Both the option-chain and raw dark-pool print counts are PostgreSQL planner estimates. Row/date counts do not certify complete coverage, non-null Greeks/quotes, or point-in-time historical availability.

| Dataset | Production coverage | Assessment |
|---|---:|---|
| Option-chain history | ~49.3 million estimated rows; sampled AAPL head reached the 2026-09-17 settled session | Large research candidate; freshness/completeness of the full universe was not verified |
| Options-flow snapshots | 1,546 rows, 75 symbols, 32 dates, 2026-07-30 through 2026-09-18 | Too young for a broad long-history claim and still Yahoo-sourced |
| GEX snapshots | 273 rows, 61 symbols, 8 dates, 2026-09-02 through 2026-09-18 | Too young for model promotion |
| Options game plans | 305 rows, 64 symbols, 8 dates | Useful for surfacing, too young for performance inference |
| Raw dark-pool prints | ~175,000 rows | Enough to start cluster and level research |
| Dark-pool alert outcomes | 596 rows | Evaluation exists, but current label asks whether a 2% move occurred, not whether the classified side was right |
| Options-flow alert outcomes | 1,758 rows | Heavily clustered by date/underlying; regime attribution remains untested |
| ETF fund flows | 15,657 rows, 21 ETFs, 767 distinct dates, 2023-09-08 through 2026-09-17 | Promising research candidate; distinct dates are across the dataset, not guaranteed complete histories for every ETF |
| Institutional ownership | 250 rows, 5 symbols, one report date | Context only until coverage expands |

The original storage paragraph mixed PostgreSQL pretty-size units and decimal GB. A subsequent raw-byte check at **2026-09-19 06:17 UTC** measured heap 8,054,677,504 bytes, table including auxiliary/TOAST storage 8,056,897,536 bytes, indexes 6,645,948,416 bytes, and total **14,702,845,952 bytes = 14.70 GB ≈13.69 GiB**. These later values replace the inconsistent original size conversions; they are not a measured growth rate.

The original host snapshot reported about 39 GB free and 62% used. Original cached provider headers reported 4,392 of 120,000 daily requests, while the app's UTC-day counter recorded 4,609 calls, including 4,203 option-chain calls. These counters have different sampling/counting boundaries. The observation showed daily quota headroom, not guaranteed peak-rate capacity or future headroom. Storage warrants planning, but calling it **the binding constraint** was unsupported without throughput, growth, retention, and quota-rate measurements.

Across the 596 dark-pool alert outcomes, the underlying's mean raw return was approximately +0.1% at one day and -0.6% at five days. That is not evidence of direction: the current evaluator deliberately asks whether an outsized move occurred, and the newer buy/sell classification has not accumulated mature, comparable five-day samples yet.

At the original snapshot, production was three commits behind local/origin `prod`: one documentation commit and two runtime changes (six-symbol chain-universe expansion and options-specific UTC/ET date handling). The later review found **both checkouts at `7fb03da`**. The old deployment-gap statement is historical, not an outstanding finding; runtime verification remains service-specific.

## Reconciliation with the September 17 audits

Project records and the reviewed branch show substantial remediation, including lock, replay-cutoff, settlement, labeling, fix-effectiveness, and CI changes. This paragraph is **not a complete independent recertification** of A07/A10/A11/A15/A17/A19/C01. In particular, a shared-image rebuild is a deployment claim, not something established by source alone, and the later email audit found that confidence labels remain misleading in email despite other UI corrections.

The items that still matter to this UW roadmap are:

- **A01–A03:** durable broker order lifecycle and reconciliation remain prerequisites for real automatic trading.
- **A06:** archived option bids are still treated as simulated fills in the options-income engine; UW-03 below gives the implementation boundary.
- **C02/C03:** unimplemented at the original `f92584c` baseline; T410 now adds `signal_outcome_horizons`, independent window resolution, and an explicit `calendar_days` unit. This is not a conversion to trading-session horizons. The subsequent production read found the new table empty, so useful population, scheduling, and consumer adoption still require verification. Do not describe the entire feature as unimplemented or fully validated.
- **Deployment state:** fixes present in source are not production facts until the relevant images are rebuilt and verified on EC2.

## Findings requiring action

### UW-01 — P1 data integrity: trading dates are still inconsistent outside the options-specific fix

The current source still uses `date.today()` in `_record_options_flow_alert_outcome()`, `_record_dark_pool_alert_outcome()`, and their outcome evaluators. In a UTC container, that becomes the next date at 8:00 p.m. New York time during daylight saving time. Production already contained a dark-pool outcome dated **2026-09-19** while New York was still on September 18.

The local T409 change fixes options-chain and options-income date handling, but does not cover these alert paths.

**Solution:** distinguish UTC event time, New York calendar date, actual exchange session, and the session covered by a recap or settled dataset. A timezone conversion alone is not an exchange-session resolver. Use shared product/calendar-aware helpers for session grouping and maturity; elapsed-duration cooldowns should remain timestamp/TTL based. Test 19:59/20:01 ET, midnight ET, holidays, early closes, and late reports. Reconcile historical event/session identity and uniqueness collisions before any date repair. This is P1 for current research/alerts; live-order safety gates remain a prerequisite before automation.

### UW-02 — P1 provenance and feature definition: the EOD aggregate remains on Yahoo

`options_flow_snapshot.compute_options_flow()` still imports `yfinance` and downloads four expiries. The interactive option chain and game-plan paths moved to UW after Yahoo returned empty chains, but the persisted dataset used by the premarket brief and ML feature loader did not.

This creates three problems:

- an external outage can silently turn the ML feature sparse;
- UI, research and ML can describe different option datasets for the same day;
- source comments now state a Yahoo-based contract that conflicts with the platform's UW migration.

Production has a September 18 snapshot and the current writer is Yahoo-based. That is consistent with successful ingestion, but the row lacks source provenance sufficient to prove how every historical row was produced. Different providers are not inherently incorrect; mismatched definitions, timestamps, partial coverage, and undisclosed fallback are the problems to resolve.

**Solution:** first define the feature. The current writer calculates `volume * lastPrice * 100` and increments `whale_count` for each contract exceeding $500,000 by that proxy. This is neither actual tape premium nor a count of whales/orders. For a chain activity feature, aggregate a consistent expiry universe from settled UW chains, keep premium explicitly estimated, and rename the count accordingly. For actual flow premium, aggregate unique trade events with validated multipliers and execution prices; a closing bid or last price cannot reconstruct the day's trades. Add `source`, `source_as_of`, `captured_at`, quality and coverage. Preserve `yfinance_v1` and create a distinctly defined `uw_chain_v2` or `uw_trade_flow_v1`; do not silently mix them in one ML column.

### UW-03 — P0 before automated options trading: settled bids are being treated as fills

The options-income engine deliberately selects contracts from the settled archive, then records the archived `nbbo_bid` as collected premium when it opens a simulated position. The underlying price can be current while the option bid is from the prior settled session. This is acceptable for candidate research if clearly labeled. It is not an executable fill model and must not be carried into broker paper or live automation.

**Solution:** split the lifecycle:

1. `candidate_created` from the settled UW chain;
2. `quote_requested` from the broker or a live entitled option feed at the intended execution time;
3. `order_submitted` with limit price and maximum spread;
4. partial/full fill events with actual price, quantity and broker order ID;
5. cancel/replace/expire/reject states;
6. broker reconciliation as the authoritative position state.

Store intended credit, executable bid/ask, fill, spread, slippage and quote timestamp separately. Reject a trade when the quote is stale, crossed, too wide, below the strategy's minimum net credit, or materially changes the expected return. Never use the settled UW archive as the source of a live fill.

### UW-04 — P1 measurement, blocking option-profit claims: the flow outcome does not measure an options trade

Options-flow alerts are evaluated with the underlying's next-session **close** as entry and calendar-day targets resolved to permitted later bars. The result answers, “did the stock move in the inferred direction under that entry/horizon convention?” It does not answer whether the alerted contract was profitable after spread, theta, IV change and timing. Intraday flow can decay before the next daily entry. Candidate outcomes also do not identify exactly which recommendations reached each email recipient.

**Solution:** keep the current underlying-direction label, rename it explicitly, and add:

- underlying returns at 15 minutes, 60 minutes, close, next open and 1/3/5 sessions;
- benchmark- and sector-residual return;
- maximum favorable/adverse excursion;
- alerted contract bid/mid/ask path and conservative exit mark;
- net contract return after spread, fees and slippage;
- IV and Greek changes;
- an `available_at` timestamp to enforce point-in-time evaluation.

This is necessary before using alert “win rate” to select actual option trades.

### UW-05 — P1: the flow parser discards the fields most useful for reducing false direction

The current `FlowAlert` retains ask/bid premium, sweep status, volume/OI and basic contract data. UW's official **streaming** schema documents additional opening/multi-leg, timing, IV and Greek fields; this does not establish their availability in the current REST response or subscription. A false `all_opening_trades` means unknown, not closing. A true value is the vendor's classification, not independently verified trader intent. [UW FlowAlert](https://api.unusualwhales.com/docs/kafka/types/FlowAlert).

Ask-side call premium is useful evidence of aggressive call buying. It is not proof of a bullish opening bet. It can be a short-call close, one leg of a spread, or a hedge.

**Solution:** persist the complete evidence envelope. Build separate feature groups for:

- provider-classified opening, single-leg flow with an explicitly inferred direction;
- multi-leg/spread flow;
- unknown opening/closing state;
- delta exposure and vega demand as separate features with validated units/signs;
- IV change during the alert window;
- premium normalized by the underlying's dollar ADV and normal option premium;
- unique trade episodes across contracts, expiries and time windows, avoiding double counting overlapping alerts;
- next-session OI change confirmation usable only once it becomes available, not retroactively as yesterday's entry feature.

Do not mix those groups into one “bullish flow” label.

### UW-06 — P1: valuable data is stored or displayed without a testable consumer

- No ML/playbook reader of the stored GEX history was found in the reviewed source; live GEX has separate consumers.
- ETF fund flow has three years of retrospective dates but no strategy consumer found.
- Market tide and NOPE have UI consumers; no durable decision-time capture was found.
- Dark-pool raw history is used for relative alert thresholds, but not for persistent price-level clusters or side-specific prediction.
- Institutional ownership is captured for only five symbols and is not read by the existing research score; the research view continues to use aggregate fundamental ownership.

**Solution:** build a point-in-time UW feature mart and a decision-evidence snapshot. Every alert, paper entry and rejected candidate should record the exact feature values, source dates, feature version and freshness state that existed when the decision was made. This creates a real denominator and makes “did UW improve the decision?” answerable.

### UW-07 — P1: fail-open responses collapse “no signal” and “feed unavailable”

The UW client intentionally returns `None` or `[]` for disabled, unauthorized, rate-limited, failed, empty and unsupported responses. That is safe for page availability, but unsafe for decision quality. A trade should not receive the same interpretation when flow is neutral and when flow could not be fetched.

**Solution:** keep fail-open UI behavior, but return an internal status envelope:

```text
status = ok | no_data | stale | disabled | unauthorized | rate_limited | provider_error
source_as_of
received_at
age_seconds
coverage
entitlement
```

Playbooks then declare each input as required, optional or informational. A missing required input produces `NO_TRADE_DATA_UNAVAILABLE`. Missing optional inputs reduce reported evidence coverage and invoke a predefined, validated fallback or abstention rule. Automatically shrinking position size is a possible risk policy, not an established prediction improvement; missing data must not become a neutral zero.

### UW-08 — P1: the option archive needs a storage design before further expansion

The later storage recheck measured 14.70 GB total, including 6.65 GB of indexes, in decimal units. Weekly deletion at the 800-day cutoff does not itself guarantee filesystem-space recovery. Ordinary VACUUM can reuse dead space and sometimes truncate empty tail pages; partition removal or a planned rewrite has different locking/capacity implications. The earlier deployment gap is historical. [PostgreSQL vacuuming](https://www.postgresql.org/docs/16/routine-vacuuming.html).

**Solution:** before broadening further:

- project monthly rows and bytes per symbol;
- partition by `as_of` month so retention drops whole partitions;
- retain compact daily strike/expiry/OI aggregates in PostgreSQL;
- move cold raw chains to compressed Parquet/object storage if long retention is required;
- keep hot contract rows only for actively researched horizons/universes;
- review index usage over a full statistics interval and remove only indexes proven redundant;
- alert at database growth rate, 70/80/90% disk and failed retention runs.

Do not drop the primary key or currently unused-looking indexes based on one `pg_stat_user_indexes` snapshot. Statistics can reset, and some indexes support rare backtests.

### UW-09 — P1: clustered recent flow results do not establish calibrated confidence

Production alert results illustrate the problem:

| Cohort | Direction | Resolved 1d | 1d win rate | Mean directional 1d return | Resolved 5d | 5d win rate | Mean directional 5d return |
|---|---:|---:|---:|---:|---:|---:|---:|
| Before Sep 5 | Bullish | 715 | 60.3% | +2.0% | 715 | 65.7% | +4.4% |
| Before Sep 5 | Bearish | 796 | 21.5% | -2.1% | 796 | 20.9% | -5.1% |
| Sep 5 onward | Bullish | 85 | 34.1% | -0.5% | 59 | 3.4% | -5.7% |
| Sep 5 onward | Bearish | 81 | 30.9% | -0.2% | 51 | 94.1% | +4.9% |

Here, a “win” means the underlying exceeded the 0.5% directional hurdle from next-session close entry at a calendar-day target; it is not an option profit. September 5 is a descriptive cutoff, not an isolated code-change experiment. Fourteen symbols/nine entry dates describe the broader recent cohort, **not the matured five-day subset**, which covers only **four entry dates and ten symbols per direction**. One- and five-day columns can have different matured members. Common market movement is a plausible explanation requiring matched benchmark/regime analysis. Same-day/same-underlying contracts are not independent trials. Returns in this original table were rounded coarsely; the later email audit provides finer recent five-day values.

**Solution:** report date-clustered and symbol-clustered bootstrap intervals, regime-stratified results, and benchmark-relative returns. Calibrate on distinct decision days, not row count alone. Keep the existing minimum-distinct-date guard and extend it to every published performance metric.

## Highest-value implementations

### 1. Market regime gate: market tide + index GEX + price trend

This is a proposed first **shadow experiment**, not a demonstrated best-performing gate. It can condition stock/option hypotheses, but must outperform the existing price/regime baseline before changing capital allocation.

Build synchronized five-minute snapshots combining market-wide tide with per-index SPY/QQQ price and GEX context. The existing `/market/market-tide` endpoint is market-wide; do not invent separate SPY/QQQ tide series from that one response. Capture:

- net call and put premium level, 5/15/30-minute slope and acceleration;
- 0DTE/near-dated tide separately when entitled;
- distance to gamma flip, call wall and put wall as a percentage of spot;
- GEX level changes and wall breaks;
- index price trend, breadth, realized volatility and volume;
- feature freshness and session phase.

Output a small state vocabulary such as `risk_on_trending`, `risk_off_trending`, `estimated_positive_gamma_range`, `estimated_negative_gamma_expansion` and `uncertain`. The existing GEX snapshot stores four levels plus underlying close; signed-gamma state needs additional explicitly defined exposure data/model assumptions. First test whether these states improve playbook selection and risk allocation; they are not independently validated BUY/SELL signals.

### 2. Directional-flow quality score

Replace the current binary direction with a transparent evidence score. Candidate components are:

- provider opening classification and its uncertainty;
- single-leg/multi-leg classification and missingness;
- ask/bid dominance after excluding mid/no-side volume;
- delta-adjusted directional exposure;
- vega demand and IV change;
- volume/OI and next-session OI confirmation;
- repeated activity across time and compatible expiries;
- premium relative to normal option premium and stock dollar volume;
- agreement with price, volume, market regime and catalyst calendar;
- penalties for deep-ITM stock substitution, very wide spreads, imminent earnings, obvious multi-leg activity and broad-index hedging.

Keep each component in the decision snapshot. A user should see why a score is high and which fields are unknown.

### 3. ETF flow regime and sector rotation

This is the best near-term research candidate because 767 production dates are already available.

“Best” here is a prioritization judgment, not a measured result. The recheck found 15,645 non-null `change_premium` values out of 15,657 rows; source dates extend to 2023, but retained `fetched_at` values start on September 7, 2026. This is retrospective history, not evidence of three years of contemporaneous capture. Check per-ETF coverage, revision policy, and publication lag before claiming point-in-time backtest performance.

For SPY, QQQ, IWM, sector ETFs, TLT, GLD and relevant international ETFs, compute:

- 1/5/20-session net creation/redemption flow;
- flow as a percentage of assets or dollar volume where a defensible denominator is available;
- rolling z-score and persistence;
- divergence between fund flow and price;
- sector-relative flow and breadth;
- FOMC/expiry-cycle interactions already present in the UW rows.

Use ETF flows to confirm sector allocation and market regime. Avoid treating one daily redemption as an intraday short trigger. Run an ablation against the current sector-rotation and market-regime baselines first.

### 4. Dark-pool accumulation/distribution levels

Transform individual prints into features by symbol and price zone:

- group prints within a small price/ATR band;
- aggregate notional, shares and count over 1/5/20 sessions;
- normalize by dollar ADV;
- retain buy, sell and unknown separately using execution-time NBBO;
- decay old clusters;
- measure whether price accepts, rejects or breaks each level;
- compare off-lit activity with lit price/volume confirmation.

Use a cluster as a candidate support/resistance feature, subject to price confirmation and ablation. Provider NBBO/venue/condition metadata supports quote-side classification, not ground-truth buyer intent. Separate verified ATS activity from broader off-exchange reports where possible, and retain unknown, midpoint, delayed, corrected, and invalid-quote cases appropriately. [UW TradeReport](https://api.unusualwhales.com/docs/kafka/types/TradeReport).

### 5. Short-squeeze state, based on changes rather than a single level

Persist UW short-interest snapshots with their market date. Derive:

- SI-float level and change;
- borrow-fee level, percentile and acceleration;
- borrow availability change;
- days-to-cover change;
- disagreement with the fallback source;
- interaction with bullish opening call flow, price breakout and volume;
- a crowding/risk flag when price is already extended.

A squeeze playbook should require price confirmation. High short interest alone often identifies a weak company rather than an imminent squeeze.

### 6. IV, term structure and skew for strategy selection

The platform already uses IV rank and expected move. Extend this into a strategy selector, subject to an entitlement probe:

- fixed-horizon implied move and its change;
- real-expiry IV term structure;
- put/call risk-reversal skew;
- expected versus realized move by symbol and event type;
- IV crush behavior after earnings;
- spread, OI and volume quality.

Use these features to choose between stock, long option, debit spread, credit spread, cash-secured put, covered call or no trade. Direction and volatility are separate forecasts. A correct direction can still lose money in an overpriced long option.

## Proposed playbooks

| Playbook | UW evidence | Non-UW confirmation | Instrument logic | Invalidation / no-trade |
|---|---|---|---|---|
| Trend continuation long | Market-wide tide, estimated index GEX context, provider-classified opening bullish flow; directional delta and separately interpreted vega demand | Uptrend, relative strength, breakout volume, no stale inputs | Experiment with stock or 30–60 DTE call/debit spread; DTE choice is unvalidated | Broad regime flips, failed breakout, earnings risk, wide spread, ambiguous multi-leg flow |
| Pullback at support | Put wall/gamma flip or persistent dark-pool buy cluster near price | Higher-timeframe uptrend and reversal confirmation | Stock, CSP at a genuinely desired purchase price, or defined-risk call spread | Price accepts below the level, sell-side cluster, negative market tide |
| Bearish breakdown | Negative tide, below gamma flip/put wall, opening ask-side puts or bid-side calls | Weak relative strength and support break | Stock hedge, long put or put spread | Flow is multi-leg/unknown, IV too expensive, breakdown reclaims quickly |
| Short squeeze | Rising fee, falling availability, high SI float, bullish opening flow | Price/volume breakout and sufficient liquidity | Smaller stock position or defined-risk call spread | No price confirmation, borrow pressure easing, extreme chase distance |
| Earnings volatility | Expected-vs-realized history, IV rank/term structure/skew, event flow | Earnings quality, guidance context and liquidity | Defined-risk premium sale only when implied move has a validated overpricing edge; long volatility only when underpricing is validated | Unclear event time, illiquid legs, unbounded short options, no historical edge |
| Income “buy low / sell high” | IV/skew, put/call OI, GEX levels, liquid live quote | Fundamental willingness to own, trend and portfolio concentration | CSP only at a planned buy price; covered call only at a planned sell price | Archived-only quote, earnings in contract life, thin cushion, excessive sector/correlation exposure |

Every alert should include entry condition, invalidation, time horizon, instrument, maximum risk, data freshness and a `NO_TRADE` state. The playbook should remain valid when no trade is recommended.

These are research hypotheses. UW's field-level GreekFlow definitions distinguish direction-signed delta from buy/sell-signed vega; positive vega demand is not necessarily bullish stock exposure. The page's general description is less precise than its field definitions, so validate actual messages and contract sign conventions before implementation. [UW GreekFlow](https://api.unusualwhales.com/docs/kafka/types/GreekFlow).

## Research and promotion design

### Point-in-time data contract

Every raw observation and derived feature should carry:

- provider and endpoint/schema version;
- stable provider event ID when available;
- `event_at`;
- `source_as_of`;
- `received_at`;
- `available_at` for backtests;
- market/trading session;
- correction/cancellation status;
- feature version and derivation window.

This is especially important because UW's official option-state documentation notes that cumulative volume can decrease after a cancellation, and several streaming topics are incremental rather than complete state. A replay must reproduce what was knowable at the decision time.

### Ablation ladder

Test each feature family in isolation:

1. Existing baseline.
2. Baseline + market regime.
3. Baseline + flow quality.
4. Baseline + GEX state.
5. Baseline + dark-pool clusters.
6. Baseline + ETF flow.
7. Only after individual evidence: selected combinations and interactions.

Use a purged walk-forward split, an embargo around overlapping horizons, and date/symbol clustered confidence intervals. Evaluate:

- coverage and freshness;
- precision, recall and calibration;
- benchmark-relative and net return;
- expectancy, profit factor, drawdown and turnover;
- spread, fees, slippage and rejected-order rate;
- results by market regime, liquidity, DTE and event proximity.

Do not select thresholds on the same holdout used to report performance. Do not promote a feature because it improves win rate while reducing net expectancy.

### Promotion gates

A practical minimum before a UW playbook influences capital is:

- at least 30 distinct entry sessions and broad symbol coverage, with dependence explicitly modeled;
- no timestamp, leakage or source-status violations;
- positive incremental net expectancy versus the same baseline decisions;
- uncertainty intervals that do not depend on treating same-day alerts as independent;
- stable behavior across at least two materially different regimes;
- shadow operation first, then broker paper trading with real quotes;
- at least 100 closed paper trades for the exact instrument/exit policy, with risk limits respected;
- manual approval for the first live phase, tiny capital limits, and a tested kill switch.

These are minimum evidence gates, not proof that future returns will match the sample.

## Implementation roadmap

### Phase 0 — integrity and deployment

1. The original checkout gap is closed at the later `7fb03da` observation. Verify relevant runtime images and useful output rather than redeploying solely on the old finding; verify T410 population and downstream adoption before calibration work.
2. Extend the ET trading-date fix to flow and dark-pool alert recording/evaluation.
3. Migrate the EOD options-flow snapshot to versioned UW data.
4. Add structured provider status and freshness to every UW consumer.
5. Separate settled research marks from executable quotes throughout options-income and paper trading.
6. Put disk-growth alerts and a partition/archive plan in place before the expanded chain universe runs for long.

### Phase 1 — preserve and normalize high-value evidence

1. Expand the flow-alert schema with opening, multi-leg, execution-time, IV and Greek fields.
2. Persist market-tide/index-GEX snapshots at five-minute intervals during the regular session for a narrow index universe.
3. Persist UW short-interest history at its real source cadence.
4. Add decision-evidence snapshots for accepted and rejected candidates.
5. Version all derived features.

### Phase 2 — highest-value experiments

1. ETF-flow regime ablation using the existing 767-date history.
2. Market-tide + SPY/QQQ GEX shadow regime.
3. Opening single-leg flow versus ambiguous-flow comparison.
4. Dark-pool cluster/level study.
5. Short-interest change and squeeze interaction study.

### Phase 3 — playbooks and alerts

Ship only playbooks whose incremental test clears the promotion gate. Show the evidence, age, invalidation, risk and historical cohort size. Alert on a meaningful state change, not every new print.

### Phase 4 — options paper trading

Add contract-level marks, live quote capture, conservative fill simulation, order state, assignment/exercise handling, multi-leg atomicity, Greeks and portfolio stress. Compare the exact paper policy against stock-only alternatives.

### Phase 5 — controlled live automation

Require the durable broker order state machine and reconciliation work identified in the earlier audit. Start with one strategy, a tiny capital ceiling, one order at a time, limit orders, daily loss and drawdown limits, exposure/Greek limits, stale-data circuit breakers, and manual pause/flatten controls. Expand capital only from measured live execution and risk results.

## What should not be built yet

- A single “whale score” combining every feed.
- Direct `call = BUY` or `put = SELL` rules.
- An LLM-generated trade recommendation that is not backed by a versioned quantitative playbook.
- A model trained on eight GEX dates.
- Live option orders priced from the settled archive.
- A subscription upgrade solely to obtain more endpoints before current data demonstrates incremental value.
- More full-chain symbols before storage growth, retention and cold-archive design are controlled.

## Recommended first delivery

The first implementation batch should be small enough to validate cleanly:

1. Fix ET dates on flow/dark-pool outcomes.
2. Add a `uw_chain_v2` EOD flow snapshot and leave the old Yahoo series intact.
3. Preserve `all_opening_trades`, multi-leg flags, execution time, IV change and alert Greeks.
4. Create a `decision_uw_context` snapshot with provider status/freshness.
5. Run two shadow studies: the three-year ETF-flow regime feature and opening-single-leg flow quality.
6. Add a broker/live-quote execution gate to the options-income paper engine before treating any candidate as filled.

This batch attacks current data ambiguity, uses the deepest available UW history, and creates the evidence needed for later GEX, dark-pool and live-options decisions.

## Official UW references used

- [Unusual Whales public API and current plan overview](https://unusualwhales.com/public-api)
- [FlowAlert schema](https://api.unusualwhales.com/docs/kafka/types/FlowAlert) — provider opening classification, multi-leg/single-leg state, premium side, trade count, IV and Greeks
- [GreekFlow schema](https://api.unusualwhales.com/docs/kafka/types/GreekFlow) — directional delta and vega flow definitions
- [TickerInterval schema](https://api.unusualwhales.com/docs/kafka/types/TickerInterval) — five-minute flow, premium, Greek flow and implied-move fields
- [DteTide schema](https://api.unusualwhales.com/docs/kafka/types/DteTide) — near-dated market net flow buckets
- [GexStrike schema](https://api.unusualwhales.com/docs/kafka/types/GexStrike) — per-strike exposure fields
- [OptionState schema](https://api.unusualwhales.com/docs/kafka/types/OptionState) — chain state, Greeks, cancellations, OI, volume and NBBO
- [InterpolatedIv schema](https://api.unusualwhales.com/docs/kafka/types/InterpolatedIv) and [IV term structure schema](https://api.unusualwhales.com/docs/kafka/types/IvTermStructure)
- [TradeReport schema](https://api.unusualwhales.com/docs/kafka/types/TradeReport) and [stock price-level volume schema](https://api.unusualwhales.com/docs/kafka/types/StockPriceLevelVolume) — lit/off-lit classification and price-level aggregation
- [UW streaming reference](https://api.unusualwhales.com/docs/kafka) — delivery semantics and incremental topics; entitlement must be verified before designing against a streaming topic

The schema references describe fields UW offers across its current API/streaming products. They do not prove that every field or transport is included in this account. Production response headers and a non-mutating entitlement probe should remain the source of truth before implementation.
