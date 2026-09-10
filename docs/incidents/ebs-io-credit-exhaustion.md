# EBS I/O Credit Exhaustion — a frontend rebuild took the instance down for ~50 minutes

## INCIDENT 2026-09-10: `Failed to load events`, then the whole box unreachable

**Reported by the user:** *"Failed to load events. when opening earning calendar"*, then
*"guess the instance is not reachable but why? We didn't have any deployment."*

**There HAD been a deployment — mine.** A `DOCKER_BUILDKIT=0 docker build` of the Next.js
frontend at ~14:50 ET, for a change that touched exactly one page (the improvements tracker).

### It was not the network, and not memory

The temptation is to read "unreachable" as a network or SG problem. It was neither:

| Probe | Result | What it ruled out |
|---|---|---|
| `ping` | 100% loss | nothing — ICMP is filtered by the SG normally |
| TCP 22 / 443 | **both OPEN** | not the security group, not the network path |
| `curl https://` | timeout after 20s | the listener accepts but cannot serve |
| `ssh` | **"timed out during banner exchange"** | **the diagnostic** |

**"Timed out during banner exchange" is the tell.** The TCP handshake completes, so sshd is
listening and the network is fine — but sshd cannot read its own host keys off the disk fast
enough to send its banner. That is a *disk* symptom, not a network one.

**No OOM.** `journalctl -b -1 | grep -iE 'out of memory|oom-kill|killed process'` returned
**nothing**. Memory was never the constraint; assuming OOM would have sent the whole
investigation the wrong way.

### The sysstat record is unambiguous

```
time     %user  %system  %iowait  %idle
14:50     75.6      9.7      4.6     8.5   <- the docker build, doing real work
15:00     76.2     10.1      3.2     7.5
15:11     22.0     18.0     56.6     0.89  <- credits exhausted
15:20     16.6     29.1     50.1     1.93
15:45      9.8     42.7     45.7     0.01
16:00      0.4     53.1     44.4     0.01  <- doing NOTHING but waiting on disk
```

**Read the collapse of `%user` against the rise of `%iowait`.** At 14:50 the box was busy
*computing* (75.6% user — the webpack build). By 16:00 user CPU was **0.4%** while the kernel
burned 53% in system time and 44% waiting on I/O. The machine was not overloaded with work; it
was starved of disk.

**`%steal` stayed ~2% throughout**, so this was not a noisy neighbour stealing CPU.

Corroborating symptoms, all the same cause:
- `dockerd`: `timed out starting health check for container ...` across **many** containers —
  Docker could not even fork the health-check processes.
- `sysstat-collect` took **6–8 minutes** (15:42→15:48) for a job that normally finishes in
  under a second.
- Journal timestamps arrived **out of order** (15:56 logged after 16:06).

### THE ROOT CAUSE WAS ACCUMULATED GARBAGE, NOT THE BUILD

At the time of the incident, on a **100 GB volume at 84% used**:

```
TYPE          TOTAL  ACTIVE  SIZE      RECLAIMABLE
Images          144      29  62.23GB   53.49GB (85%)
Containers       29      15   1.31GB   1.305GB (99%)
```

**128 dangling images** — one per historical rebuild, never cleaned.

A single `docker image prune -f` reclaimed **47.25 GB**, and `docker container prune -f`
another 1.3 GB. **The volume went from 84% → 36%.** The reclaimable garbage was *larger than
the entire live footprint of the platform.*

So each rebuild had been adding a dangling image to an ever-fuller volume, and this build was
simply the one that pushed the gp2 volume's I/O credit balance to zero. Once credits hit zero,
throughput floors to the baseline and **everything** — 12 containers' health checks, sysstat,
sshd — queues behind the disk.

**It degraded over ~30 minutes rather than failing at build time**, which is why it read as
unrelated to a deploy that had already "finished."

### What was wrong with the process, honestly

1. **No disk check before a heavy build.** Nothing looked at free space or I/O credits.
2. **No cleanup after a build.** 128 dangling images is a process failure, not bad luck.
3. **The heaviest deploy path used for the lightest change.** Only `improvements.tsx` changed;
   a full image rebuild plus `--force-recreate` was far more than that warranted.
4. **A too-early, too-narrow health check reported success.** Two containers were checked
   `healthy` ~90 seconds after recreation and the deploy was declared clean. Health checks were
   already timing out platform-wide by 15:28. **"Up" is not "healthy", and "healthy 20 seconds
   after start" is not evidence the host is fine.**

### The fix: `scripts/deploy.sh`

- **Preflight** — reports free disk, current `%iowait`, and `docker system df` before anything.
- **Refuses** a frontend build below `MIN_FREE_GB_BUILD=25` (the incident began at 17 GB free).
- **Warns** when `%iowait > 30%`, i.e. when the box is already in the failure mode.
- **Auto-prunes** below `WARN_FREE_GB=40` *before* building, and again *after* every deploy, so
  orphaned layers cannot accumulate to 128 images again.
- **Verifies after a 45-second settle**, requires the literal string `healthy`, and re-checks
  **host** disk and iowait — a deploy that leaves the host sick is not a successful deploy.

Two deliberate omissions:
- **No `docker cp` path.** CLAUDE.md is already emphatic that it is a session-scoped hotfix
  reverted by any recreation, and `scripts/check_deploy_drift.sh` exists because that has
  silently bitten this project at least eight times. Making it convenient would be a trap.
- **No `docker system prune -a`.** That removes images not currently *running*, which on a
  12-service compose file converts a one-service deploy into an unplanned full rebuild.
  Dangling-only is the safe subset and is where essentially all the waste actually was.

**Gotcha worth knowing:** the script exits non-zero on refusal, but piping it through `tail`
masks that (you get `tail`'s status). I made exactly that mistake while testing the refusal
guard and briefly read `exit=0` as the guard not working. Check `$?` unpiped, or set
`-o pipefail` in the caller.

### Standing guidance

- **Prefer the narrowest deploy that carries the change.** A frontend-only content change still
  needs the image rebuild (Next.js compiles at build time), but it does not need every service
  recreated alongside it.
- **`docker image prune -f` is safe and should be routine.** It only removes untagged layers no
  image references. Consider a weekly cron if this recurs.
- **If the box is ever unreachable again, check `%iowait` in `sar` before suspecting the
  network.** `sar -u -f /var/log/sa/sa<DD>` retains the record across the reboot, and
  `journalctl -b -1` holds the previous boot's logs.
- **The real capacity question:** a t3.medium with a 100 GB gp2 root volume builds Next.js
  images for a 12-service platform. That works only with disciplined disk hygiene. If builds
  become frequent, the durable fixes are a larger/gp3 volume (gp3 has no burst-credit model at
  baseline) or building images off-instance.
