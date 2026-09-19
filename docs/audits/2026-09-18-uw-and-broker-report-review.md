# Review of the UW Strategy Audit and Broker Lifecycle Scope

**Review date:** September 18, 2026, America/Los_Angeles. The production snapshot below was taken at **2026-09-19 06:17 UTC / September 18 23:17 PDT / September 19 02:17 EDT**.  
**Reviewed checkout:** local `7fb03da`; production checkout `7fb03daf`.  
**Reports reviewed:** [UW data strategy audit](2026-09-18-unusual-whales-data-strategy-audit.md) and [A01–A03 broker lifecycle scope](2026-09-18-a01-a03-broker-lifecycle-scoping.md).  
**Work performed:** source tracing, read-only production aggregate queries, running-file hashes, isolated execution of actual functions with fake dependencies, and official provider documentation. No real broker/provider requests, orders, emails, deployments, configuration changes, or production writes. `CLAUDE.md` was not changed.

## 1. Verdict

**Neither report was entirely correct as written.** Their main recommendations remain useful, but some claims were too strong and the broker implementation scope missed material paths.

The UW report correctly identified separate research/execution data, incomplete provenance, limited recent samples, and the need to evaluate UW's incremental benefit. Corrections were needed for performance attribution, source/schema certainty, institutional cost basis, premium/whale terminology, storage units, priority labels, and deployment status. T410 also changed the implementation state after the original audit.

The broker report correctly identified fail-open entry preflight, missing durable exit identity, missing app-generated client-order identity, and simulated manual/liquidation closes that omit broker submission. However, two full-close callers already share `_place_broker_exit()`, and the scope extends beyond four callers. Partial sells, scale-outs, and scale-ins can diverge from broker quantities too. A durable intent/fill/position lifecycle is required; one exit-ID column and a symmetric poller are insufficient.

Both source reports have been corrected and linked to this review. Their original measurement windows remain identified rather than silently replaced with current-state assertions.

## 2. Evidence observed now

| Check | Observation | What it establishes |
|---|---|---|
| Checkout state | Local and EC2 both `7fb03da` | The earlier three-commit gap is historical |
| Running market-data code | `paper_trading_engine.py`, `conditional_orders.py`, and `api/paper_portfolio.py` exactly match local SHA-256 | The reviewed broker paths exist in the running container |
| Running signal evaluator | `/app/src/api/outcomes.py` matches local, SHA-256 `9f490789ac70fe0cae91552ab153b7e1ad6c693934ca3b4a8eb5e574ebfe43cf` | T410 evaluator code is present in the running service |
| New independent-outcome table | `signal_outcome_horizons` exists but has **zero rows** | Schema/code exist; successful population is not established |
| Linked portfolios | One `etrade_sandbox`; ten unlinked | No LIVE-linked portfolio found in this table at this snapshot |
| Broker-associated paper trades | Zero rows where `broker_order_id IS NOT NULL` | No stored broker-backed paper-trade case available to measure these defects in operation |
| Historical option-chain storage | 14,702,845,952 bytes total | **14.70 decimal GB / approximately 13.69 GiB** |
| ETF history | 15,657 rows; 21 symbols; 767 distinct dates; 15,645 non-null dollar-flow values | Substantial retrospective history, with missing values |
| ETF dates/capture | Source dates September 8, 2023–September 17, 2026; retained capture timestamps September 7–18, 2026 | Source history is not three years of contemporaneous app capture |

The linkage/trade observations do **not** prove that no orders or positions exist in a broker account. No broker account API was called. They support a narrower conclusion: the production database did not show live-linked portfolio exposure or historical broker IDs on paper trades. Current defects are confirmed implementation risks, not demonstrated financial loss.

## 3. Corrections to the UW audit

