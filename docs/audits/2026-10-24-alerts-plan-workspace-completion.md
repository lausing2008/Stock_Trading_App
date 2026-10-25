# Alerts, event impacts, copilot and workspace completion review — October 24, 2026

## Result

**The seven documents are not collectively complete.** Much of their “not implemented” wording is historical: the five price-risk defects, five copilot findings, quiet hours, general company-event shadow coverage and chart/Game Plan workspace already have implementations and regression coverage. This review completes bounded detector/coverage gaps and identifies the work that cannot honestly be marked complete from code alone.

The requested `n/home/.../2026-10-18-trading-workspace.md` was resolved to the existing `docs/features/2026-10-18-trading-workspace.md`.

At the initial review this patch was local. **Authorized rollout: deployed and verified as `0a78f4ea`**, with the release record below. It changes market-data and the Risk Alerts frontend. No schema migration, real model call, account enrollment, delivery activation, trade or order. The initial aggregate checks used an explicit read-only transaction; the later authorized application rollout is recorded below.

## Document-by-document completion

| Requested record | Current implementation | Completion boundary |
| --- | --- | --- |
| [October 14 alerts audit](2026-10-14-alerts-recommendations-audit.md) | F1–F5 and escalation residual are implemented. Price freshness, observation-based expiry, disable/recheck, recovery episodes, exclusions and full-window/evidence-aware baskets remain covered by the real detector probe. Quiet hours are implemented. | Engineering defects closed. The five-session sample exists, but volume/recall/delay/coverage acceptance is not established merely by its existence; delivery stays gated. Broader channels and executable options remain partial. |
| [October 11 AI/options/news follow-up](2026-10-11-ai-options-news-followup.md) | Reviewed product watch, alias/false-RTX-tag corrections, recipient funnel, suppression explanations and research long-option plan exist. General company headlines now have a separate shadow producer. | All four recommendations have engineering foundations, not all their required outcomes. General source validation, executable option quotes/terms/account inputs and prospective model improvement remain outstanding. |
| [October 12 event-impact/funnel/options implementation](../features/2026-10-12-event-impact-flow-funnel-and-options-review.md) | Durable events/receipts/outbox, opt-in defaults, per-user flow decisions, ML quality fields and read-only long-option research route exist. The fixed launch rule is explicitly bounded; company shadow coverage supplements it. | Research workflow implemented. It does not establish a generic first-party news intake or a live actionable option producer. `option_opportunity` must not be wired to archived quotes as if executable. |
| [October 12 market/company-risk design](../features/2026-10-12-market-company-risk-alert-design.md) | Stock/theme detection, quiet-hour preferences and fail-closed sender, receipts/history, digest limits and company shadow coverage exist. This patch adds named benchmark research. | Partial overall: no unified consented market/company/macro delivery channel, source-validated macro actual/consensus/revision contract, unified morning/closing recap or completed alert-readiness acceptance. Existing premarket/RZY digests are not silently relabelled as that proposed unified product. |
| [October 15 independent copilot review](2026-10-15-copilot-plan-independent-review.md) | C1–C5 resolved: user-stated stop contract, unsupported-position blocking, whole risk-amount parsing, narrower forecast grounding and loss-versus-profit labels. Current endpoint also has completed-session/freshness and validated independent LLM proposals. | Reviewed defects closed under the stateless candidate-stop contract; no maintained broker stop is implied. Current plan acceptance suite passes 95 tests. Paid-model results and research limits retain their recorded scope. |
| [October 20 full RZY/Game Plan audit](../2026-10-20/little-rzy-stock-game-plan-full-audit.md) | F01–F08 implementations are present; F06 is a conditional reference/retest policy rather than verified after-cost execution. F09 event timing is fixed; this review also fixes the remaining snapshot processing-time/cutoff ambiguity. | Earlier production remediation is recorded; the snapshot fix is deployed as `0a78f4ea`. RZY execution/data-readiness, independent forward performance and a durable account trade ledger remain separate unfinished work. |
| [October 18 trading workspace](../features/2026-10-18-trading-workspace.md) | Pinned joined chart/plan, selected-plan overlays, scenario selection, tabs, focus/Escape, retained widget state, layout controls/sync, board saving and confirmed drafts are present. | Core scope implemented. Forty relevant workspace/plan/quiet-hour tests pass. Authenticated live desktop/mobile acceptance of today's exact release was not performed in this review. Free-form pixel resizing/masonry were explicitly outside the original change. |

