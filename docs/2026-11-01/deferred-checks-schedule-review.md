# Deferred-check schedule review, 2026-11-01

Requested on Sun Nov 1: check every deferred-check schedule, review it, and document it.

**Sources compared.** Three places hold deferred checks, and they had drifted apart:
- the 18 StockAI events in Google Calendar (`stockai2028@gmail.com`, Oct 1 – Dec 31);
- the Deferred Checks Calendar page (artifact `f2462c2d…`, last updated Fri Oct 30);
- the project notes on the pending-data and deferred-check items.

**How the checks were run.**
- Production reads used `READ ONLY` PostgreSQL transactions, Redis `GET`/`SCAN` and `printenv`.
- `scripts/check_deploy_drift.sh` ran from a clean `origin/prod` worktree (`6797e1b9`).
- Weekdays and trading-day counts were computed from `shared/common/market_calendar.py` at
  `origin/prod`, not recalled.
- Nothing was written to production.

## 1. Inventory

Status as of Sun Nov 1, 17:45 UTC. "Page" means the Deferred Checks Calendar.

| Date | Check | Waits on | Status after this review |
|---|---|---|---|
| Thu Oct 1 | MU-01 first instrumented earnings sync | a scheduled run | Closed earlier; the event had no result. Result added |
| Fri Oct 2 | Exposure reservation and shadow telemetry in a live session | a US session | **Closed now.** 27 reservations, all `consumed`; none `reserved` or `committing` at rest. The shadow line is superseded by `paper_entry_receipts` |
| Sat Oct 3 | M02 recovery markers | expiry ~Oct 6 | Closed by expiry (Oct 13). Portfolio 5's strategy review stays open |
| Sat Oct 3 | M21 outage recovery digest | a decision | Closed unsent (Oct 13) |
| Tue Oct 6 | Did the 2026-09-22 fixes take effect | elapsed time | **Was overdue; checked now. Pass** (§2) |
| Thu Oct 8 | Engineering backlog M13/M14/M20/M24/M25 | work | Reviewed Oct 13; M24 degraded, M25 dispatcher has no caller |
| Thu Oct 15 | MU-02 phase alerts; broker fill re-poll | occurrences | **Was overdue.** Cannot be verified retrospectively; moved to Nov 18. The broker half is blocked (§3) |
| Tue Oct 20 | RZY rebuilds, digest, 5m chart | a session | Read Oct 29: 5 of 7 pass; rebuilds rising |
| Fri Oct 23 | Price-risk shadow review | 5 sessions | Not decidable; a fresh trial runs Nov 2–6 |
| Mon Oct 26 | Fresh-mark shadow block rate | log lines | Deferred 0 of 0; replaced by receipts, read Nov 13 |
| Fri Oct 30 | Intraday Stage C US refresh and evaluation | a session | Passed as a combined-release check; the event had no result. Result added |
| **Mon Nov 2** | Monthly data check, first EST session, restore, storage | the month | Event updated to match the page (§4) |
| **Mon Nov 9** | Price-risk trial; RZY rebuild triggers; one evaluation per market | sessions Nov 2–6 | Unchanged; release note added (§5) |
| **Fri Nov 13** | Fresh-mark entry receipts, first read | entry attempts | Unchanged |
| **Wed Nov 18** | MU-02 phase alerts on NVDA's Nov 17 report | an earnings release | **New event** |
| **Fri Nov 27** | Thanksgiving holiday gates and NYSE early close | Nov 26 and 27 | **New event**; the page had it and the calendar did not (§4) |
| Tue Dec 1 | GEX control arm, review trigger | ~30 control outcomes | Unchanged |
| Fri Dec 4 | Did the 2026-09-22 fixes work | 52 trading days | Unchanged. Recomputed: Sep 23 – Dec 4 is exactly 52 US trading days |
| Tue Dec 8 | Quarterly structural checks | a quarter | Event corrected: holiday and 2027 items moved or already done |
| Thu Dec 31 | Regime robustness, dark-pool baseline | the market; a replay | Dec 24 half-day check added (§6) |
| Before 2027-01-01 | 2027 holiday calendar | HKEX circular | 2027 lists live since Oct 30; half-day rule inconsistent (§6) |