| ID | Original claim or omission | Review disposition and correction |
|---|---|---|
| U01 | Recent flow results are “regime-dominated” | **Overstated.** Date-cohort results are real, but no matched benchmark/regime attribution was performed. Common market movement is a hypothesis. |
| U02 | Nine entry dates characterize the five-day result | **Misleading denominator.** Nine describes the broader recent cohort. Mature five-day bullish/bearish samples each span four entry dates and ten symbols. |
| U03 | Independent outcome resolution is still scoped/unimplemented | **Superseded by T410.** Additive resolution and `calendar_days` metadata now exist; the new production table is empty. Verify population and consumer adoption before claiming completion. |
| U04 | Production remains three commits behind | **Historical only.** Both checkouts now match `7fb03da`; runtime evidence is separately scoped. |
| U05 | Roughly 13 GB, with 7.55 GB heap + 6.23 GB indexes | **Inconsistent units.** Replaced with explicitly dated raw-byte measurements and GB/GiB conversions. No growth rate inferred from differently reported samples. |
| U06 | Database growth is “the binding constraint”; quota comfortable | **Unproven ranking.** A cached daily quota sample is not peak-rate/headroom evidence. Storage planning remains sensible, but throughput/growth/retention measurements are needed. |
| U07 | 13F positions include actual institutional cost basis | **Unsupported.** The code comment was repeated without validating the vendor methodology. SEC holdings/market value do not establish purchase cost. |
| U08 | Migrating Yahoo aggregates to a UW chain creates genuine flow premium/whale data | **Incomplete solution.** `volume × lastPrice × 100` is an estimate; counting large-volume contracts does not count whales/orders. Define separate chain-activity and actual trade-flow features. |
| U09 | Parser “discards” every field in cited schema | **Transport distinction missing.** The normalized parser lacks them; cited fields are documented for streaming. Actual REST response availability and account access require validation. |
| U10 | “Guaranteed/verified opening” and quote-side direction are established intent | **Overstated.** Preserve vendor classifications and uncertainty. They do not reveal the trader's complete position or motive. |
| U11 | Positive delta/vega means a bullish exposure signal | **Needs separate definitions.** Directional delta and buy/sell-signed vega measure different things. Positive vega demand can accompany bearish put buying. |
| U12 | Market tide can be collected separately for SPY/QQQ from the current route; four GEX levels imply signed gamma state | **Implementation gap.** Current market tide is market-wide. Existing stored GEX fields are four levels plus spot, not complete signed-gamma history. |
| U13 | History counts establish research readiness and full freshness | **Too broad.** AAPL freshness was sampled; all-symbol coverage/Greeks completeness was not checked. ETF history needs publication/revision/availability checks. |
| U14 | P0 for timezone consistency, Yahoo provenance, and underlying-only outcome learning | **Priority inflation.** Reclassified as P1 data/measurement concerns; executable quote and broker correctness remain prerequisites before live capital. |
| U15 | Several prior fixes were fully reverified | **Evidence boundary unclear.** Project records/source changes are not complete behavioral or deployment re-certification; email confidence-label defects still exist. |
| U16 | Transcript is currently unentitled; absent optional data should automatically reduce size | **Not established as universal policy.** Entitlement limitation was recorded in source, not freshly probed; missing-input fallback/size rules must be explicit and validated. |

### U07: institutional ownership deserves a precise label

`InstitutionalOwnership` stores `avg_price` and its comments call this the holder's cost basis. This is not enough evidence to repeat that claim. Form 13F is a holdings and fair-market-value disclosure. A vendor may estimate acquisition costs using other assumptions, but that requires its own verified methodology and label. Treat this field as vendor metadata until that is established. [SEC Form 13F guidance](https://www.sec.gov/rules-regulations/staff-guidance/division-investment-management-frequently-asked-questions/frequently-asked-questions-about-form-13f).

### U08: fix definitions before changing data sources

In [options_flow_snapshot.py](../../services/market-data/src/services/options_flow_snapshot.py), `compute_options_flow()` reads the nearest four Yahoo expiries. Premium is cumulative contract volume times one last price, times 100. `whale_count` increments for each contract whose estimated value exceeds $500,000.

Those may be useful activity proxies, but neither is a tape reconstruction. The migration should create one of two explicitly different series:

