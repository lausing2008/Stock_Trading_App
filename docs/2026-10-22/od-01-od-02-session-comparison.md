# OD-01 / OD-02: Oct 21 vs Oct 22 (first full session after the fixes)

**Status: measured.** One session after `db89050a` (deployed 01:12 UTC Oct 22). Strategy rules are unchanged. Raw evidence: `~/Documents/MyProjects/rzy-session-evidence/2026-10-2{1,2}/`. Comparison output: [`od-evidence/comparison.json`](od-evidence/comparison.json), produced by [`od-evidence/compare_sessions.py`](od-evidence/compare_sessions.py).

## Comparability

| | Oct 21 | Oct 22 |
|---|---|---|
| market-data release | `b39800d9` | `db89050a` (container restarted 23:47 UTC after the host stall below, same image) |
| Strategy config hash / engine | `8ceb8cf7…` / `little_rzy.py` `945b6234` | identical |
| Capture confirmed | post-close ok | post-close ok **and** 153/155 US symbols observed the Oct 22 session; report exit 0; 12 artifacts hash-verified |

All figures below are for the **regular session only** (bars and signals from 09:30 to 16:00 ET), so after-hours activity, including the stall, cannot affect them. This is one session per side, not a rate.

## OD-01: restated completed bars

| Measure | Oct 21 | Oct 22 | Change |
|---|---|---|---|
| Completed-bar OHLC revisions | 45,557 | 10,253 | −77% |
| Cross-provider | 39,454 | 3,239 | −92% |
| Material cross-provider | 34,422 | 3,105 | −91% |
| Same provider | 6,103 | 7,014 | +15% |
| Cross-provider revisions per bar, max | 31 | **2** | flip-flop gone |
| Bar age at a cross-provider revision, median / p90 | 115 / 246 min | **0.5 / 26 min** | |
| Rebuilds (whole window) | 2,413 | 966 | −60% |
| …driven by material cross-provider rewrites | 1,958 | 636 | −68% |
| Stream-scans paused for a rebuild | 19.1% | 5.4% | |

Authority on Oct 22: 404 symbol/timeframe sessions. The authority refused 193,570 fallback writes and took over 4,874 fallback bars once. The trace failed in 0 sessions, and the largest refused divergence was 0.07.

**Reading.** The provider overwrite cycle is gone: no completed bar was rewritten back and forth. The remaining cross-provider revisions happen within minutes of completion, at most twice per bar. That matches the designed handover: a fallback completes a forming snapshot, then the authority takes the bar over once. The success criterion "cross-provider ≈ 0" is **not met**. Those handovers are still material changes, and they still drive 636 rebuilds. Rebuilds fell 60%, not by the order of magnitude targeted.

## OD-02: latency and delivery

| Measure | Oct 21 | Oct 22 |
|---|---|---|
| Scan duration median / p90 | 281 / 468 s | 261 / 355 s |
| Phases (Oct 22, median) | — | evaluate 230 s, of which loading and preparing bars 164 s; snapshot and commit 26 s |
| Emailable signals: seen incrementally / only in a rebuild | 16 / 8 | 20 / 4 |
| Close → recorded, incremental (median / p90) | 9.4 / 14.2 min | 7.8 / 13.3 min |
| 5m research path | 1 accepted, 5 recorded too late, 3 expired | **5 accepted**, 3 recorded too late, 0 expired |
| 15m / 1h | 2 suppressed (paused) / — | 2 accepted / 2 accepted |
| Close → provider accepted, 5m median | 8.1 min (n = 1) | 6.4 min (n = 5) |
| Outbox | 4 accepted, 2 suppressed, 3 expired | 9 accepted |

**Reading.** The independent sender works: nothing expired in the queue. Fewer pauses meant no rebuild-suppressed mail. Scans are still about 4.3 minutes, so 3 of 8 5m signals were recorded after the 8-minute queueing window. OD-02 is improved, not solved. Provider acceptance is not confirmed inbox delivery.

## Decision: skip unchanged streams? Not now

The scan loads each symbol's bars once and shares them across its 5m, 15m and 1h streams. Every 5m stream has a new completed bar on every 5-minute scan, so the 164 s load runs regardless. Skipping only saves the non-load evaluation and snapshot work of 15m/1h streams without a new bar. That is an **estimated upper bound of about 50 s per scan (≈19%)**, assuming equal per-stream cost, roughly half the streams skippable, and none of the load. Loading and preparing bars is 63% and untouched by skipping. Skipping also delays detecting restatements on the skipped streams. **Recommended next lever: incremental bar loading**: keep each symbol's prepared bars between scans and append only new bars. That targets the 164 s. It needs its own design and an output-identity check like the `prepare_bars` speedup.

## Host stall on Oct 22 (unexplained)

From about 20:32 UTC the EBS volume was saturated:
- idle time ≈ 0, queue ≈ 1.7;
- CPU about 53% (one core);
- no OOM kill;
- PostgreSQL silent after 20:57.

It lasted until a reboot at 23:47 UTC. Status checks never failed, so the reboot was not AWS auto-recovery, and what initiated it is not recorded. The first capture's report started at about 20:36, the post-close job finished at 20:34, and the nightly backup runs at 02:00, not then. The cause is **not established**: the report is small (34 MB of revisions) and later finished in about 30 s on a quiet host. The capture report now runs under `timeout` + `ionice -c3` + `nice 19` and records its exit code (`7cb109d1`). A memory-history source (e.g. sysstat) would be needed to test the memory-pressure hypothesis next time.

## Still open

- The 43 Oct 21 occurrences absent from their current generation remain **unexplained**; Oct 22's equivalent was not computed.
- Handover rebuilds (636) could be avoided by not evaluating a stream's newest bar until its authority has written it. That is a separate design decision.

## The three unaccepted Oct 22 5m alerts (classified 2026-10-23 from the captured evidence)

All three were seen in **incremental** scans (never rebuilds) and never queued; each was recorded after the 8-minute 5m queueing window. **Class: scan latency (OD-02).** None is a data, provider, authority or delivery fault.

| Alert | Candle close (UTC) | Recorded | Late by | Mechanism |
|---|---|---|---|---|
| SMH 5m TRIGGERED | 18:30 | 18:43:10 | 13.2 min | Previous scan (18:28:25) ran 562 s, so the next started at 18:38:25 (8.4 min after close); SMH reached 4.7 min into that scan |
| DDOG 5m TRIGGERED | 19:35 | 19:43:55 | 8.9 min | Previous scan (19:30:25) ran 594 s; next started 19:40:25; DDOG reached 3.5 min in. Missed by 0.9 min |
| TXN 5m TRIGGERED | 18:45 | 18:57:14 | 12.2 min | Scan at 18:44:25 started before the close; next at 18:50:25 ran 455 s; TXN (late in the alphabetical order) reached 6.8 min in |

Both mechanisms, a long scan delaying the next start and a symbol's position inside a scan, come from scan duration, dominated by bar loading. This is further support for incremental bar loading. **Delivery is not yet dependable:** 5 of 8 accepted in one session, and the 3 misses have a known, unfixed cause.
