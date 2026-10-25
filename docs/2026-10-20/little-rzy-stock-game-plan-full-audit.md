# Little RZY and stock-detail Game Plan: independent implementation audit

> **Completion re-review — 2026-10-24:** the main F01–F09 remediations are present. A residual snapshot processing-time/data-cutoff issue under F09 is fixed locally with additional regression checks; after-cost execution and RZY readiness remain distinct open gates. See [current review](../audits/2026-10-24-alerts-plan-workspace-completion.md). The original findings below are historical.

Audit date: 2026-10-20 (America/Los_Angeles). Documentation and reproduction artifacts only; no application fixes, deployment, live database writes or email sends.

## Outcome

**Nine findings: two high, seven medium.** The earlier RZA fixes are present, but the current implementation is not logically complete. The most important newly confirmed defects are an unfinished daily bar entering the Game Plan as a daily close and RZY delivery stopping at generation 129. Green focused suites did not cover either boundary.

Scope: the repository's Little RZY specification, deterministic engine, bar preparation/aggregation, persistent generations, scan/digest/outbox flow, chart helpers, research ledger/admin views, and the stock-detail **stock** Game Plan (rules, LLM selection, chart levels, board/draft interactions and scenario playbooks). The options-strategy tab is outside this audit. No assertion here establishes profitable trading or reproduces the original discretionary method.

The starting HEAD was `8b69e5e647dc6f5bd31d9e0103606aec6c196748`, with existing edits to Game Plan backend/frontend files. Another session committed those edits as `c5eca912` while this review ran. Findings F04 and F06 explicitly concern the new scenario playbook. The audit did not edit or revert those files. An evidence manifest records final code hashes. Production incidence, today's provider behavior and installed release identity were **not** independently checked this turn; local reproduction is distinguished from reported production history.

## Findings and solutions

### F01 — HIGH: unfinished daily candles can drive a supposedly daily-close Game Plan

**Code:** [daily_bars](../../services/research-engine/src/intel_reports/adapters.py), lines 40–53; [packet builder](../../services/research-engine/src/copilot/packet.py), lines 449–451 and 628–635.

`daily_bars` filters only `Price.ts <= cutoff`. A D1 row timestamped at the session date's midnight qualifies during that session. There is no exchange-session-close/completion check in this path. The downstream builder describes the value as the latest daily close and the UI says “Daily-close confirmation.” Evidence notes about unknown publication time do not enforce completion.

**Measured with the real SQLite ORM and packet builder:** at 2026-10-09 15:00 UTC, a row timestamped 2026-10-09 00:00 with close 102 is selected, marked `OK`, and used for plan levels/states. The US session closes at 20:00 UTC. The fixture's plans become blocked from an unfinished price, illustrating that the error can both create and suppress guidance. This is a reachable input-selection defect, not a claim that this exact row exists in production.

**Fix:** a market-aware completed-session adapter shared by Game Plan and its monitoring path; exclude the active session until close plus the documented settlement policy. Keep historical publication/ingestion uncertainty separate. Do not infer completion from a midnight timestamp.

**Acceptance:** real API/packet tests during regular hours, after close, early closes, weekends, DST and supported non-US markets; a partial current-session row must not affect the plan. Missing prior completed-session data must produce unavailable/stale status.

### F02 — HIGH: RZY stops mailing after generation 128, falsely reporting opt-out

**Code:** [sender](../../services/market-data/src/services/rzy_workflow.py), `drain`, line 438; `evaluate` increments generations without an upper bound.

The sender constructs event IDs only for `range(1,129)`. The generation number is not reset when old archives are pruned. Current events in generation 129 or later cannot be found, even if current, unchanged, opted in and otherwise eligible.

**Real storage/outbox reproduction:** identical eligibility with generation 128 invokes the fake sender once and reaches `accepted`; generation 129 invokes it zero times and becomes `suppressed`, terminal reason **“recipient opted out.”** No real email is sent. This failure is permanent for that stream until code/state changes; the rebuild throttle does not prevent it.

**Fix:** store/index occurrence ID, stream key and generation as queryable event/outbox provenance. Resolve the current stream first and fetch its exact event; compare against the actual enqueued generation/figures. Avoid guessing a bounded set of generation IDs. Separate eligibility/consent failures from superseded/missing-event suppression reasons.

