# RZY rebuild investigation, 2026-10-30

Read-only measurement of production, run Fri 2026-10-30 13:38 UTC. Nothing was changed: two
scripts ran inside `stockai-market-data-1` in a `READ ONLY` transaction, and no code, rule,
tolerance, configuration or database row was touched. Scripts and output are in
[`rzy-rebuild-evidence/`](rzy-rebuild-evidence/).

## Answer

- **Rebuilds per stream are rising, but bar revisions are not.** From Oct 27 to 29:
  - 5m rebuilds per 5m stream went 3.3 → 5.5 → 7.1 (382 / 645 / 833 rebuilds over 117 streams).
  - Distinct revised completed 5m bars stayed flat: 10,663 / 7,480 / 8,227.
  - Rebuilds per revised 5m bar therefore rose 0.036 → 0.086 → 0.101.
  - **Why more rebuilds come from the same volume of revisions is not established.** See "Not measured".
- **The dominant trigger is a material OHLC change to a recently completed 5m bar, from the same
  provider.**
  - On Oct 29, 881 of 1,000 retained rebuild archives were material OHLC changes, 44 were
    rounding-scale and 75 were gaps after the checkpoint.
  - No retained archive showed a `closed_at` change, an adjustment-basis (`price_basis`) change, a
    bar missing from source, an off-grid bar or legacy quarantine. No rebuild row since Oct 23 has
    any reason other than `stored_completed_bar_changed` (4,553) or `gap_after_checkpoint` (519).
  - Of completed 5m bar revisions on Oct 29:
    - 6,927 of 8,528 (81%) were revised within 15 minutes of the bar's close.
    - 5,981 were `unusual_whales → unusual_whales`.
- **15m and 1h rebuilds are explained by 5m revisions.** Of 915 retained 15m/1h archives triggered
  by a changed bar, 900 (98.4%) have a 5m revision inside that bar's interval. Each revision was
  fetched between the stream's previous rebuild and this one.
  - **No case** fits a switch between native and 5m-derived bars.
  - 14 match the bar re-derived from current 5m data while having no native row and no recorded
    5m revision. A newly arrived 5m constituent fits that pattern (inference).
  - 1 is unexplained.
- **The cascade multiplies rebuilds by about 1.4.** On Oct 29, 1,516 rebuild rows came from 1,060
  symbol-scans (one symbol rebuilt in one scan minute). 93 symbol-scans rebuilt all of 5m, 15m and
  1h together, and 219 rebuilt 5m and 15m.

## 1. Rebuilds per evaluated stream

There are 117 streams per timeframe: 351 rows in `little_rzy_streams`, the same for every day
measured. Each window runs 04:00–20:00 ET, the `rzy_session_report` window. Oct 30 ends at 09:38 ET
and is partial.

| Session | Rebuilds | 5m / stream | 15m / stream | 1h / stream | Affected symbols | Rebuilds / stream-evaluations (session + maintenance) |
|---|---|---|---|---|---|---|
| Fri Oct 23 | 1,093 | 4.80 | 3.29 | 1.25 | 115 | 1,093 / 26,868 = 4.1% |
| Mon Oct 26 | 615 | 3.76 | 1.29 | 0.21 | 105 | 615 / 40,485 = 1.5% |
| Tue Oct 27 | 661 | 3.26 | 1.88 | 0.50 | 109 | 661 / 34,311 = 1.9% |
| Wed Oct 28 | 1,021 | 5.51 | 2.51 | 0.70 | 110 | 1,021 / 25,158 = 4.1% |
| Thu Oct 29 | 1,516 | 7.12 | 4.15 | 1.69 | 110 | 1,516 / 32,277 = 4.7% |
| Fri Oct 30 (partial) | 167 | 0.68 | 0.45 | 0.30 | 80 | 167 / 1,428 |

"Per stream" divides rebuilds by the 117 streams of that timeframe. A stream-evaluation is one
stream in one scan run (`little_rzy_scan_runs.streams`, summed). The number of session scan runs
varies by day (76 / 120 / 100 / 73 / 94), so per-evaluation rates and per-stream rates move
differently.

**Repeat rebuilds** (5m streams, by how many times each was rebuilt in the session):

| Session | Rebuilt once | 2–5 times | 6+ times | Rebuilds per rebuilt 5m stream |
|---|---|---|---|---|
| Oct 23 | 3 | 87 | 25 | 4.89 |
| Oct 26 | 7 | 78 | 19 | 4.23 |
| Oct 27 | 15 | 87 | 7 | 3.50 |
| Oct 28 | 5 | 43 | 62 | 5.86 |
| Oct 29 | 4 | 25 | 81 | 7.57 |

On Oct 29, 81 of 110 rebuilt 5m streams rebuilt six times or more. The 15m and 1h figures are in
`summary.json` (`by_timeframe`).

**When rebuilds happen** (ET hour, Oct 29): 08 h 178, 10 h 298, 11 h 320, 14 h 293, 15 h 145. The
08 h block is the pre-open maintenance pass. On Oct 30 it rebuilt 166 streams, every one triggered
by a bar from an earlier session.