## Fixes and implementations in this patch

### 1. Owned-watchlist risk audience defect

`price_risk_watch._record_receipts()` joined `WatchlistItem.user_id` directly to the user. Modern items can instead identify their owner through `watchlist_id`; those users were included in the detection universe but silently omitted from recipient decisions.

The audience now resolves both representations, excludes inactive/missing owners and deduplicates by user. It still checks existing preferences and delivery rules. The real SQLite probe creates an item with NULL direct `user_id`, proves its owner receives the correct shadow decision, and proves an explicitly opted-in/live stock case queues one digest and one in-app notice. Tests use a fake provider-free environment; no actual notification is sent.

### 2. Benchmark detector wiring

The pure detector already had a 2% benchmark ladder, but the scheduled workflow always called it with `scope='symbol'` and never supplied benchmarks independently of watchlists.

The existing price-risk job now also evaluates **SPY (S&P 500 ETF), QQQ (Nasdaq-100 ETF) and SOXX (semiconductor ETF)** under `benchmark` scope. These are named, unlevered proxies, not the index/sector itself. They share the settled-session, quote-freshness, corporate-action, exclusion, episode and deduplication contracts. Benchmark scope **always forces shadow mode**, even if ordinary price-risk delivery is globally enabled. It sends no email/in-app notice and creates no new subscriptions. Only actual followers receive shadow receipt rows; global history still shows recorded detections. Missing benchmark records/prices remain explicit exclusions.

Risk Alerts can filter stocks, themes and benchmarks, marks benchmark rows as shadow research, and discloses that summary counts cover all kinds/scopes in the selected window. The pre-existing benchmark ladder is a proposed research threshold, not a validated optimum.

Regression: a 2.5% SPY move clears the 2% benchmark rung but not the ordinary stock 3% rung; global live flag and explicit opt-ins still produce no benchmark outbox/notice. Repeating the scan adds no event. Recovery looks up benchmark scope, rather than accidentally closing/reading a stock episode.

### 3. Company categories and event stage

General company coverage previously classified guidance, regulation, contracts, partnerships, products, leadership and earnings, but lacked acquisition/financing categories from the design. It also lacked an explicit structured distinction between rumor, announcement, signed agreement, approval and completion.

The shadow classifier now covers explicit acquisition/merger and financing/offering wording and stores `statement_status` per impact. Labels say **reported** speculation/denial/announcement/signed/approval/completed. Speculation and denial take precedence over completion wording. The visible reason includes the reported stage. All such events remain `provider_headline_unverified`; they do not become confirmed facts, forecast stock directions, or outbound alerts. Existing provider-tag/name binding, safe-URL checks, source links and historical/shadow receipts remain.

Pure neighboring cases and the real company event/receipt API probe cover new stages, unchanged unmeasured direction, persistence, idempotency, safe scopes and zero outbound effects. Cross-publisher semantic event deduplication and independently verified source quality remain incomplete; the current identity is normalized headline plus publication date.

## Additional review: October 20 RZY/Game Plan audit F01–F09

The user added this seventh record after the original six-document review. Its historical findings were checked against the current producer/sender, snapshot API, occurrence/rebuild records and plan tests; they were not assumed closed solely from the resolution document.

| Finding | Current result |
| --- | --- |
| F01 unfinished daily bars / F03 session freshness | Completed-session adapters and per-symbol ingestion verification are present; current 95-test plan probe covers forming rows, missing sessions, weekends and failed/late ingestion. |
| F02 generation ceiling | Sender resolves current events through occurrence/stream identity. Actual storage/sender probe exercises generations 128, 129 and 1000 plus changed/missing events and explicit suppression reasons. |
| F04 state-aware guidance / F05 account cap | Current plan tests cover blocked/stale/exit branches and consistent account-capped structured sizing. |
| F06 breakout reference versus fill | Highest acceptable fill and a three-session retest policy are implemented. Values are reference geometry before actual fees/slippage; a faithful after-cost fill simulation and executable-order lifecycle are not established by this policy. |
| F07 snapshot expiry | Old snapshot candidates are excluded; recent processing of old-cutoff inputs is now explicitly excluded too. |
| F08 retained evidence | Compact occurrence/first-seen and rebuild records survive generation-payload pruning within their retention horizon. This is operational evidence, not a complete perpetual portfolio/performance ledger. |
| F09 processing time | Event first-seen and queue freshness already use the actual clock. Snapshot writes still passed scan-start `now`, incorrectly presenting cutoff as check time; corrected in release `0a78f4ea` below. |