**Acceptance:** generations 1, 128, 129 and 1000; old-generation pruning; changed and unchanged rebuilds; opt-out; no duplicate send; accurate terminal reasons.

### F03 — MEDIUM: four-session-old prices still qualify as fresh Game Plans

**Code:** [adapters](../../services/research-engine/src/intel_reports/adapters.py), `_MAX_BAR_AGE_DAYS=5` and `price_as_of`; [packet builder](../../services/research-engine/src/copilot/packet.py), line 633.

The generic report adapter uses integer calendar-day age, not expected completed sessions. It marks prices stale only when age is greater than five days. The stock plan inherits this policy without tightening it.

**Measured:** on Friday Oct 9 at 21:00 UTC, Monday Oct 5's close is `OK` and both setups are `conditional`, despite four newer completed US sessions. Freshness gating in chart selection, LLM comparison and drafting cannot help when the upstream state is incorrectly `OK` for the intended daily-close workflow.

**Fix:** declare a Game Plan freshness policy in completed exchange sessions; expose expected/latest session and reason. Historical plans may remain inspectable but must not appear current. Do not replace this with a short wall-clock TTL that wrongly expires weekends.

**Acceptance:** missing Tuesday–Friday bars are stale/unavailable; a valid Friday close remains current over a normal weekend; exchange holidays and early closes use the same contract as F01.

### F04 — MEDIUM: blocked/stale setups still show actionable playbooks

**Code:** [plan generation](../../services/research-engine/src/copilot/plan.py), `_trade_setups` lines 988–995 and `_playbook`; [TradeSetupSummary](../../frontend/src/components/TradeSetupSummary.tsx), lines 23 and 36–45. Present in `c5eca912`'s new playbook.

Playbook generation checks numeric validity, not eligibility. Rendering shows the entire playbook for any nonempty list, independently of `blocked`, `unavailable` or `stale`. State/reasons are displayed, and save/chart gates generally block those states; this finding concerns contradictory human guidance, not automatic order placement.

**Measured:** a close of 90 produces a pullback blocked for broken support, insufficient R/R and a falling trend. Its visible playbook still says “buy only after a daily close holds above” 99. The same playbook states that buying above 98.79 fails minimum R/R: following the buy branch cannot satisfy that particular setup's R/R. A 12-day-stale plan retains four scenario branches per setup.

**Fix:** state-aware guidance: blocked setups lead with wait/rebuild and all conditions needed to become eligible. Stale/unavailable plans show historical scenarios only in explicitly disabled/reference context. After-entry instructions must be labelled hypothetical and separate from current entry eligibility. Apply the same contract to code-cited LLM text.

**Acceptance:** render blocked, stale, missing-trend and exit-triggered cases through the real setup payload; no branch presents an entry as sufficient while another mandatory gate forbids it.

### F05 — MEDIUM: supplied account size is honored by prose but ignored by structured sizing

**Code:** [plan.py](../../services/research-engine/src/copilot/plan.py), `_entry_rules` around line 728 versus `_trade_setups` line 904.

The shared backend accepts account/risk inputs. The narrative sizes at the latest close and caps against the user's stated account, while the structured setups size at their own references but never apply that account cap. Different reference prices legitimately yield different risk sizes; ignoring a provided cap does not.

**Measured:** account 1,000 and risk budget 500 gives prose “10 shares ($1,000), capped,” but the structured pullback says **500 shares / $49,500 capital**, and breakout **250 / $25,250**. A note says sizing does not check buying power, but the same response has already accepted and applied the account amount elsewhere. The stock card currently has no account input; this is directly reachable through the shared copilot/backend inputs, not a claim that the current card collects an account balance.

**Fix:** one sizing function per setup that applies risk and explicitly supplied cash-account limits consistently, without implying a broker buying-power check. Alternatively reject unsupported account-based sizing in this mode and remove the contradictory capped narrative. Keep held-share counts separate from proposed sizes.

**Acceptance:** same supplied budget/account yields mutually consistent prose, structured shares, capital and board/LLM representation; an account insufficient for one share blocks a new entry.

### F06 — MEDIUM: breakout trigger and no-chase ceiling can coincide at an unfillable reference

**Code:** [plan.py](../../services/research-engine/src/copilot/plan.py), fallback target in `_trade_setups`, `_no_chase_limit` and breakout `_playbook`. The new contradictory wording is in `c5eca912`.

