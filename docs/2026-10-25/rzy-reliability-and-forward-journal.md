# Little RZY reliability and forward simulator journal — 2026-10-25

Status: committed, pushed, deployed and independently checked in production. Research only. Strategy/configuration defaults and execution eligibility are unchanged. This record supersedes no prior evaluation: the corrected historical evaluation still shows no demonstrated edge.

## Scope and acceptance

The approved sequence is alert speed/data readiness, a complete observable simulator lifecycle, durable performance tracking, clearer email stages and genuinely unseen validation. This release implements measurement and durable recording. It cannot supply missing execution-provider/borrow evidence or manufacture a forward profitable-edge result. Trackers 457/458 remain open until full-session acceptance, even if deployment and health checks pass.

| Area | Implemented in this release | Acceptance still required |
| --- | --- | --- |
| Scan cost | Read only the eight consumed price columns instead of 6,000 ORM objects; skip the unused 1,000-candle chart projection in background scans. Same 6,000-row source window, adjustment basis, revisions, gap policy and engine rules. | Full-session before/after scan-phase and missed-signal comparison. This is **not** an incremental-history loader or a measured live speedup. |
| Data readiness | Snapshots carry actual/expected completed-bar close, canonical source, segment/gap evidence and paper blockers. The admin page separates expired snapshots, missing latest bins and unknown evidence. | Scheduled final OHLCV reconciliation, off-grid received/rejected/persisted counters and sparse-symbol classifications under 457a–c still need production evidence. No bin is invented. |
| Alert timing | Per-timeframe incremental TRIGGERED population including unmailed signals; close-to-detection, close-to-first-queue and close-to-provider-acceptance distributions, sample counts, late triggers and receipt outcomes. All session scan rows in the last 20 hours are measured, not just the latest 40. | Full-session bounded latency/coverage; exact first completed-bar ingest time is not stored (first write can be forming). Provider acceptance is not inbox delivery. A zero-event window proves no detection completeness. |
| Durable simulator | `little_rzy_paper_journal`: one position observation per stream/configuration/generation/structure with lifecycle events, position state, original geometry, configuration and first/update times. Atomic with the existing per-stream commit; no generation/occurrence pruning removes journal rows. Normal scans touch only positions emitting lifecycle events. | Real execution-grade source integration. Existing stored-price streams remain blocked; the release does not make them fillable. |
| Performance | Current-configuration/current-watchlist, last 90 days by first observation; cancellation/unresolved/closed/scored counts, net expectancy in initial-risk R, win rate, average win/loss and realised exit-order drawdown; direction/timeframe splits. Latest 200 journal entries shown; stats cover the stated full window. | Genuinely unseen completed, after-cost observations. No profitability claim or automatic promotion at any sample size. |
| Email stages | Explicit simulated-fill subject/body, fill time/price and simulator-only execution wording. Research invalidated/expired renderers cannot imply a trade exit. Queued lifecycle messages are rechecked against actual fill/time, exit price/quantity/state, P&L and reason as well as setup geometry. | Research invalidation/expiry renderers do **not** add new outbound subscriptions or promise follow-up. Existing entry/watch/cancellation/exit consent, expiry and occurrence dedup still apply. |

## Cohort and scoring rules