## 2. Distinct revised bars and archive coverage

| Session | Completed 5m revisions (rows) | Distinct revised 5m bars | Distinct revised daily bars | Retained archives / rebuild rows | Distinct triggering bars in retained archives |
|---|---|---|---|---|---|
| Oct 23 | 8,115 | 7,707 | 361 | 168 / 1,093 (15%) | 151 |
| Oct 26 | 10,254 | 9,577 | 416 | 34 / 615 (6%) | 24 |
| Oct 27 | 11,081 | 10,663 | 360 | 74 / 661 (11%) | 49 |
| Oct 28 | 7,804 | 7,480 | 211 | 153 / 1,021 (15%) | 133 |
| Oct 29 | 8,528 | 8,227 | 208 | 1,000 / 1,516 (66%) | 885 |
| Oct 30 (partial) | 1,642 | 1,531 | 204 | 167 / 167 (100%) | 158 |

**Archive coverage limits the per-trigger analysis.** `KEEP_GENERATIONS = 5` deletes all but each
stream's last five generation archives. Retained archives therefore cover 6–15% of rebuilds for
Oct 23–28, 66% for Oct 29 and all of Oct 30. The surviving older archives lean toward streams that
rebuilt *less* often since, because a frequently rebuilt stream pushes its older archives out.
Change-class shares for Oct 23–28 come from that biased remnant. Only Oct 29 and 30 are close to
complete. The `little_rzy_rebuilds` rows themselves are never pruned, so the counts in section 1
are complete.

## 3. Change types

**What triggered rebuilds** (retained archives, classified the way `_ohlc_revised` decides):

| Session | Material OHLC | Rounding-scale OHLC (≤ max(0.005, 2e-5·price)) | Gap after checkpoint | `closed_at` | `price_basis` | Missing / off-grid / quarantine |
|---|---|---|---|---|---|---|
| Oct 29 | 881 | 44 | 75 | 0 | 0 | 0 |
| Oct 30 (partial) | 135 | 23 | 9 | 0 | 0 | 0 |
| Oct 23–28 (biased remnant) | 325 | 48 | 56 | 0 | 0 | 0 |

**How large the triggering OHLC changes were** (Oct 29, 925 archives with a changed bar; largest
absolute change across O/H/L/C):

| Largest change | ≤ $0.005 | ≤ $0.01 | ≤ $0.05 | ≤ $0.25 | > $0.25 |
|---|---|---|---|---|---|
| Archives | 43 | 47 | 202 | 349 | 284 |

- **Half-cent triggers:** 43 of 925 (4.6%) changed by half a cent or less.
- **Five-cent triggers:** 292 of 925 (32%) changed by five cents or less.
- **Fields changed** (Oct 29): close only 266; low+close 214; high+low+close 166; high+close 144;
  open only 72.
- **Oct 30 pre-open:** the triggers were open only (82) or close only (74).

**Completed bar revisions** (`bar_revisions`, completed bars only):
- **Provider pairs, 5m, Oct 29** (8,528 rows):
  - `unusual_whales → unusual_whales` 5,981;
  - `yfinance → yfinance` 1,179;
  - `yfinance → unusual_whales` 778;
  - `unusual_whales → yfinance` 590.
- **Oct 30 pre-open:** 1,346 of 1,642 were `yfinance → yfinance`.
- **Bar age when revised, 5m, Oct 29:**
  - under 15 minutes after the close: 6,927;
  - 15–60 minutes: 1,161;
  - 1–6 hours: 94;
  - 6 hours or more: 346.
- **Size, 5m, Oct 29:**
  - Rounding-scale is 238 of 8,528 completed 5m revisions, about 3%.
  - By largest absolute change: 221 changed ≤ $0.005, 3,485 changed > $0.25.
  - By relative change: 3,568 moved 0.2% or more.
- **Volume:** most OHLC revisions also changed volume (Oct 29, 5m: 6,975 of 8,528 had volume
  changed alongside OHLC).
  - **Volume-only changes are not written to `bar_revisions`.** They exist only as the cumulative
    `bar_provenance.volume_only_revisions` counter: 792 for 5m bars dated in the Oct 29 window,
    and 2,739 on Oct 27.
  - **Volume cannot trigger an RZY rebuild.** `_ohlc_revised` compares only OHLC, `closed_at` and
    `price_basis`.
- **Daily bars:** 208–416 distinct daily bars revised per session, almost all with volume. They do
  not feed RZY's intraday streams.

### Finding: the rebuild test is stricter than its docstring

`rzy_workflow.evaluate()`'s docstring says a stream rebuilds on "material OHLC revision". The code
(`_ohlc_revised`) rebuilds on any OHLC change above `NOISE_REL = 1e-6` relative, or on any
`closed_at` or `price_basis` change. So a half-cent change rebuilds a stream. One example: ZS 5m
15:05 UTC Oct 29, close 244.685 → 244.69, rebuilt at 12:45 UTC Oct 30.

