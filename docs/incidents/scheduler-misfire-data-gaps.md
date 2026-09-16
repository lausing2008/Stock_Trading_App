# Scheduler Misfire — a 60-second grace turning a brief outage into a multi-day data gap

## AUD-T398-MISFIREGAP (2026-09-16)

**Symptom.** Every symbol's newest row in `option_chain_history` was `2026-09-11` while the
date was `2026-09-16` — a 5-day archive gap. Every Options Income candidate was being priced
off Friday's premiums against Tuesday's live prices, and the engine's own staleness guard
(`> 5 days`) was sitting right on the edge of tripping.

**Root cause — not a fetch failure, a discarded job.** The capture job's own Redis status said
`"status": "ok"` with no error; its last run was `2026-09-14T22:46 UTC`. It simply never ran on
09-15. `start_scheduler()`'s `_JOB_DEFAULTS` sets `misfire_grace_time=60`, and the instance was
unreachable at 18:45 ET on 2026-09-15 (the same reachability incident the user rebooted out of).
APScheduler treats a job missed by more than its grace window as **discarded, never retried** —
so a few minutes of downtime cost the entire day's capture, and the following day's run only
captures "yesterday", leaving the hole.

**Why this is worse than an ordinary missed job.** UW's option-chain history is a ROLLING
window, not an archive (measured 2026-09-14: the boundary had moved ~12 days forward since
T374 recorded it). A day not captured eventually becomes **permanently uncapturable**. For this
class of job, running LATE is strictly better than not running.

**Fix.** A long misfire grace on the idempotent data-capture jobs specifically:

| Job | Grace | Reasoning |
|---|---|---|
| `option_chain_history_daily` | **6h** | idempotent capture; a skipped day is unrecoverable |
| `options_income_step` | **4h** | 19:00 ET is already after the close, so a late run prices against the same settled closes; missing it entirely leaves expired positions unsettled and their collateral locked. 4h keeps it inside the same evening. |

**Deliberately NOT applied to `_JOB_DEFAULTS` globally.** For the ~40 alert/email jobs a late
fire is actively *wrong* — a "morning digest" arriving mid-afternoon is worse than no digest.
The long grace belongs only on idempotent data capture.

**Note the existing self-healing that was not enough.** The capture already backfills a 5-day
trailing window (`_OPTHIST_BACKFILL_WINDOW_DAYS = 5`) precisely so a missed run self-heals on
the next one. That mechanism works — but only if the job *runs*. A discarded job heals nothing,
and the 5-day backfill window means a gap longer than 5 days becomes permanent.

## Diagnosing this class

The tell is a job reporting `ok` while its data is stale — status reflects the last time it
*ran*, not whether it ran *on schedule*.

```bash
# What the job itself thinks (note: last_run, not "did it run today")
docker exec stockai-redis-1 redis-cli GET 'scheduler:job:option_chain_history_daily'

# What the data actually says — compare against today
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT symbol, MAX(as_of) FROM option_chain_history GROUP BY symbol ORDER BY 2 LIMIT 5;"
```

If `last_run` is older than the cron cadence, the job was discarded rather than failed — look
for an outage in that window, not for an exception in the logs (there won't be one).