- A new journal entry is `forward_observation` only if its trigger was emitted on an incremental pass and first observed between its candle close and timeframe + 3 minutes later. This label means timely prospective observation, **not** demonstrated execution quality, independently unseen market evidence, or an edge.
- Bootstrap/rebuild positions remain explicitly `bootstrap`/`rebuild`. An imported existing position or late incremental trigger is `imported_or_late`. Missing past checkpoint history is not reconstructed or silently claimed complete.
- Pending entries can later become simulated fills, cancellations, partial exits and closed positions. Each lifecycle event ID is recorded once. Repeated scans cannot duplicate a trade observation.
- Cancellations have no trade P&L and are never counted as losses. Unresolved positions are counted but not scored. Closed rows missing valid finite net P&L or positive directional initial fill-to-structural-stop risk are unscorable, not fabricated zero returns.
- Superseded generations are retained, labelled and excluded from performance. Corrections can therefore change the current evidence set; this is visible, not a second independent trade. The summary is not a permanent immutable performance claim.
- R = modelled net one-share P&L / initial directional fill-to-stop price risk. Modelled spread/slippage are in fills, commissions and short borrow in net P&L. R normalises the descriptive cross-stock results; sums of dollar P&L across differently priced stocks are not presented as portfolio returns.
- Realised drawdown follows closed-trade exit order. It excludes unresolved exposure, capital limits and portfolio/mark-to-market effects. Tied close times use stable journal ID order. No confidence or win probability is inferred from planned reward:risk.
- Journal storage is durable with no automatic deletion in this patch. The 90-day reporting window is not retention. Storage growth and an explicit archival policy should be monitored before any future pruning change.

## Read-only Friday-session evidence and additional status fix

Production was read on October 25 at 22:37 UTC, in a READ ONLY transaction with a 10-second statement limit. For October 23: 141 of 155 active US symbols have the full expected grid, 227 intervals remain missing and zero off-grid rows were found. Final bars are absent for SSNLF, BRK-A and AMADY. GDX, MDB, SMH, SOXX, VICR, AAPL, MU and NVDA all have their 19:55 UTC bar last-written after the 20:00 UTC close (at 20:02). This verifies the scheduled final write on those examples, **not** independent provider OHLCV equality, universal completeness or no-trade explanations.

**Additional freshness defect fixed:** native completed-bar freshness previously tolerated exactly one missing interval because it compared an unrounded clock cutoff with a whole-timeframe allowance. It now requires the latest calendar-anchored bin that has finished its two-minute settling period. A boundary test distinguishes a still-settling next bin from one that should already exist. This tightens data eligibility; it does not change RZY geometry/configuration. Derived streams remain conservative when their 5m source is stale.

The rejected-snapshot counter reads 771, but it is cumulative rather than per-session received/rejected/persisted accounting. It does not close 457b's full acceptance contract.

**Additional defect found and fixed:** the HK second reconciliation pass reported `ok` despite internal missing bars for five symbols. Status considered only missing/forming final candles, not `missing_candles`. Any internal gap now yields `partial`; the existing explicit no-session-data exclusion policy remains. A behavioural test exercises a finalized final candle with one internal gap. Historical Redis status is retained as observed and is not rewritten.

[Read-only evidence](../audits/evidence/2026-10-25-rzy-reliability/production-readiness-before.json). The 6,000-bar disposable SQLite query+adapter benchmark measured median 0.208 s for full ORM versus 0.123 s for projected columns (41% lower); five measured samples per arm, alternating order after warmup, outputs equal. [Reproducible script and result](../audits/evidence/2026-10-25-rzy-reliability/loading-benchmark.json). This excludes journal writes and full-scan work and is not a production speedup claim.

## Interfaces and access

`GET /stocks/rzy/paper` retains the existing operational-cache `positions` field and adds a `performance` report with summary, grouped metrics, window/coverage disclosures and journal records. Access is restricted to the authenticated caller's current US watchlist. `/little-rzy` remains the existing admin page and displays this caller-scoped report.

`GET /stocks/rzy/admin/overview` adds `monitoring` and per-stream health. Detection/first-queue samples are per occurrence; accepted samples and receipt states are per recipient. `not_queued` and `receipt_outside_window` count occurrences and do not assert a rejection cause. Bootstrap/rebuild occurrences are excluded from the trigger-latency population. Current-configuration occurrences are selected explicitly. Existing recent-mail tables remain a presentation sample; latency uses the complete stated window.

Old snapshots without health fields display unknown, not ready. They acquire health on their next scheduled scan. No model calls, test mail, broker orders, preference enrolment or strategy activation are required by this implementation.

## Verification

