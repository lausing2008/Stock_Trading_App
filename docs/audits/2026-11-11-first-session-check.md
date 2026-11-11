# First trading session on the options, squeeze and dark-pool release (November 11, 2026)

**Read-only check** of `f0d941ab`/`78485a75` after the November 11 US session, run at about 17:30 ET from the market-data logs and database. Fixes for what it found are committed but **not deployed** (see the end).

## What worked

| Area | Evidence |
|---|---|
| Dark-pool session gate (D11) | The last dark-pool alert row was at 19:41 UTC (14:41 ET). On Nov 5–10, rows kept arriving until about 22:30 UTC, after the close |
| Dark-pool baseline column (D10) | All 51 of today's alert rows have a stored baseline, none of them floor-only |
| Followed symbols scanned (D2) | `added: 40, wanted: 101` every run. Today 51 dark-pool candidates, against 31–36 a day before, from the wider scan |
| Options flow (own symbols) | 11 candidates today, against 1–3 a day before, from the wider scan |
| Daily desk record (Part B) | 1 row, for the one game-plan request served today |
| Repeated-ID classification | 553 `multi_row_same_id` groups recorded |
| IV rank 70/30 | Live constants read 70/30; a rank of 65 reads "normal" |

## What did not: two defects, neither in the released logic

### 1. The gamma-unwind scan measured 0 symbols all day (an existing race, exposed by the restart)

- **Symptom:** 0 gamma rows today, against 7–17 a day before. All three runs (14:05, 18:05 and 22:05 UTC) finished in about 0.3 s, where the Nov 10 18:20 run took 42 s and found 5.
- **Cause:** `ingest_universe()` deleted `stockai:live_prices` at the end of every ingest. The cache is rebuilt by the every-minute refresh.
  1. At 18:05:28 the cache was refreshed.
  2. At 18:05:37 the 5-minute ingest finished and deleted it.
  3. At 18:05:53 the gamma scan found no live price for any symbol and skipped all of them silently.
  4. Around 18:06:08 the cache was rebuilt.
- **Why today:** the 06:05 UTC restart moved the 4-hourly scan to :05:53, 16 s after each ingest ends. Before, it ran at :20:20, clear of the gap.
- **Wider effect:** every reader of the cache was blind for up to a minute after each 5-minute ingest. That includes price alerts and the short-squeeze and ignition scans, so about one cycle in five was skipped. This dates from April and was not introduced by this release.
- **Not the code:** a dry run of the scan's own steps with prices supplied measured 27 of 39 symbols and found 8 candidates. The new missing-open-interest rule skipped none.
- **Not Yahoo:** Yahoo "currently down" lines run at about 600 an hour every day, including Nov 9 and 10 when gamma worked. They come from the news fetch.
- **Fix (AUD-LIVECACHE-BUST):** the delete is removed. The cache still refreshes every minute and still expires after 90 s.

### 2. The dark-pool job overran its one-minute slot (this release)

- **Symptom:** 76 runs were skipped because the previous run was still going, against 5 on Nov 10. Median run 39 s, 90th percentile 87 s, maximum 137 s.
- **Cause:** the job re-wrote every symbol's prints (up to 50) to the identity ledger and the legacy table every minute, although the provider answer is cached for 15 minutes. That was 14 redundant writes in every 15. The 40 added symbols doubled it.
- **Fix (AUD-DP-JOB-OVERRUN):** prints are written only for a fresh provider answer. The fetch already records fresh answers in the ledger.

## Also fixed, committed earlier and not yet deployed

- **`BRK-A`:** Unusual Whales spells share classes with a dot. The dark-pool request for `BRK-A` failed about once a minute and now goes as `BRK.A` (AUD-UW-SHARECLASS, `0ed9f2b2`).
- **Call Finder card:** a note under the near-term pick saying what the [replay](2026-11-11-call-finder-replay.md) found: lower leverage, not better selection, with a spread edge on 7-day holds.

## Not checked today

- Short-squeeze alerts: 1 candidate all day, against 8–10. Part of that was the cache gap; the rest is the market or the new stale-row check. Re-read after the fix is live.
- Whether recipients received only their own symbols. The per-recipient logs record counts, not symbols, so this needs a check of the outbox or the emails themselves.

## Deployment needed

Three market-data files: `ingestion.py`, `scheduler.py` and `unusual_whales.py`. None of them is touched by the two held commits (`58d77de4`, `3369e1a5`), so the same lock-held file-level deploy applies. Plus the frontend rebuild.