When no higher resistance exists, target 1 is exactly `entry + 2 * risk`. The no-chase ceiling is therefore exactly the displayed entry, yet the trigger requires a daily close strictly above that entry/high.

**Measured:** entry 101, stop 99, target 105, “close above 101” trigger and “Do not chase above 101.” A close/fill at 101.10 has only 3.90/2.10 = 1.857R, below the minimum. The card is still labelled conditional. A later pullback fill at/below 101 could be feasible, so this is **not** proof no order sequence could ever work; the necessary two-stage confirmation/limit-entry contract is absent.

**Fix:** distinguish confirmation level, executable entry policy and maximum acceptable fill. Explicitly require confirmation followed by an eligible limit/pullback fill with expiration, or assess at the actual next fill and mark the setup wait if minimum R/R fails. Do not move targets merely to manufacture 2R.

**Acceptance:** simulate confirmation above the level, next-open gap up, eligible pullback and unfilled expiry. Every claimed executable setup must satisfy its R/R at the stated fill policy after costs.

### F07 — MEDIUM: saved snapshot candidates never expire; admin says “usable now”

**Code:** [workflow digest_snapshot](../../services/market-data/src/services/rzy_workflow.py), line 377 onward; [admin overview](../../services/market-data/src/api/little_rzy.py), snapshot status aggregation; [admin page](../../frontend/src/pages/little-rzy.tsx), line 57.

Caching fixed the request timeout, but saved `research_only` status is reused indefinitely. The preview displays age, which is helpful, yet it neither excludes expired candidates nor invalidates their eligibility. Admin counts old statuses as “Streams usable now / complete, current bars.”

**Measured:** a seven-day-old 5m snapshot returns `available=true`, age 10,080 minutes, one candidate and no exclusions. This affects cached preview/admin claims, not the scheduled digest, which still evaluates live.

**Fix:** separate snapshot availability from current usability. Apply session-aware snapshot/data freshness on read; return expired items only as historical, with explicit exclusions and stale counts. Distinguish unavailable scanner evidence from an empty candidate list.

**Acceptance:** fresh snapshot, overnight/weekend, missed pre-open refresh and scanner stopped for a week; an old `research_only` row never counts as currently usable.

### F08 — MEDIUM: pruning invalidates rebuild totals and retrospective alert-latency evidence

**Code:** [workflow `_prune_generations`](../../services/market-data/src/services/rzy_workflow.py), line 213; [admin overview](../../services/market-data/src/api/little_rzy.py), `rebuilds_last_20h`; [session report](../../scripts/rzy_session_report.py), lines 107–137.

The last-five-generation policy deletes archive/event rows. Admin then counts retained archives as total rebuilds in 20 hours. The session report reconstructs first observation from the earliest **surviving** event version. If earlier versions were pruned, it can lose occurrences or replace true first observation with a later rebuild time. Outbox totals are independently available, but cannot reconstruct all suppressed/never-queued occurrences. The report also discards events over two hours old as historical based on age alone; a genuinely delayed first evaluation is indistinguishable under that heuristic.

**Measured with production pruning code:** eight same-session archives become five; nine event versions become five. Therefore archive-based totals cannot count all eight rebuilds. The effect on a particular production day's latency distribution was not measured here.

**Fix:** retain compact append-only rebuild counters and immutable first-observation/decision records independently of large generation payload retention. Record replay versus newly observed events explicitly. Join outbox to those records; report exclusions and missing evidence instead of silently inferring “historical” from age.

**Acceptance:** a report before and after more than five rebuilds/pruning has identical first-seen times, rebuild totals and delivery denominator; a truly late first observation is counted, not removed as replay.

### F09 — MEDIUM: scan-start time is presented as event-recording time

**Code:** [workflow](../../services/market-data/src/services/rzy_workflow.py), `scan` line 257, per-stream `evaluate(..., now)` and `save_snapshot(..., now)`, and event `created_at=now` at line 191; [session report](../../scripts/rzy_session_report.py), `recorded` and `late` calculation.

One `now` is captured before processing the entire scan. Every stream gets this same value for data cutoff, event creation and snapshot checked time. Later streams can take minutes longer. Using a fixed data cutoff can be intentional, but it is not the actual completion/persistence time. The freshness-to-mail check also uses the earlier timestamp, so a late-processed signal can pass enqueue checks and then expire in the outbox. The delivery expiry still protects eventual sends; this finding does not claim an expired email bypasses the sender.

