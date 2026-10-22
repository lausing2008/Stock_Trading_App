# OD-01 / OD-02: the 2026-10-21 session evidence (trackers 457 / 458)

**Status: analysed; root cause for OD-01 established; fixes proposed, not built.** Strategy rules and configuration are unchanged.

## Evidence and boundaries

- **Capture.** The post-close capture ran at 20:39 UTC, after `us_post_close` was recorded `ok` at 20:34. Raw evidence is preserved on EC2 at `/home/ec2-user/rzy-session-evidence/2026-10-21/` and locally at `~/Documents/MyProjects/rzy-session-evidence/2026-10-21/`. Manifest, report and analysis outputs are in [`od-evidence/`](od-evidence/).
- **Release under test.** market-data `b39800d9` (occurrence and rebuild records, intraday bar trace). Daily-bar tracing and the new emails were deployed only afterwards (23:46 UTC), so **this session covers 5m intraday revisions only**. The derived 15m and 1h streams are built from those 5m bars.
- **Magnitude cut.** Revisions are split by `revision_magnitude`: rounding if every changed OHLC field moved ≤ max(0.005, 2e-5 × price), otherwise material. Signal-figure changes use relative bands.
- **Observed vs unresolved.** "Observed" means measured directly from the trace and records. "Unresolved" means the stored evidence cannot attribute a cause.

## OD-01 — completed bars are rewritten by alternating providers (cause established)

| Measure | Value |
|---|---|
| Completed-bar OHLC revisions | **51,570** on 13,424 bars across 153 symbols (median 1 revision per bar, max 33) |
| Cross-provider | **43,271** (88% of the 49,395 with a known previous provider): Unusual Whales → yfinance 23,345; yfinance → Unusual Whales 19,926 |
| Same provider | 6,124; 5,953 of them within 15 minutes of the bar's close (a just-completed bar finalising) |
| Previous provider unknown | 2,175, all on bars ≥ 6 h old that predate the trace; counted toward neither hypothesis |
| Material vs rounding | material **45,470** / rounding 6,100. Cross-provider: 37,644 material / 5,627 rounding |
| Size | median 0.03%, p95 0.39%, max 5.67% of the price |
| Volume-only revisions | 88,723 (they do not trigger rebuilds) |

**Reading.** The provider-flip hypothesis is confirmed. The two feeds take turns rewriting the same completed bars throughout the day: each incremental fetch re-upserts the whole day from whichever provider answered. Most cross-provider differences are material by the half-cent rule; their median is 0.04% of price. That is consistent with two different trade sets or aggregation conventions, not storage rounding. Same-provider corrections are rare and almost all arrive within 15 minutes of the close, which looks like legitimate finalisation.

## Effect on Little RZY

**Stream rebuilds — 2,413 in the session.** The rebuild log was linked to the traced revisions on the same symbol since the stream's previous rebuild ([`rebuild_drivers.csv`](od-evidence/rebuild_drivers.csv)):

| Driver | Rebuilds |
|---|---|
| Material cross-provider rewrite | **1,958** (81%) |
| Material same-provider / unknown | 330 |
| Gap after checkpoint (bar missing) | 109 |
| Rounding-scale changes only | 16 |
| Unresolved (no traced revision in the window) | 0 |

Rounding-scale changes are **not** the problem. Rebuilds come from material cross-provider rewrites.

**Eligibility (observed).**
- 4,708 of 25,056 stream-scans (18.8%) were *paused*: the stream was awaiting a rebuild after restated bars, and a paused stream emits nothing live.
- Both RZY mail suppressions that day had the reason "paused: stream awaiting a rebuild after restated bars": INTC 15m reversal watch and OSCR 15m bullish trigger.

**Timing (observed).**
- 512 signal occurrences had candle closes inside the session; 138 (27%) were first seen only during a rebuild.
- Of the 24 emailable ones:
  - **16 were seen incrementally**: candle close to recorded, median 9.4 min, p90 14.2, max 19.6.
  - **8 were seen only in a rebuild**: median 24.4 min, max 49.9. None of these could be mailed (the freshness window is timeframe + 3 min).

**Outcomes (observed).** Of the 512 occurrences:

| What happened after rebuilds | Occurrences |
|---|---|
| Never rebuilt | 153 |
| Rebuilt, figures unchanged | 259 |
| Figures changed | 57 |
| Absent from the current generation | 43 |

The 57 figure changes break down as 21 float noise (≤ 1e-6 relative), 28 rounding-scale (≤ 2e-5), 2 small (≤ 0.1%) and **6 material** (up to 1.9%; ARMK, PL, SES structure events). So **86% of figure "changes" are storage noise or rounding**. The 43 disappearances (3 emailable) are **unresolved**: replaying the superseded generations would be needed to tell a material pivot shift from a reset. Paper execution is disabled, so no paper P&L was affected.

