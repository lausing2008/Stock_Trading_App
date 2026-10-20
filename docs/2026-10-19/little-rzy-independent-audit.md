# Little RZY independent implementation audit — 2026-10-19 Pacific

**Continuation:** see the [October 20 audit supplement](../2026-10-20/little-rzy-audit-addendum.md) for the new evaluation framework, two additional findings and the updated status of real-data comparisons. The eight findings below describe the unchanged original paths.

**Verdict: the implementation is not fully correct. Eight reproducible findings remain: three high priority and five medium priority. No fixes were made.** The existing tests pass, but do not exercise the failure cases below. In particular, the earlier deep review's unconditional “Entry and exit logic is correct” verdict is too broad for the implemented replay inputs and reporting path.

## Scope and evidence

Reviewed against [the accepted specification](../Little%20RZY%20Integration.md): pure engine, revision/segment handling, persistence and generations, scheduled scan/maintenance/digest, central consent and outbox integration, authenticated API, paper ledger, backtest/CLI, chart overlays, SMA 5/10, and relevant tests/documentation.

The final audited checkout is **`f51fdb2c4`**. The frontend changed from `238ec03a` during this review; the final overlay changes were included. Backend behavior includes the `afcba909` rebuild/retention changes. This is not a repeat of the audit of `a959c5dc` alone.

Validation:

- Existing engine/workflow/aggregation tests: **60 passed**. Workflow cases execute real ORM/engine/adapter/outbox logic against disposable SQLite, with the external provider replaced.
- Existing RZY/chart-command/frontend component tests: **18 passed across four files**.
- Additional audit reproductions run the shipped functions, with synthetic inputs and disposable SQLite. They are preserved under [rzy-audit-evidence](rzy-audit-evidence/results.json). The reference retention run changes only retention limits, not signal or fill rules.
- Production checks were read-only: checkout, image metadata, four backend source hashes and small SQL summaries under `SET TRANSACTION READ ONLY`. All four reviewed backend source files match the running container byte-for-byte. No production evaluation, replay, reset, enqueue, sender, preference change, or deployment was invoked.
- PostgreSQL integration was **not rerun** in this audit. Existing PostgreSQL coverage does not prove these newly reproduced edge cases. No full-platform test or new performance/market-edge claim is made.

## Reproduce locally

From the repository root, using the existing Python dependencies and frontend `node_modules`:

```sh
python docs/2026-10-19/rzy-audit-evidence/pure_probe.py
python docs/2026-10-19/rzy-audit-evidence/workflow_probe.py
python docs/2026-10-19/rzy-audit-evidence/entry_probe.py
node docs/2026-10-19/rzy-audit-evidence/ui_probe.cjs
```

These report observed behavior rather than applying a fix. The workflow probe refuses `RZY_PG_URL` and creates only a temporary SQLite fixture. No production credentials or data are required. `pure_probe.py` runs the 26,000-bar bounded/unbounded comparison and can take several seconds. Outputs are recorded in `results.json`; the source commit and production hashes identify what was reviewed.

## Findings

| ID | Priority | Finding | Main consequence |
|---|---|---|---|
| RZA-01 | High | Rebuilt events have no generation identity; corrected events collide with old IDs | Current geometry and returned historical payloads disagree without a generation label |
| RZA-02 | High | Derived-frame freshness is inherited from the 5m source | Incomplete recent aggregate bins can leave old structures labelled fresh |
| RZA-03 | High | Backtest reporting reads a pruned position cache | Trade counts, expectancy/groups and drawdown become incomplete or distorted |
| RZA-04 | Medium | Replay does not enforce gaps/session continuity | A pending entry fills after missing bars that live evaluation would split |
| RZA-05 | Medium | Replay execution eligibility uses truthiness | JSON string `"false"` permits a simulated fill |
| RZA-06 | Medium | Short-side standing cancellations still pass the email filter | Repetitive “unavailable” cancellation mail remains possible |
| RZA-07 | Medium | One stale native row prevents usable 5m aggregation fallback | A supported timeframe can stop producing research despite fresh source bars |
| RZA-08 | Medium | Cancellation removes confirmed entry history from the chart | A research trigger disappears after its expected paper cancellation |

High means the defect can corrupt signal interpretation or research conclusions. Medium means a bounded behavior or input path is incorrect. None of these findings asserts that a broker order was placed; broker execution is outside this feature.

### RZA-01 — generations are not represented in event identity or payload

