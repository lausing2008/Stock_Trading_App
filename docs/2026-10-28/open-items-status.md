# Open items from the Oct 21–23 options / snapshot / 457 work: status on 2026-10-28

Continues [the Oct 22 session index](../2026-10-22/session-index.md). Every status below comes from production queries run 2026-10-28 13:40–14:10 UTC. None of the checks wrote to production. The final-candle comparison made about 60 provider requests: 20 to Unusual Whales and about 40 to yfinance, every one of which came back rate-limited.

## Summary

| Item | Tracker | Status | Basis |
|---|---|---|---|
| Duplicate-print check (Phase B) | 462 | **Passed** | 850 dark-pool prints after the deploy (to 2026-10-23 13:45:54 UTC): 0 exact duplicates, 0 hour-shifted twins |
| 457a final 5m candle | 457 | **Accepted** | Final candle present and written after completion; 20/20 sampled US candles equal the provider's completed candle |
| 457b off-grid quote rows | 457 | **Accepted** | Off-grid rows received and rejected in every session since Oct 26; 0 persisted since Oct 23 |
| 457c sparse intervals | 457 | Open | Unchanged; thin names still partial in reconciliation |
| Snapshot job (fix A) | 465 | **Scheduled runs verified** | Runs on Oct 23, 26 and 27 each wrote 37 rows, every one from the expected session |
| Nightly full-list chain capture (fix B) | 466 | **Scheduled runs verified** | Sessions Oct 23 and 26 archived for 158/158 symbols by the 18:45 ET run |
| Option-chain storage retention | 466 | **Decision needed** | Recommendation below |
| RZY readiness, 458 latency, host-stall cause | 457/458, 460 | Open | Not changed by this check |

## 457a: final candle

Acceptance: the final candle is present, written after it completed, and its OHLCV equals the provider's completed candle.

| Session | Market | Final candle present | Written after completion |
|---|---|---|---|
| Fri Oct 23 | US | 156/156 | 136 tracked. The tracking table was deployed Oct 25, so lags of 2.5 days are later re-observations, not timing evidence |
| Mon Oct 26 | US | 155/156 (AMZA missing) | 155/155, first write 2 min 18 s after the close (the 16:02 ET pass) |
| Tue Oct 27 | US | 156/156 | 156/156, first write 2 min 2 s after the close |
| Oct 23, 26, 27, 28 | HK | 41/41, 41/42 (1671.HK missing), 41/41, 41/41 | Oct 27 and 28: all 41 symbols written by the 16:12 HKT pass (08:12 UTC) |

**Finalized OHLCV:** 20 randomly ordered US symbols, Oct 27 final candle. Each was fetched again from Unusual Whales on Oct 28, and **all 20 equal the stored candle** on open, high, low, close and volume (GLW, QBTS, FRNM, DELL, MNDY, VST, DDOG, AXON, AI, APP, XSD, SOFI, ANF, PLTR, XPEV, GFS, GEV, QQQM, SOXX, BEPC).

**Not compared:**
- yfinance was rate-limiting this host at the time (see "New observations").
- HK finalization therefore rests on presence plus the 16:12 HKT post-close write, not on a fresh provider comparison.

**What "partial" means:** the reconcile job reports `partial` because of thin names, not liquid ones.
- US Oct 27: final candle missing only for BRK-A, AMADY and AMZA; 13 symbols with internal gaps.
- HK Oct 28: 4 symbols with internal gaps; no missing or forming final candle.

Those gaps belong to 457c.

## 457b: off-grid quote rows

Per-session counts from `bar_ingest_observations` (5m) and persisted off-grid rows in `prices`:

| Session | Market | Received | Off-grid rejected | Written | Off-grid persisted |
|---|---|---|---|---|---|
| Oct 26 | US | 679,611 | 962 | 607,304 | 0 |
| Oct 27 | US | 929,494 | 893 | 838,893 | 0 |
| Oct 27 | HK | 110,911 | 18 | 110,893 | 0 |
| Oct 28 (to 14:05 UTC) | US / HK | 11,042 / 85,156 | 33 / 15 | 11,009 / 85,141 | 0 |

The filter received off-grid rows in each session, so the zero persisted count is meaningful. Persisted is 0 for every 5m/15m session since 2026-10-23.

The Oct 23 counts (0 off-grid rejected) predate the per-session counters. Those counters landed on Oct 25 and do not reconstruct earlier sessions.

## Snapshot job (fix A)

| Run date | Expected session | Rows | Source session in evidence |
|---|---|---|---|
| Fri Oct 23 | Thu Oct 22 | 37 | 2026-10-22 |
| Mon Oct 26 | Fri Oct 23 | 37 | 2026-10-23 |
| Tue Oct 27 | Mon Oct 26 | 37 | 2026-10-26 |

These replace the 3 rows a day, all from Oct 15, that were written before the fix.

Oct 27 detail: 37 written, 0 stale. Two symbols were unavailable:
- BRK-A: `provider_excluded`, which is structural.
- AMZA: `no_volume`, which counts as non-structural, so the run status is `partial`.

The status is therefore `partial` on a day when nothing went wrong. A possible refinement: treat `no_volume` as a measured outcome rather than a failure. This needs review and approval; nothing was changed.

## Nightly full-list chain capture (fix B)