- **Chain activity:** a consistently selected expiry universe, cumulative volume/OI, coverage, estimated premium and number of high-activity contracts.
- **Trade flow:** unique executions/episodes, actual prices and sizes, contract multipliers, side/condition fields, corrections, and event-time aggregation.

Do not mix those series silently in the ML feature columns. Existing `opt_cp_ratio`/`opt_whale_count` consumers and the feature-ablation harness need a source/version contract and a fair before/after comparison.

### U09–U12: UW documentation supports capabilities, not all assumptions

UW documents the opening flag as a provider rule; false means unknown. The additional stream fields do not prove identical REST coverage. [UW FlowAlert](https://api.unusualwhales.com/docs/kafka/types/FlowAlert).

GreekFlow's field definitions distinguish sentiment-signed delta from buy/sell-signed vega. Its general description is less precise than the field-level description, so validate payload semantics rather than treating a positive aggregate as uniformly bullish. [UW GreekFlow](https://api.unusualwhales.com/docs/kafka/types/GreekFlow).

The storage recommendation remains valid with a nuance: DELETE does not itself reclaim filesystem space, while routine VACUUM can reuse dead space and sometimes truncate empty tail pages. Partition/rewrite decisions need capacity, lock, and retention planning. [PostgreSQL 16 routine vacuuming](https://www.postgresql.org/docs/16/routine-vacuuming.html).

### T410: recognize implementation progress without claiming successful operation

The running evaluator now has a separate per-window table and resolves windows without waiting for primary-horizon maturity. It deliberately preserves the old `signal_outcomes` behavior and uses `calendar_days`; it does not switch all consumers to trading-session returns.

At this review snapshot, the new table was empty. The review did not invoke its writer or diagnose why no rows had appeared. Next checks are scheduled-job execution, writer status, eligible cohorts, and persistence, followed by verification that intended analytics/calibration consumers actually use the new table. The earlier email audit's old-table counts remain historical observations; they should not be advertised as the current system's complete independent-window coverage once T410 populates.

The UW roadmap remains a set of experiments. Neither market-tide gating nor ETF flow nor a new flow-quality score has demonstrated incremental profitable performance in this review.

## 4. Broker scope: what is correct and what needed correction

### B01 — Entry preflight fails open: confirmed

`_place_broker_entry()` catches a failure from `broker.get_account()` and proceeds to `broker.place_order()`. The token-rejection handler does not itself add a return in that catch branch either. The broker's own buying-power rejection is not the same as successful application preflight.

**Safe reproduction:** extracting the actual function and giving it a fake broker whose account request raises `TimeoutError` still produced one captured BUY submission. No real broker object or network call was used.

**Recommendation:** make preflight status explicit. New entry requires valid account/environment, buying power, quantity/instrument rules, quote, and risk reservation. An unavailable check yields an unsent/blocked intent; it must not silently become either a broker order or a simulated fill presented as broker activity.

### B02 — Exit identity is discarded: confirmed, but “permanently unreconcilable” was too strong

`_place_broker_exit()` logs `order.order_id` and does not persist a durable exit-order link. The current fill poller selects open paper trades with pending **entry** broker IDs. The surrounding close paths mark the paper record closed before broker exit completion.

**Safe reproduction:** the actual helper sent a captured SELL to the fake broker, which returned pending status; the trade still had no exit-ID field. Both broker adapters expose order lists, and account/position sync exists, so manual/history-based recovery may be possible. The missing piece is reliable linkage and automated reconciliation into this portfolio lifecycle, not physical impossibility of recovery.

### B03 — Retry identity gap: confirmed; duplicate IDs are not a universal no-op

The interface lacks a `client_order_id` argument. E*Trade generates a fresh UUID fragment on every call. Alpaca submission does not send the application's own durable identity. A timeout/crash after broker acceptance but before DB persistence therefore leaves an ambiguous attempt; blind retry can duplicate it.

E*Trade documents client order identity for duplicate prevention. Alpaca supports client-ID lookup and warns against simply resending after a submission timeout. Neither fact justifies assuming that every duplicate request returns the same successful order. [E*Trade orders](https://apisb.etrade.com/docs/api/order/api-order-v1.html), [Alpaca order workflow and timeout guidance](https://docs.alpaca.markets/us/docs/working-with-orders).

**Recommendation:** commit intent/identity before dispatch; preserve an ambiguous-submit state; reconcile the original attempt using supported broker lookup/history before retrying. Record each provider's uniqueness scope, retention, error behavior, and cancel/replace identity rules. If absence cannot be established safely, keep the intent unresolved and surface it for recovery.

### B04 — Full-close call-site count and helper ownership were misstated

| Caller | Current broker path | Accounting behavior |
|---|---|---|
| `_monitor_positions()` full exit | Calls shared `_place_broker_exit()` | Simulated closed/cash state first |
| Conditional `_execute_close_position()` | Calls the same shared helper | Independently repeats simulated close bookkeeping first |
| `manual_exit_trade()` | Calls `_close_one_paper_trade()`, no broker exit | Marks closed and credits simulated proceeds |
| `liquidate_portfolio()` | Repeats `_close_one_paper_trade()`, no broker exit | Marks each selected trade closed and credits proceeds |

The original “three of four omit the broker” statement was wrong: **two call it and two omit it**. There is already a shared submit helper; the missing abstraction is a coordinated position-change lifecycle. `_close_one_paper_trade()` performs state/ledger/log mutations and is not merely a pure math helper.

### B05 — Manual/liquidation divergence: confirmed, impact conditional

For a genuinely broker-backed position, these endpoints could display closure without selling it at the broker. That is a valid severe risk. It is not an observed production loss, because this DB snapshot has no paper trade with a broker order ID.

**Safe reproduction:** the actual `_close_one_paper_trade()` changed the synthetic trade to `closed` and credited simulated cash while causing zero broker calls.

## 5. Additional paths and requirements missing from the scope

### B06 — Automatic scale-outs and conditional partial sells are simulated-only

The two scale-out blocks in [paper_trading_engine.py](../../services/market-data/src/services/paper_trading_engine.py), around lines 3453–3525, reduce `trade.shares` and credit cash without a broker SELL. Conditional `_execute_sell_partial()` does likewise, even when a portfolio is linked.

**Concrete conditional example:** start with 100 genuinely broker-filled shares. A simulated 33-share scale-out leaves the app at 67 while the broker still holds 100. A later full-close helper submits only 67, potentially leaving **33 real shares** while the app reports closed. A new exit-ID column alone does not fix this quantity divergence.

**Required scope:** every full/partial position reduction must create a durable quantity-specific intent; only confirmed fill deltas reduce broker-backed quantity or realize proceeds.

### B07 — Scale-ins also change paper quantity without a broker BUY

The scale-in branch, around lines 6040–6130, increases shares and blends cost basis in the simulated ledger. Repository call-site search found broker submissions only in the entry/exit helpers, with no scale-in call into entry submission in this branch.

**Example:** the app scales 100 shares to 125 while the broker remains at 100. A subsequent SELL of 125 can be rejected or create unintended short exposure if permitted. Whether this branch triggers depends on configuration and signal conditions; no such live event was observed.

**Required scope:** include scale-ins in the same intent/fill service and enforce reservations/risk limits across all linked strategies.

### B08 — Pending, rejected, cancelled, and partial entries must not become owned quantity

`BrokerOrder` already exposes `filled_qty`, but the current portfolio poller primarily reacts to `status == filled` and adjusts prices using simulated `trade.shares`. Terminal rejection/cancellation is explicitly left unresolved. `_place_broker_exit()` checks that an entry ID exists, not that the requested sell quantity was actually acquired.

**Example:** a 100-share entry fills 40. An exit request must reconcile/cancel the remaining buy, handle any racing fills, and sell only the current strategy-owned inventory. Selling the simulated 100 before resolution can create excess exposure or a rejection.

**Required scope:** track requested, cumulative filled, cancelled, and remaining quantities, with idempotent fill application and order/position states kept separate. Poll all unresolved intents even when a legacy paper trade was already labelled closed.

### B09 — Fractional simulated quantities are truncated for broker orders

Both `_place_broker_entry/_exit` and the E*Trade/Alpaca payloads use `int(qty)` in relevant paths. The simulated ledger supports four-decimal quantities. A 10.7-share simulated position can therefore submit 10 shares while accounting for 10.7; a sub-one-share remainder can become a zero-size request.

**Required scope:** validate quantity increments and minimum size per broker/instrument before creating the approved intent. Persist the actual approved quantity and any residual exposure. Do not treat a silent integer cast as quantity validation.

### B10 — Persisting the returned ID does not close the submission crash window

Broker acceptance and the database commit cannot be made one ordinary DB transaction. A crash can occur before the response is received, after it is received, or before its ID is committed.

**Required scope:** commit the immutable intent and dispatch record before sending; atomically claim work; record attempt/request identity; resolve timeout ambiguity through broker history. A DB rollback after broker acceptance does not undo the order. Holding a row lock while making a network call also does not solve this problem.

### B11 — Account-level reconciliation and concurrent reservations are necessary

Two portfolios can independently see enough buying power and both submit. Two exit triggers can race. Manual external orders can alter the broker position. Existing account-position synchronization is useful, but it updates a different view and does not provide per-intent fill allocation for these paper trades.

**Required scope:** bind each intent to immutable account/environment/instrument identity; reserve cash and inventory across strategies; serialize competing logical actions; reconcile broker order/fill history, account positions and balances; quarantine discrepancies. Do not automatically liquidate all account inventory for a symbol if only part belongs to this strategy.

### B12 — Options require a separate adapter capability milestone

The current E*Trade submit payload hardcodes equity `orderType/securityType = EQ`. The common interface has no explicit multi-leg/open-close option intent or deliverable/multiplier model. This scope cannot promise safe automated stock **and option** trading merely by adding IDs.

**Required scope:** verify supported asset types, option permissions, contract identity, buy/sell-to-open/close, complex-order support, partial-leg risk, commissions, exercise/assignment, adjusted deliverables, and expiry handling. Design for them now; enable capabilities separately after broker-specific tests.

## 6. Revised broker architecture

Use a durable model that distinguishes intent, broker activity, and portfolio accounting:

| Record | Required information |
|---|---|
| Execution configuration | RESEARCH / SIMULATED / BROKER_PAPER / LIVE, broker/account/environment, allowed instruments, risk policy version |
| Order intent | Stable ID, originating command/strategy, account/instrument, approved quantity, side/open-close, limit/TIF, risk/quote snapshot, committed creation time |
| Submission attempt | Intent ID, durable client ID, broker request identity, attempt sequence, response/error classification, timestamps, replacement lineage |
| Broker order | External ID, account/client IDs, requested/filled/remaining quantities, native and normalized states |
| Fill/event | Unique external execution/event identity, quantity, price, fees, timestamps, correction/reversal relationship |
| Position/ledger | Cash, reserved funds, inventory, realized/unrealized P&L derived from fills, ownership allocation, reconciliation state |

At minimum distinguish `created`, `blocked`, `ready`, `submitting`, `submission_unknown`, `accepted`, `partially_filled`, `filled`, `cancel_pending`, `cancelled`, `rejected`, and `expired`; represent replacement lineage explicitly. These are design states, not a requirement to encode every event in one enum. A cancelled order can still have fills; a cancel request is not proof no further fill occurred.

The close service should accept a **position-change command**, not immediately book a synthetic close. Route automatic, conditional, manual, bulk, partial, and scale-in operations through it. For broker-backed positions, a response may be “exit requested/pending”; “closed” requires reconciled zero strategy-owned quantity and no unresolved orders that can recreate exposure.

Keep simulated execution explicit. A broker error must not silently change a LIVE/BROKER_PAPER intent into a simulated success. Match the configured execution mode to the actual broker endpoint/account and reject inconsistent configuration.

For options, contractual spread risk assumes correctly paired legs; operational partial legs or assignment require independent reconciliation. The ledger and capability model must support this before enabling the options strategies described in the UW roadmap.

## 7. Milestones and acceptance tests

The original scope understated effort by describing items 3–5 as small and counting only four callers. Size work by these deliverables rather than by a column migration:

1. **Containment and inventory:** explicit modes and UI status; block new entries on failed required preflight; inventory every quantity-changing path; preserve a verified route to manage existing exposure. Do not turn a protective containment measure into silent disabled exits.
2. **Durable execution core:** committed intents, broker-aware client IDs, attempts, normalized events, reservations, decimal quantities, fill-driven ledger, and reconciliation.
3. **Route consolidation:** entry/full/partial/scale-in/manual/conditional/liquidation commands use the core; legacy simulated and broker records are explicitly migrated or quarantined.
4. **Stock broker-paper validation:** failure/restart/concurrency tests, then controlled broker-paper observation with real quotes and order outcomes. This review did not submit test orders.
5. **Options capability validation:** contracts, legs, approvals, assignment/expiry, and exact broker API behavior.
6. **Limited live rollout:** separately authorized capital/risk envelope only after reconciled paper operation and failure recovery meet acceptance criteria.

| Test | Required result |
|---|---|
| Account preflight timeout / rejected credentials | No new broker entry; explicit blocked/degraded state |
| Crash before dispatch | Committed intent remains recoverable; no lost or duplicate intent |
| Broker accepted, response lost | Original attempt becomes unknown; recovery finds/reconciles it before any new submission |
| Duplicate client-ID response | Provider-specific handling; no assumption of success/no-op |
| Duplicate/out-of-order fill events | Cash, inventory, fees and P&L applied exactly once per effective fill/correction |
| 40/100 entry fill followed by exit | Unfilled entry remainder resolved; no oversell or later untracked buy |
| Cancel/fill or replace/fill race | Final effective fills determine quantity; request acknowledgements do not force closure |
| Concurrent manual and stop exits | One coordinated reduction; no competing excessive sells |
| Two portfolios sharing buying power | Account reservations enforce the shared limit |
| Scale-out and later full close | Sum of broker fills and residual inventory reconcile to original acquired quantity |
| Scale-in and later close | Additional inventory exists only after its confirmed fill |
| Fractional/zero-size request | Valid increment or explicit rejection/residual policy; no silent truncation |
| External manual broker activity | Detected discrepancy with explicit ownership reconciliation |
| Restart after legacy simulated close | Outstanding broker orders remain discoverable; closed paper state does not exclude them |
| Options partial legs/assignment/expiry | No falsely flat position; cash, stock and option inventory reconcile |
| Paper/live account mismatch | Submission blocked before any order API call |

These are acceptance requirements, not a claim that tests or the execution model already exist.

## 8. Verification record and practical next step

Actual isolated source behavior reproduced:

```text
Preflight timeout still submitted: buy
Pending exit submitted; no exit ID persisted on trade: True
Manual helper closed and credited simulated cash; broker calls: 0
```

The harness compiled only the actual target functions via AST, supplied a fake broker/in-memory objects and inert logging, and never imported live broker credentials or issued a network request. An initial harness AST-location error was corrected before the successful run; it was not an application defect.

Production SQL used read-only transactions and eight-second statement timeouts. Storage checks used PostgreSQL metadata size functions, not a full scan of the large option archive. The empty T410 table was confirmed by both a LONG BUY query and an all-status aggregate. The writer was not invoked, so its empty state has no diagnosed cause in this review.

Relevant running/local market-data hashes:

```text
paper_trading_engine.py  688c238414309b32f67d78a0b15da136c3c4b74f03a2c3a05e0ff21b4302b320
conditional_orders.py   25dd2da72ddac441f43981fd0b11efd8597cee64bad50e07eba7516f3940b82c
api/paper_portfolio.py  85e5e0f519ae158765c73b810c0679c387c71b3c6acb2ce4363ad83330fca400
```

**Recommended next implementation decision:** prioritize message/data correctness and verify T410 population while designing the durable execution core. Evaluate UW features in shadow mode using the corrected definitions. Keep broker live enablement dependent on the expanded lifecycle scope, rather than treating the existing scoping document as a complete implementation specification.