**F09 residual fix (deployed `0a78f4ea`):** both normal and rejected stream snapshots now record actual processing time as `checked_at` and the deterministic input cutoff separately in their JSON summary. Preview eligibility and admin “usable now” counts share one helper requiring **both** timestamps to satisfy the freshness boundary. Older rows retain their existing meaning (their `checked_at` was the cutoff); unreadable cutoff metadata is explicitly unknown and excluded. The API exposes cutoff range/unknown counts without inventing a timestamp; the admin page displays the input cutoff separately from processing completion. No table migration or trading-rule change.

The real delayed-stream probe now checks the nine-minute-late stream's actual snapshot time, unchanged data cutoff, refusal to resurrect a twenty-minute-old cutoff with a fresh write, unknown metadata exclusion and legacy/session expiry behavior. This patch supersedes only the snapshot part of the old F09 claim; it does not claim that RZY has a profitable edge, reliable live execution or complete alert-readiness evidence.

## Read-only production evidence

The planned post-fix cohort **Oct 16, 19, 20, 21 and 22** is now present. It is no longer accurate to say the dates themselves have not elapsed. The bounded query grouped detections observed in the named sessions and recorded before Oct 23 00:00 Eastern; it did not reconstruct full scan denominators or measure threshold recall.

| Observed session | Events (includes exclusions) | Unmeasurable | Events using live quotes | Verified evidence |
| --- | ---: | ---: | ---: | ---: |
| Oct 16 | 154 | 4 | 99 | 0 |
| Oct 19 | 13 | 3 | 7 | 0 |
| Oct 20 | 41 | 3 | 36 | 0 |
| Oct 21 | 22 | 3 | 14 | 0 |
| Oct 22 | 198 | 3 | 188 | 0 |

All grouped events are shadow. The cohort has **196 receipts, all `shadow_suppressed`**, and there are **no price-risk outbox rows** in the queried table. Company-event storage contains **101 provider-headline-unverified events** and one reviewed official-spec/provider event. That establishes that general company shadow coverage is actually producing records; it does not validate classification accuracy.

The uneven daily counts require per-account, cold-start/episode and scan-coverage analysis. Event-only live-quote counts are not quote acceptance rates; zero “verified” does not mean zero measured moves. This query does not pass the proposed go-live criteria or activate a channel. SQL and sanitized output are retained in [the evidence directory](evidence/2026-10-24-recommendations-completion/).

## What remains and how to complete it

1. **Price-risk acceptance review:** reconstruct per-account projected digest volume, cold-start versus new episodes, complete scan/quote exclusion denominators, detection delay and independent threshold-crossing recall for the named cohort. Preserve any missing telemetry as unknown. Judge the proposed 7% pullback rung from that evidence; do not silently retune or enable delivery. New benchmark cohorts must be kept separate from the older stock/theme sample.
2. **Company/source quality:** label a held-out set for issuer binding, category, novelty, event stage and direct/indirect roles; compare false positives and missed events before enabling a consented general digest. Reliable primary-source ingestion and semantic cross-source event identity are still needed. A regex or a supplier mention cannot establish revenue impact.
3. **Unified market/macro development channels:** add explicit per-account market/topic controls and versioned canonical macro events. Existing `EconomicEvent` rows have actual/expected/previous values but lack the full units, revision and sourced-consensus evidence required by this design; they cannot automatically satisfy that contract. Reuse their IDs where appropriate and reconcile legacy macro/earnings sends to prevent duplicates. Morning/closing briefs should compile the same recorded events with coverage, exclusions and time windows; existing specialist digests remain distinct.
4. **Actionable option integration:** establish entitlements for current bid/ask **and size**, verified contract deliverables, account options permission and current options buying power/position risk. Then implement one guarded, exact-contract producer plus frozen capture/resolution tests. These inputs are missing capability evidence, not values that can be guessed from a settled archive. No provider purchase, paid call or live options activation was authorized/performed by this patch.
5. **AI quality:** suppression reasons and chronological comparison tooling are implemented; demonstrating better confidence calibration or net trading outcomes still needs common eligible cohorts, chronological/forward validation, costs and immutable candidate provenance. Keep automatic promotion safeguards intact.
6. **Workspace acceptance:** run authenticated, read-only desktop/mobile acceptance against the eventual deployed build, including switching rules/LLM plans, blocked/stale states, chart lines and saved-plan fidelity. Current fixture tests and older release evidence are not a new live-browser result.

