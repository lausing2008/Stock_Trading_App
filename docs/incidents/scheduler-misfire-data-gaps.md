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

---

## AUD-T399-CRONRESTARTGAP (2026-09-17) — the misfire grace was necessary but not sufficient

**The grace fix above did not prevent a recurrence**, because it addressed the wrong half of the
problem. A 6h `misfire_grace_time` covers a job *delayed* past its slot by an outage. It does
nothing about a job whose slot was never scheduled in the first place: **a `CronTrigger`
recomputes its next fire time from process startup**, so a container recreated at 19:00 ET
simply waits until tomorrow's 18:45. No misfire ever occurs — there is nothing to forgive.

**What happened, and this one was self-inflicted.** Repeated market-data deploys during the
2026-09-16/17 session each reset the scheduler. Every restart pushed the capture slot forward,
so the job never ran on 09-15 or 09-16 despite the instance being healthy the whole time. The
archive fell to 2026-09-11 — **6 days stale, past the options income engine's own 5-day
staleness guard — so the engine returned ZERO candidates**. Recovered by running the capture by
hand (374,696 rows, 0 errors).

**The diagnostic that isolated it.** Job status said `"status": "ok"` with `last_run`
2026-09-14, while an unrelated job (`data_quality_checks`) had run hours earlier that same day —
proving the scheduler itself was alive. Every job in the 17:00-18:45 ET window was dead;
everything outside it was fine. That pattern means slot-skipping, not scheduler failure.

**Fix: a one-shot startup check**, mirroring `_avg_volume_startup_check`'s own remedy for the
identical shape in interval triggers (MD-RVOL2, documented in scheduler.py). `opthist_startup_check`
runs ~90s after boot, walks back to the last COMPLETED US trading day, and runs the capture only
if `MAX(as_of)` is behind it. A routine restart with a current archive costs one indexed lookup
— it will not re-run a 400-second capture or spend UW quota for nothing. Wrapped so a failure
can never take the scheduler or the service down.

Verified live on the very next deploy: the check fired at 06:04:03, found the archive current,
skipped the capture, and removed itself.

**The generalisable lesson.** Interval triggers and cron triggers fail differently but produce
the same symptom, and BOTH now have precedent in this codebase:

| Trigger | Failure on restart | Remedy |
|---|---|---|
| `IntervalTrigger` | countdown resets to the full period | MD-RVOL2 startup check |
| `CronTrigger` | next fire recomputed from boot; slot skipped silently | AUD-T399 startup check |

`misfire_grace_time` fixes neither — it only forgives lateness. **If a scheduled job produces
data something else depends on, it needs a startup check asking "am I behind?", not just a
generous grace window.**
