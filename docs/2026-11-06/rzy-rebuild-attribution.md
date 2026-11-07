# RZY rebuild attribution, US sessions 2026-11-02 to 11-06

Read-only. Inputs:
- `scripts/measurement_report.py` (`rzy_rebuild_triggers`);
- `scripts/rzy_session_report.py` per day;
- the `rzy_rebuild_triggers` table (READ ONLY transaction);
- the saved market-data logs in `~/stockai-evidence/`.

Per-day outputs are on EC2 in `~/evidence/rzy-nov2-6/`. Nothing was changed. The revision check,
the throttle and the trading rules are untouched.

This answers the open item "Attribute the RZY rebuild rise" and the Nov 9 checkpoint's trigger
read.

## Answer

Two causes account for almost all 5-minute rebuilds in these sessions.

1. **The UW-cooldown skip leaves a partial candle in place, and RZY consumes it as complete.**
   This is the in-session cause and the source of the day-to-day swings.
   - How it happens:
     1. The 5-minute ingest writes the newest bar while it is still forming. For example, ANF's
        15:25 UTC bar on Nov 6 was written at 15:25:27, 27 seconds into the candle.
     2. Normally the next run, five minutes later, rewrites that bar with its final values. But
        when Unusual Whales refuses a request and the cooldown is armed, a symbol whose data is
        "fresh" skips the run (AUD-UW-COOLDOWN-YFSPILL, 2026-10-30). The newest bar counts as
        fresh because it was written seconds ago.
     3. The bar therefore stays as its 27-second snapshot past its close. Every RZY scan in the
        next five minutes treats it as a completed bar, since its close time has passed.
     4. The following run writes the final values, a 0.2–3% change in high, low and close. Every
        stream of every skipped symbol then rebuilds at once.
   - The skip minutes and the rebuild bursts coincide exactly:

     | Session | Cooldown skips (UTC, symbols) | Rebuild bursts (ET, 5m streams) |
     |---|---|---|
     | Nov 2 | 15:00 (196), 16:30 (256), 20:00 (176) | 10:00 (57), 11:30 (52), 15:00 (41) |
     | Nov 3 | none | none |
     | Nov 4 | incident day: 24 short or missed cycles | 9 bursts (the 30-min throttle caps them) |
     | Nov 5 | none | none |
     | Nov 6 | 14:50 (67), 15:30 (100), 17:15 (123) | 09:50 (55), 10:30 (67), 12:20 (66) |

   - The trigger records agree. In 1,143 of the 1,287 in-session 5m rebuilds (89%), the changed
     bar is the **last bar the checkpoint had consumed**. On Nov 6 that holds for 234 of 235, with a
     median bar age of 304 s.

2. **Every morning, nearly every stream rebuilds once.** The maintenance scans at 08:40 ET rebuild
   80–105 of about 105 5m streams daily, with the same pattern on 15m and 1h. The 04:00 ET
   premarket ingest re-fetches the previous session's bars, and Unusual Whales returns slightly
   different values for some of them: 500–830 revisions a day to bars more than 6.5 h old. For
   example, GDX's 09:30 bar on Nov 5 had its open changed from 86.77 to 86.78. Any OHLC change above
   `NOISE_REL` (1e-6) counts, by design. These rebuilds happen before the open.

The rest of the in-session rebuilds are a few "hot" streams rebuilding about every 30 minutes,
which is the throttle's minimum interval.

### Rebuilds per session (all timeframes)

| Nov 2 | Nov 3 | Nov 4 | Nov 5 | Nov 6 |
|---:|---:|---:|---:|---:|
| 912 | 440 | 1,454 | 395 | 773 |

For comparison, Oct 29 and Oct 30 had 1,516 and 1,646.

## What was ruled out

- **Cross-provider flip-flop.** About 99% of material completed-bar revisions are Unusual Whales
  revising its own bar. UW↔Yahoo revisions are only 10–400 a day.
- **Revision volume.** Completed 5m revisions are flat at about 9,700–10,800 a day (Nov 4: 7,545)
  while rebuilds swing 4×.