`option_chain_coverage`, complete / total symbol-sessions:

| Session | Complete | Total | Captured |
|---|---|---|---|
| Oct 20–22 | 158 | 201 | Backfill and nightly runs. The 43 extra totals are HK/excluded markers from before fix B |
| Fri Oct 23 | 158 | 158 | Oct 25–26 |
| Mon Oct 26 | 158 | 158 | 2026-10-27 22:46 UTC, which is 18:46 ET, the scheduled run |
| Tue Oct 27 | 40 | 40 | Startup catch-up after the 06:16 UTC restart (LEAPS only, as designed). The full list is due tonight at 18:45 ET |

**Observability gap:** Redis `scheduler:job:option_chain_history_daily` holds whichever run finished last. Today it shows the 06:26 UTC startup catch-up, not the nightly run, so check coverage rows rather than that key.

## New observations (not fixed)

1. **Weekend sessions are "captured".** Sat/Sun dates (Oct 3–4, 10–11, 17–18, 24–25) have `complete` coverage rows with 0 rows returned.
   - Cost: about 316 Unusual Whales calls a week.
   - A weekend marker looks like a completed session in any count that doesn't filter by calendar.
   - Fix: iterate exchange sessions, not calendar days. Small change; needs approval.
2. **yfinance is rate-limiting this host since 09:34 ET today.** About 4,400 rate-limit log lines from 13:34 to 14:06 UTC.
   - Mostly fundamentals fetches (about 800 each of `fundamentals.fetch_failed`, `growth_vs_index_fetch_failed` and `earnings_consensus_fetch_failed`).
   - Also yfinance-only ingest failures (`SKHYV`, `US10Y`).
   - US 5m bars were not affected: 152–155 symbols per slot from Unusual Whales.
   - Not investigated further. The bug class is in `docs/incidents/yfinance-rate-limit-amplification.md`.

## Option-chain storage retention: recommendation

### Measured (2026-10-28)

**Table size**
- `option_chain_history` holds **16 GB** (8.7 GB heap plus indexes): 53.08M rows from 2024-08-16. That is about 320 bytes per row including indexes.
- 800-day retention is already applied weekly (`_OPTHIST_RETENTION_DAYS`, last run Oct 25, ok).

**Split by symbol group**

| Group | Rows | Share of rows | Covers | Rows per session now |
|---|---|---|---|---|
| LEAPS list (35 symbols) | 49.1M | 93% | Back to 2024 | ≈135k |
| All other symbols (117) | 3.9M | 7% | Since 2026-09-10 | ≈150k (full list since Oct 23) |

**Growth and disk**
- Growth is about 285k rows per session, roughly 90 MB, which is about **23 GB a year**.
- Disk has **31 GB free** (100 GB volume).
- Seven nightly dumps take 17 GB (2.5 GB each, mostly this table), and each dump grows with the table.
- A frontend build needs about 25 GB free, so the real headroom is about 6 GB.

### Who reads it

From a code survey on 2026-10-28:

| Reader | Symbols | Depth |
|---|---|---|
| LEAPS backtest (`leaps_backtest.py`) | LEAPS list | Full archive |
| Long-option playbook | Alert symbol | `as_of <= fired_date`, newest 2,000 rows, 14–60 DTE |
| IV/HV options performance (`_t411_ivhv.py`) | One symbol | ≤40 sessions |
| Options income engine | 13 symbols, all on the LEAPS list | Latest session, ≤5 days; marks ≤5 days |
| Chain UI / flow route / snapshot job | One symbol | Latest session (on-demand fetch if >7 days old) |
| Admin contract check | One contract | Newest row |

Only the LEAPS backtest needs deep history, and it reads LEAPS symbols only.

### Recommendation: tiered retention

- **LEAPS list (`_OPTHIST_SYMBOLS`):** keep 800 days, unchanged. The backtest needs it, and Unusual Whales keeps only a rolling ~2-year window, so it cannot be recaptured later.
- **All other symbols:** keep 90 days, about 62 sessions. That covers every non-LEAPS reader with margin.

Effect:
- At 800 days, non-LEAPS history would grow to about 83M rows (≈26 GB). At 90 days it levels off at about 9M rows (≈3 GB).
- Nothing is deleted now. The first deletion would fall around 2026-12-09, 90 days after the oldest non-LEAPS row.
- PostgreSQL reuses the space freed by deletes. No `VACUUM FULL` or downtime is needed, though the file will not shrink below its peak.

Risks and limits:
- Any future study that wants raw non-LEAPS chains older than 90 days would have to recapture them while they are still inside the provider's window, at about one call per symbol-session.
- The options-flow outcome studies read other tables (`options_flow_snapshots`, alert tables), not this one.
- **Even with tiering, the LEAPS share keeps growing** (≈135k rows per session now against an historical average of ≈89k). It levels off around 74M rows (≈24 GB) once today's chain sizes fill the 800-day window, about early 2029.

Alternative if raw history matters more than disk: keep 800 days for everything and grow the EBS volume. gp3 storage costs about $0.08 per GB-month, so +100 GB is about $8 a month. The cost is bigger nightly dumps and slower restores; the Oct 23 restore took 628 s at today's size.

Implementation is a small change to `_purge_option_chain_history`: two deletes, one per tier, with tests. It **needs your approval because it deletes history**, and nothing has been changed.
