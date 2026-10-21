# Little RZY audit resolution — 2026-10-20

Resolution of the ten findings in the [independent audit](../2026-10-19/little-rzy-independent-audit.md) and its [addendum](little-rzy-audit-addendum.md). Tracker tier 456. Live trading rules (engine thresholds, configuration and its hash `8ceb8cf780c3eb981983`) are unchanged. Little RZY remains a research overlay and watch alert; the [corrected evaluation](little-rzy-evaluation-rerun.md) shows no demonstrated edge under the tested assumptions.

| ID | Finding | Status | Fix | Regression (fails when the fix is reverted) |
|---|---|---|---|---|
| RZA-01 | Rebuilt events lacked generation identity; old targets mixed with new geometry | **Fixed** | Every event carries `generation` and a stable `occurrence_id`; generation ≥ 2 gets a distinct `event_id`. Current views return only the stream's current generation; `include_superseded=true` returns history explicitly. Outbox keys use the occurrence (no duplicate mail across rebuilds); the sender delivers only if the occurrence exists in the current generation with identical trade figures. | Workflow probe `generation_identity`: the audit's scenario (extreme high +0.20) yields a new event with a new target and generation, a current view with one generation, explicit history with the old target, and a suppressed stale queued message. |
| RZA-02 | Derived 15m/1h freshness inherited from 5m | **Fixed** | Freshness from the derived frame's own completed bins: the newest bin that should be complete must exist (`stale_15m_aggregate` / `stale_1h_aggregate`, with `expected_latest_close`). Digest exclusions show the reason, data time, expected close and source. | Workflow probe `derived_freshness` through `evaluate`, the API result and `digest_preview`. |
| RZA-03 | Backtest report read the pruned position cache | **Fixed** | Backtests use an unpruned ledger engine; only realised P&L carries across gaps. | `test_rza03_backtest_ledger_survives_live_retention_caps` (retention forced to 3). |
| RZA-04 | Replay filled across missing bars | **Fixed for replay API, CLI and backtest** | Bars split at calendar-aware gaps; each segment gets a fresh engine (live already did). The bare `Engine` remains calendar-agnostic by design; callers must segment. | `test_rza04_replay_never_fills_across_a_missing_intraday_bar`; CLI smoke test. |
| RZA-05 | `"false"` passed the execution gate | **Fixed** | Only boolean `True` is evidence. | `test_rza05_only_boolean_true_is_execution_evidence` (`"false"`, `"true"`, `1`, `None`, missing). |
| RZA-06 | Standing short cancellations were mailed | **Fixed** | Missing borrow is the standing blocker; `borrow_cost_invalid` only for a supplied malformed value (including booleans). | `test_rza06_…` (None / "x" / True / −0.1 / 0.03). |
| RZA-07 | One stale native row disabled derivation | **Fixed** | One canonical source per evaluation: native only if current and at least as recent as 5m-derived; never spliced; `source` and `alternative` recorded. | Workflow probe `native_derived_choice`. |
| RZA-08 | Cancelled entries vanished from the chart | **Fixed** | `ENTRY_CANCELLED` ends a drawn setup (`no fill` label); the trigger marker stays. | Vitest RZA-08 case; the audit `ui_probe` now reports drawn = 1, trigger allowed. |
| RZA-09 | Control borrow after early exit | **Fixed** (07c8e54f) | Borrow to the actual exit bar at the shared rate. | `test_rza09_…` |
| RZA-10 | Shortened control windows | **Fixed** (07c8e54f) | Controls only where the whole window is observable. | `test_rza10_…` |

Also resolved from the audit's additional gaps:

- **Pivot confirmation disclosure**: chart labels show each pivot's confirmation time (`A ✓16:05`; daily `C ✓10-19`).
- **Paper-ledger provenance**: `/paper` rows carry `config_hash` and `generation`, limited to the current configuration.
- **Storage growth**: `PIVOT_CONFIRMED` events (≈65% of rows, no reader) are no longer persisted, bounding per-rebuild growth.
- **Retrospective holdouts**: the evaluation's 30% periods are labelled retrospective; unseen evidence requires sessions after the 2026-10-20 snapshot.
- **Image label**: the deployed market-data image carries its own release label (previously inherited `a959c5dc`).

Still open (not code defects): verified provider delay / execution basis and short-borrow sources (paper fills stay blocked), prospective outcomes from future sessions, and an input archive sufficient to replay an archived generation exactly (generation summaries hold state and hashes, not full input bars).

The audit's own probes were written to demonstrate the defects; after the fixes `workflow_probe.py` and `pure_probe.py` (needs `shared/` on `PYTHONPATH`) no longer reproduce them, `entry_probe.py` shows the `"false"` gate fixed and still fills across a hole only because it calls the bare engine directly.

## Production verification and the restatement finding (2026-10-21 00:04 UTC deploy)

Verified after deploying `e00630d8`: market-data and frontend healthy (15/15), market-data source drift 0, image label `e00630d8e`, frontend build `ShUrChd1W87DeCuD4PW93` with 0 `localhost:8000` chunks and the new chart/tracker code served. Through the real API: MU/NVDA 5m/15m/1h return `research_only`, one generation per view, `superseded_event_count` plus explicit `superseded_events`, and 15m/1h `expected_latest_close` equal to their newest completed bar (20:00 UTC). The October 20 09:10 ET digest was **provider-accepted at 13:12 UTC**; 19 pattern emails were accepted during the session (3 expired unsent). Legacy quarantines: 0 (all 95 rebuilt in the 08:40 ET maintenance pass).

**New finding: completed 5m bars are restated intraday.** The October 20 session produced **3,956 rebuilds** for `stored_completed_bar_changed` across 314 streams (median 11 minutes apart per stream, up to 30 per stream). The changed values are material (median 0.077%, p90 0.50%, all four OHLC fields together), mostly on bars 1–6 hours old. US 5m routing is UW → yfinance → Polygon; the likely cause (not yet confirmed in ingest logs) is fallback fetches from a different vendor overwriting whole days of completed bars. Charts and other 5m consumers see the same flips.

Bounded the same night (approved), with regressions that fail when reverted:

- **Rebuild cap**: at most one rebuild per stream per 30 minutes. Inside the window a restated stream pauses (`restatement_pending`: no bars processed on a known-restated history, no mail, queued mail blocked at send) and rebuilds once after. Simulated on October 20: 3,956 → 1,868 rebuilds (60 min → 1,279; 120 → 927).
- **Retention**: last 5 generations of archives and stored events per stream.
- **Digest preview** never rebuilds in memory (`rebuild_pending` exclusion) — the bulk preview previously exceeded the gateway timeout (504).

Next (separate, reviewed change): confirm the provider-flip cause in the next session's ingest logs and stop a different provider from overwriting completed bars.

Transitional: events rebuilt before this deploy were stored under the old identity scheme, so a stream's current-generation event list stays empty until its next rebuild or new bars.