## Validation

- Focused detector/audience/company/quiet-hour checks: 23 passed; expanded company persistence/category rerun: 10 passed.
- Current copilot plan acceptance: 95 passed.
- Workspace, selection, persistence and quiet-hour UI: 40 passed; benchmark filter UI: 1 passed; TypeScript passed.
- Cross-service regressions: 33 ML suppression/inventory tests, 28 news ticker tests and 14 gateway route tests passed.
- Full market-data suite before the additional RZY snapshot patch: **5,858 passed, 26 skipped**, 368.11 seconds. Skips are not counted as passes. Post-patch RZY results are recorded below; this full-suite count is not misrepresented as a rerun of that later patch.
- Added RZY follow-up: **101 engine/workflow/aggregation/email tests passed**; the **55 workflow/aggregation/email cases were rerun successfully** after aligning the admin freshness helper. The 5 relevant frontend cases and TypeScript passed after adding the cutoff display.
- No fresh PostgreSQL integration or authenticated live-browser run in this review. Existing historical deployment evidence retains its original scope.

No claim that all recommendations are complete; the remaining code/integration/research boundaries above are explicit. The initial review included no deployment; the subsequent user-authorized rollout is recorded below.

## Deployment preparation — October 24

The user authorized deployment of all reviewed fixes. Final pre-release checks on the complete patch: **5,859 market-data passed / 26 skipped**, **947 frontend passed across 78 files**, full TypeScript and whitespace checks passed. These counts supersede the earlier partial-scope validation totals for release readiness. Deployment and source-parity evidence will be recorded after the rollout; these test results alone are not a production claim.

## Production rollout — October 24 Pacific / October 25 UTC

**Application release `0a78f4ea528424c5a0475468e7a16d345d4064b9` is committed, pushed and deployed.** The pinned clean revision was fetched under the shared host lock and deployed through the existing `scripts/deploy_remote.sh` entry point. Only market-data and frontend restarted. Completion: **2026-10-25 03:01:01 UTC / Oct 24 20:01 PDT**.

Production preflight had 22 GB free, below the frontend build guard. Retention review preserved every container image and each service's newest distinct rollback; 34 superseded unused images and dangling build layers were removed under the same lock. Free space reached 36 GB before building and 35 GB afterward. No database volumes or backup files were removed. Prior market-data/frontend image IDs and logs were retained on EC2 under `/home/ec2-user/deploy-evidence/2026-10-24-alert-completion/`.

Independent post-deploy checks:

- **15/15 containers healthy; 12/12 backend Python source/shared hashes match the exact committed release.** Only the two intended container start times changed.
- Frontend build **`VJiNLtTgFI1necdbrcMPB`** is served by `/`, `/risk-alerts`, `/little-rzy` and `/stock/MU`, all HTTP 200. Compiled chunks contain the benchmark scope and input-cutoff UI. No `localhost:8000` leak in served assets.
- Loaded production code names SPY/QQQ/SOXX benchmarks, rejects a synthetic old input cutoff through the actual helper, and recognizes reported acquisition stages. These helper checks create no production events or notifications.
- Price-risk delivery remains **disabled**; RZY configuration remains **`8ceb8cf780c3eb981983`**. No delivery activation, test mail or model call was performed.
- Host health gate ended with iowait 0%. Transient build I/O pressure was investigated using vmstat/iostat and subsided; this is not a resolution of the separately recorded historical host-stall incident.

[Production source/health evidence](evidence/2026-10-24-recommendations-completion/production.json), [before state](evidence/2026-10-24-recommendations-completion/before.json), [retained/removal manifest](evidence/2026-10-24-recommendations-completion/image-retention.json), and [release validation manifest](evidence/2026-10-24-recommendations-completion/manifest.json). HTTP and compiled-asset checks are not an authenticated interactive browser acceptance run. Scheduled detection and prospective readiness gates remain open as described above.