## 2. Oct 6: did the 2026-09-22 fixes take effect — pass

1. **HK regime tag.** Criterion: the share of HK BUY signals tagged `bull` is below 60%.
   - Since Sep 22: 40 of 216, which is **18.5%**. The rest are `choppy` 175 and `unknown` 1.
   - By week: 70.3% for the week of Sep 21, which includes days before the 06:38 UTC Sep 22 fix.
     Every week since Sep 28 is 0%.
   - The baseline was 94.5%. **Pass.**
2. **Confidence sizing gated off.** Criterion: zero new entries sized up for confidence.
   - The event's query matched 1 of 29 entries: trade 1031, BRK-A, portfolio 8, Oct 14. Its note
     is `Size 1.25× (DE score 8, excess +4 from min 4)`.
   - That is the score-excess multiplier (`paper_trading_engine.py:5474`), not the confidence
     multiplier (`:5388`, `Size 1.25× (confidence …)`). Matching on `confidence` finds 0.
   - **Pass.** The query in the Dec 4 follow-up should match `Size 1.25× (confidence`.
3. **FixRecord #4 accumulating.** 4 snapshots: Sep 22, Sep 30, Oct 15, Oct 29.
   - Sample sizes are 0, 131, 2,110 and 6,047 outcomes.
   - **Pass.** Alpha is not judged until Dec 4.

## 3. Oct 15: MU-02 phase alerts and broker re-poll — cannot be closed retrospectively

**MU-02.**
- The `stockai:early_earnings_news:unresolved` key is absent, so no stale pending row is
  suppressing results.
- The job is healthy: `ok` on its last run, and its data-quality check is within 1 h.
- No evidence survives of preview, results and guidance each sending its own alert:
  - Only one phase dedup key is left, EPD 2026-10-29 `guidance`. The keys expire.
  - The October container logs were lost to recreates.
  - Earnings alerts do not pass through `notification_outbox`, which has 0 earnings rows.
- **Moved to Wed Nov 18**, the morning after NVDA reports on Tue Nov 17. Read it the same day,
  before any recreate.
- Next followed reports: NVDA Nov 17, CRDO Nov 30, AVGO Dec 9, ORCL Dec 14, MU Dec 23.

**Broker fill re-poll (M16).**
- 0 paper trades have ever had a `broker_order_id`, so the occurrence it waits for cannot happen.
- **Moved to "blocked: no broker-linked trading"** with no date, instead of staying overdue.

## 4. Calendar and page drift found and corrected

- **Nov 2 event was stale.** It still held the Oct 1 text. It now carries what the page has:
  - the first EST session after Sun Nov 1;
  - the restore test and option-chain storage;
  - the six collecting items.
  - Plus one check that had no date anywhere: `f5eabd6a` (UW 429 retry) asks for late US 5m bars
    to be measured after the Nov 2 session.
- **Thanksgiving was only on the page.** The calendar's version lived inside the Dec 8 event, as
  `docker logs --since 720h`. Container logs do not survive a recreate, and the deploy script's
  saved logs keep only the newest 10 per service. So a Dec 8 read would most likely find nothing.
  - New event Fri Nov 27, 13:30 PT, after the 13:00 ET close and the 16:07 ET pass, covering both
    Nov 26 and Nov 27.
  - The per-pass reconciliation keys (30 days) and job-status records hold the evidence if logs
    are gone.
- **Dec 8 event.** It now points the holiday item at Nov 27. It records the 2027 lists as
  deployed, with the HKEX circular still to read. The dark-pool direction test overlaps the Dec 31
  baseline replay, and both are kept.