**Source-path confirmation:** all calls share `now` in the stream loop and `RzyEvent.created_at` is explicitly assigned that value. This finding is source-confirmed; no production timing experiment was performed.

**Fix:** separate `scan_started_at`, `data_cutoff`, `evaluated_at`, persisted/queued time and provider-accepted time. Keep deterministic tests via an injected advancing clock. Evaluate freshness immediately before enqueue using actual elapsed wall time; label snapshot cutoff separately from when it was checked.

**Acceptance:** delay the second stream by several minutes in a controlled test; its evaluation timestamp and lateness outcome must reflect the delay, while its input cutoff remains explicitly identifiable.

## What is implemented correctly in the reviewed paths

- RZY uses one deterministic engine, completed-bar settlement in its own adapter, delayed pivot confirmation, frozen structure geometry and separate continuation/reversal watches. This does not imply Game Plan uses that same adapter (F01).
- Earlier RZA changes are present: generation-specific events/current-generation views, aggregate freshness, full replay ledger, calendar-aware gap segmentation, strict boolean execution eligibility, standing-borrow cancellation suppression, native/derived source selection and cancelled-trigger chart history.
- RZY remains research-only with unverified execution/borrow inputs; its research ledger is separate from account balances and broker orders. The email explains this and does not promise an exit for a cancelled paper entry.
- Rules-based Game Plan levels are code-calculated. The LLM selects a validated scenario using the same snapshot; it is not independent corroboration. Comparison fingerprints, response setup validation and explicit wait selection exist.
- Chart setup selection and board save controls reject blocked/stale setup states. Alert drafts require explicit confirmation; daily-close monitoring explicitly distinguishes fixed one-shot notifications from maintained stops.
- The corrected historical evaluation reports no demonstrated edge; retrospective holdouts are labelled as such. This audit does not rerun or overturn that statistical conclusion.

## Scope limits and remaining work

Known OD-01 provider restatements and OD-02 latency remain open; this audit did not inspect Oct 21 production logs or settle the provider-switching hypothesis. The cached preview is built, but F07 and F09 qualify its freshness claims. The frontend currently places research operations/ledger at **Admin → Little RZY (`/little-rzy`)**, with an admin link from Settings; earlier instructions to open a Paper Portfolio ledger are outdated for this revision. This is an observed navigation change, not by itself a trading-logic defect.

The bounded paper position cache and retained event generations are not a durable performance ledger. A user performance dashboard, verified prospective execution evidence, complete trade history, verified short borrow data and actual portfolio integration remain separate work. Unadjusted Game Plan prices plus a large-jump heuristic are not verified corporate-action normalization; do not interpret evidence labels as a completed adjustment contract.

No full-system security/load/browser-visual audit, fresh PostgreSQL integration run, live model call, full repository suite or production re-verification was performed. The review covers the listed feature paths, not every subsystem of the platform. A passing suite is regression evidence, not proof every specification requirement is met.

## Validation and reproducibility

- RZY engine/workflow/aggregation/email: **86 passed**.
- Copilot plan acceptance: **83 passed**, using the actual disposable SQLite packet/API harness and scripted model.
- Six frontend suites (stock card, refresh, setup summary, LLM alternative, RZY settings and chart helpers): **45 passed**.
- Audit probes: [plan probe](rzy-game-plan-audit-evidence/plan_probe.py), [results](rzy-game-plan-audit-evidence/plan-results.json); [workflow probe](rzy-game-plan-audit-evidence/workflow_probe.py), [results](rzy-game-plan-audit-evidence/workflow-results.json). Both reuse only existing harness initialization, execute shipped implementations, refuse externally configured test databases and send no real mail.
- [Manifest](rzy-game-plan-audit-evidence/manifest.json) records code hashes and test summaries. Full focused logs are copied alongside it.

Run from the repository root:

```sh
python docs/2026-10-20/rzy-game-plan-audit-evidence/plan_probe.py
python docs/2026-10-20/rzy-game-plan-audit-evidence/workflow_probe.py
```

Recommended order: fix **F01/F03** as one completed-session/freshness contract and **F02** as a separate delivery fix; then **F04/F06** guidance consistency, **F05** sizing, and **F07–F09** evidence quality. Add tests at actual public-call/persistence boundaries, then re-review. Keep strategy rules/version changes explicit; none of these findings justifies silently tuning the strategy to improve backtest results.