Validation: full market-data **5,918 passed / 26 skipped**; final affected checks **73 passed** (including the two subsequently added freshness/status boundary cases); real PostgreSQL 16 workflow gate **1 passed**; frontend **986 passed / 83 files**; full TypeScript and whitespace checks passed. The final affected checks cover the last source edits after the full run began. No other backend service changed. Eight compiled sabotages were applied in an isolated source copy, each rejected by its targeted test; the untouched isolated baseline passed. Raw logs and the reproducible mutation script are in [the evidence directory](../audits/evidence/2026-10-25-rzy-reliability/).

Behavioural coverage includes:

- projected-column adapter output identical to the original full ORM query/adapter and no Price entities left in the fresh identity map;
- chart-free evaluation identical except for the omitted chart projection;
- real SQLite/PostgreSQL journal storage, repeated-event idempotency, generation isolation, watchlist isolation, cancellation exclusion and survival after checkpoint replacement;
- real production Engine -> next-open simulated fill -> conservative stop -> journal -> net-R summary, with friction/commission loss preserved;
- unmailed incremental trigger included in latency; bootstrap excluded; queue and provider acceptance counted separately;
- simulated-fill email text/HTML and invalidated/expired wording; changed execution figures cannot pass queued-mail equivalence;
- dashboard null rates remain “—”, with explicit denominators, blockers, missing-bin status and lifecycle records.

## Final dashboard read-cost follow-up

Review after the main application commit (`8d5833b8`) found that the existing paper endpoint fetched whole bar checkpoints for its position cache, and the new journal report also needed only the generation identifier. Both paths now select the required JSON subfields on the server. The legacy position response is unchanged, while the journal generation query returns only stream key/generation. This avoids decoding large redundant bar histories on a dashboard request. Real SQLite workflow **28 passed** and PostgreSQL 16 workflow **1 passed** verify actual cursor columns and exact legacy position values; this follow-up changes market-data only. [Follow-up evidence](../audits/evidence/2026-10-25-rzy-reliability/paper-projection-manifest.json).

## Deployment and next evidence

Deployed main release **`8d5833b8`** (market-data and frontend), then read-cost follow-up **`1c8790bc`** (market-data only), under the existing host-wide deployment lock. Frontend build **`p_TOgzaqmJB50k54BZqHj`**. Previous images and logs were retained; rollback tags include `rollback-before-8d5833b8` for market-data/frontend and `rollback-before-1c8790bc` for market-data. Remote rollout evidence is `/home/ec2-user/deploy-evidence/2026-10-25-rzy-reliability/`.

Independent checks: **15/15 running healthy; zero drift across all 12 backend `src` and `shared` hashes**; only market-data/frontend start times changed. `/`, `/little-rzy`, `/stock/MU` and `/improvements` return 200 with the running build ID. Compiled assets include the timing/readiness and forward-performance sections and no `localhost:8000`. Unauthenticated requests through the published API gateway return 401 for paper and admin overview. The first verifier incorrectly assumed the private backend port was host-published; verification was corrected to use the gateway.

The new table was created through service model registration and the existing locked startup schema path, without changing existing table columns. A READ ONLY transaction executed the real paper/admin handlers: 348 monitored streams, zero journal rows, null win rate/expectancy and `execution_ready=false`. Missing-latest-bin refusal and the simulated-fill label passed against loaded production code. Configuration remains **`8ceb8cf780c3eb981983`**. No production fixture records, test emails, model calls or broker orders were generated. No authenticated interactive browser acceptance was performed. [Verification, immutable images/start times and source hashes](../audits/evidence/2026-10-25-rzy-reliability/production.json).

Host ended with 28 GB free and 0.0% measured I/O wait. The standard deployment cleanup ran for dangling images/stopped containers; no volumes were removed and rollback images remained tagged.

After deployment, use a complete scheduled session for speed/completeness acceptance, beginning with Monday October 26, 2026 (NYSE regular session). Persist session-report artifacts and release/configuration identities. Do not pool earlier releases into a claimed improvement rate. A healthy container, empty weekend journal or a source-hash match cannot close 457/458 or establish trading readiness.