`bar_provenance.revision_magnitude` would call that change rounding. The difference accounts for
only about 5% of Oct 29's triggers (44 rounding-scale of 925), so it does not explain the volume.
This is recorded, not changed.

## 4. Attribution and cascade

**Derived timeframes.** For each retained 15m/1h archive triggered by a changed bar, the script
looked for a completed 5m revision of that symbol whose bar falls inside the triggering bar's
interval. The revision must also have been fetched after the rebuild that started the archived
generation and before this rebuild (falling back to 7 days if the start is unknown). Where none
existed, it compared the archive's before/after against the stored native 15m/1h bar and against
the bar re-derived from current 5m rows.

| Result | Oct 23 | Oct 26 | Oct 27 | Oct 28 | Oct 29 | Oct 30 | Total |
|---|---|---|---|---|---|---|---|
| A 5m revision inside the interval | 129 | 23 | 45 | 110 | 511 | 82 | 900 |
| Native 15m/1h bar itself revised | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Native vs derived source switch | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| No native row, after = re-derived from 5m, no 5m revision recorded | 1 | 0 | 0 | 1 | 12 | 0 | 14 |
| Unexplained | 1 | 0 | 0 | 0 | 0 | 0 | 1 |

The source-switch hypothesis found no support: 0 of 915. The 14 "after = re-derived" cases fit a
5m constituent that arrived late, since a new bar is not a revision and is not recorded as one.
That is an inference.

**Cascade.** Rebuild rows grouped by symbol and scan minute (complete, not archive-dependent):

| Session | Symbol-scans | Rebuild rows | 5m only | 5m+15m | 5m+15m+1h | 15m and/or 1h without 5m |
|---|---|---|---|---|---|---|
| Oct 27 | 437 | 661 | 199 | 137 | 41 | 55 |
| Oct 28 | 748 | 1,021 | 442 | 138 | 63 | 103 |
| Oct 29 | 1,060 | 1,516 | 518 | 219 | 93 | 227 |

A 15m/1h rebuild with no 5m rebuild in the same scan is expected when the 5m stream is inside its
30-minute throttle (`REBUILD_MIN_INTERVAL`, `restatement_pending`) while the derived stream is not.
That is an inference: per-stream throttle state at each scan is not stored.

## 5. Before/after evidence

[`before_after_samples.csv`](rzy-rebuild-evidence/before_after_samples.csv) holds 54
representative rows:
- up to 10 each of material, rounding and gap triggers, spread across sessions;
- up to 3 per session for each attribution result.

Each row has the symbol, timeframe, bar time, rebuild time, archived generation, before and after
OHLC/`closed_at`/`price_basis`, and for derived timeframes the matched providers or the current
native and re-derived bars. Because archives are pruned, older sessions can only be sampled from
what survived.

## Measured vs inferred

**Measured:**
- every count and rate in the tables above;
- archive coverage;
- trigger classes and sizes for retained archives;
- the provider pairs and bar ages of revisions;
- the attribution table.

**Inferred, and labelled where used:**
- that 5m bars served as complete are being restated by the same provider within minutes, as late
  trades are aggregated (from 81% revised under 15 minutes, mostly same-provider);
- that the 14 unattributed derived cases are late-arriving constituents;
- that derived-without-5m rebuilds reflect the 5m throttle.

## Not measured, and why

- **Why rebuilds per revised bar rose (0.036 → 0.101, Oct 27–29).** A revision causes a rebuild
  only if a scan had already consumed the bar before the revision arrived. Neither the time each
  checkpoint consumed a bar nor per-scan throttle state is stored, so that ordering cannot be
  reconstructed. The likely factors (scan timing relative to the 5m close, scan count per day,
  throttle coalescing) are hypotheses.
- **Change classes for Oct 23–28.** Archives are pruned to the last five generations per stream,
  leaving 6–15% coverage biased toward less-rebuilt streams.
- **Volume-only revisions per session.** They live only in a cumulative per-bar counter, not as
  timestamped rows.
- **Rebuilds per evaluated stream by timeframe.** `little_rzy_scan_runs` stores one stream count
  per run, not split by timeframe. Per-timeframe rates therefore use the 117 streams per timeframe
  as the denominator, which is constant across these days.

No check was relaxed and no trading rule was changed.

**Now recorded (2026-10-30, second release).** The missing ordering and throttle state are now
stored per rebuild in `rzy_rebuild_triggers`, along with each trigger's change class and the bar
before and after, including volume. Unlike the archives, these rows are never pruned. The
question above becomes answerable from the sessions after that deploy. See
[durable-measurement-and-checks.md §8](durable-measurement-and-checks.md#8-follow-ups-closed-the-same-night-second-release).

## Review note

Re-counted independently on production (read-only, 14:00 UTC Oct 30) for Oct 29: rebuilds 5m 833,
15m 485, 1h 198; completed 5m revisions 8,528 on 8,227 distinct bars, 6,927 within 15 minutes. All
match the tables. An earlier draft's Answer section said "7,085 (83%)" for the last figure; the
evidence (`sizes.json`, `bar_age_at_revision`) says 6,927 (81%), and the Answer was corrected.
