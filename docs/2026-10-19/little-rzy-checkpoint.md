# Little RZY post-session checkpoint

**When:** Tuesday October 20, 2026, 13:30–14:00 America/Los_Angeles (16:30–17:00 Eastern; 20:30–21:00 UTC), the first session after the deep-review release `afcba909` (backend) / `b79ec9cf` (5m chart defaults).

**Calendar:** created in the connected Google Calendar (`stockai2028@gmail.com`); do not also import the `.ics`.

**Superseded baseline.** The original checklist measured growth of exact-match quarantines. The deep review ([little-rzy-deep-review.md](little-rzy-deep-review.md)) replaced quarantine with deterministic rebuilds, so this check now verifies that the rebuild path behaved. Read-only; do not send a test message or edit checkpoints.

1. **Pre-open maintenance (08:40–09:09 ET).** Expect 95 `little_rzy_stream_generations` rows with reason `legacy_quarantine` and zero checkpoints still holding `requires_audited_replay`. Scheduler logs show `scan.status=maintenance` runs and no email from them.
2. **Digest (09:10 ET / 06:10 PT).** Distinguish enqueue, provider acceptance, inbox delivery, expiry and suppression. Dry-run expectation was 24 candidates across 5m/15m/1h; record the actual count, exclusion reasons and which timeframes contributed. Ask for receipt confirmation if delivery is unobserved.
3. **Rebuild rate during the session.** Count new generation rows by reason (`stored_completed_bar_changed`, `checkpoint_bar_missing_from_source`, `gap_after_checkpoint`, `checkpoint_contains_excluded_off_grid_bar`) and by timeframe. Frequent mid-session rebuilds of the same streams mean bars are restated shortly after the 2-minute settle; measure the restatement delay before changing anything.
4. **Statuses.** Expect ~333/342 `research_only`; note `awaiting_backfill`, `stale`, short segments (`segment_bars` < 60).
5. **Mail.** Pattern emails only for fresh new events; no ENTRY_CANCELLED mail caused solely by standing blockers; any mail queued before a rebuild suppressed at send.
6. **Checkpoint size.** 5m checkpoints should trend to ≈ 1,200 bars (well under the previous 1.9 MB average) once each stream processes new bars.
7. **Chart.** On a stock page at 5m, Little RZY, SMA 5/10 and Bollinger Bands are on by default and the RZY overlay draws (status `research_only`, grid matches).
8. GFS and GOOG 5m messages of October 19 remain historical watches from superseded state; not verified outcomes.

Read-only PostgreSQL queries:

```sql
SELECT timeframe, count(*) AS streams,
       count(*) FILTER (WHERE checkpoint->'requires_audited_replay' IS NOT NULL) AS legacy_locked,
       pg_size_pretty(avg(octet_length(checkpoint::text))::bigint) AS avg_checkpoint
FROM little_rzy_streams GROUP BY timeframe ORDER BY timeframe;

SELECT reason, split_part(stream_key, ':', 2) AS timeframe, count(*)
FROM little_rzy_stream_generations GROUP BY 1, 2 ORDER BY 3 DESC;
```

Sources: [deep review](little-rzy-deep-review.md), [deployment record](little-rzy-deployment.md).