- **Results added to events that had none:** Oct 1, Oct 2, Oct 3 (both), Oct 6, Oct 8, Oct 15 and
  Oct 30. A past event without a result reads as overdue.

## 5. Before the trial starts Mon Nov 2: market-data's release is `unrecorded`

- market-data was recreated at 2026-11-01 04:42 UTC to ship `f5eabd6a`/`8c5ca84c`. The tracker
  records it as deployed at 04:3x UTC.
- The recreate ran outside `scripts/deploy_remote.sh`, so:
  - `STOCKAI_RELEASE_SHA` reads `unrecorded`. event-intelligence, deployed by the script, reads
    `2609ff6c…`.
  - No outgoing container log was saved. The newest saved logs are from Oct 31 02:15.
- **The code is current.** The drift check reports 12 of 12 backends matching `6797e1b9`.

**Effect on the Nov 2–6 price-risk trial.**
- `measurement_report.py` counts a session as complete only if all its scans share one
  `(release_sha, code_fingerprint)` pair.
- An unchanged container gives every scan the same pair, `unrecorded` plus one fingerprint, so
  the trial is still measurable.
- What is lost is the readable release name; the fingerprint still identifies the code.
- **Any market-data recreate during Nov 2–6 splits the session it lands in.**
**Resolved the same day.** With the user's approval, market-data was redeployed through
`scripts/deploy.sh market-data` at 18:09 UTC, with both markets closed:
- exit 0; `running/healthy`; all declared services present;
- `STOCKAI_RELEASE_SHA=6797e1b9b1d7…`, with no `-dirty`;
- 0 `Traceback`/`ERROR` lines since start; the scheduler started;
- the outgoing log was saved to
  `~/deploy-evidence/container-logs/market-data-20261101T180901Z-before-6797e1b9b1d7.log.gz`;
- the drift check reports 12 of 12 OK;
- the previous image is kept as `stockai-market-data:rollback-before-recorded-6797e1b9`
  (`47415e5bf99b`).

Trial scans from Nov 2 therefore carry a named release. Do not recreate market-data during
Nov 2–6.

## 6. Holiday calendar observations

- **2026 HK corrections** (`1e5249a3`) and the **2027 lists** (`6d1e5d2a`) are live in all 12
  backends.
- **2027 NYSE** is correct for every rule-derived date: observed Jul 5, Jun 18 and Dec 24; early
  close Nov 26.
- **HK half-days are handled two ways.**
  - 2027 lists Fri Dec 24 as closed ("half-day, treated as closed for scheduling").
  - 2026 lists Thu Dec 24 as an ordinary full session, and Lunar New Year's Eve 2026 (Feb 16) was
    also treated as full.
  - On **Thu Dec 24, 2026** HKEX closes at 12:00 HKT. HK jobs and the reconciliation will expect
    an afternoon, so afternoon candles will be reported missing.
  - Decide one rule: model half-days, or close them. Until then, read Dec 24's HK reconciliation
    with that in mind. Added to the Dec 31 event.
- **US Dec 24, 2026** is an NYSE early close and is listed. Dec 25 is closed in both markets.

## 7. Still open, unchanged by this review

- RZY rebuild attribution (read Nov 9).
- Making the price-risk shadow measurable: done by `price_risk_scans`; the trial is Nov 2–6.
- Price-risk email activation: the user's decision after Nov 9.
- Intraday sealing and retention gate.
- The Oct 22 host stall; 1-minute sysstat needs approval.
- Incremental RZY bar loading.
- The 43 Oct 21 occurrences.
- Portfolio 5 strategy.
- Option prospective capture (AQ11).
- Models with no CV score.
- ML attrition ledger at the next retrain.
- Anti-chase realised block rate.

**Storage baseline.** 28 GB free (72 GB used of 100 GB) on Nov 1, the same as on Oct 30. Daily
backups are present through Nov 1 (2.6 GB each).
