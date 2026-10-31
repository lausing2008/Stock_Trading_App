# 2026-10-22 post-close host stall (investigation open)

**Status: evidence preserved, cause not established.** No code fix yet. Evidence:
- EC2: `/home/ec2-user/incident-2026-10-22-stall/` and `…stall.tgz` (journal, kernel log, container logs, cron, vmstat of the re-run).
- Local: `~/Documents/MyProjects/incident-evidence/2026-10-22-host-stall/` (plus CloudWatch at 1-minute resolution and CloudTrail).

## Timeline (UTC)

| Time | Observation |
|---|---|
| 20:30 | `us_post_close` starts. Mixed reads and writes rise to 2,115 read IOPS and 26 MB/s at 20:31. Healthy: queue < 2, CPU about 80% as during the session |
| 20:34:06 | `us_post_close` records `ok` (246.8 s) |
| ~20:36 | The RZY capture's confirmation passes. Its report starts inside the market-data container; the output folder was created at 20:36 |
| **20:37:00** | **Reads pin at 131.1 MB/s, the volume's throughput limit (gp3, 125 MiB/s); writes ≈ 0.** Idle 0 s/min, queue 1.7–2.0, CPU 9–53% |
| 20:57 | Last PostgreSQL log line (it ran crash recovery cleanly after the reboot). After this the journal holds only three cron lines |
| 23:42:58 | **`RebootInstances` API call by IAM user `lausing`** (CloudTrail). Not an AWS status-check recovery: `StatusCheckFailed` stayed 0 |
| 23:47 | Host back up; all 15 containers healthy. PostgreSQL recovered without loss beyond in-flight work |

No OOM kill or hung-task messages appear in the kernel log for the previous boot.

## What the signature suggests (not proven)

Over three hours the volume did only reads, at the throughput limit, with near-zero writes. Nothing was progressing: PostgreSQL stopped logging, the journal stopped writing, and the capture report never finished. That pattern fits **page-cache thrashing under memory pressure**: the kernel repeatedly evicts and re-reads file-backed pages (executables, libraries, data files). It is consistent with `vm.swappiness=10`, which favours dropping file pages over swapping. The onset coincides with the capture report starting while the post-close work had just finished, but correlation is not cause. On a quiet host the same report later ran in about 30 s.

Not established, because the evidence does not exist:
- **No memory history.** No sysstat/PSI, and CloudWatch has no memory metric by default.
- **The market-data and frontend container logs for the window are gone.** The 00:10 UTC deploy recreated those containers; `docker logs` keeps logs only for the container that wrote them. A grep before the deploy found nothing relevant, but the full log is lost.
- **Who or what issued the reboot**, beyond the IAM user name.

## Mitigations in place

- The capture report runs under `timeout` + `ionice -c3` + `nice 19` and records its exit code (`7cb109d1`).

## Proposed (needs approval; infrastructure changes)

1. Install sysstat (sar, 1-minute) and record PSI (`/proc/pressure/{memory,io}`) so the next event leaves memory evidence.
2. Ship container logs off the container (e.g. the `local` driver with rotation into a host path, or CloudWatch Logs), so a recreate cannot erase them.
3. Then decide memory controls: a market-data memory limit, running evidence reports outside the market-data container, or swappiness. Only with measurements.
4. Ask whoever issued the 23:42 reboot what they saw; that observation is part of the evidence.

## Correction and new evidence (2026-10-30)

**"No memory history" was wrong.** sysstat was already installed and collecting every 10 minutes
with 28 days of retention, PSI included, and `/var/log/sa/sa22` still holds Oct 22
([extract](../2026-10-30/evidence/host-stall-sar/sa22_memory_psi_io_paging.txt)). What it shows:
- **Memory was already short before the onset (19:40–20:30 UTC):**
  - available memory 0.64–0.80 GB of 7.8 GB;
  - page cache shrunk to 0.78–0.94 GB;
  - committed memory 170–187% of RAM.
- **The kernel reclaimed pages all afternoon:** from 18:10, about 1,000–1,700 pages/s scanned
  and 1,600–2,700 stolen, with about 10–28 major faults/s.
- **Memory pressure spiked:** PSI `some` memory reached 12.1% at 20:00 and 11.0% at 20:30.
- **The collector died with the host.** The last sample is 20:30, seven minutes before the 20:37
  onset; nothing more until the 23:47 reboot.

This strengthens the page-cache-thrashing reading above, with measured memory starvation going
into the event. The trigger is still not established, because the samples stop before the onset.

**What changed since:**
- **Container logs (item 2):** `scripts/deploy_remote.sh` now saves each outgoing container's
  log, gzipped with timestamps, under `~/deploy-evidence/container-logs/` before every recreate,
  keeping the newest 10 per service. A deploy can no longer erase them. A crash or a manual
  recreate still can.
- **1-minute sampling (item 1):** proposed as a systemd drop-in, not applied. The host change
  was refused by the session's permission check and needs your approval:

  ```
  sudo mkdir -p /etc/systemd/system/sysstat-collect.timer.d
  printf '[Timer]\nOnCalendar=\nOnCalendar=*:*:00\n' | sudo tee /etc/systemd/system/sysstat-collect.timer.d/10-every-minute.conf
  sudo systemctl daemon-reload && sudo systemctl restart sysstat-collect.timer
  ```

  To revert, delete the file and run `daemon-reload`. It writes about 10× more sar data,
  roughly 6 MB a day.
- **Memory today:** 3.2 GB available on Oct 30, against 0.64–0.80 GB before the stall. Memory
  controls (item 3) remain a decision to make with these figures.
