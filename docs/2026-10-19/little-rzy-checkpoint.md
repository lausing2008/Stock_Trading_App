# Little RZY post-session checkpoint

**When:** Tuesday October 20, 2026, 13:30–14:00 America/Los_Angeles (16:30–17:00 Eastern; 20:30–21:00 UTC), after the first complete session following release `a959c5dc`.

**Calendar status:** importable [event](little-rzy-checkpoint.ics) prepared. Google Calendar is not connected in this session; no external calendar event has been created. Import once, or connect Google Calendar and create the event once using UID `stockai-rzy-post-session-20261020@lausing.com`. Do not do both without checking for a duplicate.

This is an evidence checkpoint, not a deployment or a replay/unlock authorization. Do not send a test message, reset checkpoints, rewrite event history, change consent, or relax the exact-bar comparison as part of this check.

1. Verify the October 20 digest for `lausing`, scheduled 09:10 ET / 06:10 PT. Read the outbox, scheduler logs and provider observations: distinguish enqueue, provider acceptance, actual inbox delivery, expiry, suppression and failure. Ask for receipt confirmation if delivery remains unobserved. A valid empty-candidate digest is not a scanner failure. Record candidate/exclusion counts and which timeframes contributed.
2. Count persisted streams and quarantines by timeframe. Baseline: 95/95 persisted 5m streams quarantined, 0/90 derived 15m, 0/90 derived 1h; 275 persisted streams total. Across all 342 scanned streams, 64 had missing candles and 3 had no completed bars. Distinguish new persisted streams from newly quarantined existing streams. A rise beyond 95 quarantined streams is a review trigger, not a reason to clear the flags.
3. For each new quarantine, retain original checkpoint and event IDs, observation time, reason and changed OHLCV fields. Separate material provider revisions, off-grid snapshot corrections, precision changes and source/config changes. Confirm queued alerts from quarantined streams are suppressed. Do not introduce a tolerance before measuring the actual changes.
4. GFS and GOOG 5m messages sent October 19 remain historical watches: their streams are quarantined and their original geometry has not been revalidated. Do not use them as verified trading outcomes. Read their preserved records alongside the source revisions if investigating them.
5. Recheck data readiness: verified provider timestamps/delay and borrow evidence remain unavailable; current streams are research-only, so no paper fills are possible. Keep §11 real-data backtest plus five comparisons, prospective results and chronological out-of-sample results open. Synthetic fixture results close none of those gates.
6. Save a dated read-only evidence snapshot and update the deployment record. If derived streams also quarantine, scope a separate canonical-data and audited-replay design; preserve old evidence and make any future replay version explicit.

Read-only PostgreSQL baseline query:

```sql
SELECT timeframe, count(*) AS persisted,
       count(*) FILTER (
         WHERE checkpoint->'requires_audited_replay' IS NOT NULL
       ) AS quarantined
FROM little_rzy_streams
GROUP BY timeframe
ORDER BY timeframe;
```

Sources: [corrected deployment record](little-rzy-deployment.md), [audit evidence](little-rzy-audit-followup.json), and [original release evidence](little-rzy-production-evidence.json). Provider acceptance is not proof of inbox delivery.
