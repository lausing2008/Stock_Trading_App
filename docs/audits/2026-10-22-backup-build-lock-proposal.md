# Proposal: one host lock for database backups and image builds

**Release update:** this reviewed implementation is committed and deployed in `a1054dcb`. The [follow-up deployment report](2026-10-23-followup-deployment.md) records production verification and the separate first-run backup status. Earlier local-only wording below describes the audit/preparation stage and is preserved as history.

Status: **implemented locally, for review; not committed or deployed.** Cron timing and journal settings are out of scope and unchanged. See "Implementation" below for how the build differs from the design.

## What happened

At 02:00 UTC on October 22, the nightly `pg_backup.sh` cron job and a frontend image build ran at the same time. At 02:05:59 the build failed with `no space left on device`, and the backup's gzip stream failed the same way. `stockai-2026-10-22.sql.gz` was left truncated (1.97 GB; `gzip -t`: unexpected end of file). The previous good backup was October 21. Postgres, Redis and the application services logged no disk-full errors in that window. A replacement backup was run the same day under the host deploy lock (13:24:45–13:36:21 UTC): exit 0, `gzip -t` passed, 2.45 GB, ending with `pg_dump`'s completion marker. October 21's file was unchanged (SHA-256 rechecked); rotation removed October 15.

Moving the cron time would not prevent a recurrence. Builds happen whenever a change ships, at any hour, and nothing currently stops a build from starting while a dump is running, or the reverse.

## Gaps in the current tooling

1. **The backup takes no lock.** `pg_backup.sh` neither checks nor holds `scripts/deploy_lock.sh`.
2. **Not every build path takes the lock.** `deploy_frontend.sh` and `rebuild_backend_images.sh` acquire it. `scripts/deploy.sh`, which runs on a workstation and builds over SSH, does not. Neither does a hand-typed `docker build`.
3. **No disk check before dumping.** A dump needs about 2.5 GB, and a frontend build needs several GB, including the full build context (`.git` is not excluded by `.dockerignore`; a fix is prepared locally).
4. **A failed dump keeps its final name.** The script writes directly to `stockai-<date>.sql.gz`. Rotation runs only after a successful dump, but it keeps the seven newest files by modification time, truncated ones included. After several failed nights, truncated files would push out good backups.
5. **Failures are not recorded in `backup.log`.** With `set -e`, the script exits before its success line. A failure appears only as gzip's stderr line in the log, and no status records it.

## Proposed design

### A. One host lock for disk-heavy work

Use the existing `flock` lock (`/tmp/stockai-deploy.lock`) as the single owner of disk-heavy work on the host: image builds, container recreates, and database dumps. Keep the current file and status format, so `deploy_lock_status` reports a backup the same way it reports a deploy (`what=backup-YYYY-MM-DD`).

| Caller | Behaviour when the lock is held |
|---|---|
| `deploy_frontend.sh`, `rebuild_backend_images.sh` | unchanged: refuse immediately (exit 75) |
| `scripts/deploy.sh` | **new:** take the lock on the EC2 side for the whole run. The build-and-recreate sequence runs as one remote command under `flock`, not as separate SSH calls, so the lock covers build *and* recreate. Refuse (exit 75) when held |
| `pg_backup.sh` | **new:** *wait*, bounded (`flock -w 3600`), rather than refuse. A late backup is better than a skipped one. If the wait times out, fail loudly (see D) |

Deploys refuse and backups wait. A deploy that starts while a backup holds the lock gets an immediate, named refusal. A backup that starts during a deploy waits for it to finish, with its own time limit.

Hand-typed `docker build` commands remain outside any lock, as they are today. The CLAUDE.md deployment section already says to use the scripts; this proposal adds no new guard for typed commands.

### B. Disk preflight

- **Backup:** before dumping, require free space of at least 2× the newest good backup's size, about 5 GB today. Otherwise fail without dumping.
- **Deploy:** `deploy.sh` already reports free space and prunes when low. Because it will hold the lock (A), a backup cannot start mid-build.

### C. Dump to a partial file, verify, then publish

1. Dump to `stockai-<date>.sql.gz.partial`.
2. Check the pipeline's exit status (`set -o pipefail`), then run `gzip -t`.
3. Only on success, rename it atomically with `mv` to `stockai-<date>.sql.gz`.
4. Rotate only after publishing, and count only published files.

A failed run leaves at most one `.partial` file, which never counts toward retention, and the next run deletes it.

### D. Record every outcome

Write one line per run to `backup.log`, `completed` or `failed` with the reason (lock timeout, low disk, dump error, gzip -t failure), and set the lock status file's exit code. Sending a failure alert through the existing alert email path is a possible follow-up, not part of this proposal.

## Verification before rollout