- **"A revision that arrives after a scan has consumed the bar causes a rebuild."** This does not
  predict rebuilds. Nov 5 had 1,636 such material revisions and 111 in-session rebuilds. The
  throttle folds every revision for a stream in a 30-minute window into one pending rebuild, and
  ordinary revisions land before the next scan consumes the bar. What matters is a bar consumed
  **while it is still partial**, which the cooldown skip produces for every skipped symbol at once.
- A first matching pass of mine reported most rebuilds as unmatched. It measured the revision
  window from the start of the rebuild's own scan, a window seconds long. Corrected before any
  conclusion was drawn.

## Side finding: scans are faster since the Nov 5 deploy

The median session scan time was 165–230 s on Nov 2–4. On Nov 5–6 it was 65–70 s, with 223–238
session scans a day against 77–134. This follows the 2026-11-05 deploy: training moved to a child
process and signal refreshes no longer overlap. It bears on the open "incremental bar loading"
item (scan latency).

## Options (not implemented; need a decision)

- **A. Do not skip a symbol whose newest bar closed after it was last written.** Such a bar is
  partial, so the run should still complete it, through the Yahoo fallback or a UW retry once the
  cooldown ends. Fully fresh symbols keep skipping, so the Yahoo-load protection of 2026-10-30
  mostly stays: at most one bar per symbol per skipped run.
- **B. RZY consumes a bar as completed only if it was last written after its close**
  (`bar_provenance.last_written_at >= closed_at`). This adds a readiness condition; it relaxes no
  check. It removes the defect for every cause of a partial bar, including missed or slow ingest
  cycles. The cost: a bar not yet rewritten waits one more ingest cycle, which adds to alert
  latency (OD-02) in exactly those cases.
- **C. Morning rebuilds.** They are pre-open and bounded at about one per stream a day. Leave them
  unless the next step shows they change events.

A and B are complementary. B is the general correction, A removes the most common trigger.

### Built 2026-11-06 (not deployed)

**A, `c4599c51`.** The cooldown skip itself is unchanged. A skipped symbol whose newest intraday
bar has closed without a post-close write is marked `completion_pending`. `ingest_universe` then
waits for the UW cooldown to end and re-fetches those symbols once, from Unusual Whales.
- The wait is bounded at 35 s.
- There is no Yahoo fallback. At a cycle boundary every symbol's head is partial, so a fallback
  would recreate the 2026-10-30 Yahoo burst. During the session the fallback's bars would also be
  refused by bar authority.
- Anything still pending waits for the next run.
- New metric: `stockai:metric:ingest_cooldown_completion_pending:{tf}:{hour}`. New log line:
  `ingest.cooldown_completion_retry`.

**B, `53467241`.** A bar of the latest regular session is consumed only when two conditions hold:
- `bar_provenance` shows a write at or after its close;
- the provenance values match the price that was read.

The first bar that fails this, and every bar after it, is held. The stream then reads as
`stale` and resumes on the next completing write, with no gap and no rebuild. A bar with no
provenance is held too. A 15m or 1h bin with a held 5m constituent reports
`awaiting_post_close_write`. The 15:30 ET hour still closes at 16:00.

The read-only check of 2026-11-06 found all 12,115 regular-session 5m bars written after their
close, so nothing is held over the weekend or at Monday's maintenance scans.

Qualifications:
- The 48 in the step-2 check were events, not emailed alerts.
- B's wait is normally one ingest cycle. It can be longer when ingestion keeps failing, and B
  then holds the stream rather than act on a partial candle.

## Next

Step 2, initial bounded check: [rzy-alert-impact-initial.md](rzy-alert-impact-initial.md).
Of 48 same-day 5m events persisted before a material correction to their signal candle, 45 are
unchanged in the current generation; three (none emailed) are absent but not attributed to a
specific rebuild. ANF's Nov 4 emailed alert was accepted before the correction and survives with
unchanged figures. Not yet covered: higher timeframes, morning rebuilds, indirect effects.

Step 2 in full: did these rebuilds change any RZY event? In particular, were events emitted from partial
candles during the skip windows, and withdrawn or altered after the rebuild? That decides how
urgent B is.
