# Options-flow snapshot job: why only three Oct 15 rows remained (traced 2026-10-22)

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

## Fix B (next, separate)

- Capture the active US universe nightly instead of on demand (removes the sawtooth).
- Stop retrying symbols the provider cannot serve: 42 HK symbols and BRK-A are recorded `incomplete / request_failed` on every attempt.
- Backfill Oct 16–21. UW serves about 2 years of history, so it is recoverable. Schedule it away from the 02:00 UTC backup at idle I/O priority, given the unexplained Oct 22 stall.
