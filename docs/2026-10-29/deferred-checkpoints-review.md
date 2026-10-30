# Deferred checkpoints review, 2026-10-29

Checked **Thu 2026-10-29 17:20–17:45 PT** (2026-10-30 00:20–00:45 UTC), read-only on production:
SQL inside `BEGIN READ ONLY … ROLLBACK`, Redis `GET`/`EXISTS`, and the read-only
`scripts/rzy_session_report.py`. Nothing was restarted, sent, enabled or written. Weekdays were
computed, not recalled.

This covers the three calendar checkpoints that had no recorded answer, plus the open items on the
[Deferred Checks Calendar](https://claude.ai/code/artifact/f2462c2d-bde6-4f30-99d9-121ede27f411)
page as of its Oct 26 update. Each calendar event now starts with its result.

| Checkpoint (calendar date) | Result |
|---|---|
| RZY rebuilds, digest and 5m chart (Tue Oct 20) | 5 of 7 pass. **Rebuilds fail: rising, 661 → 1,021 → 1,516 on Oct 27–29.** Inbox delivery and the drawn overlay not shown |
| Price-risk shadow review (Fri Oct 23) | **Not complete enough to decide.** Email stays off. Volume misses the design target |
| Fresh-mark shadow block rate (Mon Oct 26) | **Deferred: 0 of 0.** The shadow exists only in logs that were lost |
| Page checkpoint Tue Oct 27 (457a, HK passes, chain archive) | Settled by [the Oct 28 status](../2026-10-28/open-items-status.md): 457a and 457b accepted, chain archive verified |
| HK 2026 calendar defect (page alert) | **Closed.** Fixed in `1e5249a3` (Oct 26); live in every backend since the `a3107b47` rebuild tonight |

## 1. RZY rebuilds, digest and 5m chart (event Tue Oct 20)

Checklist: [`little-rzy-checkpoint.md`](../2026-10-19/little-rzy-checkpoint.md). Reports for Oct 27–29
were produced with `rzy_session_report.py` and are kept in
[`deferred-checkpoints-evidence/rzy-session-reports/`](deferred-checkpoints-evidence/rzy-session-reports/);
Oct 23 and 26 reuse the existing captures in `/home/ec2-user/rzy-session-evidence/`.

| # | Item | Result |
|---|---|---|
| 1 | Legacy quarantine | **Pass.** 0 of 351 checkpoints hold `requires_audited_replay`. The 95 were rebuilt on Oct 20 ([resolution](../2026-10-20/little-rzy-audit-resolution.md)) |
| 2 | 09:10 ET digest | **Pass for enqueue and provider acceptance**, one a day Oct 20–29, queued 09:11–09:12 ET. Inbox delivery **not shown**: `delivery_status` is NULL on every row |
| 3 | Rebuild rate | **Fail, rising.** See the table below |
| 4 | Stream readiness | **Pass.** 5m: 112 research_only, 2 awaiting_backfill (QUCY, XSD), 2 restatement_pending (AAON, BRK-A), 1 stale (AMADY), 1 no bars (SSNLF) |
| 5 | Mail | **Pass.** 7–16 mails a day, all accepted; no ENTRY_CANCELLED, none late. The "queued, then rebuilt, suppressed at send" path did not occur, so it is untested |
| 6 | Checkpoint size | **Pass.** 5m mean 1,083 bars (750 kB), max 1,299 = retain 1,200 + trim batch 100 − 1 |
| 7 | 5m chart defaults | **Pass in source** (`PriceChart.tsx` turns RZY, SMA 5/10 and Bollinger on when entering 5m; the served build contains it). Not checked in a browser |

| Session | Rebuilds | completed bar changed / gap after checkpoint | 5m / 15m / 1h |
|---|---|---|---|
| Fri Oct 23 | 1,093 | 1,002 / 91 | 562 / 385 / 146 |
| Mon Oct 26 | 615 | 517 / 98 | 440 / 151 / 24 |
| Tue Oct 27 | 661 | 548 / 113 | 382 / 220 / 59 |
| Wed Oct 28 | 1,021 | 927 / 94 | 645 / 294 / 82 |
| Thu Oct 29 | 1,516 | 1,401 / 115 | 833 / 485 / 198 |

On Oct 29, 2,018 stream-scans paused for a rebuild, against 604 on Oct 28. Material completed-bar
revisions split 1,348 cross-provider vs **6,942 same-provider** (Oct 28: 1,167 vs 6,430). The
old cross-provider flip-flop is no longer the main source. That the rebuilds follow those
same-provider revisions is an inference: individual rebuilds were not attributed to revisions the
way the Oct 21 analysis did. OD-01 stays open.

**Unaccepted 5m alerts** (page item from Oct 27): 13 across Oct 23–29 (Oct 23 5 of 10, Oct 26 0 of 7,
Oct 27 3 of 15, Oct 28 5 of 13, Oct 29 0 of 2). All were recorded after the 8-minute window and never
queued:
- **Scan latency, 8:** the bar was written within 0.5 min, but the scan reached the symbol 2.5–8.5 min into a run that started 2.9–5.7 min after the close (XPEV, SPCX ×2, UUUU, VICR ×2, NOK, MNDY). The Oct 23 four are inferred, because bar arrival was not instrumented then.
- **Settle timing plus latency, 1:** MU on Oct 27. The first scan started before the 2-minute settle.
- **Late bar arrival, 4:** POET and GRNT on Oct 28 (a missed ingest cycle), SOXX on Oct 23, and CEG's 09:30 bar on Oct 28, written 15.5 min late. That last one fits the 81–84 stale streams in the 09:37–09:48 ET scans.

Not investigated: one 1h signal on Oct 27 recorded 1,001 min after its close.

## 2. Price-risk shadow review (event Fri Oct 23)

Cohort: Fri Oct 16, Mon Oct 19 – Thu Oct 22, five US sessions. The event called Oct 16 a Thursday;
it was a Friday. Design and criteria: [risk-alert design](../features/2026-10-12-market-company-risk-alert-design.md).

| # | Criterion | Result |
|---|---|---|
| 1 | Five complete sessions with intraday scans | **Not shown.** Per-scan `intraday_quotes` and refusals go only to a log line and a last-run Redis key, so the cohort's scans cannot be re-checked. Indirect evidence (live-quote events) shows 2–4 runs every session. **Oct 16 lost its 15:40 and 20:10 ET runs:** 50 closing-price events, AVGO −4.52% among them, were stored at Sun Oct 18 23:30 ET, about 51 h late. That timing matches the Oct 16 host stall and reboot (inference) and breaks the design's ≤ 2 h detection delay |
| 2 | Quote freshness | **Pass for stored events:** 344 of 344 live quotes 0.2–2.0 min old (limit 10). 0 of 412 events refused. Per-scan refusal counts not stored |
| 3 | Missed or borderline moves | **Pass.** 287 watched symbol-sessions with a 1-day move ≤ −3% or a 5-session move ≤ −5%: 155 have an event, 129 sit inside an open episode, and 3 (FRNM, SCTX) were not yet watched. NVDA's 5-session −6.61% and −5.27% fell inside its slow-decline episode open since Oct 14; AVGO's −9.07% inside its pullback episode (next step −12%) |
| 4 | Duplicates, recovery, exclusions | **Pass.** 0 repeated episode keys; 39 episodes closed by recovery, 1 by breadth; 3 same-session re-alerts (SMTC, MXL, OSCR). Exclusions all carry a reason: `no_price_data` 15 (SKHYV, SSNLF, US10Y), `stale_observation` 1 |
| 5 | Projected email volume | **Fail against the design target.** One account. Receipts per session 67 / 1 / 17 / 10 / 101, median 17 vs a target of about 10. Runs with receipts per ET day 4 / 2 / 6 / 5 / 6, inside the 6-a-day cap, but a rolling 24 h window reaches 7 |
| 6 | Shadow sent nothing | **Pass.** 0 price-risk outbox rows; all 573 receipts to date are `shadow_suppressed`; the delivery flag key is absent |

Continuation, kept separate from the cohort (receipts per session): Oct 23 13, Oct 26 25, Oct 27 30,
Oct 28 16, Oct 29 65; 0 outbox rows. Every session had 2–4 intraday runs.

Also found:
- NVDA's and AVGO's Oct 14 episodes closed only by age on Oct 29, although NVDA was back at its six-session high on Oct 21. A fresh decline in that window would have been suppressed (inference).
- After the close, 155 US `live_prices` rows carry `session_date` 2026-10-28 with a fresh `fetched_at`.

Receipt count: the page said 233 for the cohort; recounted, it is 196 by observed session (202 by
detection date).

**What a decision needs:**
1. Per-scan results stored.
2. A fifth complete session, or your acceptance of Oct 23–29 as the cohort.
3. Your call on 17–25 receipts per session and 7 digests in a rolling 24 h.

Email stays off.

## 3. Fresh-mark shadow block rate (event Mon Oct 26)

**Deferred.** The shadow line `paper.exposure_stale_mark_shadow` is deployed
(`paper_trading_engine.py:5421`, since 2026-09-30). It is written only to the market-data log.
Every saved market-data log, across 8 files, misses every US regular session from Oct 1 to Oct 29,
and all of them contain 0 shadow lines. All six measures are therefore 0 of 0.

Denominator from the database: 27 successful exposure reservations Oct 1–29, all US. Only 7 had a
same-sector open position in the same portfolio, which is the only case that can fall back. Refusals
are not recorded outside logs. The paper engine was also mostly stopped before sizing:
`regime_gate_risk_off` ×134 and `consecutive_loss_limit` ×34 in one 3 h window. For the 7 positions
open now, entry price differs from the current mark by −4.9% to +4.8%.

To make it decidable:
1. Persist every `reserve()` call, refusals included, with fallback provenance; or save full-session logs before each recreate.
2. Collect several weeks: about 7 eligible entries a month.
3. Separately decide whether a stale daily close counts as a fresh mark. The "live" mark is the latest close from a 2-day download.

## 4. New checkpoints from this session

| When (PT) | What | Where recorded |
|---|---|---|
| Fri Oct 30 13:45 | Intraday Stage C release: US refresh and outcome evaluation (HK post-close at 01:30 PT also evaluates outcomes) | Calendar event; [implementation record](../features/2026-10-29-intraday-stage-c-implementation.md#production-deployment-2026-10-30-utc) |
| No date, before any shadow producer | Intraday sealing and retention scheduled and tested | Page, "Open, no date" |
| No date | Persist the fresh-mark shadow and the per-scan price-risk results | Page, "Open, no date" |

The release's HK refresh check closed at 01:50 UTC Oct 30: all four HK horizons wrote again at
01:46 UTC, 42 rows each ([evidence](../audits/evidence/2026-10-29-intraday-stage-c-deployment/hk-refresh-check.txt)).
The US refresh and outcome evaluation stay on the Fri Oct 30 13:45 PT checkpoint.

## Corrections to what was reported earlier

- The Intraday release's pre-recreate logs were described as covering 24 hours. They cover each container's life since its last start, capped at 24 h. That is about 17 h for ten services, but only about 20 min for market-data and research-engine, which the valuation release had recreated at 23:34–23:41 UTC.
