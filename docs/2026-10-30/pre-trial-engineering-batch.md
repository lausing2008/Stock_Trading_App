# Pre-trial engineering batch, 2026-10-30

Requested: finish everything open that does not wait on data.

**Why it had to ship before Mon Nov 2.**
- **One release per trial session.** The price-risk trial counts a session only under one
  release. A market-data deploy during Nov 2–6 would split those sessions and void them.
- **Nothing that moves RZY rebuilds.** Any change to scan timing or bar sourcing would confound
  the RZY trigger records that week is meant to collect. So nothing here changes RZY scans, bar
  ingestion, revision checks, thresholds or trading rules.

## Built

| Item | Change | Tests |
|---|---|---|
| Container logs survive deploys | `deploy_remote.sh` saves each outgoing container's log (gzipped, timestamped) to `~/deploy-evidence/container-logs/` after a successful build and before the recreate, under the host lock; newest 10 per service; never fails a deploy | 4 new; 3 sabotages caught, one of which found a real defect first: an empty listing under `pipefail` would have aborted the deploy |
| Pool holders on record | `pool_watch`: market-data logs `db.pool_pressure` with every connection holder (age, thread, our code's call site) once the pool is within 2 of its 15-connection limit, at most once a minute. Observation only | real-QueuePool probe, 12 checks; 2 sabotages caught |
| Paper-trading Yahoo calls | ATR and 5-day sector returns reused per symbol for 15 min; index-trend quote for 60 s. Failures not cached; the rewindable `as_of` path untouched | probe with counted downloads, 13 checks; 3 sabotages caught |
| Board poll `latest_prices?symbols=` | Reads the shared live-price cache under the same close rule as the alert scanners (`rows_from_live_cache`, now shared with `prices_from_live_cache`); Yahoo only for what the cache cannot answer | 4 new; 2 sabotages caught; the existing 27 pass |
| Average volume on non-trading days | When neither market trades, the cache's TTL is extended instead of downloading; a missing cache or Redis error still refreshes | 3 new; sabotage caught |
| Earnings sync scope (event-intelligence) | `sync_all_earnings` walks active, non-delisted stocks (199 of 223) | behavioural test over real `Stock` rows; sabotage caught |

## Measured, no code

- **Sparse-symbol ingestion failures (457c).** Oct 22's 7 are recovered. No interval either
  provider serves today is missing from the database. Detail in
  [intraday-bar-completeness.md](../2026-10-23/intraday-bar-completeness.md).
- **Oct 22 host stall.**
  - "No memory history" was wrong: sysstat held the day. Memory was starved before the onset:
    0.64–0.80 GB available, constant page reclaim, PSI memory up to 12%.
  - The collector stopped at 20:30, before the 20:37 onset.
  - Detail in [the incident](../incidents/2026-10-22-post-close-host-stall.md).

## Not done

- **1-minute sysstat sampling:** a host change, refused by the session's permission check. The
  command is in the incident file and needs approval.
- **UW fallback for fundamentals:** not built, on measurement.
  - Coverage is 197 of 199, and the 14-day last-good copy covers Yahoo outages.
  - It would silently mix UW's metric definitions into what signal-engine scores.
  - See the [yfinance audit](../audits/2026-10-30-yfinance-usage-audit.md).

## Waiting on data, deliberately

| Item | Waits for |
|---|---|
| US live quotes from UW (yfinance 1b) | a measured US session after the per-market change (Oct 30's logs were lost to a recreate) |
| UW bar request spacing | a second session of `uw_bar_minute_remaining` |
| RZY source policy, incremental scan loading | the Nov 2–6 rebuild-trigger records; changing either now would confound them |
| Pool exhaustion cause | its next occurrence, now logged with holders |
| Price-risk trial, fresh-mark receipts | Nov 9 and Nov 13 reads |
