# Options-flow snapshot job: why only three Oct 15 rows remained (traced 2026-10-22)

**Status (2026-10-28):**
- Fix A was deployed `5d64a486` on 2026-10-23. The scheduled runs on Oct 23, 26 and 27 each wrote 37 rows from the expected session.
- Fix B is deployed. The 18:45 ET runs archived sessions Oct 23 and 26 for 158/158 symbols.
- Storage retention: recommendation pending your decision.

Evidence for all three: [`../2026-10-28/open-items-status.md`](../2026-10-28/open-items-status.md). The original status line follows.

**Status: fix A built and tested locally, not deployed.** It is a separate change from the chain-archive fix (fix B, below) and the premarket-note wording (`05c063bd`, deployed).

## Trace

| Stage | Finding (production, read-only) |
|---|---|
| Scheduling | `options_flow_eod` 17:00 ET weekdays. Last run Oct 21 21:02 UTC, `partial`, 148 s. The Oct 22 run fell inside the host stall and did not record. |
| Symbol selection | `_bounded_options_flow_symbols`: 39 symbols (price-alert symbols plus top K-Score). Unchanged; not the cause. |
| Provider fetch | The job reads the route, which reads the **settled UW chain archive**. A session's chain is archived the **next day** (05:00–13:30 UTC). Outside the ~30 LEAPS symbols captured nightly at 18:45 ET, a symbol's archive is refreshed **only on demand, once its newest session is more than 7 days old**. That produces a weekly sawtooth: sessions up to Oct 15 were captured in one sweep around Oct 16, and Oct 16–21 are still missing for 105–144 of 155 active US symbols. On Oct 22, 27 of the 39 job symbols read Oct 15, 9 read Oct 20, 1 read Oct 21 and 2 have no options. |
| Persistence | Rows were keyed by the **run date** (`as_of` = today ET) while describing whatever session the archive held. Rows dated Oct 16–21 therefore carry **Oct 15** data: old data relabelled as current. |
| Row count | About 38 rows a day until Oct 8, then 3–7 from Oct 9. The written symbols were small names (CLBT, DIVO, GRNT, BKSY, SMTC); liquid names were absent. This matches the options-review **F1** defect: from Oct 8, sorting null premiums failed the route for any symbol with 2+ unusual contracts. F1 was fixed Oct 21 07:22 UTC. The Oct 21 evening run still wrote only 3 rows, and its cause is **not established**: that container's logs were lost when it was recreated. Today, a rolled-back dry run of the real job computes 37 of 39 symbols, but from stale sessions. |

## Fix A (this change)

- `expected_flow_session(job_date)`: the trading session before the job's ET date (the latest settled chain possible at 17:00 ET). Calendar-aware (weekends, holidays).
- The job **captures the expected session for its own symbols first** (`skip_existing`), so it no longer depends on the on-demand sawtooth.
- A row is written **only when its source session is the expected session**. It stays keyed by the **run date**, the day the data became available, and its evidence names the source session. A stale symbol is skipped and recorded, **never written under a later date**.
- Before computing, the job clears the route's 15-minute cache for its symbols, so a page view before the capture cannot hand back the previous session.
- All upserts happen in one short write at the end; no transaction stays open across provider calls.
- Every skip is recorded with its reason (`stale_source_session`, `unavailable`, errors) in the log and in Redis `scheduler:job:options_flow_eod:detail`. A symbol with no listed options does not make the run `partial`; a stale or failed symbol does.
- The premarket brief reads **current rows only**:
  - It takes the last cron run (the previous weekday; the cron runs Mon–Fri including holidays).
  - It requires the evidence to name that run's expected session.
  - If last night's run is missing, it returns nothing and the brief's empty state shows.

  **The brief therefore shows the session two trading days back**, because a settled chain is archived only the next day. That lag is accepted and stated.

**Why keep run-date keys (review finding 4).** The ML feature `opt_cp_ratio` joins snapshots by `as_of` with a backward as-of merge. Keying by source session would let a bar see flow that was only available about 25 hours later, and it would mix two keyings in one series. Run-date keys keep training and live inference on the same lag.

**Tests.**
- Expected session (weekday, Monday, Thanksgiving).
- Run-date keying with session evidence, and stale exclusion.
- Pre-capture and the cache clear.
- Status rules: no-options symbols are `ok`; real failures are `partial`.
- Brief: the stale path, a pre-fix relabelled row, a holiday, and the full stored-row → note → email path.

Sabotages caught. Independent review: no blockers; its four should-fix findings were addressed as above. The tests also found a duplicated keyword in the new log call that would have raised in production.

## Fix B: nightly full-list capture (committed, not deployed)

- **Backfill:** Oct 16–21, done 2026-10-23 after a sample test. 464 symbol-sessions, 696,897 rows, 0 errors. Complete for every active US symbol except BRK-A.
- **Nightly capture (18:45 ET):** now the LEAPS list **first** (the 19:00 ET income step reads it), then every active, non-delisted US symbol. A startup catch-up after a restart stays on the LEAPS list only, so a daytime deploy cannot launch about 1k provider calls in market hours or capture a session before the provider has archived it.
- **Explicit provider exclusions** (`uw_option_chain.PROVIDER_EXCLUDED`): BRK-A (Unusual Whales returns 422), plus all HK listings by market. They are never fetched on demand, never retried nightly, and count as structural (not stale or failed) in the snapshot job. A page view gets `reason: provider_excluded` with the detail.
- **Failures are visible:** the nightly log names `incomplete_symbols`.
- **Checked:** the only "complete with 0 rows" sessions on weekdays belong to six symbols that list no options at all (AMADY, SSNLF, DFNS, QUCY, SCTX, FRNM), so there are no permanent holes for optionable symbols.
- **Open decisions:**
  - **Storage:** about 0.55 MB per symbol-session, so about 85 MB a day (about 21 GB a year) against 32 GB free with 800-day retention. A tiered retention (LEAPS 800 days, others shorter) needs a consumer analysis and approval because it deletes history.
  - **Retries:** 4xx responses other than 429 are still retried by the Unusual Whales client.