**Defect found — exact figure comparison at send time.** `rzy_send_check` compares queued and current figures exactly. A float32 → decimal provider flip (KTOS entry 38.779998779 → 38.78) therefore reads as "a rebuild changed the trade figures" and would suppress the email. On Oct 21 no email was suppressed this way: both suppressions were pauses. But the same noise changed 49 occurrences' figures that day. **Proposed:** compare figures with the same rounding tolerance as revisions (≤ max(half a cent, 2e-5) per price field), keep the exact values in the record, and test both sides.

## OD-02 — latency: the scan itself is the main delay (observed)

| Measure | Value |
|---|---|
| Session scans | 72; every one evaluates all **348** streams |
| Scan duration | median **282 s**, p90 466 s, p95 513 s, max 645 s; 5 scans over 8 min |
| Interval between scans | median 5 min, p90 8 min |
| Duration vs paused streams | correlation −0.13: rebuild load is not what makes scans slow |
| 5m research path (9 signals) | 1 accepted (8.1 min close-to-acceptance), **5 recorded too late to queue**, **3 queued but expired** before the drain ran |

**Reading.** A 5m alert can only be queued within 8 minutes of its candle close, and only delivered within 10. With a median scan of 4.7 minutes, which fully re-evaluates every stream, plus waiting for the next scan, most 5m signals are already past the window when they are recorded.

The expired AI emails show a second effect. They were queued at 17:41, 17:47 and 18:26 with expiries 17:45, 17:50 and 18:30, but the drain runs only **after the whole scan finishes** (scan → digest → drain in one job), so it reached them too late. Provider acceptance is not confirmed inbox delivery.

## What is established, and what is not

- **Established:** cross-provider flip-flop is the cause of the intraday restatements; material changes drive 95% of rebuilds; rebuild pauses suppressed the day's two RZY emails and delayed 8 emailable signals past mailability; scan duration and drain placement drive 5m lateness; and most rebuild-induced figure changes are noise.
- **Unresolved:** why 43 occurrences disappeared; whether any material cross-provider difference was "more correct" on either side; daily-bar revision behaviour (tracing started after this session); whether these rates hold on other days, since this is a single session.

## Proposed changes (in the approved order; each separately reviewed; trading rules unchanged)

1. **Source policy (OD-01, now evidence-based).** Make one provider authoritative for a completed intraday bar. Ways to do it: pin a session's 5m series to the provider that first served it, or prefer Unusual Whales and use yfinance only to fill bars Unusual Whales lacks; never overwrite a completed bar from the other provider. A same-provider correction within a short finalisation window stays allowed. Success criterion: completed cross-provider revisions ≈ 0, rebuilds driven by material revisions down by an order of magnitude.
2. **Tolerant figure comparison at send time.** Described above; small and independent.
3. **Incremental scans (OD-02).** Advance only streams with a new completed bar, verified against a full replay. Target: scan duration well under one bar interval.
4. **Drain cadence.** Drain the outbox independently of the scan (for example every minute), so queued alerts aren't held behind a 5-minute scan.
5. **Delivery-path measurement.** Close → ingest → evaluate → queue → provider acceptance on the next sessions, after 1–4, with missed and suppressed signals in the denominator.

## Fixes deployed (2026-10-22 01:12 UTC, market-data `db89050a`) — each independently reviewed

| Step | Commits | What it does |
|---|---|---|
| 1. One authoritative provider per completed bar | `79bfe2c3`, `1194ed65`, `8a0889e5` | `bar_session_authority` records the primary provider per stock/timeframe/session, from static routing (a UW 429 cooldown cannot hand yfinance the authority). A writer **owns** a bar only from its first write at or after completion; for daily bars that starts at 16:45 local, after the post-close settle. A fallback may fill new bars, complete forming snapshots and correct its own bars, but never rewrites a bar the authority owns. The authority takes over a fallback's bar once, so an outage cannot restart the cycle. A failed provenance trace marks the session so fallbacks may only add bars; a failed authority check fails closed for fallbacks and is counted. The largest divergence of a refused row is recorded. Force refreshes bypass it. |
| 2. Tolerant send-time figure check | `009b850a` | Queued vs current prices equal within max(half a cent, 2e-5 × price); reward:risk within 1% unless it crosses the minimum; type and direction exact; NaN never equal. |
| 3a. Bar preparation | `1f32e09d` | Same output (verified on 155 symbols / 465,950 bars), 43% less time. |
| 3b. Scan-phase timing | `763582ac`, `db89050a` | `little_rzy_scan_phases` records load/prepare, evaluate, snapshot/commit and other per scan. **The skip-unchanged-streams design waits for this evidence.** Profiling showed the engine is already incremental and the cost is bar preparation, checkpoint copying and cold reads. |
| 4. Independent sender | `5fb1d339` | `drain_little_rzy_outbox` every minute, its own lock; the scan job no longer drains. Same send-time checks; four concurrent drains send once. |
| 5. Close-to-delivery measurement | — | A capture for 2026-10-22 runs after `us_post_close` is recorded ok → `/home/ec2-user/rzy-session-evidence/2026-10-22/` (log `~/rzy-capture-2026-10-22.log`). Compare against this document. |

**Still unexplained:** the 43 occurrences absent from their current generation on Oct 21 (3 emailable). They stay marked unexplained until the superseded generations are replayed.
