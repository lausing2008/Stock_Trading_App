# Pool exhaustion, memory and CPU, and a platform review (2026-11-04/05)

This document covers:

- why market-data's connection pool ran out late in the 2026-11-04 US session;
- where the host's memory and CPU go;
- a 24-hour production sweep across every container;
- an independent review of the training change made in response.

All numbers are measured on the production host unless they are marked as an estimate.

## 1. The 2026-11-04 pool exhaustion

The full chain is in `docs/incidents/db-connection-pool-exhaustion.md` (AUD-SIG-REFRESHOVERLAP).
In short, the host had no memory headroom, market-data slowed, and signal-engine's 5-minute
refresh requests each started another full pass. About a dozen passes then ran at once, and
thread starvation left used connections idle in transaction until PostgreSQL killed them.

### Refresh timings

| | Measured |
|---|---|
| market-data US cycle (ingest → rankings → signals → alerts), 162 symbols, 2026-11-03 | 74 s at the open, ~150 s mid-morning, 200–264 s in the last two hours |
| market-data US post-close | 266 s |
| market-data HK cycle, 42 symbols | 75–90 s (a few at ~205 s) |
| HK post-close, 2026-11-04 | 149 s |
| signal-engine HK pass, 42 symbols, watched live | 2 min 10 s (3.3 s per symbol, serial) |
| signal-engine US pass, ~160 symbols | **~9 min (estimate from the HK rate)**, against a 5-minute request cadence |

## 2. Memory and CPU

| Container | Memory | Where |
|---|---|---|
| market-data | 2.58 GB after 17 h | 1.27 GB glibc per-thread arenas (64 MB each), 1.14 GB other large blocks |
| ml-prediction | 2.57 GB | 2.15 GB in RAM, 0.42 GB in swap; the in-process training peak, kept all day |
| signal-engine | 1.87 GB | 1.05 GB in swap, untouched |

- **The host:** 7.6 GB of RAM plus 2 GB of swap, and the swap was full. About 160 MB was free
  with the market closed.
- **CPU per day (lifetime averages):** market-data ~375 CPU-minutes, ml-prediction ~350,
  PostgreSQL ~130, signal-engine ~77. During market hours the two cores run about 90% busy.
- **After the training change (2026-11-05 04:36 UTC):** ml-prediction restarted at 606 MB, and
  the host had 1.9 GB available.

## 3. Findings and what was done

| ID | Finding | Status |
|---|---|---|
| AUD-SIG-REFRESHOVERLAP | Refresh passes stacked; no guard | Fixed: one pass per market, `pass_done` log with duration |
| AUD-ML-TRAINCHILD | Training ran in the API process and kept its peak. Both post-closes retrained every market's models. | Fixed: child process, per-market training, `tune_all` in the child too |
| (review of 62c0d240) | One stuck training job would block nightly training for both markets indefinitely. An all-failed batch exited 0. | Fixed: 4 h run limit (8 h for tuning), 3 h lock-wait limit, exit 1 when every job fails, `exc_info` per job |
| AUD-DIGEST-CLOSEDSESSION | The morning digest used its closed session, holding a connection ~15 min twice a day | Fixed; repo-wide scan test |
| AUD-HK5M-DELAYED | Yahoo's HK 5-minute bars arrive 12–15 min after completion, so the 12:02/12:07 lunch reconcile reported every symbol missing its 11:55 bar | Fixed timing (12:20/12:25, 16:22/16:27 HKT). The delay itself is documented in `docs/features/data-provider-routing-and-schedule.md`; removing it needs a real-time feed |
| AUD-MD-ARENAS | 1.27 GB of market-data in malloc arenas | `MALLOC_ARENA_MAX=2`; effect to be measured |
| (log retention) | market-data's 100 MB log cap held under a day; the Nov 4 US session was gone by the next morning | 500 MB cap for market-data |
| AUD-RANK-SECTORLOGSPAM | 231,657 identical `sector_label_unmapped` warnings in a day, for the "Unknown" placeholder (99.9% of ranking-engine's log volume) | Fixed: the placeholder is silent; a real unmapped label logs once |

### Not done

- **Shorter connection wait (30 s → 5 s).** The pool is set in `shared/db/session.py`, so the
  change means rebuilding all 12 backends, the heavy-I/O path that took the host down on
  2026-09-10. It is deferred, because the refresh guard removes the amplification that exhausted
  the pool.
- **Sectors for real companies with "Unknown".** These are AAOI, HOOD, TSEM, BEP, BEPC, CBRS and
  USAR; the other 27 of the 34 are ETFs. Their sector comparison falls back to SPY. This needs a
  sourced value, not a guess.
- **AUD-EARNINGS-EPSMATCH:** 106 `earnings.history_row_failed` on 2026-11-04 (open since
  2026-11-03).
- **Swap.** More swap would hide the overcommit rather than fix it; revisit after the memory
  changes have been measured.
- **ML 404s:** about 35% of HK `predict_ensemble_three` calls return 404 (no model for that
  symbol and style). The rate was the same before and after the 2026-11-05 deploy. To be read
  after per-market training has run.

## 4. Checked and fine (2026-11-05)

- **TLS certificate:** valid until 2027-02-01.
- **Database backups:** daily, present through 2026-11-05.
- **Disk:** 71% used.
- **Errors:** outside the 21:00 UTC incident hour, market-data logged one error
  (event-intelligence 8-K sync timeout). api-gateway, portfolio-optimizer, strategy-engine,
  technical-analysis, research-engine, news-intelligence, redis and postgres logged none.
- **signal-engine:** all four tracebacks were in the incident window.

## 5. What to measure next

- **The training child's memory peak against the container's 3 GiB limit.** ml-prediction now
  runs two interpreters, the API and the child. `~/evidence/memwatch/` samples every minute, and
  the first full run is the 2026-11-05 08:30 UTC HK post-close.
- **`signals.refresh.pass_done` durations** for a real US pass, and how many requests are
  skipped.
- **market-data's memory after a day** on `MALLOC_ARENA_MAX=2` (2.58 GB before).
- **Pool errors** in the 2026-11-05 US session.