- A backup started while a fake deploy holds the lock waits, then runs. A deploy started during a backup refuses with exit 75 and the backup's name.
- A simulated full disk (a small loop device or tmpfs) leaves only a `.partial` file, no truncated file under a final name, and the seven good backups untouched.
- Rotation with a stray `.partial` present still keeps seven verified files.
- `deploy.sh signal-engine` holds the lock across build and recreate, as shown by `deploy_lock_status` mid-run.

## Out of scope here

- Moving the 02:00 cron time.
- Journal size limits (`/var/log/journal`, 4.0 GB).
- The image cleanup itself, handled separately against an explicit removal list.

## Implementation (local, for review)

| File | Change |
|---|---|
| `scripts/deploy_lock.sh` | Adds `deploy_lock_acquire_wait WHAT SECS`. The holder exports `DEPLOY_LOCK_TOKEN` and records it in the lock file; a child presenting that token while the lock is held proceeds as a nested holder with no EXIT trap, so only the outermost holder records the outcome |
| `scripts/pg_backup.sh` (new; replaces the unversioned `/home/ec2-user/pg_backup.sh`) | Waits for the lock (default 3600 s; exit 75 on timeout). Deletes leftover `.partial` files. Requires free space ≥ max(2 × newest backup, 5 GB). Dumps to `.partial`, checks both pipeline statuses, then in one decompression pass checks the gzip stream and the completion marker. Renames, then rotates over dated, published files only. One `complete`/`FAILED: <reason>` line per run |
| `scripts/deploy_remote.sh` (new) | The EC2 half of `deploy.sh` (low-disk prune, frontend free-space refusal, build, recreate, verify, prune), moved unchanged. It runs as one process under the lock, taken as a nested holder when called by `deploy.sh` |
| `scripts/deploy.sh` | Local preflight and the frontend test gate are unchanged. The remote work is now one SSH command that takes the lock, runs `git pull` and calls `deploy_remote.sh`. Arguments are validated as service names before interpolation. A held lock produces "REFUSED" and a non-zero exit |
| `scripts/download_weekly_backup.sh` | `rsync --exclude '*.partial'` |
| `.dockerignore` | `.git` |
| `Makefile` | `make test` also runs `scripts/tests` (CI runs `make test`) |

**Behaviour changes to note:**
- A failed build now stops the deploy before the recreate. Previously `ssh "… docker build … | tail -3"` reported `tail`'s status and recreated the service from its previous image. (The lock library's own `set -o pipefail` also applies.)
- October 23 review follow-up: verification now uses exact structured running/health state, returns failure through cleanup, and records a failed lock status. The retained false-success discrepancy was reproduced and fixed; see `2026-10-23-pending-working-tree-review.md`.
- The lock descriptor is inherited by child processes. If a deploy script dies while its `docker build` keeps running, the lock stays held until the build exits. This is intentional: the disk work has not stopped.

**Tests:** `scripts/tests/test_host_lock.py`, 38 tests after the October 23 verification follow-up, run against the real scripts with stand-ins for `docker`, `ssh`, `git`, `df` and `top`. They cover refusal, waiting, timeout, nesting without deadlock, foreign or stale tokens, release on kill, inheritance, a verified publish, waiting behind a deploy, lock timeout, dump and marker failures (no `.partial`, same-day good file kept), insufficient space (including the 2× boundary and the floor), rotation after a failed dump, a deploy refused during a backup, the lock held from `git pull` through build, recreate and prune, a backup waiting behind a deploy, a failed build stopping before the recreate, and argument validation.

Sabotage checks, each re-run against the full file:
- **Caught (suite goes red):** removing nesting; dumping straight to the final name; the backup taking no lock; no space check; no marker check; `deploy.sh` taking no outer lock; `deploy_remote.sh` taking no lock; and disabling `pipefail` after the lock library is sourced.
- **Not caught:** dropping the published-file filter from rotation. That filter is a second layer; a `.partial` cannot be present when rotation runs, because a failed run deletes its own and every run deletes leftovers while holding the lock.

## Rollout (not done; requires approval)

1. Commit and push. On EC2, run `git pull origin prod` **before** the first `deploy.sh` run. The outer lock in `deploy.sh` sources the `deploy_lock.sh` already on the host, and an old copy without token support makes the nested acquire refuse with exit 75. That fails safe, but the deploy does not run.
2. Point the existing cron entry's script at the versioned one, without changing the schedule: replace the body of `/home/ec2-user/pg_backup.sh` with `exec bash /home/ec2-user/Stock_Trading_App/scripts/pg_backup.sh`. Keep the old body as `/home/ec2-user/pg_backup.sh.pre-lock`.
3. Run one backup by hand and check exit 0, `gzip -t`, the marker and the log line, as on October 22.


Rollout result: wrapper switched with original saved as `.pre-lock`, unchanged cron schedule; first real run completed October 24 01:24:10 UTC, exit 0, verified and published 2.4 GB. A live concurrent-deploy probe was refused. [Release and evidence](2026-10-23-followup-deployment.md). Earlier restore evidence is separate; no new restore drill was run.