**Locations:** [engine emission](../../services/market-data/src/services/little_rzy.py#L107), [workflow rebuild/persistence/readback](../../services/market-data/src/services/rzy_workflow.py#L130), [models](../../services/market-data/src/services/rzy_models.py#L18).

The stream increments `generation`, but `Engine.emit()` never includes it in the payload or event ID. Event IDs depend on version/config/symbol/timeframe/anchors/kind/time. Replaying revised OHLC with the same anchors and timestamps therefore reproduces the same ID even when its target changes. Persistence skips existing IDs. Later `evaluate(persist=False)` merges saved events by config hash only, without generation filtering.

**Reproduced with real SQLite models and the shipped adapter/engine:** change the existing extreme high by 0.20. The stream rebuilds to generation 2; its target changes from **121.22631630924367 to 121.62631630924368**. A subsequent read returns the original ARMED event with target **121.22631630924367**, no `generation` field, and the rebuild stored **zero new events**. This contradicts the prior review's statement that both generations' events are distinguishable by generation.

The sender's timestamp-based supersession check does block old queued events; that protection does not repair mixed API history or preserve a corrected event occurrence. Other event kinds can similarly collide. The archive holds a checkpoint hash and selected state, not the original full input bars, so it also cannot independently reconstruct every original calculation.

**Recommended follow-up, not implemented:** version event occurrences by source generation while maintaining a separate deliberate email-dedup policy; return only the current generation by default and label historical generations explicitly. Preserve enough point-in-time input evidence to replay an archived generation. Regression: revise an extreme without changing anchor times and require distinct, traceable old/new payloads and current-generation readback.

### RZA-02 — incomplete aggregate tails remain “fresh”

**Location:** [aggregate_completed_bars](../../services/market-data/src/services/rzy_data.py#L106), especially the inherited status/stale fields at lines 137–138; digest accepts `research_only` in [workflow](../../services/market-data/src/services/rzy_workflow.py#L264).

The adapter drops 15m/1h bins missing constituents but retains the 5m source's status and `stale` flag. It changes `data_as_of` to the last completed aggregate without recalculating freshness for that timeframe.

**Reproduced:** source bars extend to **20:00 UTC**, but 24 later 15m bins have missing constituents. The newest valid aggregate closes at **14:00 UTC**. At **20:05 UTC**, the result remains **`status=research_only`, `stale=false`**. Thus six-hour-old aggregate structures can enter research/digest eligibility; the fresh-event email age guard does not protect digest candidates from this error. This is an adapter reproduction, not evidence that a particular production email contained such a candidate.

**Recommended follow-up:** compute expected/latest completed aggregate bounds after omissions and classify freshness independently for the requested timeframe. Regression: fresh 5m tail, absent aggregate constituents, old last complete 15m/hourly candle; require stale/unusable status and digest exclusion.

### RZA-03 — retention changes backtest statistics despite identical events

**Locations:** [Engine retention](../../services/market-data/src/services/little_rzy.py#L193), [backtest equity and trade collection](../../services/market-data/src/services/rzy_backtest.py#L8).

The engine retains at most 200 closed positions, including cancellations. The backtest recomputes cumulative equity from those retained positions on every bar and builds its final trade table from the same cache. Once a position is pruned, its realized P&L vanishes from the equity curve and its trade disappears from the final report. This affects trade counts, expectancy, win/loss groups, cancellation totals and drawdown.

**Reproduced:** 26,000 deterministic synthetic bars, seed 0, supported permissive research parameters (recorded in `pure_probe.py`), unchanged production retention limit 200. There are **293 closed-trade events**, but the report contains **199 trades**. Raising only the closed-position/structure retention limits reports all **293**, with **identical emitted events**. Reported drawdown changes from **18.651231176659373 to 23.356868568097468** one-share units. This does not assert that the default parameter set produces 293 trades on that fixture.

The existing bounded/unbounded test verifies event equality, which is necessary but insufficient: both runs can emit identical trades while the report loses old ones.

**Recommended follow-up:** derive immutable cumulative accounting/trade records independently of the engine's operational cache. Regression: exceed the real retention threshold with mixed closed/cancelled positions; require identical full metrics under bounded and unbounded retention. Do this before relying on real-data comparison reports.

### RZA-04 — replay fills across a missing entry interval

**Locations:** [Engine.step](../../services/market-data/src/services/little_rzy.py#L158), [pending entry](../../services/market-data/src/services/little_rzy.py#L341), [replay API](../../services/market-data/src/api/little_rzy.py#L55), [backtest CLI](../../scripts/backtest_little_rzy.py#L12).

Live evaluation segments data through `break_indices` and waits/rebuilds after gaps. The API/CLI replay paths pass input bars directly to `Engine`, which checks ordering but not timeframe/session continuity. A missing intraday candle and a legitimate overnight session boundary are therefore not distinguished in replay.

**Reproduced:** obtain a valid pending 5m entry using the real engine, then move the next candle's timestamps **45 minutes beyond the trigger close**, keeping eligible metadata and valid prices. The engine emits `PAPER_FILL`, with no cancellation. It treats the next supplied bar as the next tradable bar even though the earlier opens are unobserved. The live adapter would identify a segment break.

**Recommended follow-up:** apply the same calendar-aware gap contract to real-data replay and live inputs, or explicitly reject incomplete archives. Do not simply ban overnight/weekend gaps, which can be legitimate. Regression: an intraday hole, valid next-session open and early-close transition must have explicit, consistent behavior.

### RZA-05 — JSON `"false"` bypasses the paper execution gate

**Locations:** [pending-entry gate](../../services/market-data/src/services/little_rzy.py#L353), [untyped replay bar dictionaries](../../services/market-data/src/api/little_rzy.py#L58), JSON CLI ingestion.

`if not b.get('execution_eligible', False)` accepts any truthy value. `ReplayIn.bars` is `list[dict]`, so a string is not coerced or rejected by a typed bar model.

**Reproduced:** a valid next-open candle with **`execution_eligible="false"`** emits a paper fill. This is a replay/input-validation defect; the live stored-price adapter currently supplies the actual boolean `False`, so this reproduction does not bypass its live blocker or create a broker order. The CSV loader converts booleans, but the JSON/API paths do not.

**Recommended follow-up:** validate execution evidence strictly and require `is True`, with typed metadata at the API/CLI boundary. Regression: `False`, `"false"`, `"true"`, `1`, null and missing values must not be accepted as verified boolean evidence.

### RZA-06 — standing short cancellation mail was not fully suppressed

**Locations:** [short-entry failure list](../../services/market-data/src/services/little_rzy.py#L356), [STANDING_BLOCKERS](../../services/market-data/src/services/rzy_workflow.py#L32), [emailable](../../services/market-data/src/services/rzy_workflow.py#L202).

For the actual missing short metadata, the engine records both `shortability_or_borrow_cost_unverified` and `borrow_cost_invalid` because the borrow rate is `None`. The filter's standing set omits `borrow_cost_invalid`.

**Reproduced:** a mirrored valid short trigger with `execution_eligible=False`, `shortable=None`, `borrow_rate_annual=None` produces failures `[data_basis_or_freshness_unverified, shortability_or_borrow_cost_unverified, borrow_cost_invalid]`; `emailable()` returns **True**. The long-only standing cancellation is suppressed, but this short case still qualifies for repetitive mail. No actual mail was sent by this audit.

**Recommended follow-up:** distinguish missing standing borrow evidence from malformed supplied borrow evidence, then classify cancellations consistently. Regression: all missing-metadata-only short cancellations are recorded without mail; genuine newly actionable failure reasons remain visible.

### RZA-07 — stale native history disables the working aggregation fallback

**Location:** [load_bars](../../services/market-data/src/services/rzy_workflow.py#L75).

The fallback runs only when the native 15m/hourly query returns **no rows**. A stale or incomplete native series wins even when fresh, complete 5m constituents are available.

**Reproduced with real SQLite Price rows:** before adding any M15 row, the same symbol produces **19 derived 15m bars**. Add a single native M15 row at the session open; the result becomes **one stale bar**, with `data_as_of=13:45 UTC`, despite the existing 5m history through 18:15 UTC. This is a fallback availability defect, not silent bar fabrication. Production incidence was not measured.

**Recommended follow-up:** choose a sufficiently complete/fresh canonical source under an explicit provenance and generation policy; never splice incompatible native/derived bases silently. Regression: stale native plus valid 5m history should have a documented recovery outcome instead of disabling derivation merely because a row exists.

### RZA-08 — expected paper cancellation erases charted entry history

**Locations:** [terminal event classification](../../frontend/src/lib/rzy.ts#L26), [drawn-structure filtering](../../frontend/src/components/PriceChart.tsx#L546), [engine cancellation state](../../services/market-data/src/services/little_rzy.py#L366).

The engine changes the structure to REJECTED but emits **ENTRY_CANCELLED**, not a REJECTED event. `rzyStructuresToDraw` requires a terminal event for an ended structure, yet ENTRY_CANCELLED is absent from `RZY_TERMINAL`. `PriceChart` then filters trigger markers to drawn structure IDs. ENTRY_CANCELLED also has no label.

**Reproduced through the shipped TypeScript helper:** ARMED → TRIGGERED is drawn; after ENTRY_CANCELLED with structure state REJECTED, the drawn count changes **1 → 0**, and the earlier trigger marker is disallowed. With execution metadata currently unavailable, this is the normal cancellation path, so a valid research signal can disappear from the historical overlay on the next candle.

**Recommended follow-up:** preserve trigger history and show the cancellation separately, with a documented distinction between research confirmation and paper execution. Regression: the real engine event sequence must leave the historical trigger visible and indicate why its fill was cancelled.

## Additional specification and operational gaps

These are not folded into the eight prioritized reproductions above:

- **Exact pivot confirmation timing is no longer shown on the chart.** The latest UI replaces “known [confirmed_at]” with A/B/C labels at `PriceChart.tsx:530`; hover shows bands/line values, not per-anchor confirmation times. The generic “default 7 candles” notice is not the spec's actual `confirmed_at` disclosure. The field is still present in the API.
- **Paper ledger provenance is incomplete.** `/paper` returns positions from every matching symbol stream, across config hashes, without including config hash or generation in each row. Rebuilds replace the current checkpoint; historical positions are not a complete account ledger. Combined with RZA-01, users cannot clearly attribute rows to a configuration/rebuild.
- **Source quality/performance acceptance remains open:** verified provider observation timestamps/delay, execution basis and short borrow inputs are missing; no live eligible paper fills can occur. Real-data §11 backtests and all five comparisons, prospective results and chronological out-of-sample outcomes are not established by synthetic fixtures or these tests.
- **Digest receipt remains unobserved.** This audit occurred before the October 20 09:10 ET run. It did not claim the first digest was delivered or create a calendar event.
- **Deployment metadata is misleading:** the market-data image carries label `stockai.release=a959c5dc`, but the running engine/workflow/data/backtest source hashes match the later rebuild code in the audited checkout. The image is healthy. Use the recorded hashes; the inherited label alone does not identify this application revision. This audit did not rebuild or relabel it.
- **Runtime persistence is not the earlier read-only dry run:** the database still held 95 quarantined 5m checkpoints plus 90 15m and 90 1h checkpoints, all without stored generation numbers, at the read-only observation. The installed code has a rebuild path, so “no unlock path” is now historical; the dry run's 333 usable streams is not proof that persisted maintenance has completed. Latest sampled 100 stored events had no generation field; RZA-01 is independently reproduced on the new code, not inferred from this legacy sample.

## What held up in this review

The reviewed defaults and exercised tests support population BB/Wilder ATR calculation, explicit timezone handling, confirmed-pivot delay, frozen measured geometry, no retrospective fills, conservative target/stop ordering, adverse-gap stops, sequence handling, next-open costs, no automatic reverse trade, persistent same-event dedup, explicit channel opt-in, watchlist ownership, real send-time opt-out, and separate AI/RZY channels. SMA 5/10 are wired through chart indicators/commands and the technical-analysis endpoint. Revisions/gaps have an explicit segment/rebuild policy rather than fabricated candles.

Those positive results do not establish full correctness of every parameter combination, provider revision or long-run report. Tests that compare event streams alone miss accounting retention, and tests that check generation counters alone miss event-generation provenance.

## Recommended order — documentation only

1. Resolve RZA-01 and RZA-02 before treating displayed/digested structures as coherent current evidence.
2. Resolve RZA-03–05 before real-data replay and performance comparisons; retain the existing execution blockers until verified inputs exist.
3. Resolve RZA-06–08 and restore confirmation/provenance disclosure for usable alerts/charts.
4. Measure post-session source corrections and actual digest receipt; then run the spec's real-data and prospective acceptance work. Do not substitute another green unit-test count for those observations.

No application fixes, preference changes, pushes, deployments or live messages were performed by this audit. Only this report, local reproduction/evidence files and its documentation index pointer were added.
